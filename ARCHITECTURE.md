# Architecture

`describe-it` turns a PIL image into an alt-text string by posting one JSON
request to a local Ollama. Two pure layers and one HTTP client sit under a
composer, in a strict dependency direction:
`cli → describer → {image, prompt, client} → exceptions`, with `config` read by
`describer` and `client`. Nothing is cached and nothing is global.

## Component map

| Component | Responsibility | Entry points |
|---|---|---|
| `describe_it/__init__.py` | Public re-exports only; no logic. | `describe`, `Describer`, `OllamaClient`, `DEFAULT_MODEL`, `DEFAULT_HOST`, the nine exceptions, `__version__` |
| `describe_it/exceptions.py` | The hierarchy every failure lands in. | `DescribeItError` and descendants |
| `describe_it/image.py` | Pure: any PIL mode → JPEG bytes. Opens no socket. | `prepare_image(image, max_size)` |
| `describe_it/prompt.py` | Pure: prompt wording, reply cleanup, refusal heuristic. | `build_prompt(...)`, `clean_response(text)`, `REFUSAL_RE` |
| `describe_it/client.py` | The only module that opens a socket. Knows Ollama's API, not PIL. | `OllamaClient.chat/show/pull` |
| `describe_it/config.py` | Defaults, environment lookups, host normalisation. | `default_model()`, `default_host()`, `normalise_host()` |
| `describe_it/describer.py` | Composes the layers; owns configuration resolution. | `Describer`, `describe()` |
| `describe_it/cli.py` | argparse front end for the `describe-it` console script. | `main(argv)`, `describe_files(...)`, `describe_file(...)` |

## Flow

One `describe()` call:

1. `Describer.__init__` resolves model and host (arguments, then environment,
   then packaged defaults) and constructs an `OllamaClient`, which validates
   host and timeout.
2. `prepare_image` converts the image to an RGB JPEG — **before any socket**.
3. `build_prompt` produces one user message (there is no system message).
4. `OllamaClient.chat` POSTs `/api/chat` with `stream:false`, `think:false`,
   the base64 image, `temperature 0.2`, `num_predict = max_words*4+32`.
5. Non-2xx and unusable bodies are mapped onto the exception hierarchy, chained
   with `raise ... from`.
6. `clean_response` strips decoration to a fixed point, raises on an empty or
   refusing reply, and returns the string.

## Invariants

- **The image is prepared before any socket is opened.** Breaks: an unusable
  image costs the caller a cold model load and a timeout instead of a
  millisecond.
- **The caller's image object is never mutated.** Breaks: callers who reuse an
  image (thumbnailing, saving) silently get the converted, downscaled one.
- **`think: false` is sent on every chat request, with no capability probe.**
  Breaks: adding a probe costs an `/api/show` round trip per description;
  removing the field lets thinking models burn seconds on a caption.
- **The client uses its own opener: empty `ProxyHandler`, no redirect
  handler.** Breaks: a machine-wide `$http_proxy` captures localhost traffic;
  urllib rewrites a redirected POST into a body-less GET and the image is
  silently not sent.
- **Host normalisation mirrors Ollama's `envconfig.Host()`:** scheme-less gets
  `http://` and port 11434; a host written with a scheme is used as written.
  Breaks: `http://ollama.example.com` stops meaning port 80 and no other tool
  agrees with us.
- **Every failure is a `DescribeItError`; `TypeError`/`ValueError` are reserved
  for caller mistakes** (non-image argument, out-of-range option). Breaks:
  `except DescribeItError` starts swallowing bugs in the calling code.
- **Configuration is validated at construction, not at first request.**
  Breaks: a bad host surfaces later as an unrelated-looking connection error.
- **The unit suite is hermetic and gated at 100% line and branch coverage.**
  `# pragma: no cover` is permitted only on the `__main__` guard. Breaks: the
  uncovered lines in a library this size are exactly its error branches.
- **The live tests are opt-in and never gate.** They require
  `DESCRIBE_IT_INTEGRATION=1` *and* a host that answers `/api/version`, and the
  environment variable is checked first so collection opens no socket.

## Landmines

- The refusal check is **a heuristic**, and it is start-anchored on the
  *cleaned* text with an OR guard (under 200 chars, or no `.` before index 60).
  A short description opening "I cannot see any people" is treated as a
  refusal. That is an accepted cost, not a bug; the text is on
  `DescriptionRefusedError.response`.
- `clean_response` strips decoration **to a fixed point**, because
  `clean(clean(x)) == clean(x)` is part of the contract. Do not replace it with
  a single pass.
- Zero-width characters and the BOM are trimmed at the **leading and trailing
  edges only**. Interior ZWJ/ZWNJ/ZWSP are orthographically required by
  Persian, Urdu, Hindi, Sinhala, Malayalam, Thai and Khmer, and hold emoji
  sequences together. A global strip corrupts them.
- Wide modes (`I`, `I;16*`, `F`) **ignore `info["transparency"]`**: a colour key
  names a raw sample value and the wide-mode rescale moves every sample, so the
  image is described opaque. A key Pillow cannot apply is dropped from a copy
  (the caller's image keeps it) and the image is likewise described opaque.
- `I;16N` cannot be converted — Pillow routes it through an 8-bit unpacker and
  clamps every sample to 255 — so its bytes are **reinterpreted** as the
  explicit byte-order mode `sys.byteorder` names. Do not "simplify" that to a
  `convert`.
- `F` images: normalisation is skipped in favour of Pillow's clamping
  conversion only when an extremum is `inf`. `nan` is rescanned, because
  Pillow seeds its extrema scan with pixel (0, 0).
- `Describer.ensure_model()` **downloads gigabytes** and blocks. Nothing else
  ever pulls; a missing model is a `ModelNotFoundError` naming the command.
- `keep_alive` is omitted from the request body when unset, never sent as
  `null`: `null` overrides the server's own default instead of asking for it.
- `OllamaResponseError.status_code` is `None` only when no HTTP response was
  ever returned. An unparseable 2xx body keeps its 200.

## Where to change X

- Prompt wording or reply cleanup: `prompt.py` (pure; test without a server).
- A new image mode or conversion rule: `image.py`, `_to_rgb` and its helpers.
- A new Ollama endpoint: `client.py`, alongside `chat`/`show`/`pull`; keep it
  ignorant of PIL and of prompt wording.
- Defaults, environment variables, host parsing: `config.py`.
- A new CLI flag: `cli._build_parser`, with a `type` function if the value can
  be invalid — option validation belongs in argparse, not in a traceback.
- The fake Ollama the tests drive: `tests/conftest.py`.

---

Why any of this is the way it is:
[docs/adr/](docs/adr/) (one file per decision, generated index) and the design
specification under [docs/superpowers/specs/](docs/superpowers/specs/). For
install, usage and the test commands, see [README.md](README.md).
