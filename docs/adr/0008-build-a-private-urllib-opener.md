---
id: 0008
title: Build a private urllib opener that ignores proxies and does not follow redirects
date: 2026-08-20
status: Accepted
summary: Each client builds its own OpenerDirector with an empty ProxyHandler and no redirect handler, because both urllib defaults are wrong for a service on localhost.
---

# 0008. Build a private urllib opener that ignores proxies and does not follow redirects

## Context

Found during unit-2 review and recorded in the unit-2 errata (2026-08-20) and
commit `24e3be2`. Two of urllib's module-level defaults are wrong for this
client:

- **Proxies.** The errata: "`$http_proxy` and friends are ignored… because
  Ollama is a local service and urllib's proxy support exempts nothing — not
  even loopback — so a machine-wide corporate proxy would otherwise capture
  traffic that `ollama` itself sends direct." Commit `24e3be2` adds that
  describe-it "was routing `http://localhost:11434` through a proxy that has
  never heard of it".
- **Redirects.** The errata: "urllib follows a 30x by rewriting a redirected
  POST into a body-less GET — the image would silently not be sent."

## Decision

Every `OllamaClient` builds its own `OpenerDirector` with an empty
`ProxyHandler` and no `HTTPRedirectHandler` (unit-2 errata; commit `24e3be2`).

A 3xx is reported as `OllamaResponseError` naming the `Location` it points at,
and "a `Location` on any other status is ignored, since error pages carry one
too" (unit-2 errata; commit `d8cf761`: "calling a 500 a redirect would send its
reader after it").

## Alternatives considered

No other option is weighed in the specification, the errata or the commit
history: both defaults are described there as defects to be removed rather than
as choices between candidates.

> Rationale not recovered from project sources.

## Consequences

- An Ollama reachable only through a proxy cannot be reached. The unit-2
  errata frames the proxy exemption as the point — traffic `ollama` itself
  sends direct should go direct.
- A redirecting host is a dead end rather than a silent success. `client.py`'s
  comment: "it is nearly always a host that should have been written with the
  other scheme or another port."
- Each client carries its own opener, one per instance (unit-2 errata).
- `OllamaResponseError.status_code` is `None` only when `open()` never returned
  a response — "the reply was not HTTP at all, or `http.client` rejected it
  before handing one back" — and an unparseable 2xx body keeps its status
  (unit-2 errata).
