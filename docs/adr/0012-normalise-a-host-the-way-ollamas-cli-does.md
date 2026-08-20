---
id: 0012
title: Normalise a host the way Ollama's own CLI does
date: 2026-08-20
status: Accepted
summary: A scheme-less host gets http:// and port 11434 as Ollama's OLLAMA_HOST parsing does, while a host written with a scheme is used exactly as written.
---

# 0012. Normalise a host the way Ollama's own CLI does

## Context

Spec §2.2 requires that "A bare `host:port` (as Ollama's own CLI accepts) is
normalised to `http://host:port`", and makes `$OLLAMA_HOST` — the variable
Ollama's own CLI reads — the source of the default.

The first implementation got the port rule wrong in both directions. Commit
`d8cf761`: "Ollama's `envconfig.Host()` fills in 11434 when OLLAMA_HOST names
no port, but that applies to the bare host:port form the variable is written in
— a host given as a URL is a URL, and one without a port already means its
scheme's default."

## Decision

From commit `d8cf761` and the unit-2 errata (2026-08-20):

- A scheme-less host gets `http://` and, when it names no port, 11434 —
  "matching Ollama's `OLLAMA_HOST` parsing".
- A host written with a scheme is used as written, "so
  `http://ollama.example.com` keeps meaning port 80 rather than gaining a port
  of ours". An explicit port is always kept, "and so is a path (an Ollama
  mounted under a prefix)".
- Trailing slashes are removed. `config.py`: "Trailing slashes go, so that
  appending `/api/chat` never produces a doubled separator."

## Alternatives considered

- **Defaulting port 11434 for hosts written with a scheme too** — what the
  first implementation did, corrected by commit `d8cf761` for the reason quoted
  in Context.
- No other host syntax was weighed: spec §2.2's requirement is to accept what
  Ollama's own CLI accepts.

## Consequences

- A machine already configured for `ollama run` needs no describe-it-specific
  configuration; `config.py` records that "Ollama's own CLI reads OLLAMA_HOST,
  so a machine that is already configured for `ollama run` needs no
  describe-it-specific configuration to match it".
- `https://` survives normalisation. `config.py` gives the reason: "`https://`
  is preserved because a remote Ollama behind a reverse proxy is a real
  deployment." That, with the preserved path, is what keeps "local" in ADR 0001
  a default rather than a guarantee.
- The transport reads no ambient configuration of its own: the unit-2 errata
  fixes `OllamaClient(host=...)` at the static default, with `$OLLAMA_HOST`
  resolved by `Describer` and passed in explicitly.
- The CLI validates `--host` through the same function before argparse accepts
  it (unit-3 errata, 2026-08-20).
