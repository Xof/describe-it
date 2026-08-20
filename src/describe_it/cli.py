"""Command-line front end for the `describe-it` console script.

A thin wrapper over `Describer`, kept mainly as a manual-testing aid: it is the
shortest way to point the library at a real model and see what comes back.
Everything that is not argument parsing lives in `describe_files`, which is
handed its output streams instead of reaching for the process's own, so the
tests can drive a whole run and read exactly what it wrote.

Option values are validated by argparse `type` functions rather than left to
`Describer`, because a typo in a flag deserves a usage message and exit status
2, not a traceback out of a constructor. Nothing here runs at import time; the
parser is built inside `main`.
"""

import argparse
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from PIL import Image

from describe_it import __version__
from describe_it.config import DEFAULT_HOST, DEFAULT_MODEL, normalise_host
from describe_it.describer import Describer
from describe_it.exceptions import DescribeItError

# argparse needs literal defaults, so these mirror `Describer`'s signature. A
# test compares the two, so the copy cannot drift away from the original.
_DEFAULT_TIMEOUT = 120.0
_DEFAULT_LANGUAGE = "English"
_DEFAULT_MAX_WORDS = 30
_DEFAULT_MAX_IMAGE_SIZE = 1024


def main(argv: Sequence[str] | None = None) -> int:
    """Run the describe-it command line.

    Args:
        argv: Arguments to parse, without the program name. `None` reads
            `sys.argv`, which is what the console script wants.

    Returns:
        0 if every file was described, 1 if any of them failed.

    Raises:
        SystemExit: For `--help`, `--version` and usage errors, which argparse
            reports and exits on itself, with status 0, 0 and 2 respectively.
    """
    args = _build_parser().parse_args(argv)
    # One describer for the whole run: it owns the transport, and Ollama keeps
    # a model resident between requests, so building one per file would risk
    # paying for a model load per image.
    describer = Describer(
        model=args.model,
        host=args.host,
        timeout=args.timeout,
        language=args.language,
        max_words=args.max_words,
        max_image_size=args.max_image_size,
    )
    return describe_files(
        describer,
        args.files,
        context=args.context,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def describe_files(
    describer: Describer,
    paths: Sequence[Path],
    *,
    context: str | None,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Describe every file, reporting failures without abandoning the rest.

    A failed file is a diagnostic on stderr and a non-zero exit status at the
    end, never a stop: the common batch is a directory of images, and one
    unreadable file in it should not cost the caller the other nine hundred.

    Args:
        describer: The configured describer, shared by every file in the run.
        paths: The image files, in the order the caller gave them.
        context: Where the images appear, passed to the model verbatim.
        stdout: Where descriptions are written.
        stderr: Where failures are reported.

    Returns:
        0 if every file was described, 1 if any of them failed.
    """
    # The path prefix is what makes a multi-file run parseable. A single file
    # is the interactive case, where the caller knows what they asked about and
    # wants the description alone — pipeable into anything.
    show_path = len(paths) > 1
    failed = False
    for path in paths:
        try:
            description = describe_file(describer, path, context=context)
        except (OSError, DescribeItError) as exc:
            failed = True
            print(f"describe-it: {path}: {_reason(exc)}", file=stderr)
            continue
        print(f"{path}\t{description}" if show_path else description, file=stdout)
    return 1 if failed else 0


def describe_file(describer: Describer, path: Path, *, context: str | None) -> str:
    """Describe the image in one file.

    Args:
        describer: The configured describer.
        path: The image file to open.
        context: Where the image appears, passed to the model verbatim.

    Returns:
        The description, as a single line.

    Raises:
        OSError: If the file cannot be read, or holds nothing Pillow can
            identify as an image — `PIL.UnidentifiedImageError` is an `OSError`.
        DescribeItError: If the image cannot be prepared, the server cannot be
            reached, or no usable description came back.
    """
    # The `with` is load-bearing: Image.open holds the file open until the
    # image is closed, and a run over a few thousand files would otherwise
    # exhaust the process's descriptors long before it finished.
    with Image.open(path) as image:
        return describer.describe(image, context=context)


def _reason(exc: OSError | DescribeItError) -> str:
    """Render one failure as a single line of diagnosis.

    Args:
        exc: The failure to describe.

    Returns:
        The message on one line. A server's error body can carry newlines, and
        the output of a multi-file run is read a line at a time.
    """
    # strerror is the part of an OSError that is not the filename, which the
    # caller has already put in the message. Pillow's own errors (a file that
    # is not an image) carry no strerror and are reported whole.
    detail = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
    return " ".join(detail.split())


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        A parser for the documented flags, with defaults mirroring the
        library's own — except `--model` and `--host`, which default to nothing
        so that `Describer` resolves the environment as it does for any caller.
    """
    parser = argparse.ArgumentParser(
        prog="describe-it",
        description=(
            "Write alt text for image files, using a vision model served by a "
            "local Ollama."
        ),
    )
    parser.add_argument(
        "--model",
        type=_model,
        metavar="TAG",
        help=(
            f"Ollama model tag; must be vision-capable. "
            f"Default: $DESCRIBE_IT_MODEL, else {DEFAULT_MODEL}."
        ),
    )
    parser.add_argument(
        "--host",
        type=_host,
        metavar="URL",
        help=f"Ollama server. Default: $OLLAMA_HOST, else {DEFAULT_HOST}.",
    )
    parser.add_argument(
        "--timeout",
        type=_seconds,
        default=_DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help="Per-request socket timeout. Default: %(default)s.",
    )
    parser.add_argument(
        "--context",
        metavar="TEXT",
        help=(
            "Where the images appear, in free text ('product photo on a shoe "
            "listing'). Good alt text depends on context."
        ),
    )
    parser.add_argument(
        "--language",
        default=_DEFAULT_LANGUAGE,
        metavar="NAME",
        help="Output language, as a plain English name. Default: %(default)s.",
    )
    parser.add_argument(
        "--max-words",
        type=_positive_int,
        default=_DEFAULT_MAX_WORDS,
        metavar="N",
        help=(
            "Requested upper bound on length, asked of the model rather than "
            "enforced by truncation. Default: %(default)s."
        ),
    )
    parser.add_argument(
        "--max-image-size",
        type=_positive_int,
        default=_DEFAULT_MAX_IMAGE_SIZE,
        metavar="PX",
        # The library also accepts None here, meaning "send it at full size".
        # The flag does not: a pixel count is what a command line can express
        # unambiguously, and full-size upload is a programmatic choice.
        help="Longest edge to downscale to before upload. Default: %(default)s.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"describe-it {__version__}",
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        metavar="FILE",
        help="Image files to describe.",
    )
    return parser


def _model(text: str) -> str:
    """Parse a model tag.

    Args:
        text: The raw argument value.

    Returns:
        The tag, unchanged; `Describer` does its own stripping.

    Raises:
        argparse.ArgumentTypeError: If the tag is blank, which would otherwise
            reach the server and come back as a puzzling 404.
    """
    if not text.strip():
        raise argparse.ArgumentTypeError("model tag must not be blank")
    return text


def _host(text: str) -> str:
    """Parse and validate a host.

    Args:
        text: The raw argument value: a base URL, or a bare `host:port`.

    Returns:
        The host as written. Normalisation is left to the client, so that its
        error messages, and any log of the resolved host, quote what the caller
        actually typed.

    Raises:
        argparse.ArgumentTypeError: If it is not something requests can be
            built on.
    """
    try:
        normalise_host(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return text


def _positive_int(text: str) -> int:
    """Parse a count that has to be at least 1.

    Args:
        text: The raw argument value.

    Returns:
        The parsed count.

    Raises:
        argparse.ArgumentTypeError: If it is not a whole number, or is below 1.
    """
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from exc
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, not {value}")
    return value


def _seconds(text: str) -> float:
    """Parse a timeout in seconds.

    Args:
        text: The raw argument value.

    Returns:
        The parsed number of seconds.

    Raises:
        argparse.ArgumentTypeError: If it is not a number, or is not positive
            and finite — `inf` is a hang with extra steps, and `nan` loses
            every comparison the socket layer would make against it.
    """
    try:
        value = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a number of seconds"
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive, finite number of seconds, not {text!r}"
        )
    return value


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
