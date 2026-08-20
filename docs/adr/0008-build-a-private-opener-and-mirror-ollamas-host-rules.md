---
id: 0008
title: Build a private urllib opener that ignores proxies and does not follow redirects
date: 2026-08-20
status: Accepted
summary: Each client builds its own OpenerDirector with an empty ProxyHandler and no redirect handler, and host strings are normalised the way Ollama's own CLI reads $OLLAMA_HOST.
---

# 0008. Build a private urllib opener that ignores proxies and does not follow redirects

## Context

Two of urllib's module-level defaults are actively wrong for a client whose
server is normally on localhost. Both were found during unit-2 review.

- **Proxies.** `$http_proxy` and friends are set machine-wide in many
  corporate environments, and urllib's proxy support exempts nothing from them
  — not even loopback. describe-it was routing `http://localhost:11434`
  through a proxy that has never heard of it, where the `ollama` CLI itself
  connects directly.
- **Redirects.** urllib follows a 30x by reissuing the request, and it rewrites
  a redirected POST into a body-less GET. The image would silently not be sent
  and the model would answer about nothing.

Host configuration had its own problem: `$OLLAMA_HOST` is written the way
Ollama's CLI accepts it, which includes bare `localhost:11434`, and `urlsplit`
reads the `localhost` of that as a URL scheme.

## Decision

Every `OllamaClient` builds its own `OpenerDirector` with an empty
`ProxyHandler` and **no** `HTTPRedirectHandler`, instead of calling the
module-level `urlopen`. A 3xx is reported as `OllamaResponseError` naming the
`Location` it points at; a `Location` on any other status is ignored, since
error pages carry one too.

`normalise_host` mirrors Ollama's `envconfig.Host()`: a scheme-less host gets
`http://`, and gets port 11434 when it names no port. A host written *with* a
scheme is a URL and is used as written, so `http://ollama.example.com` keeps
meaning port 80. Explicit ports and paths are kept. Credentials, a query
string or a fragment (including an empty `?` or `#`, which parse away but
leave the slash that introduced them), a non-HTTP scheme, or an unreadable
port are `ValueError` at construction. The credentials message does not quote
the host back, so a password cannot reach a log through it.

## Alternatives considered

- **Using `urlopen` and documenting the proxy behaviour** — puts the burden on
  a user who does not know their machine has a proxy, to explain a symptom
  (a connection error, or a stranger's error page) that gives no hint of one.
- **Honouring `$no_proxy` instead of ignoring proxies entirely** — the traffic
  this library sends is to a service the user is running; there is no case
  where routing it through a proxy is what they meant. A remote Ollama behind
  a corporate proxy is a real deployment, but the proxy then belongs in the
  host, not in an ambient variable.
- **Following redirects with a handler that preserves the method** — possible,
  but a redirecting Ollama means a misconfigured host, and naming the target
  is a better diagnosis than silently following it.
- **Defaulting port 11434 for hosts written with a scheme too** — what the
  first implementation did. It made `http://ollama.example.com` mean port
  11434, which no other tool would agree with.
- **Validating the host lazily, at the first request** — rejected: a
  configuration mistake should fail where it was written.

## Consequences

- The client cannot reach an Ollama that is only reachable through a proxy.
  That is the intended trade; such a deployment should name the proxy as the
  host.
- Every `OllamaClient` carries its own opener, so a caller holding several
  clients pays for several. They are cheap.
- `OllamaResponseError.status_code` is `None` only when `open()` never
  returned a response at all — the reply was not HTTP, or `http.client`
  rejected it before handing one back. Once a response is in hand, its status
  is reported, including for an unparseable 2xx body.
- `OllamaClient(host=...)` never reads the environment: `$OLLAMA_HOST` and
  `$DESCRIBE_IT_MODEL` are resolved by `Describer` and passed in explicitly,
  so the transport has no ambient configuration of its own.
