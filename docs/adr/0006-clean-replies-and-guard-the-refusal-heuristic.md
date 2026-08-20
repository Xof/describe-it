---
id: 0006
title: Clean the model's reply to a fixed point, and guard the refusal heuristic
date: 2026-08-20
status: Accepted
summary: Replies are stripped of labels, quotes, markdown and think blocks until they stop changing, and a refusal is detected only when a refusal phrase starts a short or run-on reply.
---

# 0006. Clean the model's reply to a fixed point, and guard the refusal heuristic

## Context

Small vision models do not return bare alt text. They return
`**"Alt text: A red circle."**`, or a fenced block with an info string, or a
`<think>` block in front of the answer despite being told not to think. A
caller who asked for alt text and got markdown has to strip it themselves,
every time, which is the library's job to do once.

They also refuse. "I'm sorry, but I can't describe this image" is a *valid
string* — a caller who does not inspect it will publish it as alt text. Given
the use case in ADR 0001, refusals are common enough that letting them through
silently is the worst available outcome. But a refusal cannot be detected
reliably, only guessed at, and a description may legitimately open "I cannot
see any people in this photograph".

## Decision

`clean_response(text)` strips `<think>` blocks (closed or unclosed), a leading
`Alt text:` / `Alt:` label, code fences with their info strings, and one
wrapping pair of quotes or emphasis — repeatedly, **to a fixed point**, so that
`clean(clean(x)) == clean(x)`. Internal whitespace collapses to single spaces.
An empty result is a `DescriptionError`.

A refusal is detected by a **guarded, start-anchored** heuristic: a refusal
phrase (`i'm sorry`, `i am sorry`, `i cannot`, `i can't`, `i am unable`,
`i'm unable`, `i apologise/apologize`, `as an ai`, with straight or
typographic apostrophes)
at the very start of the *cleaned* text, **and** either the text is under 200
characters **or** it has no `.` before index 60. That raises
`DescriptionRefusedError`, which carries the cleaned text on `.response`.

Wrapping pairs are stripped only when balanced — the interior must not contain
the closing character again — and an apostrophe between two word characters is
punctuation rather than a closing quote. The BOM and zero-width characters are
removed at the leading and trailing edges only.

## Alternatives considered

- **No refusal heuristic; return refusals as strings** — offered at sign-off
  (Q4) and rejected: it makes the library's contract "a string that may or may
  not be alt text", and the failure is silent.
- **An unanchored match against the first 80 characters** — the original
  specification wording. It fires on descriptions that merely mention the
  phrase mid-sentence, and it was corrected at unit-1 review.
- **Matching against the raw text** — decoration sits in front of the phrase
  and defeats an anchored pattern, so the check has to run on cleaned text.
- **Stripping one layer of decoration** — the specification's first wording.
  Models stack decoration (`**"Alt text: …"**`), and one layer is not
  idempotent, which §6.1 requires.
- **Stripping zero-width characters throughout** — the first implementation.
  It corrupts Persian, Urdu, Hindi, Sinhala, Malayalam, Thai and Khmer text and
  breaks emoji sequences; ZWJ and ZWNJ are orthography, not noise.

## Consequences

- The heuristic can misfire. A *short* description opening "I cannot see any
  people" is treated as a refusal — an accepted cost, since the prompt forbids
  that opening and refusals are the common case. It is documented as a
  heuristic in the API docs, and `.response` is the escape hatch.
- The guard is an OR, which is deliberately generous towards raising: a long
  refusal with no early sentence break still fires.
- Cleaning is pure and testable without a server, and the idempotence
  invariant is property-tested with hypothesis rather than by example.
- A model that returns nothing but decoration is a `DescriptionError` rather
  than an empty string.
