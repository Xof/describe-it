"""Tests for the package surface: version, re-exports, console script target."""

import importlib.metadata

import pytest

import describe_it
from describe_it import cli

# The public surface the design specification promises (§4), plus the client,
# which is exported so a caller can drive the transport without the pipeline.
# Written out rather than derived, so that dropping or renaming an export has
# to be a deliberate edit here as well.
_PUBLIC_NAMES = {
    "DEFAULT_HOST",
    "DEFAULT_MODEL",
    "DescribeItError",
    "Describer",
    "DescriptionError",
    "DescriptionRefusedError",
    "ImageError",
    "ModelNotFoundError",
    "OllamaClient",
    "OllamaConnectionError",
    "OllamaError",
    "OllamaResponseError",
    "OllamaTimeoutError",
    "__version__",
    "describe",
}


def test_version_comes_from_installed_metadata() -> None:
    assert describe_it.__version__ == importlib.metadata.version("describe-it")


@pytest.mark.parametrize("name", describe_it.__all__)
def test_every_exported_name_exists(name: str) -> None:
    assert hasattr(describe_it, name)


def test_the_exported_surface_is_complete_and_sorted() -> None:
    assert set(describe_it.__all__) == _PUBLIC_NAMES
    assert describe_it.__all__ == sorted(describe_it.__all__)


def test_exception_hierarchy_is_re_exported() -> None:
    assert issubclass(describe_it.ImageError, describe_it.DescribeItError)
    assert issubclass(describe_it.DescriptionRefusedError, describe_it.DescriptionError)


def test_the_documented_defaults_are_the_static_fallbacks() -> None:
    # The environment variables that override these are read per call, so the
    # constants stay meaningful as "what would be used if nothing is set".
    assert describe_it.DEFAULT_MODEL == "qwen3.5:4b"
    assert describe_it.DEFAULT_HOST == "http://localhost:11434"


def test_console_script_target_resolves_to_the_cli() -> None:
    # Loading the entry point is what a `describe-it` invocation does, so this
    # catches a pyproject.toml that names a function the package does not have.
    (script,) = importlib.metadata.entry_points(
        group="console_scripts", name="describe-it"
    )
    assert script.load() is cli.main
