"""describe-it: alt text for PIL images from a local Ollama vision model.

This module is the public surface and holds no logic of its own. `describe`
and `Describer` — the two names callers actually reach for — arrive with the
client and describer modules in the next work unit; for now the package
exports its version and the exception hierarchy.
"""

from importlib.metadata import version

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
    "DescribeItError",
    "DescriptionError",
    "DescriptionRefusedError",
    "ImageError",
    "ModelNotFoundError",
    "OllamaConnectionError",
    "OllamaError",
    "OllamaResponseError",
    "OllamaTimeoutError",
    "__version__",
]
