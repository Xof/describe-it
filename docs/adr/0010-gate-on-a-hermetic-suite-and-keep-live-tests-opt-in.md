---
id: 0010
title: Gate CI on a hermetic suite at 100% coverage, with live tests opt-in
date: 2026-08-19
status: Accepted
summary: The merge gate is a hermetic unit suite at 100% line and branch coverage; tests against a real model are opt-in, non-blocking, and assert structure only.
---

# 0010. Gate CI on a hermetic suite at 100% coverage, with live tests opt-in

## Context

Most of what this library has to get right is a reaction to something a real
transport produces: a 404 carrying a JSON body, a 200 carrying prose, an
NDJSON stream that stops early, a read that never completes. Those are testable
hermetically. What is *not* testable hermetically is whether a vision model
produces usable alt text — and that answer changes with the model, the
hardware and the temperature.

A suite that gates on model output would be flaky by construction. A suite
that never touches a model would never have run the library end to end.

## Decision

Two suites with different jobs:

- **The gate**: `tests/*.py`, hermetic, run on Python 3.12–3.14 with
  `ruff check`, `ruff format --check`, `mypy --strict` and
  `pytest --cov -m "not integration"` at **100% line and branch coverage**.
  Client and describer tests drive the real urllib code path against an
  in-process `http.server` rather than a patched `urlopen`. Where the
  specification states an invariant (cleanup idempotence, "the caller's image
  is unchanged"), it is property-tested with hypothesis rather than by
  example. `# pragma: no cover` is permitted only on the `__main__` guard.
- **The live tests**: `tests/integration/`, marked `integration`, skipped
  unless `DESCRIBE_IT_INTEGRATION=1` *and* the configured host answers
  `/api/version`. They run in a `workflow_dispatch`-only CI job with
  `continue-on-error: true`, and assert structure only — length, language,
  exception type — never keywords from the image.

## Alternatives considered

- **A coverage target below 100%** — for a library this size, the uncovered
  lines are exactly the error branches that matter, and a percentage target
  with slack invites them to be the slack.
- **Mocking `urlopen` instead of running a server** — a mock has to be taught
  each failure mode and then asserts against its own teaching. The in-process
  server made the malformed-reply and timeout paths real.
- **Gating on the live tests** — makes every merge depend on a model download
  and on non-deterministic output. Rejected in the specification (§6.2).
- **Keyword assertions on the live output ("contains 'circle'")** — measures
  the model, not the library, and would fail on a perfectly good description
  that said "disc".

## Consequences

- The gate is fast and deterministic and can run anywhere, with no model and
  no GPU.
- Nothing in CI proves the library actually produces alt text; that evidence
  comes from a human running the live job or the opt-in tests. The
  specification accepts this explicitly.
- The live assertions are weak enough to pass with a poor model, which is the
  point — but a model that ignores an option entirely will still fail them,
  and that failure is information about the model rather than about the code.
- 100% coverage means every new branch arrives with a test, including the
  argparse `type` functions in the CLI.

## Addendum (2026-08-20)

First live runs — four of them, on Ollama 0.32.13 with `llava:7b` (see ADR
0004's addendum for why not the default model). The missing-model test and the
word-budget *ordering* test passed in all four. The first test's
≤ `max_words + 10` assertion failed once in four (78 words against a budget of
30). The French test failed in all four: `llava:7b` answers in English whatever
the prompt asks for. Both failures are model behaviour, not library behaviour,
and are exactly the reason this job does not gate a merge. The assertions were
left at the strength the specification states.
