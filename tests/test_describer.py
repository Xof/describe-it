"""Tests for `describe_it.describer`.

The describer's job is composition and configuration, so the tests check what
reaches the server (the prepared image, the built prompt, the derived options)
and what comes back out of the cleanup, rather than re-testing the layers
underneath. They run against the same fake server as the client tests, so a
describe call here is the real pipeline minus the model.
"""

import base64

import pytest
from PIL import Image

from conftest import FakeOllama
from describe_it.client import OllamaClient
from describe_it.config import DEFAULT_HOST, DEFAULT_MODEL
from describe_it.describer import Describer, describe
from describe_it.exceptions import (
    DescriptionError,
    DescriptionRefusedError,
    ImageError,
    ModelNotFoundError,
    OllamaConnectionError,
)
from describe_it.image import prepare_image

_MODEL = "llava:7b"


def _image() -> Image.Image:
    """Return a small image with two distinguishable halves."""
    image = Image.new("RGB", (40, 20), (200, 30, 30))
    image.paste((30, 30, 200), (20, 0, 40, 20))
    return image


def _reply(content: str) -> dict[str, object]:
    """Return an /api/chat body carrying one assistant message."""
    return {"message": {"role": "assistant", "content": content}}


def test_describe_runs_the_whole_pipeline(server: FakeOllama) -> None:
    image = _image()
    # A reply as a small model actually writes one: labelled and quoted.
    server.script_json("/api/chat", _reply('Alt text: "A red circle."'))
    describer = Describer(model=_MODEL, host=server.url)

    result = describer.describe(image, context="gallery thumbnail")

    assert result == "A red circle."
    body = server.requests[0].json_body
    assert body["model"] == _MODEL
    assert body["options"] == {"temperature": 0.2, "num_predict": 30 * 4 + 32}
    message = body["messages"][0]
    assert base64.b64decode(message["images"][0]) == prepare_image(image, max_size=1024)
    assert "at most 30 words" in message["content"]
    assert "write in English" in message["content"]
    assert "gallery thumbnail" in message["content"]


def test_describe_passes_its_configuration_through(server: FakeOllama) -> None:
    image = _image()
    server.script_json("/api/chat", _reply("Un cercle rouge."))
    describer = Describer(
        model="gemma4:e4b",
        host=server.url,
        language="French",
        max_words=8,
        max_image_size=None,
        keep_alive="30m",
    )

    assert describer.describe(image) == "Un cercle rouge."

    body = server.requests[0].json_body
    assert body["model"] == "gemma4:e4b"
    assert body["keep_alive"] == "30m"
    assert body["options"] == {"temperature": 0.2, "num_predict": 8 * 4 + 32}
    message = body["messages"][0]
    assert base64.b64decode(message["images"][0]) == prepare_image(image, max_size=None)
    assert "at most 8 words" in message["content"]
    assert "write in French" in message["content"]


def test_an_explicit_prompt_replaces_the_built_in_wording(server: FakeOllama) -> None:
    server.script_json("/api/chat", _reply("A red rectangle."))
    describer = Describer(model=_MODEL, host=server.url)

    describer.describe(_image(), context="ignored", prompt="Caption this in one word.")

    message = server.requests[0].json_body["messages"][0]
    assert message["content"] == "Caption this in one word."


def test_an_injected_client_replaces_the_configured_host(server: FakeOllama) -> None:
    # The documented test seam: with a client in hand, host and timeout have
    # nothing left to configure.
    server.script_json("/api/chat", _reply("A red rectangle."))
    describer = Describer(
        model=_MODEL,
        host="http://not-this-one.invalid:1",
        client=OllamaClient(host=server.url),
    )

    assert describer.describe(_image()) == "A red rectangle."


def test_the_module_level_describe_is_the_same_pipeline(server: FakeOllama) -> None:
    server.script_json("/api/chat", _reply("**A red rectangle.**"))

    result = describe(_image(), model=_MODEL, host=server.url, max_words=12)

    assert result == "A red rectangle."
    assert server.requests[0].json_body["options"]["num_predict"] == 12 * 4 + 32


def test_a_refusal_becomes_an_exception_carrying_the_text(server: FakeOllama) -> None:
    server.script_json("/api/chat", _reply("I'm sorry, but I can't describe this."))

    with pytest.raises(DescriptionRefusedError) as caught:
        describe(_image(), model=_MODEL, host=server.url)

    assert caught.value.response == "I'm sorry, but I can't describe this."


@pytest.mark.parametrize("content", ["", "   \n  ", "<think>hmm</think>"])
def test_an_empty_reply_is_a_description_error(
    server: FakeOllama, content: str
) -> None:
    server.script_json("/api/chat", _reply(content))

    with pytest.raises(DescriptionError, match="no text"):
        describe(_image(), model=_MODEL, host=server.url)


def test_a_non_image_argument_never_reaches_the_network(server: FakeOllama) -> None:
    describer = Describer(model=_MODEL, host=server.url)

    with pytest.raises(TypeError, match=r"PIL\.Image\.Image"):
        describer.describe("a path, not an image")  # type: ignore[arg-type]

    assert server.requests == []


def test_an_unusable_image_never_reaches_the_network(server: FakeOllama) -> None:
    # Image preparation comes first on purpose: a caller's mistake should cost
    # them a millisecond, not a cold model load and a timeout.
    describer = Describer(model=_MODEL, host=server.url)

    with pytest.raises(ImageError, match="zero-area"):
        describer.describe(Image.new("RGB", (0, 10)))

    assert server.requests == []


def test_a_blank_prompt_never_reaches_the_network(server: FakeOllama) -> None:
    describer = Describer(model=_MODEL, host=server.url)

    with pytest.raises(ValueError, match="prompt must not be empty"):
        describer.describe(_image(), prompt="  ")

    assert server.requests == []


def test_the_environment_supplies_the_model_and_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DESCRIBE_IT_MODEL", "moondream:1.8b")
    # Spelled as Ollama's own CLI accepts it, to prove normalisation applies to
    # what the environment says as well as to what a caller passes.
    monkeypatch.setenv("OLLAMA_HOST", "ollama.internal:11434")

    describer = Describer()

    assert describer.model == "moondream:1.8b"
    assert describer.client.host == "http://ollama.internal:11434"


def test_explicit_arguments_beat_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DESCRIBE_IT_MODEL", "moondream:1.8b")
    monkeypatch.setenv("OLLAMA_HOST", "ollama.internal:11434")

    describer = Describer(model=_MODEL, host="http://localhost:1234")

    assert describer.model == _MODEL
    assert describer.client.host == "http://localhost:1234"


@pytest.mark.parametrize("value", ["", None])
def test_the_packaged_defaults_apply_when_the_environment_is_silent(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    # An exported-but-empty variable is a deployment accident, and is treated
    # the same as an unset one.
    for name in ("DESCRIBE_IT_MODEL", "OLLAMA_HOST"):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    describer = Describer()

    assert describer.model == DEFAULT_MODEL
    assert describer.client.host == DEFAULT_HOST


@pytest.mark.parametrize("max_words", [0, -1])
def test_a_word_budget_below_one_is_rejected(max_words: int) -> None:
    with pytest.raises(ValueError, match="max_words must be at least 1"):
        Describer(max_words=max_words)


@pytest.mark.parametrize("timeout", [0.0, -1.0])
def test_a_non_positive_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout must be a positive"):
        Describer(timeout=timeout)


def test_check_is_silent_when_the_server_has_the_model(server: FakeOllama) -> None:
    server.script_json("/api/show", {"details": {"family": "llama"}})

    Describer(model=_MODEL, host=server.url).check()

    assert server.paths() == ["/api/show"]


def test_check_reports_a_missing_model(server: FakeOllama) -> None:
    server.script_json("/api/show", {"error": "model not found"}, status=404)

    with pytest.raises(ModelNotFoundError, match="ollama pull llava:7b"):
        Describer(model=_MODEL, host=server.url).check()


def test_check_reports_an_unreachable_server(closed_port: int) -> None:
    describer = Describer(model=_MODEL, host=f"127.0.0.1:{closed_port}")

    with pytest.raises(OllamaConnectionError):
        describer.check()


def test_ensure_model_does_nothing_when_the_model_is_present(
    server: FakeOllama,
) -> None:
    server.script_json("/api/show", {"details": {"family": "llama"}})

    Describer(model=_MODEL, host=server.url).ensure_model()

    # A pull can transfer gigabytes; it must not happen behind the caller.
    assert server.paths() == ["/api/show"]


def test_ensure_model_pulls_exactly_once_when_the_model_is_absent(
    server: FakeOllama,
) -> None:
    server.script_json("/api/show", {"error": "model not found"}, status=404)
    server.script_ndjson(
        "/api/pull", [{"status": "pulling manifest"}, {"status": "success"}]
    )

    Describer(model=_MODEL, host=server.url).ensure_model()

    assert server.paths() == ["/api/show", "/api/pull"]
    assert server.requests[1].json_body == {"model": _MODEL, "stream": True}


def test_an_explicitly_empty_host_is_an_error_not_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An argument the caller passed is never second-guessed into the default:
    # a blank host is a configuration bug, and silently substituting localhost
    # would hide it behind a connection error somewhere else.
    monkeypatch.setenv("OLLAMA_HOST", "ollama.internal:11434")

    with pytest.raises(ValueError, match="has no hostname"):
        Describer(host="")
