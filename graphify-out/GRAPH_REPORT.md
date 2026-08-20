# Graph Report - .  (2026-08-20)

## Corpus Check
- Corpus is ~40,193 words - fits in a single context window. You may not need a graph.

## Summary
- 549 nodes · 1512 edges · 17 communities (16 shown, 1 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 55 edges (avg confidence: 0.8)
- Token cost: 102,223 input · 0 output

## Community Hubs (Navigation)
- Ollama Client & Fake Server Tests
- Image Preparation
- Architecture & Design Decisions
- CLI Tests
- Prompt & Response Cleaning
- Command-Line Interface
- Describer Public API
- HTTP Test Fixtures
- Exception Hierarchy
- Ollama Endpoint Methods
- Exception Tests
- Live Integration Tests
- Private urllib Opener
- Configuration Defaults
- Error Constructors
- Package Surface Tests
- Console Script Entry

## God Nodes (most connected - your core abstractions)
1. `FakeOllama` - 87 edges
2. `OllamaClient` - 74 edges
3. `describe-it Design Specification (2026-08-19)` - 71 edges
4. `Describer` - 54 edges
5. `prepare_image()` - 44 edges
6. `_image_file()` - 31 edges
7. `_chat()` - 29 edges
8. `_decode()` - 26 edges
9. `clean_response()` - 23 edges
10. `OllamaResponseError` - 21 edges

## Surprising Connections (you probably didn't know these)
- `Host normalisation mirroring Ollama's envconfig.Host()` --rationale_for--> `_host()`  [INFERRED]
  docs/adr/0012-normalise-a-host-the-way-ollamas-cli-does.md → src/describe_it/cli.py
- `Sampling options: temperature 0.2, num_predict = max_words*4+32` --references--> `OllamaClient`  [INFERRED]
  docs/adr/0007-fix-the-sampling-options-for-a-caption.md → src/describe_it/client.py
- `Explicit, opt-in model pull (ensure_model)` --references--> `OllamaClient`  [INFERRED]
  docs/adr/0009-never-pull-a-model-implicitly.md → src/describe_it/client.py
- `Do not follow redirects` --rationale_for--> `_is_redirect()`  [INFERRED]
  docs/adr/0008-build-a-private-urllib-opener.md → src/describe_it/client.py
- `Private OpenerDirector per client` --rationale_for--> `_build_opener()`  [INFERRED]
  docs/adr/0008-build-a-private-urllib-opener.md → src/describe_it/client.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **One describe() call: prepare_image -> build_prompt -> OllamaClient.chat -> clean_response** — src_describe_it_describer_describer, src_describe_it_image_prepare_image, src_describe_it_prompt_build_prompt, src_describe_it_client_ollamaclient, src_describe_it_prompt_clean_response, architecture_image_before_socket [EXTRACTED 1.00]
- **DescribeItError hierarchy (nine classes shaped by what a caller would do)** — src_describe_it_exceptions_describeiterror, src_describe_it_exceptions_imageerror, src_describe_it_exceptions_ollamaerror, src_describe_it_exceptions_ollamaconnectionerror, src_describe_it_exceptions_ollamatimeouterror, src_describe_it_exceptions_modelnotfounderror, src_describe_it_exceptions_ollamaresponseerror, src_describe_it_exceptions_descriptionerror, src_describe_it_exceptions_descriptionrefusederror, docs_adr_0003_image_in_string_out_failures_are_exceptions_exception_hierarchy [EXTRACTED 1.00]
- **Verification strategy: hermetic gate at 100% coverage plus opt-in structural live tests** — docs_adr_0010_gate_on_a_hermetic_suite_and_keep_live_tests_opt_in_hermetic_unit_suite, docs_adr_0010_gate_on_a_hermetic_suite_and_keep_live_tests_opt_in_coverage_gate_100, docs_adr_0010_gate_on_a_hermetic_suite_and_keep_live_tests_opt_in_opt_in_live_tests, docs_adr_0010_gate_on_a_hermetic_suite_and_keep_live_tests_opt_in_structural_assertions_only, docs_adr_0010_gate_on_a_hermetic_suite_and_keep_live_tests_opt_in_hypothesis_property_tests, docs_adr_0002_use_stdlib_urllib_as_the_transport_in_process_http_server_fixture, github_workflows_ci_test_job, github_workflows_ci_integration_job [EXTRACTED 1.00]

## Communities (17 total, 1 thin omitted)

### Community 0 - "Ollama Client & Fake Server Tests"
Cohesion: 0.07
Nodes (65): OllamaClient, A minimal client for the three Ollama endpoints describe-it uses. Attributes:…, Configure the client. Args: host: Base URL or bare `host:port` of the Ollama…, normalise_host(), Turn a host as a human writes it into a base URL requests can be built on.…, FakeOllama, A scriptable HTTP server standing in for Ollama. Attributes: requests: Every…, Return the base URL the server is listening on. (+57 more)

### Community 1 - "Image Preparation"
Cohesion: 0.08
Nodes (69): _apply_orientation(), _finite_extrema(), _flatten_alpha(), prepare_image(), Image, Image preparation: any PIL image in, one small RGB JPEG out. The caller is…, Return the image the right way up according to its EXIF orientation. Args:…, Return an RGB image with the same visible content as `image`. Args: image: The… (+61 more)

### Community 2 - "Architecture & Design Decisions"
Cohesion: 0.08
Nodes (69): Layering: cli -> describer -> {image, prompt, client} -> exceptions, Image prepared before any socket is opened, ADR 0001: Use a local Ollama vision model rather than a hosted description API, Commercial hosted vision API (rejected), Local Ollama vision inference, Ollama (local model server), ADR 0002: Use stdlib urllib as the transport instead of the ollama client library, In-process http.server test fixture (fake Ollama) (+61 more)

### Community 3 - "CLI Tests"
Cohesion: 0.10
Nodes (56): CaptureFixture, _animation_file(), _BrokenPipeWriter, _hide_ambient_configuration(), _image_file(), _open_sink(), fixture, MonkeyPatch (+48 more)

### Community 4 - "Prompt & Response Cleaning"
Cohesion: 0.07
Nodes (42): Wrapping pairs stripped only when balanced, Refusal heuristic, Single user message, no system prompt, Image, Describe one image. Args: image: The image to describe, in any mode PIL can…, build_prompt(), clean_response(), _closer_recurs() (+34 more)

### Community 5 - "Command-Line Interface"
Cohesion: 0.08
Nodes (42): ArgumentParser, Option validation via argparse type functions, Missing and closed output stream handling, CLI one-line-per-file output contract, describe-it command line, _build_parser(), describe_file(), describe_files() (+34 more)

### Community 6 - "Describer Public API"
Cohesion: 0.11
Nodes (39): Minimal API contract: image in, string out, failures are exceptions, describe(), Describer, Verify that the server is up and has the configured model. For a start-up…, Download the configured model if the server does not already have it. Blocking,…, Describe one image, configuring a describer for the occasion. Sugar for…, A configured describer: set the options once, describe many images. Every…, _image() (+31 more)

### Community 7 - "HTTP Test Fixtures"
Cohesion: 0.07
Nodes (25): BaseHTTPRequestHandler, closed_port(), other_server(), Any, fixture, Shared fixtures: an in-process stand-in for the Ollama server. The client tests…, A ThreadingHTTPServer that carries the script and the request log., Start the server on a free port, in a daemon thread. (+17 more)

### Community 8 - "Exception Hierarchy"
Cohesion: 0.22
Nodes (18): DescribeItError exception hierarchy, DescribeItError, DescriptionError, DescriptionRefusedError, ImageError, OllamaConnectionError, OllamaError, OllamaTimeoutError (+10 more)

### Community 9 - "Ollama Endpoint Methods"
Cohesion: 0.19
Nodes (9): HTTPResponse, Ask a model one question about one image and return its reply. Args: model: The…, Report whether the server already has a model. Args: model: The model tag to…, Download a model onto the server, blocking until it is there. Args: model: The…, POST a JSON body and hand the open response to the caller. The response is…, Parse text the server sent as a JSON object. Args: text: The response body, or…, Name a call for use in an error message. Args: operation: The endpoint's short…, Shorten a response body to something an error message can carry. Args: text:… (+1 more)

### Community 10 - "Exception Tests"
Cohesion: 0.16
Nodes (9): OllamaResponseError, Raised when the server answers but the answer is unusable. Any non-2xx status…, parametrize, Tests for the exception hierarchy: shape, attributes, and messages., test_every_error_is_catchable_as_describe_it_error(), test_refusal_carries_the_model_text(), test_response_error_carries_status_and_body(), test_response_error_defaults_are_empty() (+1 more)

### Community 11 - "Live Integration Tests"
Cohesion: 0.26
Nodes (12): _describer(), image(), fixture, Live tests against a real Ollama server and a real vision model. These are the…, Yield the image under test: a red disc and the word HELLO on white. Synthetic…, Return a describer for the model under test. Args: model: An override for the…, Report why the live tests cannot run. Returns: The reason to skip, or an empty…, test_a_model_the_server_does_not_have_is_reported_with_the_remedy() (+4 more)

### Community 12 - "Private urllib Opener"
Cohesion: 0.18
Nodes (10): HTTPError, OpenerDirector, _build_opener(), _is_redirect(), _names_a_missing_model(), HTTP transport for the Ollama server. `OllamaClient` is the only part of…, Classify a non-2xx response. Args: exc: The error urllib raised, with its body…, Report whether a status code is one of the redirection statuses. Args: status:… (+2 more)

### Community 13 - "Configuration Defaults"
Cohesion: 0.27
Nodes (7): default_host(), default_model(), Configuration defaults and normalisation of a host string. These values sit in…, Return the configured Ollama host, before normalisation. Returns:…, Return the configured model tag. Returns: `$DESCRIBE_IT_MODEL` if it holds…, The library's front door: `Describer` and the module-level `describe`. This is…, Configure a describer. Args: model: Ollama model tag. `None` resolves…

### Community 14 - "Error Constructors"
Cohesion: 0.22
Nodes (6): ModelNotFoundError, Initialise the error with the model's reply. Args: response: The cleaned text…, Raised when the configured model is not present on the server. describe-it…, Initialise the error for a missing model. Args: model: The Ollama model tag…, Initialise the error with the server's reply. Args: message: Human-readable…, test_model_not_found_names_the_pull_command()

### Community 15 - "Package Surface Tests"
Cohesion: 0.22
Nodes (3): parametrize, Tests for the package surface: version, re-exports, console script target., test_every_exported_name_exists()

## Knowledge Gaps
- **1 isolated node(s):** `describe-it`
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `FakeOllama` connect `Ollama Client & Fake Server Tests` to `CLI Tests`, `Describer Public API`, `HTTP Test Fixtures`?**
  _High betweenness centrality (0.166) - this node is a cross-community bridge._
- **Why does `OllamaClient` connect `Ollama Client & Fake Server Tests` to `Architecture & Design Decisions`, `Describer Public API`, `Exception Hierarchy`, `Ollama Endpoint Methods`, `Exception Tests`, `Private urllib Opener`, `Configuration Defaults`, `Error Constructors`?**
  _High betweenness centrality (0.157) - this node is a cross-community bridge._
- **Why does `describe-it Design Specification (2026-08-19)` connect `Architecture & Design Decisions` to `Ollama Client & Fake Server Tests`, `Image Preparation`, `Prompt & Response Cleaning`, `Command-Line Interface`, `Describer Public API`, `Exception Hierarchy`, `Exception Tests`, `Error Constructors`?**
  _High betweenness centrality (0.149) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `FakeOllama` (e.g. with `_BrokenPipeWriter` and `_RecordingWriter`) actually correct?**
  _`FakeOllama` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `OllamaClient` (e.g. with `Sampling options: temperature 0.2, num_predict = max_words*4+32` and `Explicit, opt-in model pull (ensure_model)`) actually correct?**
  _`OllamaClient` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `Describer` (e.g. with `_PipeGoneError` and `OllamaClient`) actually correct?**
  _`Describer` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `describe-it` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._