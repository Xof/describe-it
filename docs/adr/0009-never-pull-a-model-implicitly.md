---
id: 0009
title: Never pull a model implicitly
date: 2026-08-19
status: Accepted
summary: A describe call against a missing model raises ModelNotFoundError naming the `ollama pull` command; downloading is only ever done by an explicit ensure_model() call.
---

# 0009. Never pull a model implicitly

## Context

Ollama will happily fetch a model that is not present, and a library could do
that transparently on the first call. The models in question are gigabytes:
`qwen3.5:4b` is roughly 3 GB. A transparent download turns a call the caller
expected to take a second into one that takes several minutes, on a connection
that may be metered, in a process that may be a web request handler.

Some callers do want it — a batch job on a fresh machine, or a container that
warms itself at start-up.

## Decision

`describe()` never pulls. A missing model raises `ModelNotFoundError`, whose
message names the model and the exact remedy: `ollama pull <model>`.

`Describer.ensure_model()` is the explicit, opt-in download: it asks
`/api/show` and pulls only if the model is absent. `Describer.check()` is the
read-only counterpart for a start-up health check — it raises
`OllamaConnectionError` or `ModelNotFoundError` and never downloads anything.

## Alternatives considered

- **Pulling on first use** — listed as a non-goal in the specification (§3).
  It hides a multi-minute, multi-gigabyte operation inside a call whose
  documented cost is one HTTP request.
- **A `pull_if_missing=True` option on `describe()`** — the same surprise with
  an opt-in switch, but it would have to be threaded through every call site,
  and `ensure_model()` already expresses it once per describer.
- **Raising a generic `OllamaResponseError` for a 404** — loses the remedy.
  The 404 body names the model; turning that into an actionable message is the
  cheapest useful thing the library can do at that point.

## Consequences

- The first run on a fresh machine fails, by design, with a message that says
  what to type. The README leads with the pull command for the same reason.
- `ensure_model()` blocks for as long as the download takes and streams
  Ollama's NDJSON progress without reporting it: there is no progress callback
  in v0.1.
- A model that does not exist upstream is reported by `/api/pull` as a `200`
  followed by an NDJSON `{"error": ...}` line. `ensure_model()` surfaces that
  as `OllamaResponseError`, not `ModelNotFoundError`, which is reserved for
  "not present on this server".
