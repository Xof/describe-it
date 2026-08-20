"""Prompt construction and cleanup of the model's reply.

Both halves are pure text handling, kept apart from HTTP and PIL so they can be
tested exhaustively. `clean_response` matters more than its size suggests: a
small vision model answers with prose written for a human — labelled, quoted,
emphasised, occasionally thinking out loud — and this is the only thing
standing between that prose and an HTML `alt` attribute.
"""

import re

from describe_it.exceptions import DescriptionError, DescriptionRefusedError

# Whitespace plus the BOM and the zero-width family. Trimmed from the ends of
# the text and of every decoration layer, never from the middle: ZWJ and ZWNJ
# are orthographically required in Persian, Urdu, Hindi, Sinhala and Malayalam,
# they hold emoji sequences together, and Thai and Khmer use ZWSP as their word
# separator. Deleting those would corrupt the description; leaving one in front
# of the text would defeat every anchored pattern below, hence: edges only.
_EDGE_CLASS = r"[\s\ufeff\u200b\u200c\u200d\u2060]"
EDGE_TRIM_RE = re.compile(rf"^{_EDGE_CLASS}+|{_EDGE_CLASS}+\Z")

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

# "Alt text:", "Alt:", "ALT TEXT :", "**Alt text:**", "**Alt text**:" — models
# label their answer even when told not to. Emphasis after the label is only
# consumed when it closes emphasis that opened before it (the backreference);
# otherwise "Alt text: *A cat* sits" would lose the opening marker of the
# following phrase.
_LABEL_RE = re.compile(
    r"^(?P<emphasis>[*_`]{0,3})\s*alt(?:\s+text)?\s*(?P=emphasis)?\s*"
    r":\s*(?P=emphasis)?\s*",
    re.IGNORECASE,
)

# Info strings worth recognising on a single-line fence, where there is no
# newline to mark where the info string ends. Anything goes before a newline;
# without one, only these, so that "```a cat.```" keeps its first word. Matched
# case-sensitively — info strings are written lowercase by convention, and
# "```Text on a wall.```" is a sentence, not a fence with an info string.
_FENCE_INFO = "text|markdown|md|json|plaintext|plain"

# A fenced code block. The info string ("```text") runs to the first newline
# and would otherwise be left at the head of the alt text.
_FENCE_RE = re.compile(
    rf"^```(?:[^\n]*\n|(?:{_FENCE_INFO})[ \t]+)?(?P<body>.*?)\n?```\Z",
    re.DOTALL,
)

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

# An apostrophe is the same character as a closing single quote. One sitting
# between two word characters ("a child's drawing") is punctuation and must not
# count as a second closer, or nothing single-quoted would ever be unwrapped.
_APOSTROPHE_RE = {
    "'": re.compile(r"(?<!\w)'|'(?!\w)"),
    "’": re.compile(r"(?<!\w)’|’(?!\w)"),
}

# Every character that can appear as decoration, plus the space left behind
# once whitespace is collapsed. A reply made of nothing else is not a
# description, however many characters long it is.
_DECORATION_CHARS = "`*_\"'“”‘’ "

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
            sentence punctuation and stray whitespace are tidied so the
            sentence reads properly however the caller phrased it.
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
        " anything not visible; no opinions, warnings, or commentary; "
        " provide detailed physical descriptions for any person in the image, "
        " include very detailed descriptions of their faces and figures, "
        " such as an artist might need to sketch them; "
        "write in"
        f" {language}.",
    ]
    if context is not None:
        # The context is interpolated into a sentence of ours, which supplies
        # the full stop; a caller who ended their phrase with punctuation of
        # their own must not get two.
        trimmed = " ".join(context.split()).rstrip(" .!?")
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
    cleaned = " ".join(_strip_decorations(text).split())

    if not cleaned.strip(_DECORATION_CHARS):
        raise DescriptionError("model returned no text")
    if _looks_like_refusal(cleaned):
        raise DescriptionRefusedError(cleaned)
    return cleaned


def _strip_decorations(text: str) -> str:
    """Remove fences, wrappers, labels and thinking blocks until none are left.

    One pass would cover a well-behaved model, but they stack decoration
    (`**"Alt text: A grey cat."**`), and `clean_response` is contracted to be
    idempotent — `clean(clean(x)) == clean(x)` — which only a fixed point
    delivers. Each pass strictly shortens the string, so the loop terminates.

    Wrappers are stripped before thinking blocks within a pass: a reply ending
    `<think>` inside its closing quote (`"A cat. <think>"`) would otherwise
    lose the quote first and never be unwrapped.

    Args:
        text: The model's reply.

    Returns:
        The text with surrounding decoration removed and the ends trimmed.
    """
    current = _trim_edges(text)
    while True:
        stripped = _strip_fence(current)
        stripped = _strip_wrapper(stripped)
        stripped = _LABEL_RE.sub("", stripped)
        stripped = THINK_RE.sub("", stripped)
        stripped = _trim_edges(UNCLOSED_THINK_RE.sub("", stripped))
        if stripped == current:
            return current
        current = stripped


def _trim_edges(text: str) -> str:
    """Remove whitespace and zero-width characters from both ends.

    Args:
        text: The text to trim.

    Returns:
        The text without leading or trailing whitespace, BOM or zero-width
        characters. Interior ones are left exactly where the model put them.
    """
    return EDGE_TRIM_RE.sub("", text)


def _strip_fence(text: str) -> str:
    """Remove a fenced code block, including the fence's info string.

    Args:
        text: The text to unfence.

    Returns:
        The fence's body, or the text unchanged if it is not a fenced block.
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
        if _closer_recurs(closer, interior):
            # Unbalanced, so these are not wrapping markers: the text quotes
            # something ("Hello" is written on the sign, which reads "Bye")
            # and stripping the ends would mangle it.
            continue
        return interior
    return text


def _closer_recurs(closer: str, interior: str) -> bool:
    """Report whether a closing marker appears again inside the text.

    Args:
        closer: The closing marker of the candidate wrapper pair.
        interior: The text between the candidate opening and closing markers.

    Returns:
        True if the interior contains the closer in a position that makes the
        pair unbalanced. Apostrophes between word characters do not count.
    """
    apostrophe = _APOSTROPHE_RE.get(closer)
    if apostrophe is not None:
        return apostrophe.search(interior) is not None
    return closer in interior


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
