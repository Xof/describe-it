---
id: 0001
title: Use a local Ollama vision model rather than a hosted description API
date: 2026-08-19
status: Accepted
summary: Descriptions come from a vision model served by a local Ollama, because the workload is high-volume and includes images hosted services refuse.
---

# 0001. Use a local Ollama vision model rather than a hosted description API

## Context

The library exists to turn images into alt text at volume. Two facts about
that workload decide the backend:

- Alt-text generation at volume is cheap locally and expensive through a
  commercial API, where every image is a billed request.
- The images may be NSFW. Commercial vision services refuse or penalise that
  content, so a hosted backend would fail on exactly the images the caller
  most needs described.

A local model has no policy layer beyond whatever is baked into its weights,
and the machine running the batch is already paid for.

## Decision

Descriptions come from a vision model served by a local
[Ollama](https://ollama.com), over its native HTTP API. The library speaks to
one server, configured by host, and never contacts a third party.

## Alternatives considered

- **A commercial hosted vision API** — loses on both counts above: per-image
  cost at volume, and a content policy that rejects part of the corpus.
  Neither is a problem the library could work around.
- **Ollama's OpenAI-compatible endpoint** — would allow a hosted service as a
  drop-in later, but it is a lossy translation of Ollama's own API (no
  `keep_alive`, no `think`) and adds a second shape of request to support for
  a backend that has been ruled out anyway. Recorded as a non-goal in the
  specification (§3).
- **llama.cpp directly, or a Python inference stack** — would drag a model
  runtime, GPU handling and model file management into a library whose only
  dependency is Pillow. Ollama already solves model storage, loading and
  unloading, and is a service the user's machine can share with other tools.

## Consequences

- The caller must install and run Ollama and pull a vision model. There is no
  zero-install path, and the README has to say so first.
- Description quality is bounded by whatever small model fits on the caller's
  machine. Output is not deterministic enough to assert on beyond structure,
  which is why the live tests are opt-in and non-blocking (ADR 0010).
- The whole library is three JSON POSTs to localhost, which is what makes the
  stdlib transport in ADR 0002 sufficient.
- Nothing leaves the machine, so an image the caller cannot legally or safely
  upload is still describable.
