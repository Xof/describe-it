"""Shared fixtures: an in-process stand-in for the Ollama server.

The client tests run against a real HTTP server on a real socket rather than
against a patched `urlopen`, because most of what `client.py` has to get right
is a reaction to something only a real transport produces: a 404 carrying a
JSON body, a 200 carrying prose, an NDJSON stream that stops early, a read that
never completes. A mock would have to be taught each of those and would then be
asserting against its own teaching.

The server is scripted per path and records every request it receives, so a
test says what the server should answer and then checks what the client
actually sent.
"""

import json
import socket
import sys
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

import pytest


@dataclass(frozen=True)
class ScriptedResponse:
    """One queued answer for one path."""

    status: int = 200
    # A sequence of chunks is written piece by piece, which is how a streamed
    # NDJSON reply reaches the client as several reads rather than one.
    body: bytes | Sequence[bytes] = b""
    headers: tuple[tuple[str, str], ...] = ()
    # Seconds to wait before answering at all. Used to provoke a client-side
    # timeout; nothing is written before the wait, so the client sees silence
    # rather than a half-sent response.
    delay: float = 0.0
    # Seconds to wait between chunks. This is the other kind of timeout: the
    # client has a status line and part of a body, and stalls mid-read.
    chunk_delay: float = 0.0
    # Bytes written with no HTTP framing at all, in place of everything above.
    # The only way to hand the client a reply that is not valid HTTP.
    raw: bytes | None = None

    def chunks(self) -> list[bytes]:
        if isinstance(self.body, bytes):
            return [self.body]
        return list(self.body)


@dataclass(frozen=True)
class RecordedRequest:
    """One request the fake server received."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    @property
    def json_body(self) -> dict[str, Any]:
        """Return the request body decoded as a JSON object."""
        parsed = json.loads(self.body)
        assert isinstance(parsed, dict)
        return parsed


class _ScriptedHandler(BaseHTTPRequestHandler):
    """Answers POSTs from the queue its server carries."""

    # HTTP/1.0 closes the connection after each response, which is what makes
    # "the stream ended" a real end of stream for the client under test.
    protocol_version = "HTTP/1.0"

    # The camel-cased name is BaseHTTPRequestHandler's dispatch convention:
    # it looks for a method named after the HTTP verb.
    def do_POST(self) -> None:
        fake = cast("_ScriptedServer", self.server).fake
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        fake.requests.append(
            RecordedRequest(
                method=self.command,
                path=self.path,
                headers=dict(self.headers.items()),
                body=body,
            )
        )

        scripted = fake.take(self.path)
        if scripted.delay:
            time.sleep(scripted.delay)
        if scripted.raw is not None:
            self.wfile.write(scripted.raw)
            self.close_connection = True
            return
        chunks = scripted.chunks()
        self.send_response(scripted.status)
        for name, value in scripted.headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(sum(len(chunk) for chunk in chunks)))
        self.end_headers()
        for index, chunk in enumerate(chunks):
            if index and scripted.chunk_delay:
                time.sleep(scripted.chunk_delay)
            self.wfile.write(chunk)
            self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        # The default writes a line to stderr for every request, which buries
        # the pytest output under traffic the tests already assert on.
        pass


class _ScriptedServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that carries the script and the request log."""

    def __init__(self, fake: "FakeOllama") -> None:
        self.fake = fake
        # Port 0: the OS picks a free one, so tests never collide with each
        # other or with a real Ollama on 11434.
        super().__init__(("127.0.0.1", 0), _ScriptedHandler)

    def handle_error(self, request: object, client_address: object) -> None:
        failure = sys.exception()
        if isinstance(failure, ConnectionError):
            # Expected, not exceptional: a test that provokes a client-side
            # timeout leaves this thread writing to a socket the client has
            # already closed. The default handler prints a traceback for each.
            return
        # Anything else is a bug in the fixture, and swallowing it would reach
        # the test as a client-side connection error instead — a fault in the
        # test harness wearing the costume of the behaviour under test. Kept
        # for teardown to raise, since this thread has no one to raise to.
        self.fake.handler_failures.append(failure)


class FakeOllama:
    """A scriptable HTTP server standing in for Ollama.

    Attributes:
        requests: Every request received, in order. Only read after the call
            under test has returned, so no locking is needed on this side.
        handler_failures: Exceptions raised inside the handler, re-raised at
            teardown so a broken fixture cannot masquerade as a test result.
    """

    def __init__(self) -> None:
        """Start the server on a free port, in a daemon thread."""
        self.requests: list[RecordedRequest] = []
        self.handler_failures: list[BaseException | None] = []
        # Per path, because a single test can drive /api/show and /api/pull in
        # sequence and each needs its own answer.
        self._script: dict[str, deque[ScriptedResponse]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._server = _ScriptedServer(self)
        self._thread = threading.Thread(
            # The poll interval is how long shutdown() waits for the accept
            # loop to notice it, and there is one teardown per test: the
            # default half-second would cost the suite more than the tests do.
            target=partial(self._server.serve_forever, poll_interval=0.01),
            name="fake-ollama",
            daemon=True,
        )
        self._thread.start()

    @property
    def url(self) -> str:
        """Return the base URL the server is listening on."""
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port!s}"

    def script(
        self,
        path: str,
        *,
        status: int = 200,
        body: bytes | Sequence[bytes] = b"",
        headers: tuple[tuple[str, str], ...] = (),
        delay: float = 0.0,
        chunk_delay: float = 0.0,
        raw: bytes | None = None,
    ) -> None:
        """Queue one answer for one path."""
        with self._lock:
            self._script[path].append(
                ScriptedResponse(
                    status=status,
                    body=body,
                    headers=headers,
                    delay=delay,
                    chunk_delay=chunk_delay,
                    raw=raw,
                )
            )

    def script_json(
        self, path: str, payload: object, *, status: int = 200, delay: float = 0.0
    ) -> None:
        """Queue one JSON answer for one path."""
        self.script(
            path,
            status=status,
            body=json.dumps(payload).encode("utf-8"),
            headers=(("Content-Type", "application/json"),),
            delay=delay,
        )

    def script_ndjson(
        self,
        path: str,
        lines: Sequence[object],
        *,
        status: int = 200,
        chunk_delay: float = 0.0,
    ) -> None:
        """Queue a streamed answer: one JSON object per line, one chunk each."""
        self.script(
            path,
            status=status,
            body=[f"{json.dumps(line)}\n".encode() for line in lines],
            headers=(("Content-Type", "application/x-ndjson"),),
            chunk_delay=chunk_delay,
        )

    def take(self, path: str) -> ScriptedResponse:
        """Pop the next queued answer for a path, for the handler's use."""
        with self._lock:
            queue = self._script[path]
            if not queue:
                # 599 is not a status any test scripts, so a test that forgot
                # to script cannot accidentally satisfy an assertion about a
                # real error status.
                return ScriptedResponse(
                    status=599,
                    body=f"no response scripted for {path}".encode(),
                )
            return queue.popleft()

    def paths(self) -> list[str]:
        """Return the path of every request received, in order."""
        return [request.path for request in self.requests]

    def close(self) -> None:
        """Stop the server, and report anything that went wrong inside it."""
        self._server.shutdown()
        # ThreadingHTTPServer sets daemon_threads, so server_close does not
        # join the handler threads; a handler still asleep in a delay cannot
        # hold teardown up. The accept loop, though, must really have stopped.
        self._server.server_close()
        self._thread.join(timeout=5)
        assert not self._thread.is_alive(), "the fake server's accept loop hung"
        if self.handler_failures:
            failure = self.handler_failures[0]
            raise AssertionError(
                f"the fake server's request handler failed: {failure!r}"
            ) from failure


def _running_server() -> Iterator[FakeOllama]:
    """Start a fake server and stop it once the test is done with it."""
    fake = FakeOllama()
    try:
        yield fake
    finally:
        fake.close()


@pytest.fixture
def server() -> Iterator[FakeOllama]:
    """Yield a running fake Ollama, torn down after the test."""
    yield from _running_server()


@pytest.fixture
def other_server() -> Iterator[FakeOllama]:
    """Yield a second fake server, for asserting traffic did NOT reach it.

    Standing in for a proxy or a redirect target: a test points the client's
    environment or a `Location` header at this one and then checks it recorded
    nothing, which no single-server fixture can express.
    """
    yield from _running_server()


@pytest.fixture
def closed_port() -> int:
    """Return a port on localhost with nothing listening on it.

    Bound and released rather than picked at random, so the number is one the
    OS handed out and a connection to it is refused rather than filtered.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port
