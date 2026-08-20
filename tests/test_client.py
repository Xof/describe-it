"""Tests for `describe_it.client.OllamaClient`.

Everything here runs against the in-process fake server from `conftest`, so the
assertions cover the real urllib path: what went out on the wire, and what the
client made of what came back. The two exceptions are the wrapped-timeout and
reset-connection cases, which a cooperating server cannot produce on demand and
which are provoked by replacing one client's opener for a single call.
"""

import base64
import urllib.request
from collections.abc import Callable
from http.client import HTTPException
from typing import NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import FakeOllama
from describe_it.client import OllamaClient
from describe_it.config import DEFAULT_HOST, normalise_host
from describe_it.exceptions import (
    ModelNotFoundError,
    OllamaConnectionError,
    OllamaResponseError,
    OllamaTimeoutError,
)

_IMAGE = b"\xff\xd8not-really-a-jpeg\xff\xd9"
_MODEL = "llava:7b"


def _chat(
    client: OllamaClient,
    *,
    model: str = _MODEL,
    prompt: str = "Describe this image.",
    image: bytes = _IMAGE,
    keep_alive: str | int | None = None,
    options: dict[str, object] | None = None,
) -> str:
    """Call `chat` with everything defaulted, so each test states only its point."""
    return client.chat(
        model=model,
        prompt=prompt,
        image=image,
        keep_alive=keep_alive,
        options=options if options is not None else {"temperature": 0.2},
    )


def test_chat_sends_the_documented_body_and_returns_the_content(
    server: FakeOllama,
) -> None:
    server.script_json("/api/chat", {"message": {"content": "A red circle."}})

    reply = _chat(OllamaClient(host=server.url))

    assert reply == "A red circle."
    recorded = server.requests[0]
    assert recorded.method == "POST"
    assert recorded.path == "/api/chat"
    assert recorded.headers["Content-Type"] == "application/json"
    body = recorded.json_body
    assert body["model"] == _MODEL
    assert body["stream"] is False
    assert body["think"] is False
    assert body["options"] == {"temperature": 0.2}
    # Omitted, not null: null would override the server's own default.
    assert "keep_alive" not in body
    messages = body["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Describe this image."
    assert len(messages[0]["images"]) == 1
    assert base64.b64decode(messages[0]["images"][0]) == _IMAGE


@pytest.mark.parametrize("keep_alive", ["30m", 0])
def test_chat_forwards_keep_alive_when_it_is_set(
    server: FakeOllama, keep_alive: str | int
) -> None:
    # 0 is the interesting one: it means "unload immediately", it is falsy, and
    # only a None check keeps it in the body.
    server.script_json("/api/chat", {"message": {"content": "A red circle."}})

    _chat(OllamaClient(host=server.url), keep_alive=keep_alive)

    assert server.requests[0].json_body["keep_alive"] == keep_alive


def test_chat_reaches_the_server_however_the_host_is_spelled(
    server: FakeOllama,
) -> None:
    port = urlsplit(server.url).port
    spellings = [
        f"127.0.0.1:{port}",
        f"http://127.0.0.1:{port}",
        f"http://127.0.0.1:{port}/",
        f"  http://127.0.0.1:{port}///  ",
    ]
    for _ in spellings:
        server.script_json("/api/chat", {"message": {"content": "A red circle."}})

    for spelling in spellings:
        assert _chat(OllamaClient(host=spelling)) == "A red circle."

    assert server.paths() == ["/api/chat"] * len(spellings)


@pytest.mark.parametrize(
    ("given_host", "expected"),
    [
        ("localhost:11434", "http://localhost:11434"),
        ("http://x:1/", "http://x:1"),
        ("http://x:1///", "http://x:1"),
        ("  localhost:11434  ", "http://localhost:11434"),
        # A host with no port means Ollama's port, as it does to Ollama's own
        # CLI -- not port 80, where a web server usually answers.
        ("localhost", "http://localhost:11434"),
        ("http://ollama.example.com", "http://ollama.example.com:11434"),
        ("https://h", "https://h:443"),
        ("https://ollama.example.com/", "https://ollama.example.com:443"),
        ("h:1", "http://h:1"),
        ("[::1]", "http://[::1]:11434"),
        # An uppercase scheme is the same scheme.
        ("HTTP://h:1", "http://h:1"),
        # A path survives: that is how an Ollama behind a prefix is addressed.
        ("http://proxy/ollama/", "http://proxy:11434/ollama"),
        ("http://proxy:8080/ollama", "http://proxy:8080/ollama"),
    ],
)
def test_normalise_host_rewrites_only_what_it_must(
    given_host: str, expected: str
) -> None:
    assert normalise_host(given_host) == expected


@pytest.mark.parametrize("given_host", ["", "   ", "http://", "///"])
def test_normalise_host_rejects_a_host_with_no_hostname(given_host: str) -> None:
    with pytest.raises(ValueError, match="has no hostname"):
        normalise_host(given_host)


@given(
    scheme=st.sampled_from(["", "http://", "https://"]),
    hostname=st.sampled_from(["localhost", "127.0.0.1", "ollama.internal", "[::1]"]),
    port=st.sampled_from(["", ":1", ":11434"]),
    slashes=st.sampled_from(["", "/", "//", "///"]),
)
@settings(max_examples=50, deadline=None)
def test_normalise_host_always_produces_a_usable_base_url(
    scheme: str, hostname: str, port: str, slashes: str
) -> None:
    normalised = normalise_host(f"{scheme}{hostname}{port}{slashes}")

    assert normalised.startswith(("http://", "https://"))
    assert not normalised.endswith("/")
    split = urlsplit(normalised)
    # An absent scheme becomes http; a stated one survives untouched.
    expected_scheme = scheme.removesuffix("://") or "http"
    assert split.scheme == expected_scheme
    # The port is always explicit afterwards: a stated one, or the default for
    # the scheme -- 11434 for Ollama over http, 443 for https.
    expected_port = port or f":{11434 if expected_scheme == 'http' else 443}"
    assert split.netloc == f"{hostname}{expected_port}"
    # Round-trips, so appending a path to it produces a URL urllib can parse.
    assert urlunsplit(split) == normalised


def test_the_default_host_is_the_static_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deliberate: the transport does not read the environment. Resolving
    # $OLLAMA_HOST is Describer's job, so a directly constructed client cannot
    # be redirected by an environment its caller never looked at.
    monkeypatch.setenv("OLLAMA_HOST", "http://elsewhere.invalid:1")

    assert OllamaClient().host == DEFAULT_HOST


def test_a_missing_model_names_the_pull_command(server: FakeOllama) -> None:
    server.script_json("/api/chat", {"error": "model 'foo' not found"}, status=404)

    with pytest.raises(ModelNotFoundError, match="ollama pull foo") as caught:
        _chat(OllamaClient(host=server.url), model="foo")

    assert caught.value.model == "foo"
    assert isinstance(caught.value.__cause__, HTTPError)


@pytest.mark.parametrize(
    "body",
    [
        b"<html>Not Found</html>",
        b"[]",
        b'{"error": 42}',
        b'{"error": "unknown path /api/chat"}',
        b'{"detail": "not found"}',
    ],
)
def test_a_404_that_is_not_about_the_model_is_a_response_error(
    server: FakeOllama, body: bytes
) -> None:
    # Only Ollama's own "not found" wording earns a ModelNotFoundError. A 404
    # from a proxy or a wrong path must not send the caller off to pull a model
    # that exists.
    server.script("/api/chat", status=404, body=body)

    with pytest.raises(OllamaResponseError) as caught:
        _chat(OllamaClient(host=server.url))

    assert caught.value.status_code == 404
    assert caught.value.body == body.decode()


@pytest.mark.parametrize("status", [400, 500])
def test_an_error_status_carries_the_status_and_the_body(
    server: FakeOllama, status: int
) -> None:
    body = b'{"error": "\\"llava:7b\\" does not support chat"}'
    server.script("/api/chat", status=status, body=body)

    with pytest.raises(OllamaResponseError, match="does not support chat") as caught:
        _chat(OllamaClient(host=server.url))

    assert caught.value.status_code == status
    assert caught.value.body == body.decode()
    assert isinstance(caught.value.__cause__, URLError)


def test_a_long_error_body_is_truncated(server: FakeOllama) -> None:
    # A host that is not Ollama answers with a page, not a line of JSON.
    server.script("/api/chat", status=502, body=b"x" * 900)

    with pytest.raises(OllamaResponseError) as caught:
        _chat(OllamaClient(host=server.url))

    assert caught.value.body.startswith("x" * 500)
    assert "900 characters in total" in caught.value.body
    assert len(caught.value.body) < 600


def test_a_body_at_the_truncation_limit_survives_whole(server: FakeOllama) -> None:
    # The boundary itself: 500 characters is short enough to keep.
    server.script("/api/chat", status=502, body=b"x" * 500)

    with pytest.raises(OllamaResponseError) as caught:
        _chat(OllamaClient(host=server.url))

    assert caught.value.body == "x" * 500


def test_a_non_json_success_is_a_response_error(server: FakeOllama) -> None:
    server.script("/api/chat", body=b"I am a teapot, not an Ollama.")

    with pytest.raises(OllamaResponseError, match="non-JSON body") as caught:
        _chat(OllamaClient(host=server.url))

    assert caught.value.status_code == 200
    assert caught.value.body == "I am a teapot, not an Ollama."


def test_a_json_array_is_not_the_documented_shape(server: FakeOllama) -> None:
    server.script_json("/api/chat", ["not", "an", "object"])

    with pytest.raises(OllamaResponseError, match="not an object") as caught:
        _chat(OllamaClient(host=server.url))

    assert caught.value.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": "a string, not an object"},
        {"message": {}},
        {"message": {"content": None}},
        {"message": {"content": 42}},
    ],
)
def test_a_reply_without_string_content_is_a_response_error(
    server: FakeOllama, payload: object
) -> None:
    server.script_json("/api/chat", payload)

    with pytest.raises(OllamaResponseError, match="no message content") as caught:
        _chat(OllamaClient(host=server.url))

    assert caught.value.status_code == 200


def test_a_refused_connection_is_a_connection_error(closed_port: int) -> None:
    client = OllamaClient(host=f"127.0.0.1:{closed_port}")

    with pytest.raises(OllamaConnectionError, match="could not connect") as caught:
        _chat(client)

    assert isinstance(caught.value.__cause__, URLError)


def test_a_slow_server_is_a_timeout(server: FakeOllama) -> None:
    # The handler sleeps well past the timeout in a daemon thread; the client
    # gives up first and teardown does not wait for the sleeper.
    server.script_json("/api/chat", {"message": {"content": "too late"}}, delay=1.0)

    with pytest.raises(OllamaTimeoutError, match=r"timed out after 0\.2s"):
        _chat(OllamaClient(host=server.url, timeout=0.2))


def test_a_timeout_wrapped_by_urllib_is_still_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A timeout while the connection is being established is wrapped in a
    # URLError; one during the read is raised bare. Only a hung TCP handshake
    # produces the wrapped form, which no cooperating server can arrange.
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise URLError(TimeoutError("timed out"))

    client = OllamaClient(host="http://127.0.0.1:1")
    monkeypatch.setattr(client._opener, "open", fail)

    with pytest.raises(OllamaTimeoutError, match="timed out after") as caught:
        _chat(client)

    assert isinstance(caught.value.__cause__, URLError)


def test_a_reset_connection_is_a_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Nothing below URLError may escape unwrapped: the library's contract is
    # that every failure is one of its own exceptions.
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise ConnectionResetError("connection reset by peer")

    client = OllamaClient(host="http://127.0.0.1:1")
    monkeypatch.setattr(client._opener, "open", fail)

    with pytest.raises(OllamaConnectionError, match="connection reset") as caught:
        _chat(client)

    assert isinstance(caught.value.__cause__, ConnectionResetError)


def test_show_reports_a_present_model(server: FakeOllama) -> None:
    server.script_json("/api/show", {"details": {"family": "llama"}})

    assert OllamaClient(host=server.url).show(_MODEL) is True
    assert server.requests[0].json_body == {"model": _MODEL}


def test_show_reports_an_absent_model_without_raising(server: FakeOllama) -> None:
    server.script_json("/api/show", {"error": "model 'foo' not found"}, status=404)

    assert OllamaClient(host=server.url).show("foo") is False


def test_show_still_raises_when_the_server_is_broken(server: FakeOllama) -> None:
    # "The server is down" must never be reported as "the model is absent".
    server.script("/api/show", status=500, body=b'{"error": "internal"}')

    with pytest.raises(OllamaResponseError) as caught:
        OllamaClient(host=server.url).show(_MODEL)

    assert caught.value.status_code == 500
    assert caught.value.body == '{"error": "internal"}'


def test_show_is_a_connection_error_when_nothing_is_listening(
    closed_port: int,
) -> None:
    with pytest.raises(OllamaConnectionError):
        OllamaClient(host=f"127.0.0.1:{closed_port}").show(_MODEL)


def test_pull_returns_when_the_stream_reports_success(server: FakeOllama) -> None:
    server.script(
        "/api/pull",
        body=[
            b'{"status": "pulling manifest"}\n',
            # Ollama pads the stream; a blank line is not the end of it.
            b"\n",
            b'{"status": "pulling 1a2b3c", "completed": 1, "total": 2}\n',
            b'{"status": "success"}\n',
        ],
    )

    OllamaClient(host=server.url).pull("foo")

    assert server.requests[0].json_body == {"model": "foo", "stream": True}


def test_pull_raises_on_an_error_line(server: FakeOllama) -> None:
    # Verbatim what Ollama 0.32 answers for a model that does not exist
    # upstream: a 200, a first progress line, and then the failure. There is no
    # 404 to map, which is why the error line has to be read and acted on.
    server.script_ndjson(
        "/api/pull",
        [
            {"status": "pulling manifest"},
            {"error": "pull model manifest: file does not exist"},
        ],
    )

    with pytest.raises(OllamaResponseError, match="file does not exist") as caught:
        OllamaClient(host=server.url).pull("foo")

    assert caught.value.status_code == 200


def test_pull_raises_when_the_stream_stops_early(server: FakeOllama) -> None:
    # The connection closed mid-download. Silence is not success: the model
    # would be missing or half-written.
    server.script_ndjson(
        "/api/pull", [{"status": "pulling manifest"}, {"status": "pulling 1a2b3c"}]
    )

    with pytest.raises(OllamaResponseError, match="ended without reporting success"):
        OllamaClient(host=server.url).pull("foo")


def test_pull_raises_on_a_line_that_is_not_json(server: FakeOllama) -> None:
    server.script("/api/pull", body=b"pulling manifest\n")

    with pytest.raises(OllamaResponseError, match="non-JSON body"):
        OllamaClient(host=server.url).pull("foo")


def test_a_stalled_pull_times_out_mid_stream(server: FakeOllama) -> None:
    # The other half of the timeout contract: the status line and the first
    # progress line arrived, and the stall is in the middle of the stream. This
    # is why the response is read inside the error-mapping context manager
    # rather than handed back from it.
    server.script_ndjson(
        "/api/pull",
        [{"status": "pulling manifest"}, {"status": "success"}],
        chunk_delay=1.0,
    )

    with pytest.raises(OllamaTimeoutError, match=r"timed out after 0\.2s") as caught:
        OllamaClient(host=server.url, timeout=0.2).pull("foo")

    assert isinstance(caught.value.__cause__, TimeoutError)


def test_a_reply_that_is_not_http_at_all_is_a_response_error(
    server: FakeOllama,
) -> None:
    # An HTTPS listener, or an unrelated daemon, on the configured port. The
    # failure is an http.client.HTTPException, which is not an OSError and so
    # would escape unwrapped if it were not mapped explicitly.
    server.script("/api/chat", raw=b"\x16\x03\x01 this is not a status line\r\n\r\n")

    with pytest.raises(OllamaResponseError, match="malformed HTTP reply") as caught:
        _chat(OllamaClient(host=server.url))

    assert caught.value.status_code is None
    assert isinstance(caught.value.__cause__, HTTPException)


def test_a_reply_cut_short_of_its_declared_length_is_a_response_error(
    server: FakeOllama,
) -> None:
    server.script(
        "/api/chat",
        raw=b"HTTP/1.0 200 OK\r\nContent-Length: 100\r\n\r\ncut short",
    )

    with pytest.raises(OllamaResponseError, match="malformed HTTP reply") as caught:
        _chat(OllamaClient(host=server.url))

    assert isinstance(caught.value.__cause__, HTTPException)


def test_an_error_body_cut_short_still_reports_its_status(server: FakeOllama) -> None:
    # The body is supporting evidence; the status is the finding. Losing the
    # body must not lose the 500 with it.
    server.script(
        "/api/chat",
        raw=b"HTTP/1.0 500 Internal Server Error\r\nContent-Length: 100\r\n\r\ncut",
    )

    with pytest.raises(OllamaResponseError, match="failed with HTTP 500") as caught:
        _chat(OllamaClient(host=server.url))

    assert caught.value.status_code == 500
    assert caught.value.body == ""


@pytest.mark.parametrize("operation", ["chat", "show", "pull"])
def test_an_error_message_names_the_operation_the_model_and_the_host(
    closed_port: int, operation: str
) -> None:
    # A message that says only "connection refused" sends its reader to the
    # wrong machine when two hosts are configured.
    client = OllamaClient(host=f"127.0.0.1:{closed_port}")
    calls: dict[str, Callable[[], object]] = {
        "chat": lambda: _chat(client),
        "show": lambda: client.show(_MODEL),
        "pull": lambda: client.pull(_MODEL),
    }

    with pytest.raises(OllamaConnectionError) as caught:
        calls[operation]()

    assert str(caught.value).startswith(
        f"{operation} request for model '{_MODEL}' on http://127.0.0.1:{closed_port} "
    )


def test_a_not_found_body_on_another_status_is_not_a_missing_model(
    server: FakeOllama,
) -> None:
    # The 404 is half the evidence. Ollama says "not found" in other contexts
    # too, and a 500 is a server fault however it words itself.
    server.script("/api/chat", status=500, body=b'{"error": "model \'foo\' not found"}')

    with pytest.raises(OllamaResponseError) as caught:
        _chat(OllamaClient(host=server.url), model="foo")

    assert caught.value.status_code == 500


def test_the_missing_model_marker_is_matched_case_insensitively(
    server: FakeOllama,
) -> None:
    server.script("/api/chat", status=404, body=b'{"error": "Model \'foo\' NOT FOUND"}')

    with pytest.raises(ModelNotFoundError):
        _chat(OllamaClient(host=server.url), model="foo")


def test_proxy_environment_variables_are_ignored(
    server: FakeOllama, other_server: FakeOllama, monkeypatch: pytest.MonkeyPatch
) -> None:
    # $http_proxy is set machine-wide in many corporate environments, and
    # urllib exempts nothing from it -- not even loopback. Ollama is a local
    # service and its own CLI honours no such variable, so neither does this.
    monkeypatch.setenv("http_proxy", other_server.url)
    monkeypatch.setenv("HTTP_PROXY", other_server.url)
    monkeypatch.setenv("ALL_PROXY", other_server.url)
    # The bypass list is the one thing that could make this pass by accident.
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    # urllib caches one global opener, built on first use; clearing it means a
    # client that fell back to the module-level urlopen would build a fresh one
    # now, pick the proxy up, and fail this test rather than pass it by luck.
    monkeypatch.setattr(urllib.request, "_opener", None, raising=False)
    server.script_json("/api/chat", {"message": {"content": "A red circle."}})

    assert _chat(OllamaClient(host=server.url)) == "A red circle."

    assert server.paths() == ["/api/chat"]
    assert other_server.requests == []


def test_a_redirect_is_reported_rather_than_followed(
    server: FakeOllama, other_server: FakeOllama
) -> None:
    # urllib follows a 30x by reissuing the request, and rewrites a redirected
    # POST into a body-less GET: the image would silently not be sent and the
    # answer would be about nothing. The redirect is the finding.
    location = f"{other_server.url}/api/chat"
    server.script("/api/chat", status=301, headers=(("Location", location),))

    with pytest.raises(
        OllamaResponseError, match="redirects are not followed"
    ) as caught:
        _chat(OllamaClient(host=server.url))

    assert location in str(caught.value)
    assert caught.value.status_code == 301
    assert other_server.requests == []


def test_a_long_malformed_status_line_is_truncated(server: FakeOllama) -> None:
    # A bad status line is server output like any other body, and this one is
    # quoted into the message; unbounded, a chatty non-HTTP daemon would become
    # the whole error.
    server.script("/api/chat", raw=b"x" * 3000 + b"\r\n\r\n")

    with pytest.raises(OllamaResponseError, match="malformed HTTP reply") as caught:
        _chat(OllamaClient(host=server.url))

    assert len(str(caught.value)) < 800


def test_a_reply_cut_short_after_a_status_keeps_the_status(
    server: FakeOllama,
) -> None:
    # The status line was read before the body failed, so it is known and worth
    # reporting; only a reply that never parsed as HTTP has no status at all.
    server.script(
        "/api/chat",
        raw=b"HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\ntoo short",
    )

    with pytest.raises(OllamaResponseError, match="malformed HTTP reply") as caught:
        _chat(OllamaClient(host=server.url))

    assert caught.value.status_code == 200


@pytest.mark.parametrize("timeout", [0, 0.0, -1.0])
def test_a_non_positive_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout must be a positive"):
        OllamaClient(timeout=timeout)


@pytest.mark.parametrize("timeout", ["5", True, None])
def test_a_timeout_that_is_not_a_number_is_rejected(timeout: object) -> None:
    # Without this, a string timeout surfaces from deep inside socket setup.
    with pytest.raises(TypeError, match="timeout must be a number"):
        OllamaClient(timeout=timeout)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "given_host",
    [
        "http://user:secret@h:1",
        "http://user@h:1",
        "user:secret@h:1",
    ],
)
def test_normalise_host_rejects_embedded_credentials(given_host: str) -> None:
    with pytest.raises(ValueError, match="must not contain credentials") as caught:
        normalise_host(given_host)

    # The message must not quote the host back, or the password lands in every
    # log that records the exception.
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "given_host",
    ["http://h:1?debug=1", "http://h:1#frag", "http://h:1/ollama?x=1"],
)
def test_normalise_host_rejects_a_query_string_or_fragment(given_host: str) -> None:
    with pytest.raises(ValueError, match="query string or fragment"):
        normalise_host(given_host)


@pytest.mark.parametrize("given_host", ["ftp://h:1", "file://h/x", "ws://h:1"])
def test_normalise_host_rejects_a_scheme_it_cannot_post_to(given_host: str) -> None:
    with pytest.raises(ValueError, match="must use http or https"):
        normalise_host(given_host)


@pytest.mark.parametrize("given_host", ["localhost:eleven", "http://h:1x"])
def test_normalise_host_rejects_an_unreadable_port(given_host: str) -> None:
    with pytest.raises(ValueError, match="unreadable port"):
        normalise_host(given_host)
