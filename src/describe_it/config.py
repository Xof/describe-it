"""Configuration defaults and normalisation of a host string.

These values sit in their own module because both layers above need them and
neither owns them: `client` needs a host, `describer` needs a model, and the
package re-exports both as documented constants.

Environment lookups are functions rather than module-level constants. A
constant would freeze whatever the environment held when the package was first
imported, which is wrong for a library a long-lived process imports once at
start-up, and untestable without reloading modules. The `DEFAULT_*` constants
are only the static fallbacks those functions fall back to.
"""

import os
import re
from urllib.parse import urlsplit

# Ollama's own default listening address.
DEFAULT_HOST = "http://localhost:11434"

# Resolved in the design spec (§8 Q1): the Qwen vision models refuse far less
# often than the Gemma line, which matters for a library whose stated use case
# includes images a commercial API would reject outright.
DEFAULT_MODEL = "qwen3.5:4b"

# Ollama's own CLI reads OLLAMA_HOST, so a machine that is already configured
# for `ollama run` needs no describe-it-specific configuration to match it.
HOST_ENV_VAR = "OLLAMA_HOST"

# The model, by contrast, is ours: OLLAMA_MODEL is not a thing Ollama defines,
# and a vision model is a describe-it concern rather than a server-wide one.
MODEL_ENV_VAR = "DESCRIBE_IT_MODEL"

# An explicit URL scheme, per RFC 3986. Matched with a regex rather than with
# `urlsplit`, which reads the "localhost" of "localhost:11434" as a scheme and
# would leave the most common bare host form unrecognised.
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def default_host() -> str:
    """Return the configured Ollama host, before normalisation.

    Returns:
        `$OLLAMA_HOST` if it is set and non-empty, otherwise `DEFAULT_HOST`.
        An empty variable is treated as unset: an exported-but-blank variable
        is a deployment accident, never a request for a nameless server.
    """
    return os.environ.get(HOST_ENV_VAR) or DEFAULT_HOST


def default_model() -> str:
    """Return the configured model tag.

    Returns:
        `$DESCRIBE_IT_MODEL` if it is set and non-empty, otherwise
        `DEFAULT_MODEL`.
    """
    return os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL


def normalise_host(host: str) -> str:
    """Turn a host as a human writes it into a base URL requests can be built on.

    Accepts what Ollama's own CLI accepts. `localhost:11434` gains the `http://`
    it is missing; `https://` is left alone, because a remote Ollama behind a
    reverse proxy is a real deployment; trailing slashes are removed so that
    appending `/api/chat` never produces a doubled separator.

    Args:
        host: A base URL or a bare `host:port`. Surrounding whitespace is
            ignored — an environment variable set from a shell script picks up
            stray spaces surprisingly often.

    Returns:
        A base URL with a scheme and no trailing slash, ready for a path to be
        appended to it.

    Raises:
        ValueError: If nothing usable as a hostname is left. Failing here names
            the configuration mistake; letting it through would surface later
            as an unrelated-looking connection error.
    """
    trimmed = host.strip()
    if _SCHEME_RE.match(trimmed) is None:
        trimmed = f"http://{trimmed}"
    normalised = trimmed.rstrip("/")
    if not urlsplit(normalised).netloc:
        raise ValueError(
            f"host {host!r} has no hostname; expected something like "
            f"'localhost:11434' or 'http://localhost:11434'"
        )
    return normalised
