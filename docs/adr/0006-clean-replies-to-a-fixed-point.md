---
id: 0006
title: Clean the model's reply to a fixed point
date: 2026-08-19
status: Accepted
summary: A reply is stripped of think blocks, labels, quotes and markdown until stripping stops changing it, and an empty result is a DescriptionError.
---

# 0006. Clean the model's reply to a fixed point

## Context

Spec §2.1 promises the caller "a non-empty `str` containing the alt text only —
no label, no surrounding quotes, no markdown, no trailing commentary, single
line, internal whitespace collapsed". Small vision models do not answer that
way, so spec §2.6 defines the post-processing that gets there.

## Decision

`clean_response(text)` applies spec §2.6's steps in order: remove a
`<think>…</think>` block ("belt and braces for models that emit one despite
`think: false`"), strip whitespace, strip a leading `Alt text:` / `Alt:` label
(case-insensitive), strip one layer of matching surrounding quotes and markdown
emphasis or code fences, collapse all internal whitespace to single spaces, and
raise `DescriptionError("model returned no text")` if nothing is left.

Spec §2.6 also fixes what is *not* done: "No sentence-level truncation is
performed. If a model overruns `max_words`, that is returned as-is; the caller
asked for alt text, not for a truncated fragment."

## Alternatives considered

- **Truncating an over-long reply** — rejected by spec §2.6, quoted above.
- The individual cleaning steps are enumerated in spec §2.6 without competing
  approaches being weighed.

  > Rationale not recovered from project sources.

## Consequences

- Cleaning is pure and needs no server, which is why spec §5 puts it in
  `prompt.py` beside `build_prompt` and spec §6.1 tests it as a table.
- Spec §6.1 requires `clean(clean(x)) == clean(x)` for every case, which is
  what makes the fixed-point behaviour in the addendum below a requirement
  rather than a refinement.

## Addendum (2026-08-20)

Corrections from the unit-1 review, recorded in the unit-1 errata and commits
`95226bd`, `9693b3d`, `d4304cb` and `2dde2fb`:

- Stripping runs **to a fixed point**, not one layer. The errata: "'One layer'
  of decoration stripping (§2.6 step 2) is not idempotent, which §6.1
  requires"; commit `95226bd` adds "because models stack it
  (`**"Alt text: ..."**`)".
- A wrapping pair is stripped only when balanced — "the interior must not
  contain the closing character again — so quoted text at both ends of a
  description survives" — and "an apostrophe between two word characters ('a
  child's drawing') is punctuation rather than a closing quote" (errata).
- Code-fence info strings, and unclosed `<think>` blocks (stripped to the end
  of the text), are removed (errata). Commit `2dde2fb` made the info-string
  match case-sensitive, because "the fence pattern matched its info strings
  case-insensitively, so '```Text on a wall.```' lost its first word".
- The BOM and zero-width characters are removed **at the leading and trailing
  edges only**. The errata gives the reason — "interior ZWJ/ZWNJ/ZWSP are
  orthographically required by several scripts (Persian, Urdu, Hindi, Sinhala,
  Malayalam, Thai, Khmer) and hold emoji sequences together" — and commit
  `d4304cb` records the regression that prompted it.
- A reply that is nothing but markers is treated as no reply (commit
  `9693b3d`).
