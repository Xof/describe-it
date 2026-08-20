---
id: 0002
title: Use stdlib urllib as the transport instead of the ollama client library
date: 2026-08-19
status: Accepted
summary: The three JSON POSTs the library makes are written against urllib.request, keeping Pillow the only runtime dependency and letting the tests drive a real HTTP server.
---

# 0002. Use stdlib urllib as the transport instead of the ollama client library

## Context

The library needs three endpoints: `POST /api/chat` for a description,
`POST /api/show` to ask whether a model is present, `POST /api/pull` to fetch
one. All three are small JSON POSTs to a host the caller configures.

The official `ollama` PyPI client would supply those, but it brings `httpx`
and `pydantic` with it — a transitive tree far larger than the library it
would be serving, whose only real dependency is Pillow. The specification (§5)
weighed that against writing the requests by hand.

## Decision

The transport is `urllib.request` from the standard library, in `client.py`,
which is the only module in the package that opens a socket. The runtime
dependency list stays `pillow>=10`.

## Alternatives considered

- **The `ollama` PyPI client** — the obvious choice, rejected on dependency
  weight: httpx and pydantic to send three dictionaries. It would also hide
  the failure modes the library has to classify (a 404 whose body names a
  model, a 200 carrying prose, an NDJSON stream that stops early) behind its
  own exception types, which would have to be mapped anyway.
- **`requests` or `httpx` directly** — a smaller version of the same trade: a
  runtime dependency bought with convenience the library barely uses. `urllib`
  needed roughly one extra screen of code, most of it error classification
  that would have been written either way.

## Consequences

- The tests run against a real in-process `http.server` rather than a patched
  `urlopen`, so 404 bodies, streamed NDJSON, replies cut short of their
  declared `Content-Length` and socket timeouts are real rather than taught to
  a mock. This is the fidelity argument that made the choice comfortable.
- urllib's defaults had to be opted out of explicitly — proxies and redirects
  (ADR 0008) — which a higher-level client might have handled differently and
  invisibly.
- No async support without adding one: an async API is a stated non-goal for
  v0.1, and would be `asyncio.to_thread` over this code rather than a rewrite.
- Streaming responses are available (`pull` reads NDJSON line by line) but
  chat is requested with `stream: false`, since a caption arrives in one piece.
