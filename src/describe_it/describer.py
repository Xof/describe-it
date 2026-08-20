"""The library's front door: `Describer` and the module-level `describe`.

This is the only module that sees the whole pipeline, and its job is to hold
the layers in the right order rather than to do work of its own. The order
matters in one place in particular: the image is prepared *before* the request
is built, so a caller who passes a closed file or a zero-area image finds out
immediately instead of after a socket, a model load and a timeout.

Configuration resolution also lives here rather than in `client`, so that
reading the environment is a documented behaviour of the library's public API
and not something a transport does behind a caller's back.
"""

from PIL import Image

from describe_it.client import OllamaClient
from describe_it.config import default_host, default_model
from describe_it.exceptions import ModelNotFoundError
from describe_it.image import prepare_image
from describe_it.prompt import build_prompt, clean_response

# Not zero: a few small models degenerate into repeating a phrase at exactly
# 0.0, and alt text is not a task where the last of the determinism is worth
# that risk.
_TEMPERATURE = 0.2

# `num_predict` is a safety stop, not the length control — the prompt is. Four
# tokens per requested word is well above English's ~1.3, and the constant
# margin keeps a very short request (max_words=1) from being cut off before the
# model has finished its first sentence.
_TOKENS_PER_WORD = 4
_TOKEN_MARGIN = 32


class Describer:
    """A configured describer: set the options once, describe many images.

    Every option has a default, so `Describer()` is a working object. Options
    that vary per image — `context` and `prompt` — are arguments to `describe`
    instead of constructor arguments.

    Attributes:
        model: The Ollama model tag, resolved at construction.
        client: The transport. Public so that callers can inspect the resolved
            host, and so tests can substitute a double.
        language: Output language, as a plain English name.
        max_words: Requested upper bound on the description's length.
        max_image_size: Longest edge in pixels the image is downscaled to.
        keep_alive: How long the server keeps the model resident afterwards.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        host: str | None = None,
        timeout: float = 120.0,
        language: str = "English",
        max_words: int = 30,
        max_image_size: int | None = 1024,
        keep_alive: str | int | None = None,
        client: OllamaClient | None = None,
    ) -> None:
        """Configure a describer.

        Args:
            model: Ollama model tag. `None` resolves `$DESCRIBE_IT_MODEL`, then
                the packaged default. Resolution happens here, once, so that a
                describer's behaviour cannot change under it if the process
                edits its own environment later.
            host: Base URL or bare `host:port` of the Ollama server. `None`
                resolves `$OLLAMA_HOST`, then the packaged default.
            timeout: Per-read socket timeout in seconds.
            language: Output language, as a plain English name the model knows.
            max_words: Requested upper bound on length, in words. Requested of
                the model in the prompt, never enforced by truncation.
            max_image_size: Longest edge in pixels to downscale to before
                upload; `None` sends the image at its original size.
            keep_alive: Passed to Ollama. `None` leaves the server's own
                default in place; `"30m"` keeps a model resident across a
                batch; `0` unloads it as soon as the call finishes.
            client: A ready-made transport, which makes `host` and `timeout`
                irrelevant — including their validation, which belongs to the
                client that would have used them. This is a test seam — it is
                what lets the describer tests run against a scripted client —
                and callers with a real server should configure `host` and
                `timeout` instead.

        Raises:
            TypeError: If `max_words` is not an `int`, or `timeout` is not a
                number.
            ValueError: If `max_words` is below 1, the model is blank, or the
                host or timeout is unusable. All of them would otherwise fail
                much later and much less clearly: a zero word budget as a
                prompt asking for a description of no length at all, a blank
                model as a puzzling 404.
        """
        # bool is an int, and `max_words=True` meaning "one word" is a typo
        # every time — the same rule `prepare_image` applies to `max_size`.
        if isinstance(max_words, bool) or not isinstance(max_words, int):
            raise TypeError(f"max_words must be an int, not {type(max_words).__name__}")
        if max_words < 1:
            raise ValueError(f"max_words must be at least 1, not {max_words}")

        # Stripped because a model tag picked up from a file or a form arrives
        # with a newline more often than not, and Ollama would 404 on it with a
        # message that shows nothing wrong.
        resolved_model = (model if model is not None else default_model()).strip()
        if not resolved_model:
            raise ValueError("model must not be blank; pass None for the default")
        self.model = resolved_model
        # `timeout` is validated by the client, which is the object that uses
        # it; constructing the client here is what surfaces that at the same
        # point in the caller's code as everything above.
        self.client = (
            client
            if client is not None
            else OllamaClient(
                host=host if host is not None else default_host(), timeout=timeout
            )
        )
        self.language = language
        self.max_words = max_words
        self.max_image_size = max_image_size
        self.keep_alive = keep_alive

    def describe(
        self,
        image: Image.Image,
        *,
        context: str | None = None,
        prompt: str | None = None,
    ) -> str:
        """Describe one image.

        Args:
            image: The image to describe, in any mode PIL can convert. It is
                not modified.
            context: Free text about where the image appears ("product photo on
                a shoe listing"). Good alt text depends on context, and the
                model is told this verbatim.
            prompt: A complete replacement for the built-in wording. When
                given, `context`, `language` and `max_words` no longer reach
                the model — the caller has taken over the wording — though the
                reply is still cleaned and still checked for a refusal.

        Returns:
            The description: one line, no label, no wrapping quotes or
            markdown, internal whitespace collapsed.

        Raises:
            TypeError: If `image` is not a `PIL.Image.Image`.
            ValueError: If `prompt` is given but blank.
            ImageError: If the image cannot be prepared for upload.
            OllamaError: If the server cannot be reached, times out, does not
                have the model, or answers with anything unusable.
            DescriptionError: If the model returned no usable text, or
                (`DescriptionRefusedError`) refused to describe the image.
        """
        # Before anything else, and before the network in particular: a bad
        # image is the caller's mistake and should cost them a millisecond, not
        # a cold model load. `prepare_image` also raises the TypeError for a
        # non-image argument, so there is no type check duplicated here.
        data = prepare_image(image, max_size=self.max_image_size)
        text = build_prompt(
            max_words=self.max_words,
            language=self.language,
            context=context,
            prompt=prompt,
        )
        options: dict[str, object] = {
            "temperature": _TEMPERATURE,
            "num_predict": self.max_words * _TOKENS_PER_WORD + _TOKEN_MARGIN,
        }
        reply = self.client.chat(
            model=self.model,
            prompt=text,
            image=data,
            keep_alive=self.keep_alive,
            options=options,
        )
        return clean_response(reply)

    def check(self) -> None:
        """Verify that the server is up and has the configured model.

        For a start-up health check, so that a service fails at boot with a
        clear message rather than on its first image.

        Raises:
            OllamaConnectionError: If the server cannot be reached.
            OllamaTimeoutError: If the check exceeds the configured timeout.
            ModelNotFoundError: If the server does not have the model.
            OllamaResponseError: If the server answers unusably.
        """
        if not self.client.show(self.model):
            raise ModelNotFoundError(self.model)

    def ensure_model(self) -> None:
        """Download the configured model if the server does not already have it.

        Blocking, and may transfer gigabytes. Never called implicitly: a
        describe call against a missing model raises `ModelNotFoundError`
        rather than silently starting a download the caller did not budget for.

        Raises:
            OllamaConnectionError: If the server cannot be reached.
            OllamaTimeoutError: If the pull stalls for longer than the timeout.
            OllamaResponseError: If the pull fails — including when there is no
                such model to pull, which Ollama reports mid-stream rather than
                as a 404 — or if the stream ends without reporting success.
        """
        if not self.client.show(self.model):
            self.client.pull(self.model)


def describe(
    image: Image.Image,
    *,
    model: str | None = None,
    host: str | None = None,
    timeout: float = 120.0,
    context: str | None = None,
    language: str = "English",
    max_words: int = 30,
    max_image_size: int | None = 1024,
    prompt: str | None = None,
    keep_alive: str | int | None = None,
    client: OllamaClient | None = None,
) -> str:
    """Describe one image, configuring a describer for the occasion.

    Sugar for `Describer(**options).describe(image, context=..., prompt=...)`,
    and the call the README leads with. Constructing a `Describer` directly is
    worth it for a batch: this function resolves its configuration on every
    call.

    Args:
        image: The image to describe, in any mode PIL can convert.
        model: Ollama model tag; `None` resolves `$DESCRIBE_IT_MODEL`.
        host: Ollama base URL; `None` resolves `$OLLAMA_HOST`.
        timeout: Per-read socket timeout in seconds.
        context: Free text about where the image appears.
        language: Output language, as a plain English name.
        max_words: Requested upper bound on length, in words.
        max_image_size: Longest edge in pixels to downscale to; `None` disables.
        prompt: A complete replacement for the built-in wording.
        keep_alive: How long the server keeps the model resident afterwards.
        client: A ready-made transport; a test seam, as on `Describer`.

    Returns:
        The description.

    Raises:
        TypeError: If `image` is not a `PIL.Image.Image`, or if `max_words` or
            `timeout` is not a number of the right kind.
        ValueError: If `max_words`, `model`, `host`, `timeout` or `prompt` is
            out of range or blank.
        ImageError: If the image cannot be prepared for upload.
        OllamaError: If the server cannot be reached or answers unusably.
        DescriptionError: If no usable description came back.
    """
    describer = Describer(
        model=model,
        host=host,
        timeout=timeout,
        language=language,
        max_words=max_words,
        max_image_size=max_image_size,
        keep_alive=keep_alive,
        client=client,
    )
    return describer.describe(image, context=context, prompt=prompt)
