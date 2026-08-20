"""Image preparation: any PIL image in, one small RGB JPEG out.

The caller is promised that describe-it accepts whatever `PIL.Image.Image` they
have, so this module is where PIL's awkward corners are dealt with once: EXIF
orientation, palette and colour-key transparency, premultiplied alpha, and the
wide (16/32-bit integer and float) modes whose samples do not fit in a byte. It
is pure — no network, no filesystem, no mutation of the caller's image.
"""

import io
import math
from typing import cast

from PIL import Image, ImageOps

from describe_it.exceptions import ImageError

# Quality 90 is the usual "visually lossless enough" point for photographic
# JPEG. JPEG rather than PNG because these images go out base64-encoded inside
# a JSON body: a 1024px photo is a few hundred KB as JPEG and several MB as
# PNG, and the model sees no benefit from the extra fidelity.
_JPEG_QUALITY = 90

# Alpha is composited onto white, not black. Transparent web imagery is drawn
# for light backgrounds; on black, every soft edge gains a dark halo and the
# model dutifully describes the halo.
_BACKGROUND = (255, 255, 255, 255)

# Modes with an alpha channel, including the premultiplied variants. These are
# routed through RGBA so the alpha is composited rather than discarded.
_ALPHA_MODES = frozenset({"LA", "La", "PA", "RGBA", "RGBa"})

# Modes whose samples are wider than 8 bits. `convert("RGB")` clips these to
# 0..255 instead of scaling them, which turns a 16-bit scan into a mostly-white
# rectangle, so they get an explicit rescale first.
_WIDE_MODES = frozenset({"F", "I", "I;16", "I;16B", "I;16L", "I;16N"})

# The byte-order-tagged 16-bit rawmodes. PIL exposes them as image modes but
# implements almost nothing for them — `getextrema()` and `point()` both raise
# "image has wrong mode" — so they are normalised to `I` before any arithmetic.
# I;16L and I;16B survive that conversion with all 16 bits; I;16N ("native
# order") does not, because PIL's own unpacker for it is 8-bit. Nothing here
# can recover those bits, but the image still converts instead of failing.
_TAGGED_16BIT_PREFIX = "I;16"


def prepare_image(image: Image.Image, max_size: int | None = 1024) -> bytes:
    """Convert an image to the JPEG bytes sent to the vision model.

    The image is rotated according to its EXIF orientation, flattened to RGB
    (alpha composited onto white), optionally downscaled so its longer edge is
    at most `max_size`, and encoded as JPEG. Multi-frame images contribute
    their current frame only. The output carries no EXIF of its own.

    Args:
        image: The image to prepare. Any mode PIL can convert is accepted, and
            the object is left exactly as it was found.
        max_size: Longest edge in pixels after downscaling. Images smaller than
            this are never enlarged. `None` disables downscaling entirely.

    Returns:
        JPEG-encoded bytes of an RGB image.

    Raises:
        TypeError: If `image` is not a `PIL.Image.Image`, or `max_size` is
            neither an `int` nor `None`.
        ValueError: If `max_size` is not a positive number of pixels.
        ImageError: If the image has zero area, cannot be loaded, or cannot be
            converted and encoded. The originating PIL exception, if any, is on
            `__cause__`.
    """
    if not isinstance(image, Image.Image):
        raise TypeError(f"image must be a PIL.Image.Image, not {type(image).__name__}")
    if max_size is not None:
        # bool is a subclass of int, and `max_size=True` meaning "one pixel" is
        # a typo every time, never an intention. Floats are rejected outright
        # rather than rounded: 1.5 pixels has no meaning here.
        if not isinstance(max_size, int) or isinstance(max_size, bool):
            raise TypeError(
                f"max_size must be an int or None, not {type(max_size).__name__}"
            )
        if max_size < 1:
            raise ValueError(f"max_size must be at least 1 pixel, not {max_size}")

    width, height = image.size
    if width <= 0 or height <= 0:
        raise ImageError(f"cannot prepare a zero-area image (size {width}x{height})")

    try:
        # Force the pixels in now: a lazily-opened image that turns out to be
        # closed or truncated should fail here, as ImageError, rather than
        # halfway through encoding.
        image.load()
        # Orientation before anything else. A phone photo is stored in sensor
        # order with the rotation in EXIF; skip this and portrait pictures are
        # described sideways. exif_transpose() returns a new image unless asked
        # to work in place, so the caller's object is untouched, and it drops
        # the orientation tag from what it returns.
        working = ImageOps.exif_transpose(image)
        rgb = _to_rgb(working)
        if max_size is not None:
            # thumbnail() resizes in place and never enlarges, which is exactly
            # the "longer edge <= max_size" rule; `rgb` is always a fresh image
            # by this point, never the caller's.
            rgb.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    except Exception as exc:
        # Deliberate catch-all: this is the boundary between PIL and this
        # library's contract. PIL signals failures with OSError, ValueError and
        # others depending on version and codec, and the caller only needs to
        # know that this image could not be prepared.
        raise ImageError(
            f"could not prepare image (mode {image.mode!r}, "
            f"size {width}x{height}): {exc}"
        ) from exc

    return buffer.getvalue()


def _to_rgb(image: Image.Image) -> Image.Image:
    """Return a new RGB image with the same visible content as `image`.

    Args:
        image: The image to convert; it is not modified.

    Returns:
        A freshly allocated RGB image, safe to resize in place.
    """
    mode = image.mode
    if mode in _WIDE_MODES:
        working = image.convert("I") if mode.startswith(_TAGGED_16BIT_PREFIX) else image
        return _rescale_to_grey(working).convert("RGB")
    # `info["transparency"]` is how PNG colour-key and palette transparency
    # arrive, in every mode from P to RGB to L. Converting such an image
    # straight to RGB paints the keyed colour instead of honouring it, so
    # anything carrying the key goes through RGBA and gets composited.
    if "transparency" in image.info or mode in _ALPHA_MODES:
        return _flatten_alpha(_to_rgba(image))
    if mode == "RGB":
        # convert() to the same mode already copies, but say so explicitly:
        # the result is about to be resized in place.
        return image.copy()
    return image.convert("RGB")


def _to_rgba(image: Image.Image) -> Image.Image:
    """Return `image` as RGBA, routing around PIL's missing conversions.

    Args:
        image: An image with alpha, or with a transparency key in `info`.

    Returns:
        An RGBA image.
    """
    if image.mode == "La":
        # PIL has no La -> RGBA (or even La -> L) conversion, but it will
        # un-premultiply La -> LA, and LA -> RGBA is supported.
        return image.convert("LA").convert("RGBA")
    return image.convert("RGBA")


def _rescale_to_grey(image: Image.Image) -> Image.Image:
    """Map a wide-sample image linearly onto 8-bit greyscale.

    Args:
        image: An image in `I` or `F` mode.

    Returns:
        An `L`-mode image spanning the full 0..255 range.
    """
    # Wide modes are single-band, so getextrema() gives a plain (min, max)
    # pair; the declared union covers the multi-band case this never sees.
    low, high = cast("tuple[float, float]", image.getextrema())
    if not (math.isfinite(low) and math.isfinite(high)):
        # An F image with inf or nan samples — a depth map with holes, say —
        # has no usable range: scaling against it would map every finite
        # sample to black. PIL's own conversion clamps to 0..255 instead, which
        # keeps the finite part visible. Known limitation: a non-finite sample
        # costs the image its dynamic-range normalisation.
        return image.convert("L")
    if high <= low:
        # Constant image: there is no contrast to preserve and the scale factor
        # would divide by zero. Black is as faithful as anything else.
        return Image.new("L", image.size, 0)
    # Normalise against the observed range rather than the nominal one. A
    # 16-bit scan that only uses 0..4095, or a float depth map in 0..1, would
    # otherwise reach the model as a uniformly black rectangle.
    scale = 255.0 / (high - low)
    offset = -low * scale
    return image.point(lambda value: value * scale + offset).convert("L")


def _flatten_alpha(rgba: Image.Image) -> Image.Image:
    """Composite an RGBA image onto opaque white and return it as RGB.

    Args:
        rgba: An image in `RGBA` mode.

    Returns:
        An RGB image with the transparency resolved against white.
    """
    background = Image.new("RGBA", rgba.size, _BACKGROUND)
    return Image.alpha_composite(background, rgba).convert("RGB")
