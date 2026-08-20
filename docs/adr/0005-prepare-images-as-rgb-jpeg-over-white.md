---
id: 0005
title: Prepare every image as an RGB JPEG flattened onto white
date: 2026-08-20
status: Accepted
summary: Any PIL mode is normalised in one pure function — EXIF orientation, alpha and colour keys flattened onto white, LANCZOS downscale, JPEG quality 90 — and the caller's image is never mutated.
---

# 0005. Prepare every image as an RGB JPEG flattened onto white

## Context

Spec §2.1 puts the conversion burden on the library: the call "accepts any
`PIL.Image.Image` regardless of mode (`RGB`, `RGBA`, `L`, `P`, `1`, `CMYK`,
`I;16`, `LA`, animated first-frame, …). The caller never has to convert
first", and "never mutates the caller's image object".

The steps below were specified on 2026-08-19 (spec §2.4). Three of them were
corrected on 2026-08-20 during unit-1 review, after failing against real files;
those are cited individually.

## Decision

`prepare_image(image, max_size)` is the single pure function that produces the
upload bytes:

1. **EXIF orientation** is honoured with `ImageOps.exif_transpose`, and only
   when the image carries an orientation other than 1; the output carries no
   EXIF (unit-1 errata, 2026-08-20).
2. **Convert to `RGB`**, compositing alpha onto **white** (spec §2.4 step 1).
   Any image carrying `info["transparency"]` is flattened the same way, except
   in the wide modes (unit-1 errata).
3. **Wide modes** (`I`, `I;16*`, `F`) are rescaled against the image's own
   min/max before the 8-bit conversion (unit-1 errata).
4. **Downscale** with `Image.thumbnail` and LANCZOS so the longer edge is
   ≤ `max_size`, never upscaling; `None` disables (spec §2.4 step 2, §2.2).
5. **Encode** as JPEG at quality 90, then base64 for the JSON body (spec §2.4
   step 3).

## Alternatives considered

- **Flattening onto black** — rejected in spec §2.4 step 1: "not black — most
  web images with transparency are meant to sit on light backgrounds, and
  black halos mislead the model".
- **A plain `convert("RGB")` for the wide modes** — what the first
  implementation did. The unit-1 errata records why it was replaced: "because
  `Image.convert("RGB")` clips 16-bit data to white".
- **Ignoring EXIF orientation** — the first implementation again. Commit
  `9693b3d`: "A phone photo is stored in sensor order with its rotation in a
  tag, so portrait pictures were being described sideways."
- **Flattening only palette images** — commit `9693b3d`: "PNG colour-key
  transparency arrives on RGB and L images too, and was being painted as the
  keyed colour instead of white."
- The encoding (JPEG) and the quality (90) are stated in spec §2.4 without
  alternatives being weighed.

  > Rationale not recovered from project sources.

## Consequences

- Every mode Pillow can open is describable; the failures are narrow, and spec
  §2.4 names them: a zero-area image and an image that cannot be loaded, both
  `ImageError`, with the Pillow original on `__cause__`.
- Preparation happens before any socket is opened. `describer.py` records the
  reason in its module docstring: "a caller who passes a closed file or a
  zero-area image finds out immediately instead of after a socket, a model load
  and a timeout."
- A colour key on a wide-mode image is ignored and the image is described
  opaque, because "a colour key names a raw sample value, and the wide-mode
  rescale moves every sample" (unit-1 errata). A key Pillow cannot apply is
  dropped from a copy, leaving the caller's image untouched (commit `2dde2fb`).
- `I;16N` keeps its precision only by having its bytes reinterpreted as the
  explicit byte-order mode the host uses, because "Pillow routes it through an
  8-bit unpacker and clamps every sample to 255" (unit-1 errata).
- `F` images with an `inf` extremum fall back to Pillow's clamping conversion;
  a `nan` at pixel (0, 0) forces a rescan, "because Pillow seeds its extrema
  scan with pixel (0, 0)" (unit-1 errata, commit `2dde2fb`).
- JPEG is lossy, so spec §6.1 asks the tests to sample pixels "with tolerance
  for JPEG" rather than compare exactly.
