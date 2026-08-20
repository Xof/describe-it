---
id: 0004
title: Default to the qwen3.5:4b vision model
date: 2026-08-19
status: Accepted
summary: The packaged default model is qwen3.5:4b, chosen for refusal resistance over caption quality, and overridable with $DESCRIBE_IT_MODEL.
---

# 0004. Default to the qwen3.5:4b vision model

## Context

`describe()` works with no configuration (spec §2.2: "Every option has a
sensible default so the zero-config call works"), which means naming a model.
Spec §8 records that "no vision model is installed locally today", so the
choice was put to sign-off as open question Q1 rather than measured.

## Decision

`DEFAULT_MODEL = "qwen3.5:4b"`, per spec §8 Q1 and its Resolution ("**Q1:**
`qwen3.5:4b` is the default model"). Q1 describes it as "Alibaba; ~3 GB;
vision+thinking, thinking disabled by us; Qwen models are generally less
refusal-prone than Google's Gemma line, which matters for the NSFW use case".

`$DESCRIBE_IT_MODEL` overrides it (spec §2.2). It is resolved when a describer
is constructed — which is per call for `describe()` — and `config.py`'s module
docstring records why it is not a module-level constant: a constant "would
freeze whatever the environment held when the package was first imported,
which is wrong for a library a long-lived process imports once at start-up".
A long-lived `Describer` resolves once, which `describer.py` documents as
deliberate, so its behaviour "cannot change under it if the process edits its
own environment later".

## Alternatives considered

- **`gemma4:e4b`** — the one alternative spec §8 Q1 offers: "higher caption
  quality in my experience, more likely to refuse". It lost because refusal
  resistance is what the NSFW use case needs (spec §1, §8 Q1).
- No other model is weighed anywhere in the specification, its errata or the
  commit history — in particular, no smaller Qwen tag is discussed.

  > Rationale not recovered from project sources.

## Consequences

- The model must be pulled once; nothing is downloaded implicitly (spec §2.3,
  ADR 0009), so a fresh machine gets `ModelNotFoundError` with the command in
  its message.
- The choice rests on the character spec §8 Q1 attributes to the two model
  families, not on a benchmark. Spec §8 Q1 says as much ("in my experience"),
  and revisiting it needs a live comparison, which is what the opt-in tests of
  ADR 0010 make possible.
- Nothing checks that the configured model is vision-capable: spec §3 makes
  that a non-goal, noting "Ollama returns a clear error for text-only models;
  it is surfaced as `OllamaResponseError`".

## Addendum (2026-08-20)

Observed while running the live tests on this machine, and recorded because it
affects anyone else who runs them: Ollama 0.32.13 here cannot load its own
`qwen3.5:4b` blob. `describe-it` reports the server's message verbatim —
`llama-server process has terminated: exit status 1: error: Failed to load CLIP
model ... key qwen35.rope.dimension_sections has wrong array length; expected
4, got 3` — as an `OllamaResponseError`. The live tests were therefore run with
`DESCRIBE_IT_MODEL=llava:7b`. The default is unchanged, and CI's integration
job still pulls `qwen3.5:4b`.
