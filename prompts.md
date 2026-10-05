# Prompt Log

Chronological record of the prompts used to build this pipeline with Claude Code, and
what each one produced. Session context (file reads, config already established
earlier in the conversation) was carried forward rather than re-supplied each time.

---

### 1. Initial scaffold

> "I am building a Python service to fetch the latest 10-K reports for public companies
> from the SEC EDGAR API and convert them to PDF... Modularity is required so this
> pipeline can easily transition into a worker job. The code must be cleanly separated
> into client.py... resolver.py... converter.py... pipeline.py... Create the
> production-ready directory layout under `src/` and `tests/`... Write the clean
> implementation of `src/models.py`... and `src/converter.py`... store the target company
> registry in a dedicated config module (`src/config.py`)..."

Given a prior validation script (`validate_sec_api.py`) and a committed architecture
(client/resolver/converter/pipeline split, Pydantic models, Playwright for rendering),
the explicit ask that round was scoped to the layout plus `config.py`, `models.py`, and
`converter.py`. `client.py`, `resolver.py`, and `pipeline.py` were scaffolded as typed
stubs (`NotImplementedError`) to complete the directory structure without implementing
logic that hadn't been asked for yet — called out explicitly rather than silently
shipped as if finished.

**Output:** `src/config.py`, `src/models.py`, `src/converter.py`, `pyproject.toml`,
`requirements.txt`, `tests/test_models.py`, `tests/test_converter.py`.

---

### 2. Client and resolver

> "Let's move on to client and resolver so we can connect live data fetching to the
> pipeline... Make sure it adheres strictly to the 10 requests per second limit with a
> basic sleep throttle... includes a retry with exponential backoff if the SEC returns a
> 429 or 503... parse the submissions json payload using the parallel arrays in
> filings.recent... locate the first standard 10-K... Please provide only the
> implementations for client.py and resolver.py, along with the corresponding unit tests
> in tests/test_resolver.py..."

Explicitly scoped to two files plus one test file — no test_client.py was requested or
written.

**Output:** `src/client.py` (`SECClient`), `src/resolver.py` (`resolve_latest_10k`),
`tests/test_resolver.py`.

---

### 3. Pipeline wiring — and a real bug

> "Let's finally connect everything together in pipeline.py... Set up a CLI entrypoint
> using argparse... iterate through the tickers, pull submissions from SECClient, resolve
> the latest 10-K and then send the URL over to our Playwright converter. Save the output
> to the config output directory grouped by ticker... Make sure you wrap each company
> loop in a try/except..."

After wiring `pipeline.py` and passing all mocked tests, a live run against the real SEC
API for AAPL produced a 1-page, 40KB PDF — clearly wrong for a 10-K. Investigation (fetch
the same URL with plain `httpx` vs. Playwright, compare `navigator.webdriver`) showed SEC
was serving its "undeclared automated tool" block page to Playwright's headless Chromium
specifically, regardless of a compliant User-Agent. This was a design-level fix, not a
one-line patch, so the two candidate approaches (route the fetch through `SECClient` and
render from a local file vs. attempt to spoof Playwright's automation fingerprint) were
put to the user before touching `client.py`/`converter.py`/`pipeline.py` again. The first
option was chosen.

**Output:** `src/pipeline.py`, `tests/test_pipeline.py`; `SECClient.get_document()`
added; `HTMLToPDFConverter.convert_url_to_pdf()` replaced with
`convert_html_to_pdf()`; `tests/test_converter.py` rewritten for the new contract.
Verified by re-running the live AAPL fetch and inspecting the resulting PDF to confirm
it was the genuine, complete filing (signature page present, internal filing footer
reading "Form 10-K | 58") rather than mocking the fix and assuming it worked.

---

### 4. Containerization

> "Create a lean, production-grade Dockerfile using an official Python 3.11 slim base
> image... installs necessary Chromium dependencies, runs as a non-root user and sets the
> entrypoint to the pipeline CLI... Create the docker-compose.yml file that mounts a
> local ./output directory as a volume... Keep requirements clean..."

"Keep requirements clean" prompted a check of whether every pinned dependency was
actually imported anywhere in `src/` — `rich` and `tenacity` were in `requirements.txt`
from the original design but never used (manual retry/backoff replaced tenacity;
`logging` replaced rich's console output). Both were dropped before writing the
Dockerfile rather than baked into the image unused.

Docker wasn't available in the dev sandbox, so the build/run couldn't be verified
directly — that limitation was stated plainly, with exact commands handed off for the
user to run and report back on.

**Output:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`, pruned
`requirements.txt`/`pyproject.toml`.

---

### 5. Docs

> "The Docker build and container run passed. Now let's wrap up the final
> deliverables... Create a clean, professional README.md... Create prompts.md
> documenting the collaborative workflow and prompt log..."

**Output:** `README.md`, `prompts.md` (this file).

---

## Notes on the workflow

- Each implementation step was followed by actually running it — `pytest`, `mypy
  --strict`, and for the pipeline specifically, a live call against the real SEC API —
  rather than treating a plausible-looking diff as done.
- Scope was held to what was explicitly asked each turn; unimplemented modules were left
  as visible stubs rather than quietly filled in ahead of being asked.
- The anti-bot discovery changed a public API (`SECClient`, `HTMLToPDFConverter`) that
  had already shipped with passing tests in the prior turn. That kind of change went
  through a confirmation step before the rewrite, since it affected files the user had
  already accepted as finished.
