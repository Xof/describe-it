---
id: 0005
title: Prepare every image as an RGB JPEG flattened onto white
date: 2026-08-19
status: Accepted
summary: Any PIL mode is normalised in one pure function — alpha flattened onto white, LANCZOS downscale, JPEG quality 90 — and the caller's image is never mutated.
---

# 0005. Prepare every image as an RGB JPEG flattened onto white

## Context

Spec §2.1 puts the conversion burden on the library rather than the caller: the
call "accepts any `PIL.Image.Image` regardless of mode (`RGB`, `RGBA`, `L`,
`P`, `1`, `CMYK`, `I;16`, `LA`, animated first-frame, …). The caller never has
to convert first", and "never mutates the caller's image object".

## Decision

`prepare_image(image, max_size)` is the one pure function that produces the
upload bytes, per spec §2.4:

1. Convert to `RGB`. "Alpha is flattened onto white… `P`/`PA`/`LA`/`1`/`L`/
   `CMYK`/`I`/`F`/`I;16` all go through `Image.convert`. For multi-frame images
   only the current frame is used."
2. "Downscaled with `Image.thumbnail` (LANCZOS) so the longer edge is
   ≤ `max_size`. Never upscaled." `None` disables it (spec §2.2).
3. "Encoded as JPEG, quality 90, then base64 for the JSON payload."

Spec §2.4 also fixes the failures: a zero-area image "raises `ImageError`
before any network activity", an unloadable image is "wrapped in `ImageError`
with the original as `__cause__`", and a non-image argument raises `TypeError`.

## Alternatives considered

- **Flattening onto black** — rejected in spec §2.4 step 1: "not black — most
  web images with transparency are meant to sit on light backgrounds, and black
  halos mislead the model."
- **Requiring the caller to convert first** — rejected by spec §2.1's "The
  caller never has to convert first."
- The encoding (JPEG), the quality (90) and the resampling filter (LANCZOS) are
  stated in spec §2.4 without competing options being weighed.

  > Rationale not recovered from project sources.

## Consequences

- Every mode Pillow can open is describable, and the two failure modes are
  narrow and named (spec §2.4).
- Preparation happens before any socket is opened. `describer.py`: "a caller
  who passes a closed file or a zero-area image finds out immediately instead
  of after a socket, a model load and a timeout."
- Upload size and latency are bounded rather than quality maximised: spec §2.2
  notes "Models resize internally anyway; this bounds upload size and latency."
- JPEG is lossy, so spec §6.1 asks the tests to sample a pixel with "tolerance
  for JPEG" rather than compare exactly.

## Addendum (2026-08-20)

Refinements from the unit-1 review. Commit `9693b3d` describes the failures
that prompted them; the rules themselves are in the unit-1 errata:

- **EXIF orientation** is honoured before any mode conversion, and only when
  the image carries an orientation other than 1; the output carries no EXIF.
  Commit `9693b3d`: "A phone photo is stored in sensor order with its rotation
  in a tag, so portrait pictures were being described sideways."
- **Wide modes** (`I`, `I;16*`, `F`) are rescaled against the image's own
  min/max before the 8-bit conversion, "because `Image.convert("RGB")` clips
  16-bit data to white" (errata). `I;16B`/`I;16L` go through `convert("I")`
  first, "because `getextrema()` and `point()` reject those modes outright";
  `I;16N` has its bytes reinterpreted as the host's byte-order mode, since
  Pillow "routes it through an 8-bit unpacker and clamps every sample to 255".
- **Colour keys**: any image carrying `info["transparency"]` is flattened onto
  white, not only palette images. Commit `9693b3d`: "PNG colour-key
  transparency arrives on RGB and L images too, and was being painted as the
  keyed colour instead of white." A key on a wide-mode image is ignored,
  because "a colour key names a raw sample value, and the wide-mode rescale
  moves every sample" (errata); a key Pillow cannot apply is dropped from a
  copy, so "the caller's image keeps it" (errata, commit `2dde2fb`).
- **Non-finite samples** in `F` images: normalisation is skipped in favour of
  Pillow's clamping conversion only when an extremum is `inf`; a `nan` is
  rescanned, "because Pillow seeds its extrema scan with pixel (0, 0)"
  (errata, commit `2dde2fb`).
- `max_image_size <= 0` raises `ValueError`, and a `max_image_size` that is not
  an `int` — `bool` included — raises `TypeError` (errata).
