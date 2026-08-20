"""Prompt construction and cleanup of the model's reply.

Both halves are pure text handling, kept apart from HTTP and PIL so they can be
tested exhaustively. `clean_response` matters more than its size suggests: a
small vision model answers with prose written for a human, and this is the only
thing standing between that prose and an HTML `alt` attribute.
"""

import re

from describe_it.exceptions import DescriptionError, DescriptionRefusedError

# Thinking-capable models are asked not to think (`think: false`), but some
# emit a block anyway; strip it rather than caption an image with the model's
# inner monologue.
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

# Phrases that open a refusal. Anchored at the start on purpose: "I cannot"
# halfway through a sentence is ordinary description.
REFUSAL_RE = re.compile(
    r"^(i'?m sorry|i cannot|i can'?t|i am unable|i'?m unable"
    r"|i apologi[sz]e|as an ai)",
    re.IGNORECASE,
)

# "Alt text:", "Alt:", "ALT TEXT :" — models like to label their answer even
# when told not to.
_LABEL_RE = re.compile(r"^alt(?:\s+text)?\s*:\s*", re.IGNORECASE)

# Opening/closing pairs a model wraps its answer in. Longer markers first so
# that ``` is not mistaken for ` and ** is not mistaken for *. The last four
# are the ASCII and curly quote pairs.
_WRAPPERS: tuple[tuple[str, str], ...] = (
    ("```", "```"),
    ("**", "**"),
    ("`", "`"),
    ("*", "*"),
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
)

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
            `None` or blank omits the context sentence entirely.
        prompt: A complete replacement for the built-in wording. When given, it
            is returned verbatim and every other argument is ignored — the
            caller has taken ownership of the wording.

    Returns:
        The prompt text.
    """
    if prompt is not None:
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
    if context is not None and context.strip():
        lines.append(f"The image appears in this context: {context}.")
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
    cleaned = _strip_decorations(THINK_RE.sub("", text))
    cleaned = " ".join(cleaned.split())

    if not cleaned:
        raise DescriptionError("model returned no text")
    if _looks_like_refusal(cleaned):
        raise DescriptionRefusedError(cleaned)
    return cleaned


def _strip_decorations(text: str) -> str:
    """Remove label prefixes and wrapping markers until none are left.

    One pass would cover a well-behaved model, but they stack decoration
    (`**"Alt text: A grey cat."**`), and `clean_response` is contracted to be
    idempotent — `clean(clean(x)) == clean(x)` — which only a fixed point
    delivers. Each pass strictly shortens the string, so the loop terminates.

    Args:
        text: Text with the thinking block already removed.

    Returns:
        The text with surrounding decoration removed and the ends trimmed.
    """
    current = text.strip()
    while True:
        stripped = _strip_wrapper(_LABEL_RE.sub("", current)).strip()
        if stripped == current:
            return current
        current = stripped


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
        if (
            text.startswith(opener)
            and text.endswith(closer)
            and len(text) >= len(opener) + len(closer)
        ):
            return text[len(opener) : -len(closer)]
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
