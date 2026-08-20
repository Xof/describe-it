"""Tests for `describe_it.image.prepare_image`.

The assertions decode the produced JPEG and sample pixels rather than
inspecting intermediate objects: what matters is what the model will see.
Tolerances are generous because JPEG at quality 90 is lossy; every sample is
taken well inside a solid block so chroma subsampling cannot reach it.
"""

import io
import math
import struct
from collections.abc import Sequence
from typing import Any, cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from describe_it.exceptions import ImageError
from describe_it.image import prepare_image

# One representative fill per mode. Values are mid-range where possible so a
# clipping or scaling mistake shows up as a wrong grey rather than as black.
_MODE_FILLS: dict[str, Any] = {
    "RGB": (10, 120, 200),
    "RGBA": (10, 120, 200, 128),
    "RGBa": (10, 120, 200, 128),
    "LA": (140, 128),
    "La": (140, 255),
    "L": 140,
    "P": 3,
    "PA": (3, 128),
    "1": 1,
    "CMYK": (10, 120, 200, 5),
    "I;16": 40000,
    "I;16B": 40000,
    "I;16L": 40000,
    "I;16N": 40000,
    "I": 70000,
    "F": 0.5,
}

_WHITE = (255, 255, 255)
_RED = (255, 0, 0)
_BLUE = (0, 0, 255)
_ORIENTATION_TAG = 274


def _decode(data: bytes) -> Image.Image:
    """Decode prepared bytes, asserting they really are a JPEG."""
    decoded = Image.open(io.BytesIO(data))
    decoded.load()
    assert decoded.format == "JPEG"
    assert decoded.mode == "RGB"
    return decoded


def _pixel(image: Image.Image, xy: tuple[int, int]) -> tuple[int, ...]:
    """Read one pixel, narrowing PIL's loosely typed return value."""
    value = image.getpixel(xy)
    assert isinstance(value, tuple)
    return value


def _assert_close(
    actual: tuple[int, ...], expected: tuple[int, ...], tolerance: int = 12
) -> None:
    """Assert two colours match within JPEG's rounding noise."""
    assert len(actual) == len(expected)
    for got, want in zip(actual, expected, strict=True):
        assert abs(got - want) <= tolerance, f"{actual} != {expected}"


def _banded(mode: str, values: Sequence[Any], height: int = 32) -> Image.Image:
    """Build a single-band image of equal-width vertical bands."""
    band_width = 32
    image = Image.new(mode, (band_width * len(values), height))
    for index, value in enumerate(values):
        image.paste(
            Image.new(mode, (band_width, height), value), (index * band_width, 0)
        )
    return image


def _assert_three_grey_bands(decoded: Image.Image) -> None:
    """Assert a three-band image spans dark, mid and light greys.

    A clipping conversion renders both of the brighter bands as white, so the
    middle band is what proves the samples were scaled rather than clamped.
    """
    dark, mid, light = (_pixel(decoded, (x, 16))[0] for x in (16, 48, 80))
    assert dark < 40
    assert 90 < mid < 165
    assert light > 215


def _big_endian_16bit_tiff(values: Sequence[int], size: tuple[int, int]) -> bytes:
    """Assemble a minimal uncompressed big-endian 16-bit greyscale TIFF.

    PIL cannot write one (it saves little-endian), and `MM` byte order is what
    produces the `I;16B` mode this module has to cope with, so the file is
    built by hand: 8-byte header, pixel data, then the IFD.

    Args:
        values: Row-major sample values, one per pixel.
        size: `(width, height)`, matching the number of values.

    Returns:
        The TIFF file's bytes.
    """
    width, height = size
    pixels = b"".join(struct.pack(">H", value) for value in values)

    def entry(tag: int, field_type: int, value: int) -> bytes:
        # SHORT values sit in the first two bytes of the four-byte value field.
        payload = (
            struct.pack(">HH", value, 0)
            if field_type == 3
            else struct.pack(">I", value)
        )
        return struct.pack(">HHI", tag, field_type, 1) + payload

    entries = [
        entry(256, 3, width),  # ImageWidth
        entry(257, 3, height),  # ImageLength
        entry(258, 3, 16),  # BitsPerSample
        entry(259, 3, 1),  # Compression: none
        entry(262, 3, 1),  # PhotometricInterpretation: black is zero
        entry(273, 4, 8),  # StripOffsets: straight after the header
        entry(277, 3, 1),  # SamplesPerPixel
        entry(278, 3, height),  # RowsPerStrip
        entry(279, 4, len(pixels)),  # StripByteCounts
    ]
    ifd = struct.pack(">H", len(entries)) + b"".join(entries) + struct.pack(">I", 0)
    return b"MM\x00\x2a" + struct.pack(">I", 8 + len(pixels)) + pixels + ifd


def _two_frame_gif() -> Image.Image:
    """Open a freshly built two-frame GIF: frame 0 red, frame 1 blue."""
    frames = []
    for index in (0, 1):
        # Distinct palettes, or PIL's GIF writer notices the frames are
        # identical and collapses them into one.
        frame = Image.new("P", (32, 32), index)
        frame.putpalette([255, 0, 0, 0, 0, 255] + [0, 0, 0] * 254)
        frames.append(frame)
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:])
    animation = Image.open(io.BytesIO(buffer.getvalue()))
    assert getattr(animation, "n_frames", 1) == 2
    return animation


@pytest.mark.parametrize("mode", sorted(_MODE_FILLS))
def test_every_mode_produces_a_decodable_rgb_jpeg(mode: str) -> None:
    image = Image.new(mode, (48, 32), _MODE_FILLS[mode])

    decoded = _decode(prepare_image(image, max_size=None))

    assert decoded.size == (48, 32)


def test_transparent_rgba_region_becomes_white() -> None:
    image = Image.new("RGBA", (64, 32), (*_RED, 255))
    image.paste((0, 0, 0, 0), (32, 0, 64, 32))

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_close(_pixel(decoded, (16, 16)), _RED)
    _assert_close(_pixel(decoded, (48, 16)), _WHITE)


def test_premultiplied_la_is_flattened_onto_white() -> None:
    # PIL refuses La -> RGBA outright, so this exercises the LA detour.
    image = Image.new("La", (64, 32), (140, 255))
    image.paste((0, 0), (32, 0, 64, 32))

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_close(_pixel(decoded, (16, 16)), (140, 140, 140))
    _assert_close(_pixel(decoded, (48, 16)), _WHITE)


def test_palette_transparency_flattens_to_white_not_the_palette_colour() -> None:
    # The transparent index still has a colour in the palette (blue here); a
    # direct convert("RGB") would paint it instead of honouring transparency.
    image = Image.new("P", (64, 32))
    image.putpalette([255, 0, 0, 0, 0, 255] + [0, 0, 0] * 254)
    image.paste(1, (32, 0, 64, 32))
    image.info["transparency"] = 1

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_close(_pixel(decoded, (16, 16)), _RED)
    _assert_close(_pixel(decoded, (48, 16)), _WHITE)


def test_palette_without_transparency_keeps_its_colours() -> None:
    image = Image.new("P", (64, 32))
    image.putpalette([255, 0, 0, 0, 0, 255] + [0, 0, 0] * 254)
    image.paste(1, (32, 0, 64, 32))

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_close(_pixel(decoded, (16, 16)), _RED)
    _assert_close(_pixel(decoded, (48, 16)), _BLUE)


def test_rgb_colour_key_transparency_is_flattened() -> None:
    # PNG tRNS on a truecolour image: the key arrives as info["transparency"]
    # with the mode still RGB, so mode alone is not enough to spot it.
    source = Image.new("RGB", (64, 32), _RED)
    source.paste(_BLUE, (32, 0, 64, 32))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG", transparency=_BLUE)
    image = Image.open(io.BytesIO(buffer.getvalue()))
    assert image.mode == "RGB"

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_close(_pixel(decoded, (16, 16)), _RED)
    _assert_close(_pixel(decoded, (48, 16)), _WHITE)


def test_greyscale_colour_key_transparency_is_flattened() -> None:
    source = Image.new("L", (64, 32), 40)
    source.paste(200, (32, 0, 64, 32))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG", transparency=200)
    image = Image.open(io.BytesIO(buffer.getvalue()))
    assert image.mode == "L"

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_close(_pixel(decoded, (16, 16)), (40, 40, 40))
    _assert_close(_pixel(decoded, (48, 16)), _WHITE)


@pytest.mark.parametrize(
    ("mode", "values"),
    [
        ("I;16", (0, 32768, 65535)),
        ("I;16L", (0, 32768, 65535)),
        ("I;16N", (0, 32768, 65535)),
        ("I", (0, 50000, 100000)),
        ("F", (-1.0, 0.0, 1.0)),
    ],
)
def test_wide_modes_are_rescaled_rather_than_clipped(
    mode: str, values: Sequence[Any]
) -> None:
    _assert_three_grey_bands(_decode(prepare_image(_banded(mode, values), None)))


def test_big_endian_16bit_tiff_is_rescaled() -> None:
    # I;16B rejects getextrema() and point() ("image has wrong mode"), so this
    # is the file that used to come back as ImageError instead of a JPEG.
    width, height = 96, 32
    row = [0] * 32 + [32768] * 32 + [65535] * 32
    tiff = _big_endian_16bit_tiff(row * height, (width, height))
    image = Image.open(io.BytesIO(tiff))
    assert image.mode == "I;16B"

    decoded = _decode(prepare_image(image, max_size=None))

    assert decoded.size == (width, height)
    _assert_three_grey_bands(decoded)


def test_constant_wide_image_does_not_divide_by_zero() -> None:
    image = Image.new("I;16", (48, 32), 40000)

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_close(_pixel(decoded, (24, 16)), (0, 0, 0))


def test_infinite_float_samples_keep_the_finite_bands_visible() -> None:
    # Normalising against an infinite maximum would black out every finite
    # sample; the fallback clamps instead, which at least stays legible.
    image = _banded("F", (0.0, 200.0, float("inf")))

    decoded = _decode(prepare_image(image, max_size=None))

    dark, mid, light = (_pixel(decoded, (x, 16))[0] for x in (16, 48, 80))
    assert dark < 40
    assert 150 < mid < 230
    assert light > 215


def test_nan_float_samples_do_not_disturb_the_rescale() -> None:
    # PIL's getextrema() ignores nan, so the finite range is still (0, 200) and
    # the normal rescale runs: the finite bands span black to white and the nan
    # band itself lands on black. Pinned because it is the one wide-mode input
    # whose outcome comes from PIL's arithmetic rather than ours.
    image = _banded("F", (0.0, 200.0, float("nan")))

    decoded = _decode(prepare_image(image, max_size=None))

    assert decoded.size == (96, 32)
    dark, bright, not_a_number = (_pixel(decoded, (x, 16))[0] for x in (16, 48, 80))
    assert dark < 15
    assert bright > 245
    assert not_a_number < 15


def test_native_byte_order_16bit_keeps_full_precision() -> None:
    # I;16N goes through PIL's 8-bit unpacker, which clamps every sample to
    # 255; its bytes are reinterpreted instead, so the four levels stay apart.
    image = _banded("I;16N", (0, 32767, 32768, 65535))

    decoded = _decode(prepare_image(image, max_size=None))

    black, half, half_up, white = (
        _pixel(decoded, (x, 16))[0] for x in (16, 48, 80, 112)
    )
    assert black < 10
    assert abs(half - 127) <= 4
    assert abs(half_up - 127) <= 4
    assert white > 245


def test_bogus_transparency_key_is_ignored() -> None:
    # info["transparency"] holds whatever the encoder wrote. PIL raises
    # TypeError on a string, and a broken hint must not cost the caller their
    # description.
    image = Image.new("RGB", (64, 32), _RED)
    image.paste(_BLUE, (32, 0, 64, 32))
    image.info["transparency"] = "nope"

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_close(_pixel(decoded, (16, 16)), _RED)
    _assert_close(_pixel(decoded, (48, 16)), _BLUE)


@pytest.mark.parametrize(
    ("mode", "bogus"),
    [
        ("L", "x"),
        ("L", 1.5),
        ("P", "x"),
        ("P", 1.5),
        ("RGB", None),
    ],
)
def test_bogus_transparency_key_never_costs_the_description(
    mode: str, bogus: object
) -> None:
    # PIL consults the key on the plain L and P conversions too, so dropping
    # the flatten route is not enough on its own.
    image = Image.new(mode, (48, 32), _MODE_FILLS[mode])
    image.info["transparency"] = bogus

    decoded = _decode(prepare_image(image, max_size=None))

    assert decoded.size == (48, 32)
    # The key is dropped from a copy; the caller's image keeps whatever it had.
    assert image.info["transparency"] == bogus


def test_nan_at_the_first_pixel_does_not_black_out_the_image() -> None:
    # PIL seeds its extrema scan with pixel (0, 0), so a nan there comes back
    # as a nan range for the whole image; rescanning finds the real one.
    image = _banded("F", (0.2, 0.8))
    image.putpixel((0, 0), float("nan"))
    assert math.isnan(cast("tuple[float, float]", image.getextrema())[0])

    decoded = _decode(prepare_image(image, max_size=None))

    dark, light = (_pixel(decoded, (x, 16))[0] for x in (16, 48))
    assert dark < 15
    assert light > 240


def test_all_nan_float_image_comes_out_black() -> None:
    image = Image.new("F", (48, 32), float("nan"))

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_close(_pixel(decoded, (24, 16)), (0, 0, 0))


def test_wide_mode_ignores_a_colour_key() -> None:
    # A 16-bit PNG can carry tRNS, but the key names a raw sample value and
    # rescaling moves every sample, so the key is documented as ignored: the
    # keyed band comes out mid-grey rather than white.
    source = _banded("I;16", (0, 30000, 60000))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG", transparency=30000)
    image = Image.open(io.BytesIO(buffer.getvalue()))
    assert image.mode == "I;16"
    assert image.info["transparency"] == 30000

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_three_grey_bands(decoded)


def test_exif_orientation_is_applied_before_conversion() -> None:
    # A phone photo: stored landscape, tagged "rotate 90° clockwise".
    source = Image.new("RGB", (64, 32), _RED)
    source.paste(_BLUE, (32, 0, 64, 32))
    exif = source.getexif()
    exif[_ORIENTATION_TAG] = 6
    buffer = io.BytesIO()
    source.save(buffer, format="JPEG", exif=exif, quality=95)
    image = Image.open(io.BytesIO(buffer.getvalue()))
    assert image.size == (64, 32)

    decoded = _decode(prepare_image(image, max_size=None))

    assert decoded.size == (32, 64)
    _assert_close(_pixel(decoded, (16, 16)), _RED)
    _assert_close(_pixel(decoded, (16, 48)), _BLUE)
    # The rotation is baked into the pixels, so the tag must not travel with
    # them, and the caller's image is left as it was found.
    assert dict(decoded.getexif()) == {}
    assert image.size == (64, 32)
    assert image.getexif()[_ORIENTATION_TAG] == 6


@pytest.mark.parametrize(
    ("size", "max_size", "expected"),
    [
        ((3000, 1500), 1024, (1024, 512)),
        ((1500, 3000), 1024, (512, 1024)),
        ((800, 600), 1024, (800, 600)),
        ((3000, 1500), None, (3000, 1500)),
        ((1024, 1024), 1024, (1024, 1024)),
    ],
)
def test_downscaling_bounds_the_longer_edge(
    size: tuple[int, int], max_size: int | None, expected: tuple[int, int]
) -> None:
    image = Image.new("RGB", size, (10, 120, 200))

    decoded = _decode(prepare_image(image, max_size=max_size))

    assert decoded.size == expected


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "P", "I;16"])
def test_the_callers_image_is_left_alone(mode: str) -> None:
    image = Image.new(mode, (40, 30), _MODE_FILLS[mode])
    before = image.getpixel((0, 0))

    prepare_image(image, max_size=8)

    assert image.mode == mode
    assert image.size == (40, 30)
    assert image.getpixel((0, 0)) == before


def test_multi_frame_gif_uses_the_first_frame() -> None:
    animation = _two_frame_gif()

    decoded = _decode(prepare_image(animation, max_size=None))

    assert decoded.size == (32, 32)
    _assert_close(_pixel(decoded, (16, 16)), _RED)
    assert animation.tell() == 0


def test_seeked_gif_prepares_the_current_frame() -> None:
    animation = _two_frame_gif()
    animation.seek(1)

    decoded = _decode(prepare_image(animation, max_size=None))

    _assert_close(_pixel(decoded, (16, 16)), _BLUE)
    # Preparing an image must not rewind the caller's animation.
    assert animation.tell() == 1


@pytest.mark.parametrize("size", [(0, 0), (0, 10), (10, 0)])
def test_zero_area_image_is_rejected(size: tuple[int, int]) -> None:
    image = Image.new("RGB", size)

    with pytest.raises(ImageError, match="zero-area"):
        prepare_image(image)


@pytest.mark.parametrize("argument", ["not an image", None, 42, b"jpeg bytes"])
def test_non_image_argument_raises_type_error(argument: object) -> None:
    with pytest.raises(TypeError, match=r"PIL\.Image\.Image"):
        prepare_image(argument)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_size", [True, 1.5, float("nan"), float("inf")])
def test_non_integer_max_size_raises_type_error(max_size: object) -> None:
    image = Image.new("RGB", (40, 30))

    with pytest.raises(TypeError, match="max_size must be an int"):
        prepare_image(image, max_size=max_size)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_size", [0, -1])
def test_non_positive_max_size_is_rejected(max_size: int) -> None:
    image = Image.new("RGB", (40, 30))

    with pytest.raises(ValueError, match="max_size must be at least"):
        prepare_image(image, max_size=max_size)


def test_unloadable_image_is_wrapped_with_its_cause() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buffer, format="PNG")
    image = Image.open(io.BytesIO(buffer.getvalue()))
    image.close()

    with pytest.raises(ImageError, match="could not prepare image") as caught:
        prepare_image(image)

    assert caught.value.__cause__ is not None


@given(
    width=st.integers(min_value=1, max_value=3000),
    height=st.integers(min_value=1, max_value=3000),
    max_size=st.integers(min_value=1, max_value=2048),
)
@settings(max_examples=50, deadline=None)
def test_output_fits_the_bound_and_keeps_the_aspect_ratio(
    width: int, height: int, max_size: int
) -> None:
    image = Image.new("RGB", (width, height), (10, 120, 200))

    decoded = _decode(prepare_image(image, max_size=max_size))

    new_width, new_height = decoded.size
    assert 1 <= new_width <= max_size
    assert 1 <= new_height <= max_size
    # Never upscale; downscale by one factor applied to both edges, up to the
    # one pixel of rounding an integer size costs.
    scale = min(1.0, max_size / max(width, height))
    assert abs(new_width - width * scale) <= 1
    assert abs(new_height - height * scale) <= 1
