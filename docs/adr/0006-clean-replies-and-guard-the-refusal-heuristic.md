---
id: 0006
title: Clean the model's reply to a fixed point, and guard the refusal heuristic
date: 2026-08-20
status: Accepted
summary: Replies are stripped of labels, quotes, markdown and think blocks until they stop changing, and a refusal is detected only when a refusal phrase opens a short or run-on reply.
---

# 0006. Clean the model's reply to a fixed point, and guard the refusal heuristic

## Context

Spec §2.1 promises the caller "a non-empty `str` containing the alt text only —
no label, no surrounding quotes, no markdown, no trailing commentary, single
line, internal whitespace collapsed", so spec §2.6 defines the post-processing
that gets there.

Refusals are the harder half. Spec §2.6 step 5 describes the problem: the
common failure is "the common 'I'm sorry, but I can't describe this image'
failure", which is a valid string and would otherwise be published as alt text.
Spec §8 Q4 put the strictness of detecting it to sign-off, offering the guard
or dropping the heuristic entirely.

## Decision

`clean_response(text)` applies spec §2.6's steps: remove `<think>…</think>`,
strip whitespace, strip a leading `Alt text:`/`Alt:` label, strip one wrapping
pair of quotes or markdown emphasis, collapse internal whitespace, and raise
`DescriptionError("model returned no text")` on an empty result.

Stripping runs **to a fixed point** rather than one layer. The unit-1 errata
records why: "'One layer' of decoration stripping (§2.6 step 2) is not
idempotent, which §6.1 requires", and commit `95226bd` adds "because models
stack it (`**"Alt text: ..."**`)".

The refusal check is the guard resolved at sign-off (spec §8 Q4 Resolution):
"fire only when a refusal phrase starts the cleaned text *and* (the text is
under 200 characters *or* it has no '.' before index 60).
`DescriptionRefusedError.response` carries the cleaned text." The phrase list
is spec §2.6's, extended by the unit-1 errata to accept "U+2018/U+2019
apostrophes and `i am sorry`"; the regex in `prompt.py` also carries
`i'm unable` alongside `i am unable`.

## Alternatives considered

- **Dropping the heuristic and letting refusals through as plain strings** —
  offered explicitly in spec §8 Q4 and rejected by its Resolution.
- **An unanchored match against the first 80 characters of the raw text** — the
  specification's original wording, corrected by the unit-1 errata: "§2.6 step
  5 originally said 'against the first 80 characters' and 'raw text';
  corrected above to the start-anchored, guarded rule and cleaned text."
- **Stripping one layer of decoration** — see Decision; not idempotent.
- **Stripping zero-width characters throughout the text** — the first
  implementation. Commit `d4304cb`: "it removed ZWJ, ZWNJ, ZWSP and the word
  joiner from the whole text, which corrupts Persian and Urdu (the ZWNJ in
  'می‌خواهم' separates prefix from verb), Hindi and Sinhala conjuncts,
  Malayalam…". They are trimmed at the edges only (unit-1 errata).
- **Stripping a wrapping pair unconditionally** — replaced by the balance rule
  in the unit-1 errata, "so quoted text at both ends of a description
  survives", with an apostrophe between two word characters exempted.

## Consequences

- The heuristic can misfire, and spec §2.6 step 5 says so: "This is a heuristic
  and is documented as such". Spec §6.1 accepts the specific cost: "a *short*
  description opening with such a phrase is treated as a refusal — accepted
  cost, since the prompt forbids that opening and refusals are the common
  case." `.response` is the escape hatch (spec §2.6 step 5).
- The guard is an OR (spec §8 Q4 Resolution), so a long refusal with no early
  sentence break still fires.
- Cleaning is pure and needs no server (spec §5's module layout), and its
  idempotence is property-tested (spec §6.1; `tests/test_prompt.py`).
- A reply that is nothing but decoration is a `DescriptionError` rather than an
  empty string (commit `9693b3d`: "Treat a reply that is nothing but markers as
  no reply").
- No truncation is ever applied. Spec §2.6: "If a model overruns `max_words`,
  that is returned as-is; the caller asked for alt text, not for a truncated
  fragment."
