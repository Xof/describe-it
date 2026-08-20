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
from urllib.parse import urlsplit, urlunsplit

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

# The port assumed when the host names none. Ollama's CLI does the same thing:
# a host without a port means Ollama's port, not the scheme's. Getting this
# wrong is quiet and confusing — port 80 usually answers, with a web server.
_DEFAULT_PORTS = {"http": 11434, "https": 443}


def default_host() -> str:
    """Return the configured Ollama host, before normalisation.

    Returns:
        `$OLLAMA_HOST` if it holds anything but whitespace, otherwise
        `DEFAULT_HOST`. A blank variable is treated as unset: an
        exported-but-empty variable is a deployment accident, never a request
        for a nameless server.
    """
    return os.environ.get(HOST_ENV_VAR, "").strip() or DEFAULT_HOST


def default_model() -> str:
    """Return the configured model tag.

    Returns:
        `$DESCRIBE_IT_MODEL` if it holds anything but whitespace, otherwise
        `DEFAULT_MODEL`. Blank is treated as unset, as it is for the host.
    """
    return os.environ.get(MODEL_ENV_VAR, "").strip() or DEFAULT_MODEL


def normalise_host(host: str) -> str:
    """Turn a host as a human writes it into a base URL requests can be built on.

    Accepts what Ollama's own CLI accepts, and fills in the same blanks it
    does. `localhost:11434` gains the `http://` it is missing; `localhost`
    gains Ollama's port as well, because a host with no port would otherwise
    mean port 80 and reach a web server rather than a model; `https://` is
    preserved, because a remote Ollama behind a reverse proxy is a real
    deployment; trailing slashes go, so that appending `/api/chat` never
    produces a doubled separator. A path is kept — that is how an Ollama
    mounted under a prefix on a shared host is addressed.

    Args:
        host: A base URL or a bare `host:port`. Surrounding whitespace is
            ignored — an environment variable set from a shell script picks up
            stray spaces surprisingly often.

    Returns:
        A base URL with a scheme, an explicit port and no trailing slash, ready
        for a path to be appended to it.

    Raises:
        ValueError: If what is left is not something this client can post to: no
            hostname, a scheme that is not HTTP, an unreadable port, embedded
            credentials, or a query string or fragment (neither of which can
            survive having `/api/chat` appended to it). Failing here names the
            configuration mistake; letting it through would surface later as an
            unrelated-looking connection error, or as a request sent somewhere
            unintended.
    """
    trimmed = host.strip()
    if _SCHEME_RE.match(trimmed) is None:
        trimmed = f"http://{trimmed}"
    # urlsplit lowercases the scheme for us, so HTTP:// and http:// converge.
    split = urlsplit(trimmed.rstrip("/"))

    # Checked first, and reported without quoting the host back: everything
    # below puts the host in its message, and a message is the last place a
    # password should end up.
    if split.username is not None or split.password is not None:
        raise ValueError(
            "host must not contain credentials; Ollama has no authentication "
            "of its own, so put any that a proxy needs in the proxy's config"
        )
    if not split.hostname:
        raise ValueError(
            f"host {host!r} has no hostname; expected something like "
            f"'localhost:11434' or 'http://localhost:11434'"
        )
    if split.scheme not in _DEFAULT_PORTS:
        raise ValueError(f"host {host!r} must use http or https, not {split.scheme!r}")
    if split.query or split.fragment:
        raise ValueError(
            f"host {host!r} must not have a query string or fragment; it is a "
            f"base URL, and the API path is appended to it"
        )
    try:
        port = split.port
    except ValueError as exc:
        # urlsplit defers the port until it is asked for, and then reports it
        # in terms of casting rather than of configuration.
        raise ValueError(f"host {host!r} has an unreadable port") from exc

    if port is None:
        netloc = f"{split.netloc}:{_DEFAULT_PORTS[split.scheme]}"
    else:
        netloc = split.netloc
    return urlunsplit((split.scheme, netloc, split.path, "", ""))
