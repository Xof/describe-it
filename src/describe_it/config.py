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

# The port a scheme-less host is assumed to mean, matching how Ollama's own
# `envconfig.Host()` parses OLLAMA_HOST: "localhost" there means Ollama's port,
# not port 80. A host written with a scheme is a URL, and a URL without a port
# already means its scheme's default — so nothing is added to one of those.
_DEFAULT_PORT = 11434

# The only two schemes this client can post to. Anything else is a
# configuration mistake worth naming, and would reach urllib as an opener with
# no handler for it.
_SUPPORTED_SCHEMES = frozenset({"http", "https"})


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

    Accepts what Ollama's own CLI accepts, and fills in the same blank it does:
    a host written without a scheme — `localhost`, `localhost:11434`, `[::1]` —
    gets `http://`, and gets Ollama's port when it names none, because that is
    what `OLLAMA_HOST=localhost` means to `ollama` itself. A host written *with*
    a scheme is a URL and is used as written: `http://ollama.example.com` keeps
    meaning port 80, as it does everywhere else, and `https://` is preserved
    because a remote Ollama behind a reverse proxy is a real deployment.

    Trailing slashes go, so that appending `/api/chat` never produces a doubled
    separator. A path is kept — that is how an Ollama mounted under a prefix on
    a shared host is addressed.

    Args:
        host: A base URL or a bare `host:port`. Surrounding whitespace is
            ignored — an environment variable set from a shell script picks up
            stray spaces surprisingly often.

    Returns:
        A base URL with a scheme and no trailing slash, ready for a path to be
        appended to it.

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
    # Whether the *caller* wrote a scheme, which is what decides the port rule
    # below. Recorded before one is prepended, obviously.
    scheme_given = _SCHEME_RE.match(trimmed) is not None
    if not scheme_given:
        trimmed = f"http://{trimmed}"
    # Rejected on the raw text rather than on the parsed parts, because an
    # empty query or fragment ("http://h:1/?") parses as no query at all while
    # still leaving the slash that produced it — and a request to "//api/chat".
    if "?" in trimmed or "#" in trimmed:
        raise ValueError(
            f"host {host!r} must not have a query string or fragment; it is a "
            f"base URL, and the API path is appended to it"
        )
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
    if split.scheme not in _SUPPORTED_SCHEMES:
        raise ValueError(f"host {host!r} must use http or https, not {split.scheme!r}")
    try:
        port = split.port
    except ValueError as exc:
        # urlsplit defers the port until it is asked for, and then reports it
        # in terms of casting rather than of configuration.
        raise ValueError(f"host {host!r} has an unreadable port") from exc

    if scheme_given or port is not None:
        netloc = split.netloc
    else:
        netloc = f"{split.netloc}:{_DEFAULT_PORT}"
    # The whole string was already stripped of trailing slashes, but the path
    # keeps its own invariant rather than depending on that having happened.
    return urlunsplit((split.scheme, netloc, split.path.rstrip("/"), "", ""))
