# describe-it — design specification

**Date:** 2026-08-19
**Status:** Accepted 2026-08-19 (open questions resolved in §8)

## 1. Purpose

`describe-it` is a small Python library that turns a `PIL.Image.Image` into a
short, alt-text-quality description by asking a *local* vision model served by
[Ollama](https://ollama.com). It exists because (a) alt-text generation at
volume is cheap locally and expensive via commercial APIs, and (b) the images
may be NSFW, which commercial services refuse or penalise. A local model has
no such policy layer beyond whatever is baked into the weights.

The API contract is deliberately minimal: **PIL image in, description string
out, every failure is an exception.** No result objects, no status codes, no
`None` returns.

## 2. Behavioral requirements

### 2.1 Core call

```python
from PIL import Image
from describe_it import describe

alt = describe(Image.open("cat.jpg"))
# -> "A grey tabby cat asleep on a red armchair beside a sunlit window."
```

`describe(image, **options) -> str`:

- Accepts any `PIL.Image.Image` regardless of mode (`RGB`, `RGBA`, `L`, `P`,
  `1`, `CMYK`, `I;16`, `LA`, animated first-frame, …). The caller never has to
  convert first.
- Returns a non-empty `str` containing the alt text only — no label, no
  surrounding quotes, no markdown, no trailing commentary, single line,
  internal whitespace collapsed.
- Is synchronous and blocking. (An async variant is a non-goal for v0.1.)
- Never mutates the caller's image object.

### 2.2 Options

Every option has a sensible default so the zero-config call works.

| Option           | Default                               | Meaning |
|------------------|---------------------------------------|---------|
| `model`          | `$DESCRIBE_IT_MODEL` or `"qwen3.5:4b"`          | Ollama model tag. Must be a vision-capable model. |
| `host`           | `$OLLAMA_HOST` or `"http://localhost:11434"` | Base URL of the Ollama server. A bare `host:port` (as Ollama's own CLI accepts) is normalised to `http://host:port`. |
| `timeout`        | `120.0` seconds                       | Per-request socket timeout. Cold model loads on a laptop can take tens of seconds, hence the generous default. |
| `context`        | `None`                                | Free-text hint about where the image appears ("product photo on a shoe listing", "diagram in a Postgres blog post"). Good alt text depends on context; this is passed to the model verbatim. |
| `language`       | `"English"`                           | Output language, as a plain English name the model will understand. |
| `max_words`      | `30`                                  | Target upper bound on length. Enforced by prompt, not by truncation — the library never cuts a sentence mid-way. (Conventional alt text is ≤ ~125 characters; 30 words is a slightly generous ceiling.) |
| `max_image_size` | `1024`                                | Longest edge in pixels the image is downscaled to before sending. `None` disables. Models resize internally anyway; this bounds upload size and latency. |
| `prompt`         | `None`                                | Full replacement for the built-in prompt. When set, `context`, `language`, and `max_words` are ignored (the caller owns the wording). Post-processing still applies. |
| `keep_alive`     | `None` (Ollama default, 5 min)        | Passed through to Ollama; lets a batch caller keep the model resident (`"30m"`) or unload immediately (`0`). |

### 2.3 `Describer` — configure once, call many

```python
from describe_it import Describer

d = Describer(model="gemma4:e4b", max_words=20, keep_alive="1h")
d.ensure_model()            # pulls the model if it is not present (explicit, opt-in)
for img in images:
    alt = d.describe(img, context="gallery thumbnail")
```

`Describer.__init__` takes the same keyword options as `describe()` except
`context` and `prompt`, which are per-call. `describe()` at module level is
sugar for `Describer(**opts).describe(image, context=..., prompt=...)`.

`Describer` also exposes:

- `ensure_model() -> None` — `POST /api/pull` for the configured model if
  `/api/show` reports it missing. Blocking; may download gigabytes. Never
  called implicitly — a describe call on a missing model raises
  `ModelNotFoundError` whose message says `ollama pull <model>`.
- `check() -> None` — raises `OllamaConnectionError` if the server is
  unreachable or `ModelNotFoundError` if the model is absent; returns
  silently otherwise. For startup health checks.

### 2.4 Image handling

Before upload the image is:

1. Converted to `RGB`. Alpha is flattened onto white (not black — most web
   images with transparency are meant to sit on light backgrounds, and black
   halos mislead the model). `P`/`PA`/`LA`/`1`/`L`/`CMYK`/`I`/`F`/`I;16`
   all go through `Image.convert`. For multi-frame images only the current
   frame is used.
2. Downscaled with `Image.thumbnail` (LANCZOS) so the longer edge is
   ≤ `max_image_size`. Never upscaled.
3. Encoded as JPEG, quality 90, then base64 for the JSON payload.

A zero-area image (`size == (0, 0)` in either dimension) raises `ImageError`
before any network activity. A closed/unloadable image (Pillow raises on
`.load()`) is wrapped in `ImageError` with the original as `__cause__`.
A non-`PIL.Image.Image` argument raises plain `TypeError` — that is the
standard Python contract for a wrong-type argument and callers expect it.

### 2.5 Prompt

Single `user` message (no `system` message — several small vision models
ignore or mishandle system prompts). Built-in wording, with `{max_words}`,
`{language}`, and an optional context sentence substituted:

> Write alt text for this image, for use on a web page.
> Requirements: at most {max_words} words; one or two plain sentences;
> describe what is visually present — the main subject, setting, and any
> action; quote any clearly legible text verbatim; do not begin with
> "image of", "picture of", or "photo of"; do not guess at anything not
> visible; no opinions, warnings, or commentary; write in {language}.
> [The image appears in this context: {context}.]
> Reply with the alt text only.

Request body (`POST /api/chat`):

```json
{"model": ..., "stream": false, "think": false, "keep_alive": ...,
 "messages": [{"role": "user", "content": <prompt>, "images": [<b64 jpeg>]}],
 "options": {"temperature": 0.2, "num_predict": <max_words * 4 + 32>}}
```

`think: false` stops thinking-capable models (qwen3.5, gemma4) from burning
seconds on hidden reasoning for a caption. **Implementation must verify**
that Ollama 0.32 accepts `think: false` for non-thinking models; if it
rejects it with a 400, the field is sent only after `/api/show` reports the
`thinking` capability. `num_predict` is a safety stop, not the length
control — the prompt is.

### 2.6 Response post-processing

Applied in order to `message.content`:

1. Remove any `<think>…</think>` block (belt and braces for models that emit
   one despite `think: false`).
2. Strip whitespace; strip a leading `Alt text:` / `Alt:` label
   (case-insensitive); strip one layer of matching surrounding quotes
   (`"…"`, `'…'`, `“…”`) and markdown emphasis/code fences.
3. Collapse all internal whitespace (including newlines) to single spaces.
4. If the result is empty → `DescriptionError("model returned no text")`.
5. If the result matches the refusal heuristic — case-insensitive match of
   `^(i'?m sorry|i cannot|i can'?t|i am unable|i'?m unable|i apologi[sz]e|as an ai)`
   at the very start of the cleaned text, subject to the guard in §8 Q4 —
   → `DescriptionRefusedError`, carrying the cleaned text as `.response`.
   This is a heuristic and is documented as such; it converts the common
   "I'm sorry, but I can't describe this image" failure into an exception
   instead of garbage alt text. Callers who want the text anyway can read it
   off the exception.
6. Return the string.

No sentence-level truncation is performed. If a model overruns `max_words`,
that is returned as-is; the caller asked for alt text, not for a truncated
fragment.

### 2.7 Exceptions

All inherit from `DescribeItError(Exception)`, so `except DescribeItError`
catches everything the library itself raises. Plain `TypeError` for a
non-image argument is the one deliberate exception to that rule.

```
DescribeItError
├── ImageError                 image cannot be prepared (zero size, unloadable)
├── OllamaError                anything involving the server
│   ├── OllamaConnectionError  cannot connect (refused, DNS, unreachable host)
│   ├── OllamaTimeoutError     socket timeout; a sibling of ConnectionError, not a subclass — a timeout usually means "model is slow", not "server is down"
│   ├── ModelNotFoundError     HTTP 404 whose body mentions the model; message includes `ollama pull <model>`
│   └── OllamaResponseError    any other non-2xx, or a 2xx whose body is not the expected JSON shape; `.status_code` and `.body` attributes
└── DescriptionError           server answered but produced no usable description
    └── DescriptionRefusedError  refusal heuristic fired; `.response` holds the cleaned text
```

Every wrapped lower-level exception (`urllib.error.URLError`,
`socket.timeout`, `json.JSONDecodeError`, Pillow errors) is chained via
`raise ... from exc` so the original is on `__cause__`.

### 2.8 CLI

The `uv init` scaffold registered a `describe-it` console script; it is kept
as a thin wrapper, mainly as a manual-testing aid:

```
describe-it [--model M] [--host URL] [--timeout S] [--context TEXT]
            [--language L] [--max-words N] [--max-image-size PX] FILE [FILE ...]
```

- One file: prints the description and a newline.
- Several files: prints `<path>\t<description>` per line.
- Any `DescribeItError` (or a file that Pillow cannot open): message on
  stderr, exit status 1. Processing continues to the next file; exit status
  is 1 if any file failed.
- `--version` prints the package version.

## 3. Non-goals (v0.1)

- Async API. (Trivial to add later as `adescribe` over `asyncio.to_thread`;
  not worth an `httpx` dependency now.)
- Streaming output.
- Batching several images into one model call.
- Any backend other than Ollama's native HTTP API (no OpenAI-compatible
  endpoint, no llama.cpp direct).
- Caching of results.
- Automatic model pulling inside `describe()`.
- Guaranteeing output length — the model is asked, not forced.
- Detecting whether a model is actually vision-capable before sending. Ollama
  returns a clear error for text-only models; it is surfaced as
  `OllamaResponseError`.

## 4. Interface changes

All new. Public names exported from `describe_it`:

`describe`, `Describer`, `DEFAULT_MODEL`, `DEFAULT_HOST`, and the nine
exception classes in §2.7. `__version__` is read from package metadata.

Console script `describe-it` (already declared in `pyproject.toml`; the
target moves from `describe_it:main` to `describe_it.cli:main`).

## 5. Implementation decisions

- **Transport: `urllib.request` from the standard library**, not the
  `ollama` PyPI client. The library makes three trivial JSON POSTs; the
  official client would add `httpx` + `pydantic` as transitive dependencies
  to a package whose only real dependency is Pillow. Test fidelity is also
  better: tests run against a real in-process `http.server`, exercising the
  actual urllib code path including 404 bodies, malformed JSON, and slow
  responses. Runtime dependency list: `pillow>=10`.
- **Package layout** (`src/describe_it/`):
  - `__init__.py` — public re-exports only.
  - `exceptions.py` — the hierarchy in §2.7.
  - `image.py` — `prepare_image(image, max_size) -> bytes` (pure, §2.4).
  - `prompt.py` — `build_prompt(...)` and `clean_response(text) -> str`
    (pure, §2.5–2.6, including refusal heuristic).
  - `client.py` — `OllamaClient`: `chat`, `show`, `pull`, error mapping.
    Knows nothing about images or prompts.
  - `describer.py` — `Describer`, `describe()`; composes the above.
  - `cli.py` — argparse entry point.
- **Python `>=3.12`** rather than the scaffold's `>=3.14`, so the
  library is usable on current-stable distros. CI matrix 3.12 / 3.13 / 3.14.
- **Tooling:** `uv` for env/lock/build (`uv_build` backend as scaffolded),
  `ruff` (lint + format), `mypy --strict`, `pytest` + `pytest-cov`.
  Coverage gate 100% on the unit suite, with `# pragma: no cover` permitted
  only for the integration-skip branch and `if __name__ == "__main__"`.
- **Determinism:** `temperature: 0.2`. Not 0 — a few small models degenerate
  into repetition at exactly 0.

## 6. Verification oracle

### 6.1 Unit tests (hermetic; run in CI; the coverage gate)

`tests/test_image.py`
- Each source mode in {`RGB`, `RGBA`, `LA`, `L`, `P`, `PA`, `1`, `CMYK`,
  `I;16`, `I`, `F`} produces valid JPEG bytes decodable by Pillow as `RGB`.
- RGBA with a fully transparent region → that region is white in the output
  (sample the pixel; tolerance for JPEG).
- 3000×1500 input, `max_image_size=1024` → output is 1024×512.
  800×600 input → stays 800×600 (no upscaling). `max_image_size=None` →
  unchanged at 3000×1500.
- Input image object is unchanged afterwards (mode, size, pixel sample).
- `(0, 0)`, `(0, 10)` images → `ImageError`. Non-image argument → `TypeError`.
- Multi-frame GIF → uses frame 0 without error.

`tests/test_prompt.py`
- `build_prompt` contains `max_words`, `language`; contains the context
  sentence iff `context` is given; an explicit `prompt=` is returned
  verbatim.
- `clean_response` table tests: `<think>` removal; leading `Alt text:`;
  surrounding `"…"`, `'…'`, `“…”`, backticks, `**…**`; newline + multi-space
  collapse; idempotence (`clean(clean(x)) == clean(x)` for every case).
- Empty / whitespace-only / think-block-only → `DescriptionError`.
- Each refusal prefix in the heuristic → `DescriptionRefusedError` with
  `.response` equal to the cleaned text. Guard cases (the guard is an OR:
  raise if shorter than 200 chars OR no "." before index 60): "I'm sorry,
  but I can't help with that." raises (short); a 250-character refusal whose
  first period is at char 80 raises (no early period); a ≥200-character
  description whose first sentence ends before char 60 passes through even
  though it opens with "I cannot see any people"; a *short* description
  opening with such a phrase is treated as a refusal — accepted cost, since
  the prompt forbids that opening and refusals are the common case.

`tests/test_client.py` — against an in-process `http.server` fixture that
records requests and returns scripted responses:
- Happy path: request is `POST /api/chat`, body has `stream: false`,
  `think: false`, one user message with one base64 image that decodes to the
  prepared JPEG; `message.content` is returned.
- `host` normalisation: `"localhost:11434"`, `"http://x:1/"` (trailing slash)
  both produce `http://…/api/chat`.
- 404 with `{"error": "model 'foo' not found"}` → `ModelNotFoundError`
  mentioning `ollama pull foo`.
- 500 / 400 → `OllamaResponseError` with `.status_code` and `.body`.
- 200 with non-JSON body, or JSON lacking `message.content` →
  `OllamaResponseError`.
- Connection refused (port with no listener) → `OllamaConnectionError`.
- Handler sleeps past `timeout` → `OllamaTimeoutError`.
- `show` 200 → model present; 404 → absent; `pull` streams NDJSON and returns
  on `{"status": "success"}`; an `{"error": …}` line mid-stream →
  `OllamaResponseError`.
- `keep_alive` is forwarded when set and absent from the body when `None`.

`tests/test_describer.py`
- `describe()` composes: prepared image → prompt → client → clean. Checked
  end-to-end through the fake server.
- Env defaults: `DESCRIBE_IT_MODEL`, `OLLAMA_HOST` honoured; explicit
  arguments win over env.
- `check()` and `ensure_model()` paths (present → no pull; absent → pull
  called once).

`tests/test_cli.py`
- Single file → description + newline, exit 0. Two files → tab-separated
  lines. Nonexistent file → stderr message, exit 1, other files still
  processed. Server error → exit 1. `--version`.

### 6.2 Integration tests (opt-in; not a CI gate)

`tests/integration/test_live.py`, skipped unless `DESCRIBE_IT_INTEGRATION=1`
and the configured Ollama host answers `/api/version`.

- A synthetic image drawn with Pillow (red filled circle on white, the word
  "HELLO" in large black text) → `describe()` returns a non-empty string of
  ≤ `max_words + 10` words, not matching the refusal heuristic, containing no
  newline. Structural assertions only — no keyword matching, because small
  models are not reliable enough to gate on.
- `max_words=8` produces a shorter result than `max_words=40` (weak ordering
  check; asserts `len(short) < len(long) + 20` to tolerate noise).
- `language="French"` → response contains at least one of a handful of
  common French function words (`le`, `la`, `un`, `une`, `des`, `sur`).
- `ModelNotFoundError` for `model="definitely-not-a-model:latest"`.

### 6.3 Static gates

`ruff check`, `ruff format --check`, `mypy --strict src tests` all clean.

## 7. Work units (stack of PRs, bottom to top)

1. **`feat/scaffold-and-pure-layers`** — pyproject (deps, tooling config,
   python floor), `.github/workflows/ci.yml` (lint, type, unit tests with
   coverage on 3.12–3.14; separate non-blocking `workflow_dispatch` job for
   integration with Ollama installed), `exceptions.py`, `image.py`,
   `prompt.py`, their tests. Green CI with 100% coverage of what exists.
2. **`feat/ollama-client-and-describer`** — `client.py`, `describer.py`,
   public `__init__`, fake-server test fixture, their tests.
3. **`feat/cli-docs-integration`** — `cli.py` + tests, integration tests,
   `README.md`, `ARCHITECTURE.md`, ADR records under `docs/adr/` (transport
   choice, default model, exception design, refusal heuristic, JPEG+white
   flatten), first model pull and a live run recorded in the PR body.

## 8. Open questions for sign-off

- **Q1 — default model.** No vision model is installed locally today.
  Recommendation: `qwen3.5:4b` (Alibaba; ~3 GB; vision+thinking, thinking
  disabled by us; Qwen models are generally less refusal-prone than Google's
  Gemma line, which matters for the NSFW use case). Alternative: `gemma4:e4b`
  (higher caption quality in my experience, more likely to refuse).
  Whichever is chosen gets pulled on this machine for integration testing.
- **Q2 — GitHub.** There is no remote. The PR workflow needs one. Create
  `github.com/Xof/describe-it`? Public or private?
- **Q3 — Python floor.** `>=3.12` (proposed) vs the scaffold's `>=3.14`.
- **Q4 — refusal heuristic strictness.** Proposed: fire only when the match
  is at the very start of the text *and* the text is shorter than 200
  characters or contains no period before the 60th character — i.e. it looks
  like a refusal sentence, not a description that happens to start with
  "I cannot see…". Accept, or drop the heuristic entirely and let refusals
  through as plain strings?

### Resolutions (2026-08-19)

- **Q1:** `qwen3.5:4b` is the default model.
- **Q2:** Private repository `github.com/Xof/describe-it`; work lands as a
  stack of PRs with GitHub Actions CI.
- **Q3:** `requires-python = ">=3.12"`; CI matrix 3.12 / 3.13 / 3.14.
- **Q4:** Guarded refusal heuristic: fire only when a refusal phrase starts
  the cleaned text *and* (the text is under 200 characters *or* it has no "."
  before index 60). `DescriptionRefusedError.response` carries the cleaned
  text.

### Errata (2026-08-20, found during unit 1)

- §2.6 step 5 originally said "against the first 80 characters" and "raw
  text"; corrected above to the start-anchored, guarded rule and cleaned text.
- §6.1's refusal guard example originally described a 99-character text as
  passing through; under the OR guard it raises. Example replaced.
- "One layer" of decoration stripping (§2.6 step 2) is not idempotent, which
  §6.1 requires; the implementation strips to a fixed point instead.
- Unspecified and now decided: `max_image_size <= 0` raises `ValueError`;
  wide modes (`I`, `I;16`, `F`) are normalised against the image's own
  min/max before conversion to 8-bit (a constant image maps to black), because
  `Image.convert("RGB")` clips 16-bit data to white.
- EXIF orientation is honoured: `ImageOps.exif_transpose` runs as a new step
  before §2.4 step 1, and only when the image carries an orientation other
  than 1. Output carries no EXIF.
- The refusal regex (§2.6 step 5) also accepts U+2018/U+2019 apostrophes and
  `i am sorry`.
- Wrapping quotes/emphasis are stripped only when balanced — the interior must
  not contain the closing character again — so quoted text at both ends of a
  description survives. An apostrophe between two word characters ("a child's
  drawing") is punctuation rather than a closing quote and does not count, so
  single-quoted text is still unwrapped.
- Code-fence info strings and unclosed `<think>` blocks (stripped to end of
  text) are removed during cleaning. The BOM and zero-width characters are
  removed at the leading and trailing edges only — of the text and of each
  decoration layer — because interior ZWJ/ZWNJ/ZWSP are orthographically
  required by several scripts (Persian, Urdu, Hindi, Sinhala, Malayalam,
  Thai, Khmer) and hold emoji sequences together.
- `max_image_size` must be an `int` (not `bool`); other types raise
  `TypeError`.
- `prompt=""` raises `ValueError`.
- Images whose `info["transparency"]` is set are flattened onto white in every
  mode except the wide ones: a colour key names a raw sample value, and the
  wide-mode rescale moves every sample, so a key on an `I`/`I;16*`/`F` image is
  ignored and the image is described opaque. A key Pillow cannot apply (a
  string, `None`, a mismatched tuple) is dropped — from a copy, so the caller's
  image keeps it — and the image is described opaque. The key has to be dropped
  before the plain `convert("RGB")` as well, because Pillow consults it again
  on the `L` and `P` paths.
- Non-finite samples in `F` images: normalisation is skipped, in favour of
  Pillow's clamping conversion, only when an extremum is `inf`. `nan` is
  handled rather than skipped: Pillow seeds its extrema scan with pixel (0, 0),
  so a `nan` there reports a `nan` range for the whole image and the finite
  range is rescanned from the raw samples (an image that is entirely `nan`
  comes out black). A `nan` anywhere else never reaches the guard, because
  Pillow's extrema skip it. `nan` pixels themselves always come out black.
- `I;16B` / `I;16L` are normalised with `convert("I")` before rescaling,
  because `getextrema()` and `point()` reject those modes outright. `I;16N`
  cannot be converted — Pillow routes it through an 8-bit unpacker and clamps
  every sample to 255 — so its bytes are reinterpreted as the explicit
  byte-order mode this machine uses (`sys.byteorder`) and it keeps full
  precision like the other two.

### Errata (2026-08-20, found during unit 2)

- §4: `OllamaClient` is also exported from the package, and `Describer` /
  `describe()` accept a keyword-only `client=` argument. Both exist as a test
  seam (the suite drives the real `urllib` path against an in-process HTTP
  server and needs to inject a client bound to it); they are supported public
  surface but not part of the "simple API" story.
- §2.2: `OllamaClient(host=...)` defaults to the static
  `http://localhost:11434`; `$OLLAMA_HOST` and `$DESCRIBE_IT_MODEL` are
  resolved only by `Describer` / `describe()`, at call time. The transport
  never reads ambient configuration.
- §2.2/§2.3: `max_words < 1`, `timeout <= 0`, and `host=""` raise
  `ValueError` at construction.
- §2.3: `/api/pull` answers `200` and then an NDJSON `{"error": ...}` line
  for a model that does not exist upstream; `ensure_model()` surfaces that as
  `OllamaResponseError`, not `ModelNotFoundError` (which is reserved for "not
  present on this server").
- §2.7: `OllamaResponseError.status_code` is `None` only when `open()` never
  returned a response — the reply was not HTTP at all, or `http.client`
  rejected it before handing one back (a malformed status line, a header line
  over its length limit, more than 100 headers), all of which surface as an
  `http.client.HTTPException`. Once a response is in hand its status is
  reported, both for an unparseable 2xx body and for a body cut short of its
  declared `Content-Length`. `ModelNotFoundError`'s message names the model and
  the `ollama pull` remedy but not the host or operation.
- §5: proxy environment variables (`$http_proxy` and friends) are ignored. The
  client builds one private `OpenerDirector` per instance with an empty
  `ProxyHandler`, because Ollama is a local service and urllib's proxy support
  exempts nothing — not even loopback — so a machine-wide corporate proxy would
  otherwise capture traffic that `ollama` itself sends direct.
- §5: redirects are not followed. That same private opener has no
  `HTTPRedirectHandler`, because urllib follows a 30x by rewriting a redirected
  POST into a body-less GET — the image would silently not be sent. A 3xx is
  reported as `OllamaResponseError` naming the `Location` it points at; a
  `Location` on any other status is ignored, since error pages carry one too.
- §2.2: a scheme-less `host` gets `http://` and, when no port is given, 11434
  — matching Ollama's `OLLAMA_HOST` parsing; a `host` with an explicit scheme is
  used as written, so `http://ollama.example.com` keeps meaning port 80 rather
  than gaining a port of ours. An explicit port is always kept, and so is a path
  (an Ollama mounted under a prefix). A `host` carrying userinfo, a query string
  or a fragment raises `ValueError` — including an empty `?` or `#`, which parse
  away but leave behind the slash that introduced them — as does one whose
  scheme is neither `http` nor `https`, or whose port is unreadable. The
  credentials message does not quote the host back, so a password cannot reach
  a log through it.
- §2.2/§2.3: `timeout` validation lives in `OllamaClient` (`TypeError` if it is
  not a real number, `ValueError` if it is not positive and finite — `inf` is a
  hang with extra steps and `nan` loses every comparison), and `Describer`
  inherits it by constructing the client; passing `client=` therefore skips it
  along with `host`. `model` must not be blank after stripping (`ValueError`),
  a blank `$DESCRIBE_IT_MODEL` counts as unset exactly as a blank `$OLLAMA_HOST`
  does, and `max_words` must be an `int` and not a `bool` (`TypeError`).

### Errata (2026-08-20, found during unit 3)

- §2.8: every option value that the library can reject is validated by an
  argparse `type` function, so a bad one is a usage message and exit status 2
  rather than a traceback out of `Describer` — `--max-words` and
  `--max-image-size` must be integers of at least 1, `--timeout` a positive
  finite number, `--model` non-blank, and `--host` something `normalise_host`
  accepts. (`--language` and `--context` are free text, which the library does
  not constrain either.) `--model` and `--host` default to `None` and are
  passed through as `None`, so `$DESCRIBE_IT_MODEL` and `$OLLAMA_HOST` mean the
  same thing on the command line as in the library — and because those two
  reach `Describer` without passing through a `type` function, `main` turns the
  `ValueError` they can raise into the same usage error, exit 2 as well.
- §2.8: `--max-image-size` takes a pixel count only; the library's `None`
  ("send at full size") is not expressible on the command line. `keep_alive`
  and `prompt` have no flags either — one describer is built per run, and both
  are programmatic choices.
- §2.8: a failure is reported as `describe-it: <path>: <reason>` on one line,
  with internal whitespace collapsed, because a server's error body can carry
  newlines and a multi-file run is read a line at a time. An `OSError` is
  reported by its `strerror` where it has one (the filename is already in the
  message); Pillow's `UnidentifiedImageError` has none and is reported whole.
  `--version` prints `describe-it <version>`. Pillow's
  `DecompressionBombError` is reported the same way even though it is not an
  `OSError`: it is raised by `Image.open`, before `prepare_image` could wrap
  it, and it is a file the CLI cannot read like any other.
- §6.2: the live tests check `$DESCRIBE_IT_INTEGRATION` *before* probing
  `/api/version`, so collecting the module during a unit run opens no socket.
  The probe uses a proxy-free opener with a 2 s timeout, matching the client's
  posture (unit-2 errata) so that a machine-wide proxy cannot skip the tests on
  a machine where the library works.
- §6.2: run four times against Ollama 0.32.13 with `llava:7b` (this machine's
  Ollama cannot load its own `qwen3.5:4b` blob). The first test's
  ≤ `max_words + 10` assertion failed once in four; the word-budget *ordering*
  test and the missing-model test passed every time; the French test failed
  every time — `llava:7b` answers in English whatever the prompt asks. Both
  failures are model behaviour; the assertions are left at the strength stated
  above, and the job stays non-blocking.
- §6.2: the synthetic image's text is drawn with `ImageFont.load_default(size=)`,
  which needs Pillow ≥ 10.1, while the library's own floor stays at the `>=10`
  of §5 — the smaller font Pillow 10.0 would give is illegible to a vision
  model at this resolution, and the requirement belongs to the opt-in tests
  rather than to the package.
- §5: the distribution carries a `py.typed` marker, a `Repository` URL,
  keywords and classifiers (Python 3.12–3.14, Multimedia :: Graphics,
  Intended Audience :: Developers, Typing :: Typed). No license is declared, so
  none is claimed in the metadata or the README.
