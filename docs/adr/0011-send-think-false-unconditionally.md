---
id: 0011
title: Send think:false unconditionally, without a capability probe
date: 2026-08-20
status: Accepted
summary: Every chat request carries think:false, because Ollama 0.32 accepts it from models that cannot think and the specified fallback probe would cost a round trip per description.
---

# 0011. Send think:false unconditionally, without a capability probe

## Context

Spec §2.5 puts `"think": false` in every request body and gives the reason:
"`think: false` stops thinking-capable models (qwen3.5, gemma4) from burning
seconds on hidden reasoning for a caption."

It also left one thing for the implementation to settle: "**Implementation must
verify** that Ollama 0.32 accepts `think: false` for non-thinking models; if it
rejects it with a 400, the field is sent only after `/api/show` reports the
`thinking` capability."

## Decision

The field is sent unconditionally, with no capability probe. Commit `b4ebab7`
records the verification and the conclusion: "`think: false` goes out
unconditionally — Ollama 0.32 accepts it from models that cannot think, so a
capability probe would cost a round trip to learn nothing". `client.py` names
what it was verified against: "verified against llava:7b, which has no thinking
capability at all".

## Alternatives considered

- **Probing `/api/show` for the `thinking` capability first** — the fallback
  spec §2.5 allowed for, conditional on Ollama rejecting the field. It did not
  reject it, so the condition never arose; commit `b4ebab7` records the cost
  that made it unattractive anyway: a round trip per description "to learn
  nothing".

## Consequences

- Every description is one HTTP request, not two.
- The conclusion is pinned to a version and a model: Ollama 0.32, checked with
  `llava:7b`. A future server that rejects the field would reopen spec §2.5's
  conditional.
- A thinking model that emits a `<think>` block despite the flag is still
  handled: spec §2.6 step 1 strips it "belt and braces" (ADR 0006).
- `keep_alive` is treated the opposite way — omitted rather than sent as null.
  Commit `b4ebab7`: "`keep_alive` is omitted rather than sent as null, because
  null overrides the server's own default instead of asking for it."
