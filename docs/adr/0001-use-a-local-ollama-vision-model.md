---
id: 0001
title: Use a local Ollama vision model rather than a hosted description API
date: 2026-08-19
status: Accepted
summary: Descriptions come from a vision model served by Ollama, because the workload is high-volume and includes images commercial services refuse.
---

# 0001. Use a local Ollama vision model rather than a hosted description API

## Context

From spec §1, which states the two reasons the library exists:

- "alt-text generation at volume is cheap locally and expensive via commercial
  APIs";
- "the images may be NSFW, which commercial services refuse or penalise. A
  local model has no such policy layer beyond whatever is baked into the
  weights."

## Decision

Descriptions come from a vision model served by [Ollama](https://ollama.com),
over its native HTTP API (spec §1, §3). The server is named by a `host` option
that defaults to `http://localhost:11434` (spec §2.2).

## Alternatives considered

- **A commercial hosted vision API** — rejected for the two reasons quoted in
  Context (spec §1): per-image cost at volume, and a content policy that
  rejects part of the corpus.
- **Ollama's OpenAI-compatible endpoint, and llama.cpp directly** — spec §3
  lists both as non-goals ("Any backend other than Ollama's native HTTP API
  (no OpenAI-compatible endpoint, no llama.cpp direct)") without giving
  reasons.

  > Rationale not recovered from project sources.

## Consequences

- The caller must run Ollama and have the model present: describe-it never
  pulls one implicitly (spec §2.3, ADR 0009), so the first call on a fresh
  machine raises `ModelNotFoundError` naming the `ollama pull` command.
- Description quality is bounded by the local model, and its output is not
  stable enough to assert on beyond structure — spec §6.2 requires
  "structural assertions only — no keyword matching, because small models are
  not reliable enough to gate on" (ADR 0010).
- The library's whole job is three JSON POSTs (spec §5), which is what makes
  the stdlib transport in ADR 0002 sufficient.
- "Local" is the default, not a guarantee: `host` accepts any HTTP or HTTPS
  URL, and the unit-2 errata (2026-08-20) records that a remote Ollama behind
  a reverse proxy is a supported deployment.
