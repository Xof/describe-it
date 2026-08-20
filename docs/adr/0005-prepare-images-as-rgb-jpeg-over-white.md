---
id: 0005
title: Prepare every image as an RGB JPEG flattened onto white
date: 2026-08-20
status: Accepted
summary: Any PIL mode is accepted and normalised in one place — EXIF orientation, alpha and colour keys flattened onto white, LANCZOS downscale to 1024, JPEG quality 90 — and the caller's image is never mutated.
---

# 0005. Prepare every image as an RGB JPEG flattened onto white

## Context

Callers hand the library whatever Pillow opened: `RGBA` PNGs, palette GIFs,
`CMYK` TIFFs, 16-bit scientific images, phone photographs stored sideways with
their rotation in an EXIF tag. Ollama wants base64-encoded bytes in a format
the model understands. Something has to normalise, and the specification (§2.4)
put that burden on the library rather than the caller: "the caller never has to
convert first."

The details below were specified on 2026-08-19; the EXIF, wide-mode and
transparency-key rules were settled during unit-1 review on 2026-08-20, after
they failed against real files.

## Decision

`prepare_image(image, max_size)` is the single pure function that turns any
image into upload bytes:

1. EXIF orientation is honoured (`ImageOps.exif_transpose`), and only when the
   image carries an orientation other than 1. The output carries no EXIF.
2. The image is converted to `RGB`. Alpha and any `info["transparency"]`
   colour key are flattened onto **white**, not black. Wide modes (`I`,
   `I;16*`, `F`) are rescaled against the image's own min/max before the 8-bit
   conversion.
3. `Image.thumbnail` with LANCZOS brings the longer edge to `max_size`
   (default 1024). Never upscaled; `None` disables.
4. JPEG, quality 90, then base64 for the JSON body.

The caller's image object is never mutated: every step that would change
something works on a copy.

## Alternatives considered

- **Flattening onto black** — the natural default for a straight `convert`,
  and wrong for this corpus: most web images with transparency are meant to
  sit on light backgrounds, and a black halo misleads the model about what it
  is looking at.
- **Requiring `RGB` input** — pushes a fiddly, easy-to-get-wrong conversion
  onto every caller, and would make the common `describe(Image.open(path))`
  call fail on ordinary PNGs.
- **PNG instead of JPEG** — lossless, and several times the bytes for a
  photograph. The model resizes internally anyway; upload size and latency are
  what the encoding controls.
- **Plain `convert("RGB")` for the wide modes** — what the first
  implementation did. Pillow clips 16-bit data to white, so a 16-bit TIFF was
  described as a blank page.
- **Leaving EXIF orientation alone** — portrait phone photographs were being
  described sideways, which is a wrong description rather than a missing one.

## Consequences

- Every mode Pillow can open is describable, and the failure modes are narrow:
  a zero-area image and an image that cannot be loaded, both `ImageError`.
- Preparation happens *before* any socket is opened, so an unusable image
  costs the caller a millisecond rather than a cold model load and a timeout.
- A colour key on a wide-mode image is ignored, and a key Pillow cannot apply
  is dropped (from a copy): the image is then described opaque. Both are
  documented landmines rather than errors.
- `I;16N` has no conversion path in Pillow that keeps its precision, so its
  bytes are reinterpreted as the explicit byte-order mode the host uses. That
  trick is correct on a machine whose byte order matches the file's, which is
  the case that produced the mode in the first place.
- JPEG is lossy, so pixel-exact assertions about the uploaded image are not
  possible; the tests sample with a tolerance.
