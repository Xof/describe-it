# describe-it

Alt-text descriptions for PIL images, from a vision model running on your own
machine.

## What it is

`describe-it` takes a `PIL.Image.Image` and returns a short, alt-text-quality
description of it as a plain string, by asking a vision model served by
[Ollama](https://ollama.com) — on your own machine by default, and no third
party at any point. That matters for two reasons: generating alt text at volume
is cheap locally and expensive through a commercial API, and the images may be
NSFW, which hosted services refuse or penalise — a local model has no policy
layer beyond whatever is in its weights.

The contract is deliberately small: **image in, description out, every failure
is an exception.** No result objects, no status codes, no `None` returns.

See [ARCHITECTURE.md](ARCHITECTURE.md) for how it is put together and
[docs/adr/](docs/adr/) for why.

## Requirements

- Python 3.12 or newer.
- A running Ollama with a vision-capable model pulled:

  ```console
  $ ollama pull qwen3.5:4b
  ```

Pillow is the only runtime dependency; the Ollama transport is `urllib` from
the standard library.

## Install

Not published to PyPI yet, so install it from a checkout:

```console
$ git clone https://github.com/Xof/describe-it
$ cd describe-it
$ uv sync
```

Once it is published, `uv add describe-it` or `pip install describe-it` will be
the whole story.

## Usage

The zero-configuration call:

```python
from PIL import Image
from describe_it import describe

alt = describe(Image.open("cat.jpg"))
# -> "A grey tabby cat asleep on a red armchair beside a sunlit window."
```

Any mode Pillow can open works — `RGBA`, palette GIFs, `CMYK` TIFFs, 16-bit
scientific images, sideways phone photographs. You never have to convert first,
and your image object is not modified.

For a batch, configure once and keep the model resident:

```python
from describe_it import Describer

d = Describer(model="gemma4:e4b", max_words=20, keep_alive="1h")
d.ensure_model()  # downloads the model if it is missing (explicit, opt-in)
for img in images:
    alt = d.describe(img, context="gallery thumbnail")
```

Every failure is an exception, and they all share a base class:

```python
from describe_it import DescribeItError, DescriptionRefusedError

try:
    alt = describe(image)
except DescriptionRefusedError as exc:
    # The model declined. Its actual words are on the exception.
    print(f"refused: {exc.response}")
except DescribeItError as exc:
    print(f"no description: {exc}")
```

### Options

Both `describe()` and `Describer()` take these; `context` and `prompt` are
per-call, so they are arguments to `Describer.describe()` rather than to its
constructor.

| Option | Default | Meaning |
|---|---|---|
| `model` | `$DESCRIBE_IT_MODEL`, else `"qwen3.5:4b"` | Ollama model tag. Must be vision-capable. Blank is a `ValueError`. |
| `host` | `$OLLAMA_HOST`, else `"http://localhost:11434"` | Base URL, or a bare `host:port` as Ollama's own CLI accepts. A scheme-less host gets `http://` and, if it names no port, 11434; a host written with a scheme is used as written. Credentials, a query string or a fragment are a `ValueError`. |
| `timeout` | `120.0` | Per-read socket timeout in seconds. Generous because a cold model load can take tens of seconds. Must be positive and finite. |
| `context` | `None` | Free text about where the image appears ("product photo on a shoe listing"). Good alt text depends on context; this reaches the model verbatim. |
| `language` | `"English"` | Output language, as a plain English name the model will understand. |
| `max_words` | `30` | Requested upper bound on length. Asked of the model in the prompt, never enforced by truncation — the library will not cut a sentence in half. |
| `max_image_size` | `1024` | Longest edge in pixels to downscale to before upload. `None` sends the image at its original size. Never upscales. |
| `prompt` | `None` | A complete replacement for the built-in wording. When set, `context`, `language` and `max_words` no longer reach the model. The reply is still cleaned and still checked for a refusal. |
| `keep_alive` | `None` (Ollama's default, 5 minutes) | Passed through to Ollama: `"30m"` keeps the model resident across a batch, `0` unloads it as soon as the call finishes. |
| `client` | `None` | A ready-made `OllamaClient`, which replaces `host` and `timeout`. A test seam and an escape hatch, not part of the ordinary path. |

### Exceptions

Everything the library raises inherits from `DescribeItError`, so one `except`
clause catches all of it. A wrong-type argument is the deliberate exception: a
non-image raises plain `TypeError`, and out-of-range options raise `ValueError`.

```
DescribeItError
├── ImageError                  the image cannot be prepared (zero size, unloadable)
├── OllamaError                 anything involving the server
│   ├── OllamaConnectionError   cannot connect (refused, DNS, unreachable)
│   ├── OllamaTimeoutError      a read exceeded the timeout (a sibling, not a subclass)
│   ├── ModelNotFoundError      the server does not have the model; names the pull command
│   └── OllamaResponseError     any other non-2xx, or an unusable body; has .status_code and .body
└── DescriptionError            the server answered but produced no usable description
    └── DescriptionRefusedError the model refused; .response holds its actual words
```

### Environment variables

| Variable | Effect |
|---|---|
| `DESCRIBE_IT_MODEL` | The model tag to use when none is passed. |
| `OLLAMA_HOST` | The server to talk to when no `host` is passed — the same variable Ollama's own CLI reads, so a machine already configured for `ollama run` needs nothing else. |

Both are read when a describer is constructed — which is per call for
`describe()`, and once for a long-lived `Describer` — never at import time. A
blank value counts as unset.

### Command line

```
describe-it [--model TAG] [--host URL] [--timeout SECONDS] [--context TEXT]
            [--language NAME] [--max-words N] [--max-image-size PX]
            [--version] FILE [FILE ...]
```

One file prints the description alone; several print `<path>\t<description>`
per line. A file that cannot be read or described is reported on stderr and the
run continues, with exit status 1 at the end if anything failed.

```console
$ describe-it photo.jpg
A blue and yellow flag against a bright sky.

$ describe-it --max-words 15 photo.jpg diagram.png
photo.jpg	A blue and yellow flag against a bright sky.
diagram.png	A flow chart with three boxes joined by arrows.
```

### Choosing a model

Any vision-capable Ollama model works; the tag is passed straight through. The
default is `qwen3.5:4b` (~3 GB), chosen because the Qwen line refuses far less
often than the alternatives, which matters when the images are the sort a
hosted service would reject.

- `qwen3.5:2b` — the smaller model in the same family, for a constrained
  machine. Not benchmarked here.
- `gemma4:e4b` — better captions, more likely to refuse.
- `llava:7b` — older and widely available; in this project's live runs it
  ignored the `language` option and overran the word budget.

A model that declines to describe an image produces `DescriptionRefusedError`
rather than alt text, so a refusal-prone model turns into exceptions rather
than into garbage on your web pages.

## Development

```console
$ uv sync --locked --group dev
```

The four commands CI gates on, which should all be run before pushing:

```console
$ uv run ruff check .
$ uv run ruff format --check .
$ uv run mypy src tests
$ uv run pytest --cov --cov-report=term-missing -m "not integration"
```

The unit suite is hermetic — no Ollama, no model — and the coverage gate is
100%.

The tests under `tests/integration/` talk to a real server and a real model.
They are opt-in, skipped unless both the environment variable is set and the
host answers:

```console
$ DESCRIBE_IT_INTEGRATION=1 DESCRIBE_IT_MODEL=qwen3.5:4b \
      uv run pytest -m integration -v
```

Their assertions are structural (length, language, exception type), because a
small vision model's output is not deterministic enough to gate a merge on.

Design records live in [docs/adr/](docs/adr/) — one file per decision, with a
generated index. The design specification is under
[docs/superpowers/specs/](docs/superpowers/specs/).
