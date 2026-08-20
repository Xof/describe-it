---
id: 0004
title: Default to the qwen3.5:4b vision model
date: 2026-08-19
status: Accepted
summary: The packaged default model is qwen3.5:4b, chosen for refusal resistance over caption quality, and overridable with $DESCRIBE_IT_MODEL.
---

# 0004. Default to the qwen3.5:4b vision model

## Context

`describe()` has to work with no configuration, which means naming a model.
No vision model was installed on the development machine when the
specification was written, so the choice was made on the models' documented
character rather than on measurement, and put to sign-off as open question Q1.

The deciding constraint is the use case in ADR 0001: some of the images are
NSFW. A model that refuses them produces a `DescriptionRefusedError` instead
of alt text, and no amount of prompt wording fixes a refusal that comes from
the weights.

## Decision

`DEFAULT_MODEL = "qwen3.5:4b"` (Alibaba, roughly 3 GB, vision plus thinking,
with thinking disabled by us — ADR 0007). `$DESCRIBE_IT_MODEL` overrides it,
and is read per call rather than frozen at import, so a long-lived process can
be reconfigured without reloading the package.

## Alternatives considered

- **`gemma4:e4b`** — better caption quality in the author's experience, and
  the Gemma line refuses more readily. Caption quality is worth less here than
  getting an answer at all, so it lost. It remains a good explicit choice for
  callers whose images are uncontroversial.
- **`qwen3.5:2b`** — smaller and faster, and named in the specification as a
  fallback for constrained machines. Rejected as the default because a 2B
  model's captions are noticeably weaker and the 4B fits comfortably on the
  hardware this library targets.
- **No default at all (require an explicit model)** — rejected: it would break
  the zero-config call that the whole API shape in ADR 0003 is built around.

## Consequences

- The caller must pull the model once (`ollama pull qwen3.5:4b`); nothing is
  downloaded implicitly (ADR 0009), so the first call against a fresh machine
  raises `ModelNotFoundError` with that command in its message.
- The choice was not measured against a benchmark, and the record should not
  be read as though it had been. Revisiting it needs a live comparison, which
  is what the opt-in tests in ADR 0010 exist to make easy.
- Nothing in the library checks that the configured model is vision-capable.
  Ollama reports a text-only model clearly and that surfaces as
  `OllamaResponseError` — detecting it in advance is a stated non-goal.

## Addendum (2026-08-20)

Recorded because it affects anyone running the live tests, not as a change of
decision: the development machine's Ollama 0.32.13 cannot load its own
`qwen3.5:4b` blob (`llama-server` fails with `key
qwen35.rope.dimension_sections has wrong array length; expected 4, got 3`), so
the live tests there are run with `DESCRIBE_IT_MODEL=llava:7b`. The default
is unchanged; CI's integration job still pulls and uses `qwen3.5:4b`.
