---
id: 0007
title: Fix the sampling options for a caption call
date: 2026-08-19
status: Accepted
summary: Every chat request carries temperature 0.2 and num_predict = max_words * 4 + 32, the latter a safety stop rather than the length control.
---

# 0007. Fix the sampling options for a caption call

## Context

Spec §2.5 fixes the body of every `/api/chat` request, including its
`options` object. Two questions had to be settled there: how deterministic to
make a caption, and how to stop a model that will not stop by itself.

## Decision

From spec §2.5's request body:

```json
"options": {"temperature": 0.2, "num_predict": <max_words * 4 + 32>}
```

`num_predict` is a stop, not a length control. Spec §2.5: "`num_predict` is a
safety stop, not the length control — the prompt is."

## Alternatives considered

- **`temperature: 0`** — rejected in spec §5: "Not 0 — a few small models
  degenerate into repetition at exactly 0."
- **Truncating the reply to `max_words`** — rejected in spec §2.6: "No
  sentence-level truncation is performed. If a model overruns `max_words`, that
  is returned as-is; the caller asked for alt text, not for a truncated
  fragment."
- **A `num_predict` closer to the real ratio** — `describer.py`'s comment
  records why the constant is loose: "Four tokens per requested word is well
  above English's ~1.3, and the constant margin keeps a very short request
  (max_words=1) from being cut off before the model has finished its first
  sentence."

## Consequences

- Output is not reproducible run to run, so the live tests assert structure
  only (spec §6.2, ADR 0010).
- `num_predict` scales with `max_words`, so raising the caller's word budget
  raises the stop with it; nothing else needs adjusting.
- Length is requested, never enforced: spec §2.2 describes `max_words` as
  "Enforced by prompt, not by truncation", and spec §3 lists "Guaranteeing
  output length" as a non-goal — "the model is asked, not forced."
