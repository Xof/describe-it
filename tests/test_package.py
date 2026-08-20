"""Tests for the package surface: version, re-exports, console script target."""

import importlib.metadata

import pytest

import describe_it
from describe_it import cli


def test_version_comes_from_installed_metadata() -> None:
    assert describe_it.__version__ == importlib.metadata.version("describe-it")


@pytest.mark.parametrize("name", describe_it.__all__)
def test_every_exported_name_exists(name: str) -> None:
    assert hasattr(describe_it, name)


def test_exception_hierarchy_is_re_exported() -> None:
    assert issubclass(describe_it.ImageError, describe_it.DescribeItError)
    assert issubclass(describe_it.DescriptionRefusedError, describe_it.DescriptionError)


def test_console_script_target_is_importable() -> None:
    # The entry point declared in pyproject.toml has to resolve from the first
    # commit; the CLI itself lands in a later work unit.
    with pytest.raises(NotImplementedError):
        cli.main()
