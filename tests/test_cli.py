"""Tests for `describe_it.cli`.

The CLI is driven the way a shell drives it — `main(argv)`, output read off
stdout and stderr — against the same fake Ollama the client and describer tests
use, so a run here is the real pipeline minus the model. What is checked is the
CLI's own contract: which stream each thing lands on, the exit status, that one
bad file does not stop the rest, and that every option actually reaches the
request.

Every test runs with `$OLLAMA_HOST` and `$DESCRIBE_IT_MODEL` removed, so that a
developer machine configured for a real Ollama cannot change what the suite
does — or, worse, let a test pass by talking to a live server.
"""

import base64
import errno
import importlib.metadata
import inspect
import io
import os
import sys
import warnings
from pathlib import Path

import pytest
from PIL import Image, ImageFile

from conftest import FakeOllama
from describe_it import cli
from describe_it.config import HOST_ENV_VAR, MODEL_ENV_VAR
from describe_it.describer import Describer


@pytest.fixture(autouse=True)
def _hide_ambient_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the machine's own Ollama configuration for the whole module."""
    monkeypatch.delenv(HOST_ENV_VAR, raising=False)
    monkeypatch.delenv(MODEL_ENV_VAR, raising=False)


def _image_file(directory: Path, name: str = "image.png", size: int = 32) -> Path:
    """Write a small PNG and return its path."""
    path = directory / name
    Image.new("RGB", (size, size * 3 // 4), (200, 30, 30)).save(path)
    return path


def _animation_file(directory: Path, name: str = "frames.gif") -> Path:
    """Write a small multi-frame GIF and return its path.

    Multi-frame because Pillow keeps the file open for a format it may have to
    seek in, which is what makes a leaked handle observable.
    """
    path = directory / name
    frames = [Image.new("RGB", (32, 24), (60 * step, 30, 30)) for step in range(3)]
    frames[0].save(path, save_all=True, append_images=frames[1:])
    return path


def _reply(content: str) -> dict[str, object]:
    """Return an /api/chat body carrying one assistant message."""
    return {"message": {"role": "assistant", "content": content}}


class _RecordingWriter(io.StringIO):
    """A stdout stand-in that records writes and flushes in the order they came."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    def write(self, text: str) -> int:
        self.events.append(text)
        return len(text)

    def flush(self) -> None:
        self.events.append("<flush>")


class _BrokenPipeWriter(io.TextIOBase):
    """A stdout stand-in that behaves like a pipe whose reader has gone away."""

    def __init__(self, fd: int) -> None:
        # A real descriptor, so that the handler's dup2 has somewhere to point
        # that is not the test runner's own stdout.
        self._fd = fd

    def write(self, text: str) -> int:
        raise BrokenPipeError(errno.EPIPE, os.strerror(errno.EPIPE))

    def fileno(self) -> int:
        return self._fd


def test_one_file_prints_the_description_alone(
    server: FakeOllama, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server.script_json("/api/chat", _reply("A red rectangle."))
    path = _image_file(tmp_path)

    status = cli.main(["--host", server.url, str(path)])

    captured = capsys.readouterr()
    assert status == 0
    # No path prefix: the single-file case is meant to be pipeable as it is.
    assert captured.out == "A red rectangle.\n"
    assert captured.err == ""


def test_several_files_are_prefixed_with_their_paths(
    server: FakeOllama, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server.script_json("/api/chat", _reply("A red rectangle."))
    server.script_json("/api/chat", _reply("Another red rectangle."))
    first = _image_file(tmp_path, "first.png")
    second = _image_file(tmp_path, "second.png")

    status = cli.main(["--host", server.url, str(first), str(second)])

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out == (
        f"{first}\tA red rectangle.\n{second}\tAnother red rectangle.\n"
    )


def test_a_missing_file_is_reported_and_the_others_are_still_described(
    server: FakeOllama, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server.script_json("/api/chat", _reply("A red rectangle."))
    missing = tmp_path / "missing.png"
    present = _image_file(tmp_path)

    # The missing file comes first, so that continuing past it is what the
    # second line of output proves.
    status = cli.main(["--host", server.url, str(missing), str(present)])

    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == f"{present}\tA red rectangle.\n"
    # os.strerror rather than the English text: the C library localises it.
    reason = os.strerror(errno.ENOENT)
    assert captured.err == f"describe-it: {missing}: {reason}\n"


def test_a_file_that_is_not_an_image_never_reaches_the_server(
    server: FakeOllama, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("this is not a PNG")

    status = cli.main(["--host", server.url, str(path)])

    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    # Pillow's own error, which carries no errno to report in its place — and
    # whose message ends with the path the line already starts with, trimmed.
    assert captured.err == f"describe-it: {path}: cannot identify image file\n"
    assert server.requests == []


def test_an_image_that_trips_pillows_bomb_guard_is_reported(
    server: FakeOllama,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The guard is not an OSError and fires from Image.open, so it is the one
    # failure that would reach the user as a traceback if it were not named.
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    path = _image_file(tmp_path)

    status = cli.main(["--host", server.url, str(path)])

    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert captured.err.startswith(f"describe-it: {path}: ")
    assert "decompression bomb" in captured.err
    assert server.requests == []


def test_a_server_error_is_one_line_on_stderr(
    server: FakeOllama, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A multi-line body, because an error page from a misconfigured host is
    # exactly what arrives here and the output has to stay one line per file.
    server.script("/api/chat", status=500, body=b"failed to load model\nout of memory")
    path = _image_file(tmp_path)

    status = cli.main(["--host", server.url, str(path)])

    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert captured.err.startswith(f"describe-it: {path}: ")
    assert "HTTP 500" in captured.err
    assert "failed to load model out of memory" in captured.err


def test_the_model_and_host_options_reach_the_request(
    server: FakeOllama, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server.script_json("/api/chat", _reply("A red rectangle."))
    path = _image_file(tmp_path)

    status = cli.main(["--model", "llava:7b", "--host", server.url, str(path)])

    assert status == 0
    assert capsys.readouterr().out == "A red rectangle.\n"
    # The host reached the request by definition: this fake server recorded it.
    request = server.requests[0]
    assert request.path == "/api/chat"
    assert request.json_body["model"] == "llava:7b"


def test_the_environment_supplies_the_model_and_the_host(
    server: FakeOllama,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The CLI passes None through for both, so the environment stays as
    # meaningful for `describe-it` as it is for a caller of the library.
    monkeypatch.setenv(HOST_ENV_VAR, server.url)
    monkeypatch.setenv(MODEL_ENV_VAR, "gemma4:e4b")
    server.script_json("/api/chat", _reply("A red rectangle."))

    status = cli.main([str(_image_file(tmp_path))])

    assert status == 0
    assert capsys.readouterr().out == "A red rectangle.\n"
    assert server.requests[0].json_body["model"] == "gemma4:e4b"


def test_the_prompt_options_reach_the_request(
    server: FakeOllama, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    server.script_json("/api/chat", _reply("Un rectangle rouge."))
    path = _image_file(tmp_path, size=64)

    status = cli.main(
        [
            "--host",
            server.url,
            "--language",
            "French",
            "--max-words",
            "8",
            "--context",
            "gallery thumbnail",
            "--max-image-size",
            "16",
            str(path),
        ]
    )

    assert status == 0
    assert capsys.readouterr().out == "Un rectangle rouge.\n"
    body = server.requests[0].json_body
    assert body["options"] == {"temperature": 0.2, "num_predict": 8 * 4 + 32}
    message = body["messages"][0]
    assert "at most 8 words" in message["content"]
    assert "write in French" in message["content"]
    assert "gallery thumbnail" in message["content"]
    sent = Image.open(io.BytesIO(base64.b64decode(message["images"][0])))
    assert sent.size == (16, 12)


def test_the_timeout_option_reaches_the_transport(
    server: FakeOllama, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Asserted through a server that answers too slowly rather than by reading
    # the number back off the describer: the number only matters because a
    # socket eventually acts on it.
    server.script_json("/api/chat", _reply("A red rectangle."), delay=0.5)
    path = _image_file(tmp_path)

    status = cli.main(["--host", server.url, "--timeout", "0.05", str(path)])

    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert "timed out after 0.05s" in captured.err


def test_every_file_handle_is_released_before_the_run_ends(
    server: FakeOllama,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A run over a directory of images would exhaust the process's descriptors
    # if the CLI leaked one per image, and nothing else in the suite would
    # notice: leaked handles are only fatal at scale.
    #
    # Two details make this test able to fail. It uses a multi-frame GIF,
    # because Pillow releases the handle by itself for a single-frame PNG and
    # keeps it for a format it may have to seek in; and it asserts on the
    # underlying file object, captured at open time, because `image.fp` is set
    # to None by `load()` whether or not the file was ever closed.
    handles: list[object] = []
    real_open = Image.open

    def recording_open(path: Path) -> ImageFile.ImageFile:
        image = real_open(path)
        # Private, and deliberately so: it is the only reference that survives
        # the close and still reports whether the descriptor was released.
        handles.append(image._fp)
        return image

    monkeypatch.setattr(Image, "open", recording_open)
    server.script_json("/api/chat", _reply("Three red frames."))

    assert cli.main(["--host", server.url, str(_animation_file(tmp_path))]) == 0

    capsys.readouterr()
    assert handles, "Image.open was never called"
    for handle in handles:
        assert isinstance(handle, io.IOBase)
        assert handle.closed


def test_one_describer_serves_the_whole_run(
    server: FakeOllama,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Ollama keeps a model resident between requests; a describer per file
    # would throw that away, and every other assertion in this file would still
    # pass. The recorder spells out what the CLI passes, so a new option that
    # is parsed but never forwarded fails here too.
    built: list[Describer] = []

    def recording_describer(
        *,
        model: str | None,
        host: str | None,
        timeout: float,
        language: str,
        max_words: int,
        max_image_size: int | None,
    ) -> Describer:
        describer = Describer(
            model=model,
            host=host,
            timeout=timeout,
            language=language,
            max_words=max_words,
            max_image_size=max_image_size,
        )
        built.append(describer)
        return describer

    monkeypatch.setattr(cli, "Describer", recording_describer)
    server.script_json("/api/chat", _reply("A red rectangle."))
    server.script_json("/api/chat", _reply("Another red rectangle."))
    first = _image_file(tmp_path, "first.png")
    second = _image_file(tmp_path, "second.png")

    assert cli.main(["--host", server.url, str(first), str(second)]) == 0

    capsys.readouterr()
    assert len(built) == 1
    assert len(server.requests) == 2


def test_a_path_with_control_characters_still_produces_one_line_each(
    server: FakeOllama, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # POSIX filenames may contain tabs and newlines. Unescaped, the first would
    # forge a column and the second would split one record into two, which is
    # how a batch consumer silently loses or mispairs a description.
    server.script_json("/api/chat", _reply("A red rectangle."))
    awkward = _image_file(tmp_path, "two\tcolumns\nand a line.png")
    missing = tmp_path / "gone\there.png"

    status = cli.main(["--host", server.url, str(awkward), str(missing)])

    captured = capsys.readouterr()
    assert status == 1
    escaped = f"{tmp_path}/two\\tcolumns\\nand a line.png"
    assert captured.out == f"{escaped}\tA red rectangle.\n"
    assert captured.out.count("\n") == 1
    assert captured.err.startswith(f"describe-it: {tmp_path}/gone\\there.png: ")
    assert captured.err.count("\n") == 1


def test_each_description_is_flushed_as_it_is_produced(
    server: FakeOllama, tmp_path: Path
) -> None:
    # A batch of a thousand images is minutes of silence if stdout is only
    # flushed at exit, and a reader on the other end of a pipe cannot tell that
    # from a hung run.
    server.script_json("/api/chat", _reply("A red rectangle."))
    server.script_json("/api/chat", _reply("Another red rectangle."))
    writer = _RecordingWriter()
    first = _image_file(tmp_path, "first.png")
    second = _image_file(tmp_path, "second.png")

    status = cli.describe_files(
        Describer(model="llava:7b", host=server.url),
        [first, second],
        context=None,
        stdout=writer,
        stderr=io.StringIO(),
    )

    assert status == 0
    assert writer.events == [
        f"{first}\tA red rectangle.",
        "\n",
        "<flush>",
        f"{second}\tAnother red rectangle.",
        "\n",
        "<flush>",
    ]


def test_a_closed_output_pipe_ends_the_run_without_a_traceback(
    server: FakeOllama, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `describe-it *.png | head` closes the pipe under a run that is still
    # writing. Unhandled, that is a traceback plus an "Exception ignored" from
    # the interpreter's own final flush.
    server.script_json("/api/chat", _reply("A red rectangle."))
    sink = os.open(tmp_path / "sink", os.O_WRONLY | os.O_CREAT)
    monkeypatch.setattr(sys, "stdout", _BrokenPipeWriter(sink))

    try:
        status = cli.main(["--host", server.url, str(_image_file(tmp_path))])
    finally:
        os.close(sink)

    assert status == 1


def test_an_image_near_pillows_pixel_limit_is_described_without_a_warning(
    server: FakeOllama,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Over the limit but under twice it, Pillow decodes the image and warns.
    # The image is describable, so the run succeeds; the warning is suppressed
    # because two more lines on stderr would break one-line-per-file.
    server.script_json("/api/chat", _reply("A red rectangle."))
    path = _image_file(tmp_path)
    pixels = Image.open(path).size[0] * Image.open(path).size[1]
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", pixels - 1)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        status = cli.main(["--host", server.url, str(path)])

    captured = capsys.readouterr()
    assert status == 0
    assert captured.out == "A red rectangle.\n"
    assert captured.err == ""
    assert not [
        record
        for record in caught
        if issubclass(record.category, Image.DecompressionBombWarning)
    ]


def test_the_version_option_prints_the_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])

    assert exit_info.value.code == 0
    version = importlib.metadata.version("describe-it")
    # The exact line, not just the number: the program has to name itself, or
    # a version scraped from a pipeline says nothing about what produced it.
    assert capsys.readouterr().out == f"describe-it {version}\n"


def test_the_parser_defaults_mirror_the_library_defaults() -> None:
    # The CLI has to write its defaults out as literals for the help text; this
    # is what keeps the copy honest when a library default changes.
    defaults = cli._build_parser().parse_args(["image.png"])
    signature = inspect.signature(Describer.__init__)

    for name in ("timeout", "language", "max_words", "max_image_size"):
        assert getattr(defaults, name) == signature.parameters[name].default
    # Not copied but deferred: None is what makes the describer read the
    # environment, so the CLI and a plain `describe()` call resolve alike.
    assert defaults.model is None
    assert defaults.host is None
    assert defaults.context is None
    assert defaults.files == [Path("image.png")]


@pytest.mark.parametrize("option", ["--max-words", "--max-image-size"])
def test_a_count_below_one_is_a_usage_error(
    option: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([option, "0", str(_image_file(tmp_path))])

    assert exit_info.value.code == 2
    assert "at least 1" in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--max-words", "--max-image-size"])
# " 5 " and "1_0" are accepted by int() and mean 5 and 10; on a command line
# both are typos, so the parser is stricter than the constructor it feeds.
@pytest.mark.parametrize("value", ["lots", " 5 ", "1_0", "5.0", ""])
def test_a_count_that_is_not_a_plain_number_is_a_usage_error(
    option: str, value: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([option, value, str(_image_file(tmp_path))])

    assert exit_info.value.code == 2
    assert "not a whole number" in capsys.readouterr().err


# "1e400" parses as a float and overflows to infinity, which is the one way an
# infinite timeout can still reach the range check.
@pytest.mark.parametrize("value", ["0", "-1", "1e400"])
def test_a_timeout_that_is_not_positive_and_finite_is_a_usage_error(
    value: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--timeout", value, str(_image_file(tmp_path))])

    assert exit_info.value.code == 2
    assert "positive, finite" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["soon", "inf", "nan", " 5 ", "1_0", ""])
def test_a_timeout_that_is_not_a_plain_number_is_a_usage_error(
    value: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--timeout", value, str(_image_file(tmp_path))])

    assert exit_info.value.code == 2
    assert "not a number of seconds" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ftp://localhost:11434", "must use http or https"),
        ("http://localhost:11434/?", "query string or fragment"),
        ("", "no hostname"),
    ],
)
def test_an_unusable_host_is_a_usage_error(
    value: str, expected: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Validated in the parser rather than left to the client, so a mistyped
    # host is a usage message rather than a traceback out of a constructor.
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--host", value, str(_image_file(tmp_path))])

    assert exit_info.value.code == 2
    assert expected in capsys.readouterr().err


def test_an_unusable_host_in_the_environment_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # $OLLAMA_HOST does not pass through a `type` function, so without the
    # guard in main() a misconfigured machine gets a traceback rather than the
    # usage message a mistyped --host produces.
    monkeypatch.setenv(HOST_ENV_VAR, "ftp://nowhere:1")

    with pytest.raises(SystemExit) as exit_info:
        cli.main([str(_image_file(tmp_path))])

    assert exit_info.value.code == 2
    assert "must use http or https" in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--model", "--language"])
@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_model_or_language_is_a_usage_error(
    option: str, value: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A blank model reaches the server as a puzzling 404; a blank language asks
    # the model to write in nothing at all.
    with pytest.raises(SystemExit) as exit_info:
        cli.main([option, value, str(_image_file(tmp_path))])

    assert exit_info.value.code == 2
    assert "must not be blank" in capsys.readouterr().err


def test_at_least_one_file_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])

    assert exit_info.value.code == 2
    assert "FILE" in capsys.readouterr().err
