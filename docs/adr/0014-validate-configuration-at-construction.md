---
id: 0014
title: Validate configuration where it is written, not at the first request
date: 2026-08-20
status: Accepted
summary: An unusable host, timeout, model tag or word budget raises at construction rather than surfacing later as an unrelated-looking connection error.
---

# 0014. Validate configuration where it is written, not at the first request

## Context

Configuration used to be taken on trust and to fail at the first request, where
the symptom no longer resembles the mistake. Commit `24e3be2` states the change
— "Configuration is now checked where it is written rather than at the first
request" — and `config.py` states the cost of the old behaviour: letting a bad
host through "would surface later as an unrelated-looking connection error, or
as a request sent somewhere unintended".

## Decision

From commit `24e3be2` and the unit-2 errata (2026-08-20), each value is checked
by the object that will use it, at construction:

- **Host** (`normalise_host`, called from `OllamaClient.__init__`):
  credentials, a query string or a fragment — "including an empty `?` or `#`,
  which parse away but leave behind the slash that introduced them" — a scheme
  that is neither `http` nor `https`, an unreadable port, or no hostname at all
  are all `ValueError`.
- **Timeout** (`OllamaClient`): `TypeError` "if it is not a real number",
  `ValueError` "if it is not positive and finite — `inf` is a hang with extra
  steps and `nan` loses every comparison".
- **Model** (`Describer`): must not be blank after stripping (`ValueError`),
  "and a blank `$DESCRIBE_IT_MODEL` counts as unset exactly as a blank
  `$OLLAMA_HOST` does".
- **Word budget** (`Describer`): `max_words` "must be an `int` and not a `bool`"
  (`TypeError`), and at least 1 (`ValueError`).

The credentials message is the one that does not quote the host back, "so a
password cannot reach a log through it" (unit-2 errata).

## Alternatives considered

- **Validating lazily, at the first request** — the previous behaviour,
  replaced by commit `24e3be2` for the reason quoted in Context.
- No other placement was weighed in the sources.

## Consequences

- `Describer` inherits the host and timeout checks by constructing a client;
  the unit-2 errata notes the gap that leaves: "passing `client=` therefore
  skips it along with `host`".
- These are `ValueError` and `TypeError`, not `DescribeItError` (ADR 0003 and
  its addendum), so a caller catching library failures does not catch its own
  mistakes.
- The CLI converts them back into usage errors: its `type` functions cover the
  flags, and `main` catches the `ValueError` that an environment-supplied host
  or model can still raise (unit-3 errata, 2026-08-20).
