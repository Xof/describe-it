---
id: 0012
title: Normalise a host the way Ollama's own CLI does, and validate it at construction
date: 2026-08-20
status: Accepted
summary: A scheme-less host gets http:// and port 11434 as Ollama's OLLAMA_HOST parsing does, a host with a scheme is used as written, and an unusable host is a ValueError where it was written.
---

# 0012. Normalise a host the way Ollama's own CLI does, and validate it at construction

## Context

Spec §2.2 requires that "A bare `host:port` (as Ollama's own CLI accepts) is
normalised to `http://host:port`", and spec §2.2 makes `$OLLAMA_HOST` — the
variable Ollama's own CLI reads — the source of the default.

The first implementation got the port rule wrong in both directions. Commit
`d8cf761`: "Ollama's `envconfig.Host()` fills in 11434 when OLLAMA_HOST names
no port, but that applies to the bare host:port form the variable is written in
— a host given as a URL is a URL, and one without a port already means its
scheme's default."

It also let malformed hosts through. Commit `d8cf761`: an empty query or
fragment "parses away to nothing, so the check on the parsed parts let it
through — and the slash that introduced it stayed, leaving a base URL that
posts to `//api/chat`."

## Decision

From the unit-2 errata (2026-08-20) and commit `d8cf761`:

- A scheme-less host gets `http://` and, when it names no port, 11434 —
  "matching Ollama's `OLLAMA_HOST` parsing".
- A host written with a scheme is used as written, "so
  `http://ollama.example.com` keeps meaning port 80 rather than gaining a port
  of ours". An explicit port is kept, and so is a path, "an Ollama mounted
  under a prefix".
- Trailing slashes are removed so that appending `/api/chat` cannot double a
  separator.
- Userinfo, a query string or a fragment — "including an empty `?` or `#`,
  which parse away but leave behind the slash that introduced them" — a scheme
  that is not `http`/`https`, and an unreadable port are all `ValueError`. "The
  credentials message does not quote the host back, so a password cannot reach
  a log through it."

Validation happens at construction, not at the first request. Commit
`24e3be2`: "Configuration is now checked where it is written rather than at the
first request."

## Alternatives considered

- **Defaulting port 11434 for hosts written with a scheme too** — what the
  first implementation did, corrected by commit `d8cf761` for the reason quoted
  in Context.
- **Checking the query and fragment on the parsed parts** — also the first
  implementation, and also corrected by `d8cf761`: an empty one parses away
  while its slash remains.
- No other host syntax was weighed; the specification's requirement is to match
  what Ollama's CLI accepts.

## Consequences

- A machine already configured for `ollama run` needs no describe-it-specific
  configuration: `config.py` records that "Ollama's own CLI reads OLLAMA_HOST".
- `https://` is preserved, so a remote Ollama behind a reverse proxy is
  addressable (unit-2 errata) — the deployment that keeps ADR 0001's "local"
  a default rather than a guarantee.
- A misconfiguration fails where it was written rather than "later as an
  unrelated-looking connection error, or as a request sent somewhere
  unintended" (`config.py`).
- The transport reads no ambient configuration of its own: the unit-2 errata
  fixes `OllamaClient(host=...)` at the static default, with `$OLLAMA_HOST`
  resolved by `Describer` and passed in explicitly.
- The CLI validates `--host` through the same function before argparse accepts
  it, and turns the `ValueError` from an environment-supplied host into the
  same usage error (unit-3 errata, 2026-08-20).
