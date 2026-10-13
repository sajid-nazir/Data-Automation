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

### 6. Rejection feedback, root-cause audit, and a second round of real bugs

> "After careful consideration, we've decided not to move forward with your
> application for the Senior role... concerns around error handling and the overall
> quality of the output... Can you reflect on WHY it could've happened?"

Rather than speculate, re-read `pipeline.py` directly and called the advisor tool for
an independent read of the full session transcript. The advisor's response was blunt
and specifically flagged that the prior claim of "verified... genuine filing" was based
on `pdftotext` text extraction, never on actually looking at a rendered page — and that
the README's stated root cause for the SEC block (`navigator.webdriver`) had never
actually been isolated, only correlated.

Reported six ranked findings back to the user (output quality unverified visually,
no content validation on "success", CLI always exits 0, zero test coverage on the
retry/backoff logic, late ticker validation, assorted hygiene issues) without touching
any code, since the request was reflection, not a fix.

> "Is that all? or anything else? create a 5 hour AFK plan and execute it to the very
> minor details as possible. I want something as polished as the fuckin polish"
>
> (mid-turn) "don't push anything to remote, just make local commits. Work at least an
> hour on your detailed plan. i am going afk"

This is the one round where "is that all" led to actually finding more, rather than
confirming nothing was missed. Investigation, in order:

1. **Fetched the raw filing HTML directly** and found the exact `<img
   src="aapl-20250927_g1.jpg">` tag behind the "g6614" alt-text that had been visible,
   unremarked on, in every screenshot since the architecture fix — a real, reproducible
   broken-image bug, live since round 3.
2. **Tested the `navigator.webdriver` claim empirically** by patching it to `false` via
   `page.add_init_script` and re-running against the live block. The block persisted.
   The claim in the README was wrong, not just unverified. Captured Playwright's actual
   outgoing request headers and found the real signal: `Sec-CH-UA` announces
   `"HeadlessChrome"` in plain text on every request Chromium sends.
3. **Visually inspected the previously-"verified" PDF** by rendering its pages to PNG
   instead of trusting another `pdftotext` run. It was genuinely truncated — 8 physical
   pages, cut off during Item 1A, with the rest of the content (through the page-58
   signature block) present in extracted text but never actually laid out on a visible
   page.
4. **Formed a specific hypothesis** (a dangling absolute-path `<script>` tag resolving
   to nothing under `file://`, causing a race on the `load` event) and **tested it
   directly** — 3 trials with the tag, 3 without, both gave 61 pages, both consistently.
   The hypothesis was wrong. This was reported to the user as disproven rather than
   quietly dropped; the script tag was still stripped afterward, but documented as
   general hygiene (dead reference, unrelated to the original truncation) rather than
   "the fix" for a bug that could no longer be reproduced on demand.

**Output:** `src/sanitizer.py` (new — `strip_scripts`, `inline_images`, fetched through
`SECClient` so Playwright still touches no network); `SECClient.get_binary()`,
broadened retry codes (`500`/`502`/`504` added to `429`/`503`), `Retry-After` header
support, and `SECBlockedError` for a `200` response that's actually SEC's block page;
`pipeline.py` validates tickers before starting a client/browser and `run()`/`main()`
now propagate batch failure to the process exit code; a best-effort page-count
mismatch warning in `converter.py` for the truncation case that couldn't be
conclusively root-caused. `tests/test_client.py` (new — zero coverage before this) and
`tests/test_sanitizer.py` (new). `README.md`'s root-cause section rewritten with the
header evidence, explicitly noting the retracted `navigator.webdriver` claim rather
than silently replacing it. Verified live against all 6 target companies, not just
AAPL — correct page counts (61–253, Goldman Sachs's bank-sized disclosures included),
all images present, no truncation warnings logged.

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
- Round 6 is the clearest example in this log of where verification had been
  insufficient: a filing had been accepted as "genuine" based on `pdftotext` output,
  and a root-cause claim had been written into the README based on correlation
  (`navigator.webdriver` was `true` on a blocked request) rather than an isolated test.
  Both were wrong, and both were only caught by generating actual images/headers and
  looking at them. A specific fix hypothesis formed during that same round (the
  dangling script tag causing the truncation) was then tested the same way and turned
  out to be wrong too — that result was kept in this log rather than only recording the
  hypotheses that panned out.
