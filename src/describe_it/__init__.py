"""describe-it: alt text for PIL images from a local Ollama vision model.

This module is the public surface and holds no logic of its own. `describe` is
the whole library for most callers; `Describer` is the same thing with the
configuration hoisted out of the loop, and `OllamaClient` is exposed for the
rare caller who wants the transport without the pipeline.

`DEFAULT_MODEL` and `DEFAULT_HOST` are the static fallbacks, documented so a
caller can report what would be used; the environment variables that override
them (`DESCRIBE_IT_MODEL`, `OLLAMA_HOST`) are read at call time, not here.
"""

from importlib.metadata import version

from describe_it.client import OllamaClient
from describe_it.config import DEFAULT_HOST, DEFAULT_MODEL
from describe_it.describer import Describer, describe
from describe_it.exceptions import (
    DescribeItError,
    DescriptionError,
    DescriptionRefusedError,
    ImageError,
    ModelNotFoundError,
    OllamaConnectionError,
    OllamaError,
    OllamaResponseError,
    OllamaTimeoutError,
)

# Read from installed metadata rather than duplicated here, so pyproject.toml
# stays the single place a version number is written down.
__version__ = version("describe-it")

__all__ = [
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
]
