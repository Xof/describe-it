"""Prompt construction and cleanup of the model's reply.

Both halves are pure text handling, kept apart from HTTP and PIL so they can be
tested exhaustively. `clean_response` matters more than its size suggests: a
small vision model answers with prose written for a human — labelled, quoted,
emphasised, occasionally thinking out loud — and this is the only thing
standing between that prose and an HTML `alt` attribute.
"""

import re

from describe_it.exceptions import DescriptionError, DescriptionRefusedError

# Zero-width characters and the BOM. Models copy them out of their training
# data; left in place they sit in front of the text and defeat every anchored
# pattern below, so they are the very first thing removed.
INVISIBLE_RE = re.compile("[\ufeff\u200b\u200c\u200d\u2060]")

# Thinking-capable models are asked not to think (`think: false`), but some
# emit a block anyway; strip it rather than caption an image with the model's
# inner monologue.
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# A block that never closes — the model hit its token limit mid-thought. What
# follows an unclosed opener is monologue too, so it goes to the end of text.
UNCLOSED_THINK_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)

# Phrases that open a refusal. Anchored at the start on purpose: "I cannot"
# halfway through a sentence is ordinary description. The apostrophe class
# covers the typographic quotes models produce as often as ASCII ones.
REFUSAL_RE = re.compile(
    r"^(i['‘’]?m sorry|i am sorry|i cannot|i can['‘’]?t"
    r"|i am unable|i['‘’]?m unable|i apologi[sz]e|as an ai)",
    re.IGNORECASE,
)

# Markdown emphasis markers, which models like to wrap around a label as well
# as around the text: `**Alt text:**`, `**Alt text**:`.
_EMPHASIS = r"[*_`]{0,3}"

# "Alt text:", "Alt:", "ALT TEXT :", "**Alt text:**" — models label their
# answer even when told not to.
_LABEL_RE = re.compile(
    rf"^{_EMPHASIS}\s*alt(?:\s+text)?\s*{_EMPHASIS}\s*:\s*{_EMPHASIS}\s*",
    re.IGNORECASE,
)

# A fenced code block, with the info string ("```text") the fence may carry.
# Matched separately from the plain wrapper pairs because the info string runs
# to the first newline and would otherwise be left in the alt text.
_FENCE_RE = re.compile(r"^```[^\n]*\n(?P<body>.*?)\n?```\Z", re.DOTALL)

# Opening/closing pairs a model wraps its answer in. Longer markers first so
# that ``` is not mistaken for `, ** for *, or __ for _. The last four are the
# ASCII and curly quote pairs.
_WRAPPERS: tuple[tuple[str, str], ...] = (
    ("```", "```"),
    ("**", "**"),
    ("__", "__"),
    ("`", "`"),
    ("*", "*"),
    ("_", "_"),
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
)

# Every character that can appear as decoration. A reply made of nothing else
# is not a description, however many characters long it is.
_MARKER_CHARS = frozenset("`*_\"'“”‘’")

# A refusal is short, or it rambles before its first sentence break. A real
# description that happens to start "I cannot see any faces..." closes that
# first sentence quickly and then keeps going. See `_looks_like_refusal`.
_REFUSAL_MAX_CHARS = 200
_REFUSAL_SENTENCE_WINDOW = 60


def build_prompt(
    *, max_words: int, language: str, context: str | None, prompt: str | None
) -> str:
    """Build the single user message sent with the image.

    There is no system message: several small vision models ignore or
    mishandle one, and everything here fits comfortably in the user turn.

    Args:
        max_words: Target upper bound on the reply's length, in words. It is
            requested, never enforced by truncation.
        language: Output language, as a plain English name the model knows.
        context: Where the image appears ("product photo on a shoe listing").
            `None` or blank omits the context sentence entirely. Trailing
            periods and stray whitespace are tidied so the sentence reads
            properly however the caller phrased it.
        prompt: A complete replacement for the built-in wording. When given, it
            is returned verbatim and every other argument is ignored — the
            caller has taken ownership of the wording.

    Returns:
        The prompt text.

    Raises:
        ValueError: If `prompt` is given but blank. An empty prompt would ask
            the model to describe the image with no instructions at all, which
            is a mistake at the call site rather than a way to opt out.
    """
    if prompt is not None:
        if not prompt.strip():
            raise ValueError("prompt must not be empty; pass None for the default")
        return prompt

    lines = [
        "Write alt text for this image, for use on a web page.",
        f"Requirements: at most {max_words} words; one or two plain sentences;"
        " describe what is visually present — the main subject, setting, and"
        " any action; quote any clearly legible text verbatim; do not begin"
        ' with "image of", "picture of", or "photo of"; do not guess at'
        " anything not visible; no opinions, warnings, or commentary; write in"
        f" {language}.",
    ]
    if context is not None:
        # The context is interpolated into a sentence of ours, so a caller who
        # wrote their own full stop must not get two.
        trimmed = " ".join(context.split()).rstrip(".")
        if trimmed:
            lines.append(f"The image appears in this context: {trimmed}.")
    lines.append("Reply with the alt text only.")
    return "\n".join(lines)


def clean_response(text: str) -> str:
    """Turn the model's raw reply into a single line of alt text.

    Args:
        text: The `message.content` returned by the model.

    Returns:
        The alt text: one line, no label, no wrapping quotes or markdown,
        internal whitespace collapsed to single spaces.

    Raises:
        DescriptionError: If nothing is left after cleaning.
        DescriptionRefusedError: If the reply reads as a refusal.
    """
    cleaned = INVISIBLE_RE.sub("", text)
    cleaned = THINK_RE.sub("", cleaned)
    cleaned = UNCLOSED_THINK_RE.sub("", cleaned)
    cleaned = _strip_decorations(cleaned)
    cleaned = " ".join(cleaned.split())

    if not cleaned or all(char in _MARKER_CHARS for char in cleaned):
        raise DescriptionError("model returned no text")
    if _looks_like_refusal(cleaned):
        raise DescriptionRefusedError(cleaned)
    return cleaned


def _strip_decorations(text: str) -> str:
    """Remove fences, label prefixes and wrapping markers until none are left.

    One pass would cover a well-behaved model, but they stack decoration
    (`**"Alt text: A grey cat."**`), and `clean_response` is contracted to be
    idempotent — `clean(clean(x)) == clean(x)` — which only a fixed point
    delivers. Each pass strictly shortens the string, so the loop terminates.

    Args:
        text: Text with thinking blocks already removed.

    Returns:
        The text with surrounding decoration removed and the ends trimmed.
    """
    current = text.strip()
    while True:
        stripped = _strip_fence(current)
        stripped = _LABEL_RE.sub("", stripped)
        stripped = _strip_wrapper(stripped).strip()
        if stripped == current:
            return current
        current = stripped


def _strip_fence(text: str) -> str:
    """Remove a fenced code block, including the fence's info string.

    Args:
        text: The text to unfence.

    Returns:
        The fence's body, or the text unchanged if it is not a fenced block
        with an info string or a newline after the opening fence.
    """
    match = _FENCE_RE.match(text)
    if match is None:
        return text
    return match.group("body")


def _strip_wrapper(text: str) -> str:
    """Remove one pair of matching surrounding markers, if present.

    Args:
        text: The text to unwrap.

    Returns:
        The text without its outermost marker pair, or unchanged if it is not
        wrapped. A bare pair of markers unwraps to nothing, which the caller
        turns into a "no text" error rather than alt text reading `""`.
    """
    for opener, closer in _WRAPPERS:
        if not text.startswith(opener) or not text.endswith(closer):
            continue
        if len(text) < len(opener) + len(closer):
            # A single marker character matches both ends of itself.
            continue
        interior = text[len(opener) : -len(closer)]
        if closer in interior:
            # Unbalanced, so these are not wrapping markers: the text quotes
            # something ("Hello" is written on the sign, which reads "Bye")
            # and stripping the ends would mangle it.
            continue
        return interior
    return text


def _looks_like_refusal(text: str) -> bool:
    """Judge whether cleaned text is a refusal rather than a description.

    Guarded deliberately, because the opening phrase alone is not evidence: a
    description may legitimately open "I cannot see the far bank. Two rowers
    sit in a red boat under a stone bridge, ...". The match therefore only
    counts when the text also *looks* like a refusal sentence — short, or
    running on past the point where a description would have closed its first
    sentence. A refusal that clears both guards is returned as alt text, which
    is the intended trade: a wrong description beats a wrongly rejected one.

    Args:
        text: Cleaned, whitespace-collapsed text.

    Returns:
        True if the text should be treated as a refusal.
    """
    if REFUSAL_RE.match(text) is None:
        return False
    if len(text) < _REFUSAL_MAX_CHARS:
        return True
    return "." not in text[:_REFUSAL_SENTENCE_WINDOW]
