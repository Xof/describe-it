"""Tests for prompt construction and response cleanup."""

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from describe_it.exceptions import DescriptionError, DescriptionRefusedError
from describe_it.prompt import (
    EDGE_TRIM_RE,
    REFUSAL_RE,
    THINK_RE,
    UNCLOSED_THINK_RE,
    build_prompt,
    clean_response,
)

_CONTEXT_SENTENCE = "The image appears in this context:"

# Named, because these are invisible in a source file and a stray one would be
# impossible to spot in a diff.
_BOM = "﻿"
_ZWSP = "​"
_ZWNJ = "‌"
_ZWJ = "‍"
_WORD_JOINER = "⁠"

# Text that needs its zero-width characters: Persian (ZWNJ separates the prefix
# from the verb), Hindi (ZWJ forces the conjunct form), and an emoji sequence
# held together by ZWJ. Deleting them corrupts the words.
_PERSIAN = f"می{_ZWNJ}خواهم"
_HINDI = f"क्{_ZWJ}ष"
_FAMILY = f"👨{_ZWJ}👩{_ZWJ}👧"

# Every opening phrase the heuristic knows, as a model would actually write it
# — including the typographic apostrophes a model produces at least as often
# as ASCII ones.
_REFUSALS = [
    "I'm sorry, but I can't help with that.",
    "I’m sorry, but I can’t help with that.",
    "I‘m sorry, but I can‘t help with that.",
    "Im sorry, I will not do that.",
    "I am sorry, but I cannot help with this image.",
    "I cannot describe this image.",
    "I can't describe this image.",
    "I can’t describe this image.",
    "I cant describe this image.",
    "I am unable to describe this image.",
    "I'm unable to describe this image.",
    "I’m unable to describe this image.",
    "I apologize, but I will not describe this image.",
    "I apologise, but I will not describe this image.",
    "As an AI, I do not describe images like this one.",
]

_WRAPPER_PAIRS = [
    ("", ""),
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
    ("`", "`"),
    ("```", "```"),
    ("**", "**"),
    ("*", "*"),
    ("__", "__"),
    ("_", "_"),
]

_CLEANING_CASES = [
    (
        "A grey tabby cat asleep on a red armchair.",
        "A grey tabby cat asleep on a red armchair.",
    ),
    ("  A grey cat.  ", "A grey cat."),
    ("<think>Let me look.</think>A grey cat.", "A grey cat."),
    ("<think>\nmulti\nline\n</think>\n\nA grey cat.", "A grey cat."),
    ("<THINK>upper</THINK> A grey cat.", "A grey cat."),
    ("A grey cat. <think>trailing thought never closed", "A grey cat."),
    ('"A grey cat. <think>"', "A grey cat."),
    ("Alt text: A grey cat.", "A grey cat."),
    ("alt:A grey cat.", "A grey cat."),
    ("ALT TEXT : A grey cat.", "A grey cat."),
    ("**Alt text:** A grey cat.", "A grey cat."),
    ("**Alt text**: A grey cat.", "A grey cat."),
    ("`Alt:` A grey cat.", "A grey cat."),
    ("_Alt text:_ A grey cat.", "A grey cat."),
    ("Alt text: *A cat* sits on a mat.", "*A cat* sits on a mat."),
    ("**Alt text: A grey cat.**", "A grey cat."),
    ('"A grey cat."', "A grey cat."),
    ("'A grey cat.'", "A grey cat."),
    ("“A grey cat.”", "A grey cat."),
    ("‘A grey cat.’", "A grey cat."),
    ("`A grey cat.`", "A grey cat."),
    ("```A grey cat.```", "A grey cat."),
    ("```a grey cat.```", "a grey cat."),
    ("```\nA grey cat.\n```", "A grey cat."),
    ("```text\nA grey cat.\n```", "A grey cat."),
    ("```json\nA grey cat.\n```", "A grey cat."),
    ("```text A grey cat.```", "A grey cat."),
    ("```Text on a wall.```", "Text on a wall."),
    ("```md A grey cat.```", "A grey cat."),
    ("**A grey cat.**", "A grey cat."),
    ("*A grey cat.*", "A grey cat."),
    ("_A grey cat._", "A grey cat."),
    ("__A grey cat.__", "A grey cat."),
    ('**"Alt text: A grey cat."**', "A grey cat."),
    ("A grey cat\n\nasleep   on\ta red chair.", "A grey cat asleep on a red chair."),
    ("A cat *sitting* on a mat.", "A cat *sitting* on a mat."),
    (f"{_BOM}A grey cat.", "A grey cat."),
    (f'"{_ZWSP}A grey cat."', "A grey cat."),
    # Apostrophes are not closing quotes: single-quoted text still unwraps.
    ("'A child's drawing of a house.'", "A child's drawing of a house."),
    ("‘A child’s drawing of a house.’", "A child’s drawing of a house."),
    # Balance: quoted fragments at both ends are text, not wrapping.
    (
        '"Hello" is written on the sign, which reads "Bye"',
        '"Hello" is written on the sign, which reads "Bye"',
    ),
    (
        "'Hello' is written on the sign, which reads 'Bye'",
        "'Hello' is written on the sign, which reads 'Bye'",
    ),
    ("'A cat.' 'A dog.'", "'A cat.' 'A dog.'"),
    ("*Red* balloons and *blue* ones.", "*Red* balloons and *blue* ones."),
]

# Zero-width characters that carry meaning must survive in the interior.
_PRESERVED_CASES = [
    (_PERSIAN, _PERSIAN),
    (_HINDI, _HINDI),
    (_FAMILY, _FAMILY),
    (
        f"A sign reading {_PERSIAN} above the door.",
        f"A sign reading {_PERSIAN} above the door.",
    ),
    (f"A{_ZWSP}cat", f"A{_ZWSP}cat"),
    (f'"{_FAMILY} on a bench."', f"{_FAMILY} on a bench."),
]

_EMPTY_CASES = [
    "",
    "   ",
    "\n\n\t",
    "<think>only thinking</think>",
    "<think>a</think>\n  \n",
    "<think>thinking forever about this cat.",
    f"{_BOM}{_ZWSP}{_WORD_JOINER}",
    "**  **",
    '""',
    "**",
    '"',
    "*",
    "`",
    "_",
    "* * *",
]


def test_build_prompt_states_the_limit_and_the_language() -> None:
    prompt = build_prompt(
        max_words=17, language="Portuguese", context=None, prompt=None
    )

    assert "at most 17 words" in prompt
    assert "write in Portuguese." in prompt
    assert prompt.endswith("Reply with the alt text only.")


def test_build_prompt_includes_the_context_sentence_when_given() -> None:
    prompt = build_prompt(
        max_words=30,
        language="English",
        context="product photo on a shoe listing",
        prompt=None,
    )

    assert f"{_CONTEXT_SENTENCE} product photo on a shoe listing." in prompt


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ("ends with period.", "ends with period."),
        ("ends with periods...", "ends with periods."),
        ("ends with ?", "ends with."),
        ("ends with!", "ends with."),
        ("  padded\n\tand   spaced  ", "padded and spaced."),
    ],
)
def test_build_prompt_tidies_the_context(context: str, expected: str) -> None:
    prompt = build_prompt(
        max_words=30, language="English", context=context, prompt=None
    )

    assert f"{_CONTEXT_SENTENCE} {expected}" in prompt
    assert ".." not in prompt
    assert "?." not in prompt
    assert "!." not in prompt


@pytest.mark.parametrize("context", [None, "", "   \n ", "...", "?!"])
def test_build_prompt_omits_the_context_sentence_when_blank(
    context: str | None,
) -> None:
    prompt = build_prompt(
        max_words=30, language="English", context=context, prompt=None
    )

    assert _CONTEXT_SENTENCE not in prompt


def test_explicit_prompt_is_returned_verbatim() -> None:
    prompt = build_prompt(
        max_words=5,
        language="French",
        context="a gallery page",
        prompt="Describe the image in one word.",
    )

    assert prompt == "Describe the image in one word."


@pytest.mark.parametrize("prompt", ["", "   \n"])
def test_blank_explicit_prompt_is_rejected(prompt: str) -> None:
    with pytest.raises(ValueError, match="prompt must not be empty"):
        build_prompt(max_words=30, language="English", context=None, prompt=prompt)


@pytest.mark.parametrize(("raw", "expected"), _CLEANING_CASES + _PRESERVED_CASES)
def test_clean_response_table(raw: str, expected: str) -> None:
    assert clean_response(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), _CLEANING_CASES + _PRESERVED_CASES)
def test_clean_response_is_idempotent_on_the_table(raw: str, expected: str) -> None:
    assert clean_response(clean_response(raw)) == expected


@pytest.mark.parametrize("raw", _EMPTY_CASES)
def test_empty_result_raises_description_error(raw: str) -> None:
    with pytest.raises(DescriptionError) as caught:
        clean_response(raw)

    # Not the refusal subclass: nothing came back, nobody refused anything.
    assert type(caught.value) is DescriptionError
    assert "no text" in str(caught.value)


@pytest.mark.parametrize("raw", _REFUSALS)
def test_refusals_raise_with_the_cleaned_text_attached(raw: str) -> None:
    with pytest.raises(DescriptionRefusedError) as caught:
        clean_response(f'**"{raw}"**')

    assert caught.value.response == raw


@pytest.mark.parametrize("prefix", [_BOM, _ZWSP, _ZWNJ, _ZWJ, _WORD_JOINER])
def test_invisible_characters_do_not_hide_a_refusal(prefix: str) -> None:
    with pytest.raises(DescriptionRefusedError) as caught:
        clean_response(f"{prefix}I cannot describe this.")

    assert caught.value.response == "I cannot describe this."


def test_long_refusal_with_a_late_sentence_break_is_caught() -> None:
    # Over the 200-character guard, so what gives it away is running on for
    # more than 60 characters without ending a sentence.
    text = (
        "I cannot look at this image and will not be producing a description"
        " of it. There is nothing further I am able to offer you here, and no"
        " amount of rephrasing the request is going to change that answer."
    )
    assert len(text) >= 200
    assert text.index(".") >= 60

    with pytest.raises(DescriptionRefusedError):
        clean_response(text)


def test_long_description_opening_with_a_caveat_is_kept() -> None:
    # Both guards clear it: over 200 characters, and its first sentence ends
    # early, the way a description does and a refusal does not.
    text = (
        "I cannot see the far bank. Two rowers in a red boat pass under a"
        " stone bridge, with willow branches trailing in the water and a heron"
        " standing on the gravel in the foreground of the photograph, which was"
        " taken from the towpath early on a misty morning."
    )
    assert len(text) >= 200
    assert text.index(".") < 60

    assert clean_response(text) == text


def test_short_caveat_opening_is_treated_as_a_refusal() -> None:
    # Documented consequence of the accepted guard (design spec section 8, Q4):
    # under 200 characters and matching an opening phrase is enough, so this
    # short description is rejected. The alternative — letting every short
    # "I'm sorry, I can't help with that" through as alt text — is worse.
    text = (
        "I cannot see any people, only a dog on a beach, with a long pier"
        " stretching behind it into the fog."
    )
    assert len(text) < 200

    with pytest.raises(DescriptionRefusedError):
        clean_response(text)


def test_refusal_phrases_only_count_at_the_start() -> None:
    text = "A dog on a beach. I cannot tell what the sign behind it says."

    assert REFUSAL_RE.search(text) is None
    assert clean_response(text) == text


def test_think_patterns_span_newlines_and_ignore_case() -> None:
    assert THINK_RE.sub("", "<Think>\na\nb\n</think>kept") == "kept"
    assert UNCLOSED_THINK_RE.sub("", "kept <THINK>\nrambling") == "kept "


def test_edge_trim_pattern_leaves_the_interior_alone() -> None:
    padded = f"{_BOM}{_ZWSP} {_PERSIAN} {_ZWJ}"

    assert EDGE_TRIM_RE.sub("", padded) == _PERSIAN


_SAFE_BODY = st.text(
    alphabet=st.sampled_from([*list("acinost e.,'’“”*_`\n"), _ZWSP, _ZWJ]),
    min_size=2,
    max_size=40,
)
_LABELS = st.sampled_from(["", "Alt text: ", "alt:", "**ALT TEXT :** "])


@given(
    body=_SAFE_BODY,
    label=_LABELS,
    wrapper=st.sampled_from(_WRAPPER_PAIRS),
)
@settings(max_examples=50, deadline=None)
def test_clean_response_is_idempotent(
    body: str, label: str, wrapper: tuple[str, str]
) -> None:
    opener, closer = wrapper
    raw = f"{opener}{label}{body}{closer}"

    try:
        once = clean_response(raw)
    except DescriptionError:
        # A generated body can clean away to nothing, or (with this alphabet,
        # rarely) read as a refusal. Neither outcome has a fixed point to
        # compare against; both have their own tests above.
        assume(False)
        raise

    assert clean_response(once) == once
