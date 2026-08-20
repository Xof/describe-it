---
id: 0009
title: Never pull a model implicitly
date: 2026-08-19
status: Accepted
summary: A describe call against a missing model raises ModelNotFoundError naming the ollama pull command; downloading happens only in an explicit ensure_model() call.
---

# 0009. Never pull a model implicitly

## Context

Ollama can fetch a model that is not present, so a library could do it on the
first call. Spec §2.3 records what that would cost: `ensure_model()` is
"Blocking; may download gigabytes."

Some callers do want it — spec §2.3 shows `ensure_model()` being called before
a batch loop.

## Decision

From spec §2.3: `ensure_model()` issues "`POST /api/pull` for the configured
model if `/api/show` reports it missing", is "explicit, opt-in", and is "Never
called implicitly — a describe call on a missing model raises
`ModelNotFoundError` whose message says `ollama pull <model>`".

`check()` is the read-only counterpart: it "raises `OllamaConnectionError` if
the server is unreachable or `ModelNotFoundError` if the model is absent;
returns silently otherwise. For startup health checks" (spec §2.3).

## Alternatives considered

- **Pulling on first use** — spec §3 lists "Automatic model pulling inside
  `describe()`" as a non-goal. The only cost recorded against it is spec §2.3's
  "may download gigabytes".

  > No further rationale recovered from project sources.

- No other approach to a missing model is weighed in the sources.

## Consequences

- The first run on a fresh machine fails by design, with a message naming the
  command to run (spec §2.3, §2.7).
- `ensure_model()` blocks for the length of the download and reports no
  progress; spec §2.3 describes only the blocking behaviour, and no progress
  callback is specified.
- A model that does not exist upstream is not a `ModelNotFoundError`. The
  unit-2 errata (2026-08-20): "`/api/pull` answers `200` and then an NDJSON
  `{"error": ...}` line for a model that does not exist upstream;
  `ensure_model()` surfaces that as `OllamaResponseError`, not
  `ModelNotFoundError` (which is reserved for 'not present on this server')."
