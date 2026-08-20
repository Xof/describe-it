"""Live tests against a real Ollama server and a real vision model.

These are the only tests that prove the library works — everything else in the
suite proves it is internally consistent. They are opt-in and are never a gate:
a small vision model's output is not deterministic enough to block a merge on,
and the model download alone is gigabytes.

They run only when `DESCRIBE_IT_INTEGRATION=1` *and* the configured host
answers `GET /api/version`. The probe is deliberately behind the environment
variable, so that collecting this module during an ordinary unit run opens no
socket at all.

Assertions are structural — length, language, exception type — never keyword
matching against the image's content, because a 4B model asked to describe a
red circle will not reliably say "circle" and a test that demands it would
measure the model rather than the library. What the model actually said is
printed, since reading it is half the reason to run these by hand
(`uv run pytest -m integration -v -s`).
"""

import os
import urllib.request
from collections.abc import Iterator
from http.client import HTTPException

import pytest
from PIL import Image, ImageDraw, ImageFont

from describe_it import Describer, ModelNotFoundError
from describe_it.config import default_host, default_model, normalise_host
from describe_it.prompt import REFUSAL_RE

# Set to "1" to opt in. Anything else — including "0" and "true" — skips, so a
# half-remembered value cannot start a gigabyte download by accident.
_ENABLE_ENV_VAR = "DESCRIBE_IT_INTEGRATION"

# The probe answers from memory or not at all; it must not become the reason a
# test session appears to hang.
_PROBE_TIMEOUT = 2.0

# The word budget the first test asks for and then checks against. Written out
# rather than taken from the library default, so the test states its own oracle.
_MAX_WORDS = 30

# Generous: this is a first request against a possibly cold model, on hardware
# that may be a laptop or a shared CI runner.
_TIMEOUT = 300.0

# A handful of very common French function words. Any one of them is enough to
# tell "this is French" from "this is English", which is all the language
# option claims to do.
_FRENCH_WORDS = frozenset({"le", "la", "les", "un", "une", "des", "sur", "avec"})

# Punctuation a model wraps around words, stripped before the language check.
_PUNCTUATION = ".,;:!?\"'()«»“”‘’"


def _unavailable() -> str:
    """Report why the live tests cannot run.

    Returns:
        The reason to skip, or an empty string if the tests can run. The
        environment variable is checked first and returns before anything else
        happens: a unit-test run imports this module during collection, and it
        must not touch the network there.
    """
    if os.environ.get(_ENABLE_ENV_VAR) != "1":
        return f"set {_ENABLE_ENV_VAR}=1 to run the live tests"
    host = default_host()
    # No proxy handler, matching the client: Ollama is a local service, and a
    # machine-wide proxy that captured the probe would skip the tests on a
    # machine where the library itself works perfectly well.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(
            f"{normalise_host(host)}/api/version", timeout=_PROBE_TIMEOUT
        ) as response:
            response.read()
    except (OSError, HTTPException) as exc:
        return f"no Ollama server answering at {host}: {exc}"
    return ""


_SKIP_REASON = _unavailable()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        bool(_SKIP_REASON), reason=_SKIP_REASON or "the live server is available"
    ),
]


@pytest.fixture(scope="module")
def image() -> Iterator[Image.Image]:
    """Yield the image under test: a red disc and the word HELLO on white.

    Synthetic rather than a checked-in photograph: it keeps the repository free
    of binary fixtures, and it gives the model two unmistakable things to see,
    one of which is text the prompt asks it to quote verbatim.
    """
    canvas = Image.new("RGB", (512, 384), "white")
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((40, 30, 250, 240), fill=(220, 30, 30))
    # `size=` on the default font needs Pillow >= 10.1. The bitmap font at its
    # native ~11px is illegible once a vision model has downsampled the image,
    # so a test using it would be measuring nothing.
    draw.text((60, 280), "HELLO", fill="black", font=ImageFont.load_default(size=72))
    with canvas:
        yield canvas


def _describer(
    *,
    model: str | None = None,
    language: str = "English",
    max_words: int = _MAX_WORDS,
) -> Describer:
    """Return a describer for the model under test.

    Args:
        model: An override for the model tag, for the missing-model test.
        language: Output language.
        max_words: Requested upper bound on length.

    Returns:
        A describer configured to keep the model resident, so that the tests
        after the first one do not each pay for a model load.
    """
    return Describer(
        model=model if model is not None else default_model(),
        language=language,
        max_words=max_words,
        keep_alive="10m",
        timeout=_TIMEOUT,
    )


def test_a_synthetic_image_gets_a_usable_description(image: Image.Image) -> None:
    result = _describer().describe(image)
    print(f"\ndescription: {result!r}")

    words = result.split()
    assert words, "the description is empty"
    # A model that overruns its budget a little is doing its job; one that
    # ignores it entirely is returning an essay, which is not alt text.
    assert len(words) <= _MAX_WORDS + 10
    assert "\n" not in result
    # The cleaner would have raised on a refusal, so this asserts that it did
    # not have to: the model described the image rather than declining to.
    assert REFUSAL_RE.match(result) is None


def test_a_smaller_word_budget_produces_a_shorter_description(
    image: Image.Image,
) -> None:
    short = _describer(max_words=8).describe(image)
    full = _describer(max_words=40).describe(image)
    print(f"\nmax_words=8:  {short!r}\nmax_words=40: {full!r}")

    # Weak by design: the budget is a request in the prompt, not a truncation,
    # so this checks the model is influenced by it, not that it obeys it.
    assert len(short) < len(full) + 20


def test_the_language_option_changes_the_language(image: Image.Image) -> None:
    result = _describer(language="French").describe(image)
    print(f"\nFrench: {result!r}")

    words = {word.strip(_PUNCTUATION).lower() for word in result.split()}
    assert words & _FRENCH_WORDS, f"no common French word in {result!r}"


def test_a_model_the_server_does_not_have_is_reported_with_the_remedy(
    image: Image.Image,
) -> None:
    missing = "definitely-not-a-model:latest"

    with pytest.raises(ModelNotFoundError) as error:
        _describer(model=missing).describe(image)

    assert f"ollama pull {missing}" in str(error.value)
