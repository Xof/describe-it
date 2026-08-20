---
id: 0003
title: Image in, string out, every failure an exception
date: 2026-08-19
status: Accepted
summary: describe() returns a plain str and raises on every failure, with one exception hierarchy rooted at DescribeItError and TypeError/ValueError reserved for caller mistakes.
---

# 0003. Image in, string out, every failure an exception

## Context

The library does one thing, and the shape of its API decides how much code a
caller writes around it. A result object or a `None` return would put a status
check between every caller and the string they actually wanted, for a call
that either produces alt text or fails.

At the same time a caller does need to distinguish failures: "the server is
not running" and "the model refused" ask for different responses, and a
service doing this at volume wants to retry one and log the other.

## Decision

`describe(image, **options) -> str` returns a non-empty string, and every
failure is an exception. All library failures inherit from `DescribeItError`,
so one `except DescribeItError` catches everything; the tree branches by what
a caller would *do* about the failure (`ImageError`, `OllamaConnectionError`,
`OllamaTimeoutError`, `ModelNotFoundError`, `OllamaResponseError`,
`DescriptionError`, `DescriptionRefusedError`).

Plain `TypeError` and `ValueError` are reserved for the caller's own mistakes
— a non-image argument, `max_words=0`, a blank model tag — because those are
programming errors, not describe-it failures, and Python callers expect the
standard types. `Describer` is the same pipeline with the configuration
hoisted out of the loop.

## Alternatives considered

- **A result object (`Description(text=..., error=...)`)** — every caller
  writes the same unwrap, and the failure that matters most (a refusal) would
  be an attribute nobody checks. Rejected in the specification's opening
  paragraph.
- **`None` on failure** — loses the reason entirely and pushes the diagnosis
  into a log the caller cannot reach.
- **Wrapping the argument-type error too (`ImageError` for a non-image)** —
  rejected because `except DescribeItError` would then swallow what is plainly
  a bug in the calling code.
- **A flat exception list** — the hierarchy costs nothing and lets a caller
  catch `OllamaError` without enumerating its four members.

## Consequences

- A refusal is an exception rather than a plausible-looking string, which is
  the point of `DescriptionRefusedError` (ADR 0006); the text is still
  available on `.response` for a caller who disagrees with the heuristic.
- `OllamaTimeoutError` is a *sibling* of `OllamaConnectionError`, not a
  subclass: a timeout usually means the model is slow (retry, or raise the
  timeout) while a connection error means the server is not there.
- Every wrapped lower-level exception is chained with `raise ... from exc`, so
  the urllib, socket, JSON or Pillow original is always on `__cause__`.
- The API cannot report a partial success — there is no "here is a description
  but it may be wrong" channel. Anything the library doubts, it raises on.
