"""Tests for the exception hierarchy: shape, attributes, and messages."""

import pytest

from describe_it.exceptions import (
    DescribeItError,
    DescriptionError,
    DescriptionRefusedError,
    ImageError,
    ModelNotFoundError,
    OllamaConnectionError,
    OllamaError,
    OllamaResponseError,
    OllamaTimeoutError,
)

_LIBRARY_ERRORS = [
    ImageError,
    OllamaError,
    OllamaConnectionError,
    OllamaTimeoutError,
    ModelNotFoundError,
    OllamaResponseError,
    DescriptionError,
    DescriptionRefusedError,
]


@pytest.mark.parametrize("error_class", _LIBRARY_ERRORS)
def test_every_error_is_catchable_as_describe_it_error(
    error_class: type[DescribeItError],
) -> None:
    assert issubclass(error_class, DescribeItError)


def test_describe_it_error_is_an_exception() -> None:
    assert issubclass(DescribeItError, Exception)


@pytest.mark.parametrize(
    "error_class",
    [
        OllamaConnectionError,
        OllamaTimeoutError,
        ModelNotFoundError,
        OllamaResponseError,
    ],
)
def test_server_errors_group_under_ollama_error(
    error_class: type[OllamaError],
) -> None:
    assert issubclass(error_class, OllamaError)


def test_timeout_is_a_sibling_of_connection_error() -> None:
    # A timeout means "the model is slow", not "the server is down", so the two
    # must stay distinguishable by `except`.
    assert not issubclass(OllamaTimeoutError, OllamaConnectionError)
    assert not issubclass(OllamaConnectionError, OllamaTimeoutError)


def test_refusal_is_a_description_error() -> None:
    assert issubclass(DescriptionRefusedError, DescriptionError)


def test_image_error_is_not_an_ollama_error() -> None:
    assert not issubclass(ImageError, OllamaError)


def test_model_not_found_names_the_pull_command() -> None:
    error = ModelNotFoundError("qwen3.5:4b")

    assert error.model == "qwen3.5:4b"
    assert "ollama pull qwen3.5:4b" in str(error)


def test_response_error_carries_status_and_body() -> None:
    error = OllamaResponseError(
        "chat request failed", status_code=500, body='{"error": "boom"}'
    )

    assert error.status_code == 500
    assert error.body == '{"error": "boom"}'
    assert str(error) == "chat request failed"


def test_response_error_defaults_are_empty() -> None:
    error = OllamaResponseError("chat request returned an unparseable body")

    assert error.status_code is None
    assert error.body == ""


def test_refusal_carries_the_model_text() -> None:
    error = DescriptionRefusedError("I'm sorry, but I can't help with that.")

    assert error.response == "I'm sorry, but I can't help with that."
    assert "I'm sorry, but I can't help with that." in str(error)


def test_errors_chain_their_cause() -> None:
    original = OSError("truncated file")
    try:
        try:
            raise original
        except OSError as exc:
            raise ImageError("could not prepare image") from exc
    except ImageError as exc:
        assert exc.__cause__ is original
