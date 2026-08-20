---
id: 0003
title: Image in, string out, every failure an exception
date: 2026-08-19
status: Accepted
summary: describe() returns a plain str and raises on every failure, under one hierarchy rooted at DescribeItError, with TypeError kept for a wrong-type argument.
---

# 0003. Image in, string out, every failure an exception

## Context

From spec §1: "The API contract is deliberately minimal: **PIL image in,
description string out, every failure is an exception.** No result objects, no
status codes, no `None` returns."

A caller still has to tell failures apart, and the hierarchy is shaped for
that. `exceptions.py` states the rule: "The tree is shaped by what a caller
would *do* about the failure, not by where the error happened to arise."

## Decision

`describe(image, **options) -> str` returns a non-empty string (spec §2.1), and
every failure is an exception. All library failures inherit from
`DescribeItError` "so `except DescribeItError` catches everything the library
itself raises" (spec §2.7). The tree is the one drawn in spec §2.7:
`ImageError`, `OllamaError` with `OllamaConnectionError`, `OllamaTimeoutError`,
`ModelNotFoundError` and `OllamaResponseError` beneath it, and
`DescriptionError` with `DescriptionRefusedError` beneath it.

A non-`PIL.Image.Image` argument raises plain `TypeError`, which spec §2.4
calls "the standard Python contract for a wrong-type argument", and spec §2.7
calls "the one deliberate exception to that rule".

`Describer` is the same pipeline with the configuration hoisted out of the
loop; module-level `describe()` is "sugar for
`Describer(**opts).describe(image, context=..., prompt=...)`" (spec §2.3).

## Alternatives considered

- **Result objects, status codes, `None` returns** — all three are rejected by
  name in spec §1.

  > Rationale not recovered from project sources: §1 states the rejection
  > without arguing it.

- **Making `OllamaTimeoutError` a subclass of `OllamaConnectionError`** —
  rejected in spec §2.7: a timeout "usually means 'model is slow', not 'server
  is down'", so the two are siblings.
- No other API shape was weighed in the sources.

## Consequences

- A refusal is an exception rather than a plausible-looking string (spec §2.6
  step 5, ADR 0013); the cleaned text stays available on
  `DescriptionRefusedError.response`.
- Every wrapped lower-level exception is chained with `raise ... from exc`, so
  the urllib, socket, JSON or Pillow original is on `__cause__` (spec §2.7).
- There is no channel for a partial success: anything the library will not
  vouch for, it raises on (spec §1).

## Addendum (2026-08-20)

The unit-1 and unit-2 errata extend the "caller's mistakes keep the standard
types" rule from `TypeError` to `ValueError`, at construction rather than at
first use: `max_words < 1`, `timeout <= 0`, `host=""`, a blank `model` and
`max_image_size <= 0` all raise `ValueError`, as does `prompt=""`; a
`max_words` or `max_image_size` that is not an `int` (or is a `bool`) raises
`TypeError`. None of these are `DescribeItError`s.
