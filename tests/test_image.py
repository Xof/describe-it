"""Tests for `describe_it.image.prepare_image`.

The assertions decode the produced JPEG and sample pixels rather than
inspecting intermediate objects: what matters is what the model will see.
Tolerances are generous because JPEG at quality 90 is lossy; every sample is
taken well inside a solid block so chroma subsampling cannot reach it.
"""

import io
from collections.abc import Sequence
from typing import Any

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
    "LA": (140, 128),
    "L": 140,
    "P": 3,
    "PA": (3, 128),
    "1": 1,
    "CMYK": (10, 120, 200, 5),
    "I;16": 40000,
    "I": 70000,
    "F": 0.5,
}

_WHITE = (255, 255, 255)
_RED = (255, 0, 0)


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
    _assert_close(_pixel(decoded, (48, 16)), (0, 0, 255))


@pytest.mark.parametrize(
    ("mode", "values"),
    [
        ("I;16", (0, 32768, 65535)),
        ("I", (0, 50000, 100000)),
        ("F", (-1.0, 0.0, 1.0)),
    ],
)
def test_wide_modes_are_rescaled_rather_than_clipped(
    mode: str, values: Sequence[Any]
) -> None:
    # convert("RGB") clips these to 0..255, which would render both of the two
    # brighter bands as pure white; a linear rescale keeps them apart.
    image = _banded(mode, values)

    decoded = _decode(prepare_image(image, max_size=None))

    dark, mid, light = (_pixel(decoded, (x, 16))[0] for x in (16, 48, 80))
    assert dark < 40
    assert 90 < mid < 165
    assert light > 215


def test_constant_wide_image_does_not_divide_by_zero() -> None:
    image = Image.new("I;16", (48, 32), 40000)

    decoded = _decode(prepare_image(image, max_size=None))

    _assert_close(_pixel(decoded, (24, 16)), (0, 0, 0))


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
    # Distinct palettes, or PIL's GIF writer notices the frames are identical
    # and collapses them into one.
    frames = []
    for index in (0, 1):
        frame = Image.new("P", (32, 32), index)
        frame.putpalette([255, 0, 0, 0, 0, 255] + [0, 0, 0] * 254)
        frames.append(frame)
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:])
    animation = Image.open(io.BytesIO(buffer.getvalue()))
    assert getattr(animation, "n_frames", 1) == 2

    decoded = _decode(prepare_image(animation, max_size=None))

    assert decoded.size == (32, 32)
    assert animation.tell() == 0


@pytest.mark.parametrize("size", [(0, 0), (0, 10), (10, 0)])
def test_zero_area_image_is_rejected(size: tuple[int, int]) -> None:
    image = Image.new("RGB", size)

    with pytest.raises(ImageError, match="zero-area"):
        prepare_image(image)


@pytest.mark.parametrize("argument", ["not an image", None, 42, b"jpeg bytes"])
def test_non_image_argument_raises_type_error(argument: object) -> None:
    with pytest.raises(TypeError, match=r"PIL\.Image\.Image"):
        prepare_image(argument)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_size", [0, -1])
def test_non_positive_max_size_is_rejected(max_size: int) -> None:
    image = Image.new("RGB", (40, 30))

    with pytest.raises(ValueError, match="max_size"):
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
