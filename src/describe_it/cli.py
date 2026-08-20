"""Command-line front end for the `describe-it` console script.

A thin wrapper over `Describer`, kept mainly as a manual-testing aid: it is the
shortest way to point the library at a real model and see what comes back.
Everything that is not argument parsing lives in `describe_files`, which is
handed its output streams instead of reaching for the process's own, so the
tests can drive a whole run and read exactly what it wrote.

The output contract is one line per file, on stdout for a description and on
stderr for a failure, so that a run can be piped into anything that reads
lines. Three things defend it: whitespace in a diagnostic is collapsed, every
control character in a description, a path or an error message is escaped, and
Pillow's decompression-bomb *warning* is suppressed (its error is still
reported, as a normal failure line).

A stream can also be missing outright — `describe-it a.png >&-` leaves
`sys.stdout` as `None` — which is treated as absent rather than as an error:
descriptions have nowhere to go, so the run exits 1 without doing the work,
and diagnostics with nowhere to go are dropped.

Option values are validated by argparse `type` functions rather than left to
`Describer`, because a typo in a flag deserves a usage message and exit status
2, not a traceback out of a constructor. The two environment variables reach
the describer without passing through one, so `main` turns the same `ValueError`
into the same usage error. Nothing here runs at import time; the parser is
built inside `main`.
"""

import argparse
import math
import os
import re
import sys
import warnings
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

# `int()` accepts surrounding whitespace and digit separators, so " 5 " and
# "1_0" would silently become 5 and 10. On a command line both are typos, never
# intentions, and the same goes for float()'s "inf" and "nan" spellings.
_INTEGER_RE = re.compile(r"[+-]?[0-9]+")
_DECIMAL_RE = re.compile(r"[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?")

# Everything a terminal or a line-oriented reader would rather not meet in the
# middle of a record: the C0 controls (which include the three line endings a
# universal-newline reader splits on, and ESC, which starts a terminal escape
# sequence), DEL, and the C1 range.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# The three with a conventional spelling; everything else becomes \xNN.
_NAMED_ESCAPES = {"\t": r"\t", "\n": r"\n", "\r": r"\r"}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the describe-it command line.

    Args:
        argv: Arguments to parse, without the program name. `None` reads
            `sys.argv`, which is what the console script wants.

    Returns:
        0 if every file was described, 1 if any of them failed or if the output
        pipe was closed early.

    Raises:
        SystemExit: For `--help`, `--version` and usage errors, which argparse
            reports and exits on itself, with status 0, 0 and 2 respectively.
            An unusable `$OLLAMA_HOST` or `$DESCRIBE_IT_MODEL` is reported the
            same way as a bad flag, and exits 2 as well.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    # One describer for the whole run: it owns the transport, and Ollama keeps
    # a model resident between requests, so building one per file would risk
    # paying for a model load per image.
    try:
        describer = Describer(
            model=args.model,
            host=args.host,
            timeout=args.timeout,
            language=args.language,
            max_words=args.max_words,
            max_image_size=args.max_image_size,
        )
    except ValueError as exc:
        # The flags are validated by their `type` functions, but $OLLAMA_HOST
        # and $DESCRIBE_IT_MODEL reach the describer without passing through
        # one. A misconfigured environment is still a configuration mistake and
        # deserves the same usage message, not a traceback.
        parser.error(str(exc))

    try:
        status = describe_files(
            describer,
            args.files,
            context=args.context,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        # Flushed here, not left to the interpreter's flush on the way out:
        # there, a broken pipe is unhandleable and prints "Exception ignored".
        # stdout's failure is the run's failure; stderr's is not, so it is only
        # tidied away.
        _flush(sys.stdout)
        _quiet_flush(sys.stderr)
        return status
    except _PipeGoneError as gone:
        # `describe-it *.png | head` closes the pipe while the run is still
        # writing. Pointing the descriptor at /dev/null gives the interpreter's
        # own final flush somewhere harmless to go — the recipe from the Python
        # documentation. Only the stream that actually broke is redirected: a
        # run with `2>&1 >out.txt` has a healthy stdout that still owes its
        # buffer to the file.
        _silence(gone.stream)
        # Under `2>&1` both streams share the one pipe, so stderr is just as
        # gone, and its exit flush would fail where nothing can be done.
        _quiet_flush(sys.stderr)
        return 1


def describe_files(
    describer: Describer,
    paths: Sequence[Path],
    *,
    context: str | None,
    stdout: TextIO | None,
    stderr: TextIO | None,
) -> int:
    """Describe every file, reporting failures without abandoning the rest.

    A failed file is a diagnostic on stderr and a non-zero exit status at the
    end, never a stop: the common batch is a directory of images, and one
    unreadable file in it should not cost the caller the other nine hundred.

    Args:
        describer: The configured describer, shared by every file in the run.
        paths: The image files, in the order the caller gave them.
        context: Where the images appear, passed to the model verbatim.
        stdout: Where descriptions are written. `None` when the process was
            started without a usable stdout (`describe-it a.png >&-`), which
            Python reports by setting `sys.stdout` to `None`.
        stderr: Where failures are reported, or `None` as for `stdout`.

    Returns:
        0 if every file was described, 1 if any of them failed, and 1 without
        describing anything if there is no stdout to deliver descriptions to.

    Raises:
        _PipeGoneError: If the reader of either stream has gone away. Left to the
            caller, which has the process-level remedy.
    """
    if stdout is None:
        # No stdout, no product. Reporting that on stderr would be shouting
        # about a stream nobody asked for, so the status carries it alone.
        return 1
    # The path prefix is what makes a multi-file run parseable. A single file
    # is the interactive case, where the caller knows what they asked about and
    # wants the description alone — pipeable into anything.
    show_path = len(paths) > 1
    failed = False
    # Diagnostics are commentary, so losing their reader is not a reason to
    # stop producing descriptions that still have one: a run with
    # `2>&1 >out.txt | head` keeps filling out.txt. Losing stdout's reader is
    # different, and ends the run — see `_write`.
    stderr_gone = False
    for path in paths:
        try:
            description = describe_file(describer, path, context=context)
        except (OSError, Image.DecompressionBombError, DescribeItError) as exc:
            failed = True
            if not stderr_gone:
                try:
                    _write(
                        stderr, f"describe-it: {_escape(path)}: {_reason(exc, path)}"
                    )
                except _PipeGoneError as gone:
                    # gone.stream is the stream that actually raised, which is
                    # stderr here by construction and typed as present.
                    _silence(gone.stream)
                    stderr_gone = True
            continue
        # The description is the model's text, not ours: it reaches a terminal
        # and may carry anything the server put in it.
        text = _escape_controls(description)
        _write(stdout, f"{_escape(path)}\t{text}" if show_path else text)
    return 1 if failed else 0


def _write(stream: TextIO | None, line: str) -> None:
    """Write one line to one stream, and say which stream if the reader is gone.

    Args:
        stream: Where to write, or `None` if the process has no such stream, in
            which case the line is dropped. The stream is always named
            explicitly: `print(file=None)` would fall back to `sys.stdout` and
            put diagnostics in with the descriptions.
        line: The line, without its terminator.

    Raises:
        _PipeGoneError: If the reader has closed the pipe. Which stream broke is
            part of the diagnosis, and `BrokenPipeError` does not carry it.
    """
    if stream is None:
        return
    try:
        # Flushed per line so a reader on the far end of a pipe sees each
        # description as it is produced rather than when the run ends — a batch
        # of a thousand images is minutes of silence otherwise. It also keeps
        # stdout and stderr in the order they were written.
        print(line, file=stream, flush=True)
    except BrokenPipeError as exc:
        raise _PipeGoneError(stream) from exc


def _flush(stream: TextIO | None) -> None:
    """Flush one stream, and say which stream if the reader is gone.

    Args:
        stream: The stream to flush, or `None` if the process has no such
            stream, which leaves nothing to flush.

    Raises:
        _PipeGoneError: If the reader has closed the pipe.
    """
    if stream is None:
        return
    try:
        stream.flush()
    except BrokenPipeError as exc:
        raise _PipeGoneError(stream) from exc


def _quiet_flush(stream: TextIO | None) -> None:
    """Flush one stream, redirecting it instead of failing if its reader is gone.

    Args:
        stream: The stream to flush, or `None` if the process has no such
            stream. Used for stderr, whose reader going away costs the run
            nothing but a place to put its commentary.
    """
    if stream is None:
        return
    try:
        stream.flush()
    except BrokenPipeError:
        _silence(stream)


def _silence(stream: TextIO) -> None:
    """Point one stream's file descriptor at /dev/null.

    Args:
        stream: The stream whose reader has gone away. Its descriptor is
            replaced, so that anything still owed to it — including the
            interpreter's own flush at exit — is written somewhere that cannot
            fail.
    """
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, stream.fileno())
    os.close(devnull)


class _PipeGoneError(Exception):
    """Internal: the reader of one of this run's output streams has gone away.

    Attributes:
        stream: The stream whose write or flush failed, so that only that one
            is redirected.
    """

    def __init__(self, stream: TextIO) -> None:
        """Record which stream was lost.

        Args:
            stream: The stream whose reader closed the pipe.
        """
        super().__init__("the reader of an output stream has gone away")
        self.stream = stream


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
        PIL.Image.DecompressionBombError: If the image is large enough to trip
            Pillow's hard bomb guard, which is checked as the file is opened.
        DescribeItError: If the image cannot be prepared, the server cannot be
            reached, or no usable description came back.
    """
    with warnings.catch_warnings():
        # An image over Pillow's pixel limit but under twice it decodes fine
        # and only warns. The warning would reach stderr as two more lines and
        # break the one-line-per-file contract, and it is not a failure — the
        # image is described. The hard guard above twice the limit still raises
        # and is still reported as a failure.
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        # The `with` is load-bearing: Image.open holds the file open until the
        # image is closed, and a run over a few thousand files would otherwise
        # exhaust the process's descriptors long before it finished.
        with Image.open(path) as image:
            return describer.describe(image, context=context)


def _escape(path: Path) -> str:
    """Render a path so that it cannot break the one-line-per-file contract.

    Args:
        path: The path as the caller wrote it.

    Returns:
        The path with backslashes and every control character escaped.

    A POSIX filename may hold any byte but `/` and NUL, so a tab can forge a
    column, any of CR, LF and the rest of the C0 range can split one record in
    two for a universal-newline reader, and an ESC can carry a terminal escape
    sequence into whatever prints the output.
    """
    # Backslash first, or the escapes introduced below would be escaped again.
    return _escape_controls(str(path).replace("\\", "\\\\"))


def _escape_controls(text: str) -> str:
    """Replace every control character in `text` with a printable escape.

    Args:
        text: The text to make safe for one line of output.

    Returns:
        The text with C0, DEL and C1 characters spelled out.
    """
    return _CONTROL_RE.sub(
        lambda match: _NAMED_ESCAPES.get(match.group(), f"\\x{ord(match.group()):02x}"),
        text,
    )


def _reason(exc: Exception, path: Path) -> str:
    """Render one failure as a single line of diagnosis.

    Args:
        exc: The failure to describe.
        path: The file it happened on, which the caller has already named.

    Returns:
        The message on one line. A server's error body can carry newlines, and
        the output of a multi-file run is read a line at a time.
    """
    # strerror is the part of an OSError that is not the filename, which the
    # caller has already put in the message. Pillow's own errors (a file that
    # is not an image) carry no strerror and are reported whole.
    detail = exc.strerror if isinstance(exc, OSError) and exc.strerror else str(exc)
    # Pillow ends "cannot identify image file '<path>'" with the path it was
    # given, which the line already starts with. Trimmed only when it really is
    # a repeat, so a message that merely mentions another path keeps it.
    repeated = f" {str(path)!r}"
    if detail.endswith(repeated):
        detail = detail[: -len(repeated)]
    # Collapsing whitespace deals with the newlines and tabs; the escape pass
    # deals with the rest, because this text can come from a server's error
    # body and ESC is not whitespace.
    return _escape_controls(" ".join(detail.split()))


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
        type=_nonblank,
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
        help="Per-read socket timeout. Default: %(default)s.",
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
        type=_nonblank,
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


def _nonblank(text: str) -> str:
    """Parse a value that must say something.

    Args:
        text: The raw argument value.

    Returns:
        The value, unchanged; the library does its own stripping.

    Raises:
        argparse.ArgumentTypeError: If it is blank. A blank model tag would
            reach the server and come back as a puzzling 404; a blank language
            would ask the model to write in nothing at all.
    """
    if not text.strip():
        raise argparse.ArgumentTypeError("must not be blank")
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
        argparse.ArgumentTypeError: If it is not a plain whole number, or is
            below 1.
    """
    if not _INTEGER_RE.fullmatch(text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number")
    value = int(text)
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
        argparse.ArgumentTypeError: If it is not a plain decimal number, or is
            not positive and finite — "1e400" overflows to infinity, which is a
            hang with extra steps, and the spellings of infinity and nan are
            rejected outright as numbers of seconds.
    """
    if not _DECIMAL_RE.fullmatch(text):
        raise argparse.ArgumentTypeError(f"{text!r} is not a number of seconds")
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive, finite number of seconds, not {text!r}"
        )
    return value


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
