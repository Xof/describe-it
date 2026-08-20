---
id: 0010
title: Gate CI on a hermetic suite at 100% coverage, with live tests opt-in
date: 2026-08-19
status: Accepted
summary: The merge gate is a hermetic unit suite at 100% line and branch coverage; tests against a real model are opt-in, non-blocking, and assert structure only.
---

# 0010. Gate CI on a hermetic suite at 100% coverage, with live tests opt-in

## Context

Spec §6 splits verification in two because the two halves have different
natures. Most of the library's behaviour is a reaction to something a real
transport produces, which spec §6.1 enumerates: "404 bodies, malformed JSON,
and slow responses" (spec §5), a `pull` stream that carries an error line, a
handler that "sleeps past `timeout`".

Model output is not like that. Spec §6.2 states the constraint directly:
"Structural assertions only — no keyword matching, because small models are not
reliable enough to gate on."

## Decision

Two suites, per spec §6:

- **The gate** (spec §6.1, §6.3, §5): the hermetic unit suite, plus
  `ruff check`, `ruff format --check` and `mypy --strict`, run on 3.12/3.13/
  3.14 (spec §5). Spec §5 sets the coverage rule: "Coverage gate 100% on the
  unit suite, with `# pragma: no cover` permitted only for the
  integration-skip branch and `if __name__ == '__main__'`." Client and
  describer tests run "against an in-process `http.server` fixture that records
  requests and returns scripted responses" (spec §6.1).
- **The live tests** (spec §6.2): `tests/integration/test_live.py`, "skipped
  unless `DESCRIBE_IT_INTEGRATION=1` and the configured Ollama host answers
  `/api/version`", in a `workflow_dispatch`-only, `continue-on-error` CI job
  (spec §7 unit 1).

Where the specification states an invariant, it is property-tested with
hypothesis rather than by example. Three such properties exist in the suite
today: the prepared image fits its bound and keeps its aspect ratio
(`tests/test_image.py`), `clean_response` is idempotent
(`tests/test_prompt.py`, required by spec §6.1), and `normalise_host` always
produces a usable base URL (`tests/test_client.py`). Other stated invariants —
"the caller's image object is unchanged" among them — are covered by
enumerated cases rather than by generated ones.

## Alternatives considered

- **Mocking `urlopen` instead of running a server** — rejected in spec §5 on
  fidelity: running a real server exercises "the actual urllib code path
  including 404 bodies, malformed JSON, and slow responses".
- **Gating on the live tests** — rejected by spec §6.2, which labels them
  "opt-in; not a CI gate".
- **Keyword assertions on live output** — rejected by spec §6.2: small models
  "are not reliable enough to gate on".
- **A coverage target below 100%** — spec §5 sets 100% without weighing a
  lower figure.

  > Rationale not recovered from project sources.

## Consequences

- The gate is deterministic and needs no model, no GPU and no network.
- Nothing in CI demonstrates that the library produces usable alt text; that
  evidence comes only from a human running the live job. Spec §6.2 accepts
  this by making them non-blocking.
- The live assertions are weak by construction, so a model that ignores an
  option outright still fails them — which is information about the model.
- 100% branch coverage means every new branch arrives with a test, including
  the CLI's argparse `type` functions (unit-3 errata, 2026-08-20).

## Addendum (2026-08-20)

The live tests were run seven times on 2026-08-20 against this machine's Ollama
0.32.13 with `llava:7b` (see ADR 0004's addendum for why not the default
model). Observed:

- the missing-model test and the word-budget **ordering** test passed in all
  seven runs;
- the first test's `≤ max_words + 10` assertion failed in two of the seven,
  with replies of 56 and 78 words against a budget of 30;
- the French test failed in all seven — `llava:7b` answered in English every
  time, with no French function word in any reply.

Both failures are model behaviour rather than library behaviour: the unit
suite asserts that "at most 8 words" and "write in French" really are in the
request body. They are also exactly why spec §6.2 keeps this job off the merge
gate. The assertions were left at the strength spec §6.2 states.
