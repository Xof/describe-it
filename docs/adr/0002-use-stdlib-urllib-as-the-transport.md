---
id: 0002
title: Use stdlib urllib as the transport instead of the ollama client library
date: 2026-08-19
status: Accepted
summary: The library's three JSON POSTs are written against urllib.request, keeping Pillow the only runtime dependency and letting the tests drive a real HTTP server.
---

# 0002. Use stdlib urllib as the transport instead of the ollama client library

## Context

From spec §5: "The library makes three trivial JSON POSTs" — `/api/chat`,
`/api/show`, `/api/pull` (spec §2.3, §2.5) — to a host the caller configures.
The question is whether to make them with the official `ollama` PyPI client or
by hand.

## Decision

The transport is `urllib.request` from the standard library (spec §5), in
`client.py`, the only module in the package that opens a socket. The runtime
dependency list is `pillow>=10` (spec §5).

## Alternatives considered

- **The `ollama` PyPI client** — rejected in spec §5 for two stated reasons:
  "the official client would add `httpx` + `pydantic` as transitive
  dependencies to a package whose only real dependency is Pillow", and "test
  fidelity is also better: tests run against a real in-process `http.server`,
  exercising the actual urllib code path including 404 bodies, malformed JSON,
  and slow responses."
- No other transport library was weighed in the specification, and spec §3
  rules out non-Ollama backends entirely.

## Consequences

- The tests drive a real in-process `http.server` rather than a patched
  `urlopen`. Spec §5 names what that makes real — "404 bodies, malformed JSON,
  and slow responses" — and spec §6.1 adds the streamed NDJSON of `pull` and a
  handler that "sleeps past `timeout`". A body cut short of its declared
  `Content-Length` is the unit-2 errata's addition (2026-08-20).
- urllib's defaults had to be opted out of explicitly: the unit-2 errata
  (2026-08-20) and commit `24e3be2` record proxies being ignored and redirects
  not followed (ADR 0008).
- An async API is a stated non-goal for v0.1 (spec §3), which notes it would be
  "trivial to add later as `adescribe` over `asyncio.to_thread`; not worth an
  `httpx` dependency now."
- Chat is requested with `stream: false` (spec §2.5); `pull` reads Ollama's
  NDJSON stream line by line (spec §6.1).
