"""Exception hierarchy for describe-it.

Every failure the library can produce is an exception, and every one of them
descends from `DescribeItError`, so a caller that only cares "did this work?"
can write a single `except DescribeItError`. The one deliberate exception to
that rule is a plain `TypeError` for an argument that is not a PIL image: a
wrong-type argument is a programming error, not a describe-it failure, and
Python callers expect the standard type.

The tree is shaped by what a caller would *do* about the failure, not by where
the error happened to arise. That is why `OllamaTimeoutError` is a sibling of
`OllamaConnectionError` rather than a subclass: a timeout usually means the
model is slow (retry, or raise the timeout), while a connection error means
the server is not there (start it, or fix the host).
"""


class DescribeItError(Exception):
    """Base class for every error raised by this library."""


class ImageError(DescribeItError):
    """Raised when an image cannot be prepared for upload.

    Covers a zero-area image, an image PIL cannot load (closed file, truncated
    data), and modes PIL cannot convert to RGB. The underlying PIL exception,
    when there is one, is on `__cause__`.
    """


class OllamaError(DescribeItError):
    """Base class for every failure involving the Ollama server."""


class OllamaConnectionError(OllamaError):
    """Raised when the Ollama server cannot be reached at all.

    Connection refused, DNS failure, unreachable host: the server is not
    answering, as opposed to answering slowly or answering with an error.
    """


class OllamaTimeoutError(OllamaError):
    """Raised when a request to the Ollama server exceeds its timeout.

    Deliberately not a subclass of `OllamaConnectionError`: the server is
    reachable, it is just taking longer than allowed (a cold model load on a
    laptop can run to tens of seconds).
    """


class ModelNotFoundError(OllamaError):
    """Raised when the configured model is not present on the server.

    describe-it never pulls a model implicitly, so the message tells the caller
    exactly which command to run.
    """

    def __init__(self, model: str) -> None:
        """Initialise the error for a missing model.

        Args:
            model: The Ollama model tag that the server does not have.
        """
        super().__init__(
            f"model {model!r} is not available on the Ollama server; "
            f"run: ollama pull {model}"
        )
        self.model = model


class OllamaResponseError(OllamaError):
    """Raised when the server answers but the answer is unusable.

    Any non-2xx status other than a missing model, and any 2xx whose body is
    not the JSON shape the API documents. The raw material for a bug report is
    kept on the exception rather than folded into the message alone.
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, body: str = ""
    ) -> None:
        """Initialise the error with the server's reply.

        Args:
            message: Human-readable description naming the failed operation.
            status_code: HTTP status, or None if the failure was not an HTTP
                status (for example a 200 with an unparseable body).
            body: The response body as text, truncated by the caller if need be.
        """
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class DescriptionError(DescribeItError):
    """Raised when the server answered but produced no usable description."""


class DescriptionRefusedError(DescriptionError):
    """Raised when the model refused to describe the image.

    Detected by a documented heuristic (see `describe_it.prompt`), so it can in
    principle fire on a genuine description. The text the model actually
    returned is kept on `.response` for callers who would rather judge for
    themselves.
    """

    def __init__(self, response: str) -> None:
        """Initialise the error with the model's reply.

        Args:
            response: The cleaned text the model returned instead of alt text.
        """
        super().__init__(f"model refused to describe the image: {response}")
        self.response = response
