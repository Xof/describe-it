---
id: 0007
title: Send think:false unconditionally, with temperature 0.2 and num_predict as a stop
date: 2026-08-20
status: Accepted
summary: Every chat request carries think:false without a capability probe, temperature 0.2, and num_predict = max_words * 4 + 32 as a safety stop rather than a length control.
---

# 0007. Send think:false unconditionally, with temperature 0.2 and num_predict as a stop

## Context

The default model and its likely alternatives (qwen3.5, gemma4) are
thinking-capable. Left to themselves they spend seconds of hidden reasoning on
a one-sentence caption, which is latency the caller pays for nothing.

The specification (§2.5) required implementation to *verify* that Ollama
accepts `think: false` from a model with no thinking capability, and to fall
back to sending the field only after an `/api/show` capability probe if it did
not — a round trip before every description.

Two other sampling questions came with it: how deterministic to be, and how to
keep a model from running on forever.

## Decision

- `think: false` is sent on every `/api/chat` request, unconditionally.
  Verified against Ollama 0.32 with `llava:7b`, which has no thinking
  capability at all and accepts the field without complaint, so the capability
  probe the specification allowed for is not implemented.
- `temperature: 0.2`.
- `num_predict: max_words * 4 + 32`, as a **safety stop**. The length control
  is the prompt, which asks for at most `max_words` words. No truncation is
  ever applied to the reply.

## Alternatives considered

- **Probing `/api/show` for the `thinking` capability first** — the
  specification's fallback. It costs a round trip per description (or a cache
  with its own invalidation question) to learn something the server does not
  care about. Only worth implementing if a future Ollama starts rejecting the
  field.
- **`temperature: 0`** — the obvious choice for a deterministic caption, and
  rejected because a few small models degenerate into repeating a phrase at
  exactly 0. Alt text is not a task where the last of the determinism is worth
  that risk.
- **Truncating the reply to `max_words`** — rejected in the specification
  (§2.6): the caller asked for alt text, not for a fragment cut off mid
  sentence. An overrun is returned as-is.
- **A `num_predict` closer to the real token/word ratio (~1.3 for English)** —
  four tokens per word plus a constant margin is deliberately loose, so that a
  short budget (`max_words=1`) does not cut the model off before its first
  sentence, and so the stop never becomes the thing shaping the answer.

## Consequences

- A thinking model that ignores `think: false` and emits a `<think>` block
  anyway is still handled: cleaning strips it (ADR 0006).
- Output is not reproducible run to run. The live tests therefore assert
  structure only (ADR 0010).
- `num_predict` scales with `max_words`, so a caller who raises the budget
  also raises the stop; nothing else needs adjusting.
- `keep_alive` is omitted from the body entirely when unset, rather than sent
  as `null`, because `null` overrides the server's own five-minute default
  instead of asking for it.
