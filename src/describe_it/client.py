"""HTTP transport for the Ollama server.

`OllamaClient` is the only part of describe-it that opens a socket, and the
only part that knows the shape of Ollama's API. It deals in models, prompts as
opaque strings and images as opaque bytes; PIL, prompt wording and reply
cleanup live in `image`, `prompt` and `describer`. The split is what lets the
transport be tested against a real HTTP server with no model installed, and the
pure layers be tested with no server at all.

The transport is `urllib.request` from the standard library. Three small JSON
POSTs do not justify pulling httpx and pydantic into the dependency tree of a
package whose only real dependency is Pillow, and a hand-rolled request gives
the tests the true urllib code path — real 404 bodies, real streamed NDJSON,
real socket timeouts.
"""

import base64
import json
import math
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http import HTTPStatus
from http.client import HTTPException, HTTPResponse
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPDefaultErrorHandler,
    HTTPErrorProcessor,
    HTTPHandler,
    HTTPSHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
)

from describe_it.config import DEFAULT_HOST, normalise_host
from describe_it.exceptions import (
    ModelNotFoundError,
    OllamaConnectionError,
    OllamaError,
    OllamaResponseError,
    OllamaTimeoutError,
)

# Error bodies go into exception messages, and a misconfigured host answers
# with an HTML error page rather than Ollama's one-line JSON. Truncating keeps
# a stray proxy from dumping a kilobyte of markup into every log line; the
# status code, which is the part that actually diagnoses, is never truncated.
_MAX_BODY_CHARS = 500

# The substring Ollama uses for a model it does not have ("model 'x' not
# found"). Matched case-insensitively on the JSON `error` field only, so a 404
# from something that is not Ollama at all is reported as what it is.
_MISSING_MODEL_MARKER = "not found"


class OllamaClient:
    """A minimal client for the three Ollama endpoints describe-it uses.

    Attributes:
        host: The normalised base URL every request is built on.
        timeout: Socket timeout in seconds, applied to each individual read
            rather than to a whole call — see `pull`.
    """

    def __init__(self, host: str = DEFAULT_HOST, timeout: float = 120.0) -> None:
        """Configure the client.

        Args:
            host: Base URL or bare `host:port` of the Ollama server. The
                default is the static fallback rather than `$OLLAMA_HOST`: this
                class is a transport, and reading the ambient environment is a
                policy decision that belongs to `Describer`, which resolves the
                variable and passes the result in explicitly.
            timeout: Socket timeout in seconds, applied per read.  Generous by
                default because a cold model load on a laptop runs to tens of
                seconds.

        Raises:
            TypeError: If `timeout` is not a number of seconds.
            ValueError: If `host` is not usable as a base URL, or `timeout` is
                not positive. Both are caught here rather than at the first
                request, so a misconfiguration fails where it was written.
        """
        # bool is an int, and `timeout=True` meaning "one second" is a typo
        # every time it is written.
        if isinstance(timeout, bool) or not isinstance(timeout, int | float):
            raise TypeError(
                f"timeout must be a number of seconds, not {type(timeout).__name__}"
            )
        # inf and nan both slip past a plain `<= 0`: nan loses every comparison,
        # and an infinite timeout is a hang with extra steps.
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(
                f"timeout must be a positive, finite number of seconds, not {timeout}"
            )
        self.host = normalise_host(host)
        self.timeout = timeout
        self._opener = _build_opener()

    def chat(
        self,
        *,
        model: str,
        prompt: str,
        image: bytes,
        keep_alive: str | int | None,
        options: dict[str, object],
    ) -> str:
        """Ask a model one question about one image and return its reply.

        Args:
            model: The Ollama model tag to run.
            prompt: The complete text of the single user message.
            image: The image bytes, in a format the model understands (JPEG,
                here); base64 encoding for the JSON body happens inside.
            keep_alive: How long the server should keep the model resident
                after this call. `None` leaves the field out of the request
                entirely, which is not the same as sending null: it lets the
                server apply its own default of five minutes.
            options: Ollama sampling options, passed through verbatim.

        Returns:
            The model's reply text, exactly as it arrived — cleanup is the
            caller's job.

        Raises:
            OllamaConnectionError: If the server cannot be reached.
            OllamaTimeoutError: If a read exceeds `timeout`.
            ModelNotFoundError: If the server does not have `model`.
            OllamaResponseError: On any other non-2xx status, or a reply that
                is not the documented JSON shape.
        """
        payload: dict[str, object] = {
            "model": model,
            "stream": False,
            # Sent unconditionally, with no capability probe first: Ollama 0.32
            # accepts think:false from models that cannot think (verified
            # against llava:7b, which has no thinking capability at all), so
            # the alternative would be an extra /api/show round trip before
            # every description in order to learn nothing.
            "think": False,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [base64.b64encode(image).decode("ascii")],
                }
            ],
            "options": options,
        }
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive

        context = self._context("chat", model)
        with self._post("/api/chat", payload, context=context, model=model) as response:
            status = response.status
            text = response.read().decode("utf-8", errors="replace")

        reply = self._json_object(text, status=status, context=context)
        message = reply.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise OllamaResponseError(
                f"{context} returned no message content: {_truncate(text)}",
                status_code=status,
                body=_truncate(text),
            )
        return content

    def show(self, model: str) -> bool:
        """Report whether the server already has a model.

        Args:
            model: The model tag to look for.

        Returns:
            True if the server knows the model, False if it does not.

        Raises:
            OllamaConnectionError: If the server cannot be reached.
            OllamaTimeoutError: If the request exceeds `timeout`.
            OllamaResponseError: On any non-2xx status other than the 404 that
                means "no such model".
        """
        context = self._context("show", model)
        payload: dict[str, object] = {"model": model}
        try:
            with self._post("/api/show", payload, context=context, model=model):
                # The body describes the model's parameters and template, none
                # of which this library has any use for; the status is the
                # whole answer. The context manager closes the response.
                pass
        except ModelNotFoundError:
            # Here a missing model is the answer to the question, not a
            # failure. Every other error still propagates: "the server is
            # down" must never be reported as "the model is absent".
            return False
        return True

    def pull(self, model: str) -> None:
        """Download a model onto the server, blocking until it is there.

        Args:
            model: The model tag to pull.

        Raises:
            OllamaConnectionError: If the server cannot be reached.
            OllamaTimeoutError: If the stream stalls for longer than `timeout`.
            ModelNotFoundError: If the server answers 404 for the model. Note
                that Ollama 0.32 does not: it accepts the request for a model
                that does not exist upstream, opens the stream, and reports the
                failure as an error line, which arrives as an
                `OllamaResponseError` instead.
            OllamaResponseError: If the server reports an error mid-stream, or
                the stream ends without reporting success.
        """
        context = self._context("pull", model)
        # Streaming is requested rather than avoided: a non-streaming pull
        # holds one socket open with nothing on it for as long as the download
        # takes, which no timeout setting can distinguish from a hang.
        payload: dict[str, object] = {"model": model, "stream": True}
        with self._post("/api/pull", payload, context=context, model=model) as response:
            status = response.status
            # `timeout` applies to each read, so every progress line the server
            # sends resets the clock. That is deliberate: a multi-gigabyte pull
            # legitimately runs far longer than a per-request timeout, while a
            # gap of `timeout` seconds with no progress at all is a real stall.
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                update = self._json_object(line, status=status, context=context)
                error = update.get("error")
                if error is not None:
                    raise OllamaResponseError(
                        f"{context} failed: {error}",
                        status_code=status,
                        body=_truncate(line),
                    )
                if update.get("status") == "success":
                    return
        # Falling out of the loop means the connection closed mid-download.
        # Silence is not success: the model would be missing or half-written.
        raise OllamaResponseError(
            f"{context} ended without reporting success",
            status_code=status,
            body="",
        )

    @contextmanager
    def _post(
        self,
        path: str,
        payload: Mapping[str, object],
        *,
        context: str,
        model: str,
    ) -> Iterator[HTTPResponse]:
        """POST a JSON body and hand the open response to the caller.

        The response is yielded rather than returned so that the error mapping
        also covers what happens while the body is being read: a timeout part
        way through a streamed pull, or a connection reset mid-reply, arrives
        as this library's exception rather than a raw `OSError`.

        Args:
            path: Absolute path on the server, beginning with a slash.
            payload: The request body, serialised as JSON.
            context: Human-readable naming of the call, for error messages.
            model: The model the call is about, for `ModelNotFoundError`.

        Yields:
            The open response.

        Raises:
            OllamaConnectionError: If the server cannot be reached.
            OllamaTimeoutError: If a read exceeds `timeout`.
            ModelNotFoundError: On a 404 that names a missing model.
            OllamaResponseError: On any other non-2xx status.
        """
        request = Request(
            f"{self.host}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # None until a status line has been read, which is exactly the
        # distinction OllamaResponseError.status_code draws: a reply that was
        # cut short still has a status worth reporting, a reply that was never
        # HTTP has none.
        status: int | None = None
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                # open() is typed as returning Any; for an http(s) URL it is
                # always an HTTPResponse, and saying so gives callers a real
                # type instead of propagating Any through the module.
                typed = cast("HTTPResponse", response)
                status = typed.status
                yield typed
        # HTTPError is a subclass of URLError, which is a subclass of OSError,
        # so the handlers run most specific first.
        except HTTPError as exc:
            raise self._http_error(exc, context=context, model=model) from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                # A timeout during connection setup is wrapped by urllib; one
                # during the read is not (see the next handler). Same failure,
                # two shapes.
                raise OllamaTimeoutError(
                    f"{context} timed out after {self.timeout}s"
                ) from exc
            raise OllamaConnectionError(
                f"{context} could not connect: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            # socket.timeout has been an alias of TimeoutError since 3.10, so
            # this one handler covers both spellings.
            raise OllamaTimeoutError(
                f"{context} timed out after {self.timeout}s"
            ) from exc
        except HTTPException as exc:
            # Not an OSError, so the handler below would miss it: something
            # answered on the port but did not speak HTTP (a TLS listener, an
            # unrelated daemon), or the reply was cut short of its declared
            # length. The server answered and the answer is unusable, which is
            # what OllamaResponseError means. The repr is truncated like any
            # other body: a bad status line is server output, and a long one
            # would otherwise become the whole error message.
            raise OllamaResponseError(
                f"{context} got a malformed HTTP reply: {_truncate(repr(exc))}",
                status_code=status,
            ) from exc
        except OSError as exc:
            # A reset or a broken pipe part way through a reply. The contract
            # is that every failure is one of this library's exceptions, so
            # nothing below URLError is allowed to escape unwrapped.
            raise OllamaConnectionError(f"{context} failed: {exc}") from exc

    def _http_error(self, exc: HTTPError, *, context: str, model: str) -> OllamaError:
        """Classify a non-2xx response.

        Args:
            exc: The error urllib raised, with its body still unread.
            context: Human-readable naming of the call.
            model: The model the call was about.

        Returns:
            The exception to raise. Returned rather than raised so that the
            caller keeps `raise ... from exc` in one place and the original
            error always lands on `__cause__`.
        """
        # Parsed before truncation: a long error body would otherwise stop
        # being JSON and a missing model would be misreported as a plain
        # response error.
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except (HTTPException, OSError):
            # The body is supporting evidence, not the finding. A connection
            # that dies before it arrives still leaves a status worth
            # reporting, and swallowing this keeps a truncated 500 from
            # surfacing as something unrelated to the 500.
            raw = ""
        finally:
            # HTTPError holds the socket open until it is closed, and it
            # outlives this function on the caller's `__cause__`.
            exc.close()
        if exc.code == HTTPStatus.NOT_FOUND and _names_a_missing_model(raw):
            return ModelNotFoundError(model)
        body = _truncate(raw)
        # A Location header only means a redirect on a 3xx. Error pages carry
        # one often enough — a 500 from a proxy that would rather you logged in
        # — and reporting that as a redirect would send the reader after it.
        redirect = exc.headers.get("Location")
        if _is_redirect(exc.code) and redirect is not None:
            # Redirects are not followed (see `_build_opener`), so this is a
            # dead end rather than a step on the way somewhere. Naming the
            # target is the whole diagnosis: it is nearly always a host that
            # should have been written with the other scheme or another port.
            return OllamaResponseError(
                f"{context} was redirected to {_truncate(redirect)} "
                f"(HTTP {exc.code}), and redirects are not followed",
                status_code=exc.code,
                body=body,
            )
        return OllamaResponseError(
            f"{context} failed with HTTP {exc.code}: {body}",
            status_code=exc.code,
            body=body,
        )

    def _json_object(
        self, text: str, *, status: int, context: str
    ) -> dict[str, object]:
        """Parse text the server sent as a JSON object.

        Args:
            text: The response body, or one line of a streamed one.
            status: The HTTP status the body arrived with.
            context: Human-readable naming of the call.

        Returns:
            The decoded object.

        Raises:
            OllamaResponseError: If the text is not JSON, or is JSON that is
                not an object. A 200 carrying prose is what a reverse proxy or
                a different service on the port produces, and it is a server
                problem rather than a description problem.
        """
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise OllamaResponseError(
                f"{context} returned a non-JSON body: {_truncate(text)}",
                status_code=status,
                body=_truncate(text),
            ) from exc
        if not isinstance(parsed, dict):
            raise OllamaResponseError(
                f"{context} returned JSON that is not an object: {_truncate(text)}",
                status_code=status,
                body=_truncate(text),
            )
        return parsed

    def _context(self, operation: str, model: str) -> str:
        """Name a call for use in an error message.

        Args:
            operation: The endpoint's short name (`chat`, `show`, `pull`).
            model: The model the call is about.

        Returns:
            A phrase naming the operation, the model and the server, so that a
            message reads as a diagnosis rather than as a category.
        """
        return f"{operation} request for model {model!r} on {self.host}"


def _is_redirect(status: int) -> bool:
    """Report whether a status code is one of the redirection statuses.

    Args:
        status: The HTTP status the server answered with.

    Returns:
        True for 3xx.
    """
    return HTTPStatus.MULTIPLE_CHOICES <= status < HTTPStatus.BAD_REQUEST


def _build_opener() -> OpenerDirector:
    """Build the opener every request from one client goes through.

    Assembled by hand rather than with `urllib.request.build_opener`, to leave
    out two of its defaults:

    - **No proxy handler with an environment behind it.** `$http_proxy` is set
      machine-wide in many corporate environments, and urllib's proxy support
      has no loopback exemption, so the module-level `urlopen` would route
      `http://localhost:11434` through a proxy that has never heard of it —
      where `ollama` itself, which honours no such variable, connects directly.
      Ollama is a local service; an explicitly empty `ProxyHandler` says so.
    - **No redirect handler.** urllib follows a 30x by reissuing the request,
      and it rewrites a redirected POST into a GET with no body: the image
      would silently not be sent, and the server would answer something
      unrelated to what was asked. Better to surface the redirect.

    Returns:
        An opener with HTTP, HTTPS and the two error handlers that turn a
        non-2xx status into `HTTPError`.
    """
    opener = OpenerDirector()
    for handler in (
        ProxyHandler({}),
        HTTPHandler(),
        HTTPSHandler(),
        HTTPErrorProcessor(),
        HTTPDefaultErrorHandler(),
    ):
        opener.add_handler(handler)
    return opener


def _names_a_missing_model(body: str) -> bool:
    """Judge whether a 404 body is Ollama saying it has no such model.

    Args:
        body: The undecorated response body.

    Returns:
        True if the body is a JSON object whose `error` field reports a missing
        model. A 404 from anything else — a proxy, a wrong path, another
        service on the port — deliberately does not qualify: telling a caller
        to `ollama pull` a model that exists would send them the wrong way.
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, dict):
        return False
    error = parsed.get("error")
    return isinstance(error, str) and _MISSING_MODEL_MARKER in error.lower()


def _truncate(text: str) -> str:
    """Shorten a response body to something an error message can carry.

    Args:
        text: The body as text.

    Returns:
        The text, or its first `_MAX_BODY_CHARS` characters with a note saying
        how much was left out.
    """
    if len(text) <= _MAX_BODY_CHARS:
        return text
    return f"{text[:_MAX_BODY_CHARS]}... ({len(text)} characters in total)"
