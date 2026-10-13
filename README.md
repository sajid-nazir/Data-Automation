# SEC 10-K Fetcher

Fetches the latest 10-K filing for a set of public companies from SEC EDGAR and converts
each one to a paginated PDF.

## Architecture

| Module | Responsibility |
|---|---|
| `sec_filings/config.py` | `Settings` (required User-Agent, rate/retry limits, host allowlist, target company registry) |
| `sec_filings/errors.py` | One exception hierarchy for every expected failure mode |
| `sec_filings/client.py` | `SECClient` — all HTTP traffic to sec.gov |
| `sec_filings/resolver.py` | Resolves the latest 10-K from `filings.recent`, falling back to overflow pages |
| `sec_filings/converter.py` | `HTMLToPDFConverter` — renders already-fetched HTML to PDF via Playwright |
| `sec_filings/pipeline.py` | CLI entrypoint: fetch → resolve → convert → save, per ticker |

### Fetching and rendering are deliberately decoupled

`SECClient` is the only thing that talks to sec.gov. It enforces an 8 req/s throttle
(below SEC's documented 10 req/s ceiling, for headroom — see Design trade-offs), sends
the configured User-Agent, checks every request (including redirect hops) against a
host allowlist, and retries with jittered exponential backoff on `429`/`500`/`502`/
`503`/`504`, honoring a server `Retry-After` header when present (capped, and rejecting
non-finite values). The submissions JSON, the filing HTML, and every image embedded in
it all go through it, so every request to SEC — not just the first one — is governed by
the same policy.

`HTMLToPDFConverter` has no network access of its own: the page's `java_script_enabled`
is `False`, and every request the page makes is intercepted via Playwright's
`context.route()`. The filing's own HTML (already fetched) is served directly from that
intercept; images/stylesheets/fonts are served through a `fetch` callback the caller
supplies (in practice, `SECClient.get_binary`, so they stay rate-limited and
host-allowlisted); anything else — scripts, unexpected resource types, any URL that
isn't the document — is aborted.

This split exists because of a real failure mode, not just layering for its own sake:
pointing Playwright directly at a `sec.gov` URL gets served SEC's "undeclared automated
tool" block page, even with a fully compliant User-Agent string. The actual mechanism,
confirmed by capturing Playwright's outgoing request headers and comparing them to a
working `httpx` request for the same URL: Chromium's own `Sec-CH-UA` client-hint header
announces itself in plain text —

```
sec-ch-ua: "HeadlessChrome";v="153", "Not_A Brand";v="8", "Chromium";v="153"
```

— which SEC's edge can block on with zero fingerprinting or JS execution required. (An
earlier version of this document attributed the block to `navigator.webdriver`; that was
disproven by patching it to `false` via `page.add_init_script` and confirming the block
persisted regardless.) No User-Agent string changes that header, so the only reliable
fix is to keep Chromium off the network entirely.

An earlier version of this architecture fetched HTML once, then rewrote it with regex
(stripping `<script>` tags, inlining images as base64 data URIs) before handing it to a
Playwright `file://` navigation. That worked but had two real problems: regex-based HTML
rewriting is fragile (a `<script>` tag inside a quoted attribute, or an `on*=` handler,
isn't caught by a tag-matching regex), and it duplicated logic the browser already has —
a real DOM parser and request pipeline. Routing through `context.route()` instead means
Chromium's own parser handles the HTML, and network isolation is enforced structurally
(nothing *can* reach the network, not "nothing currently does").

### Resolving the latest 10-K

`filings.recent` only covers roughly the most recent year. For a company that hasn't
filed a 10-K in that window (an infrequent filer, or a very long fiscal-year gap),
`resolve_latest_10k` falls back to `filings.files[]` overflow pages, fetched newest-first
via an injected `fetch_page` callable, stopping at the first page containing a 10-K.
The resolver also validates that all five columnar arrays (`form`, `accessionNumber`,
`filingDate`, `reportDate`, `primaryDocument`) are the same length before indexing into
them, picks the row with the latest `filingDate` rather than trusting array order (which
SEC doesn't document), and raises `StaleFilingError` if the resolved filing is older than
`settings.max_filing_age_days` (550 by default) — a company that stopped filing 10-Ks
shouldn't silently produce a several-year-old PDF as if it were current.

### Error handling

`pipeline.run()` validates every requested ticker against `TARGET_COMPANIES` up front —
before a client or browser is ever started — then processes the rest sequentially,
isolating each one. Every raised error is a `PipelineError` subclass (see
`sec_filings/errors.py`) or it isn't: the per-ticker loop catches `PipelineError`
specifically as an *expected* operational failure (SEC outage, no filing found, a
conversion that failed validation) and anything else as an *unexpected* one — a bug,
tracked separately. `run()` returns a `RunResult(succeeded, failed, crashed)`, and
`main()` turns that into a process exit code: `0` only if every ticker succeeded, `1` if
any ticker had an expected failure (including "no 10-K found" — previously this exited
`0`, which is the exact silent-success failure mode a production pipeline shouldn't
have), `3` if anything crashed unexpectedly.

A generated PDF is also checked against its own source before being treated as a
success: SEC's filings mark page boundaries with `page-break-after`, and the renderer
counts those markers (via computed style, which works even with JS disabled —
Playwright's `page.evaluate()` runs through its own protocol channel, independent of the
page's `java_script_enabled` setting) and compares them to the actual rendered page
count. A page count *below* that marker count hard-fails as `PDFValidationError` — this
is the fix for a truncation bug found mid-development, where a PDF rendered only 8 of 61
actual pages with no error of any kind. A page count *above* tolerance is retried at a
smaller scale (see below) before being treated as a failure.

## Quickstart

### Local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

export SEC_USER_AGENT="Your Company your-contact@example.com"  # required, no default

# Defaults to every company in TARGET_COMPANIES
python -m sec_filings.pipeline

# Or specific tickers
python -m sec_filings.pipeline AAPL META
```

PDFs land in `./output/{TICKER}/{TICKER}_10K_{filing_date}.pdf`. Exit code is `0` only
if every requested ticker succeeded (see Error handling above).

### Docker

```bash
# Build
docker build -t sec-10k-fetcher:latest .

# Run — all target companies, PDFs persisted to ./output
# --shm-size=1gb: Chromium uses /dev/shm for rendering; Docker's 64MB default is too
# small for image-heavy filings and fails as a crash or silent degradation, not a
# clear error.
docker run --rm --shm-size=1gb \
  -e SEC_USER_AGENT="Your Company your-contact@example.com" \
  -v "$(pwd)/output:/app/output" --user "$(id -u):$(id -g)" \
  sec-10k-fetcher:latest

# Run — specific tickers
docker run --rm --shm-size=1gb \
  -e SEC_USER_AGENT="Your Company your-contact@example.com" \
  -v "$(pwd)/output:/app/output" --user "$(id -u):$(id -g)" \
  sec-10k-fetcher:latest AAPL META
```

Or via compose:

```bash
export SEC_USER_AGENT="Your Company your-contact@example.com"
docker compose build
docker compose run --rm sec-10k-fetcher            # all companies
docker compose run --rm sec-10k-fetcher AAPL META  # specific tickers
```

`docker-compose.yml`'s `user: "${UID:-1000}:${GID:-1000}"` defaults to `1000:1000`, not
your actual host UID/GID — bash doesn't export `$UID` to child processes, so that
substitution almost always falls through to the hardcoded default. If your host user
isn't UID/GID 1000, run `UID=$(id -u) GID=$(id -g) docker compose run ...` explicitly, or
bind-mounted `./output` will end up owned by a different user than you inside the
container.

### Configuration

All settings are overridable via `SEC_`-prefixed environment variables (see
`sec_filings/config.py`). `SEC_USER_AGENT` is required — there's no default, so the tool
fails at startup rather than silently identifying as whoever wrote it to SEC:

```bash
export SEC_USER_AGENT="Your Company your-contact@example.com"
```

## Project layout

```
.
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt       # loose ranges — what this package is compatible with
├── requirements.lock      # hash-pinned — what the Docker image actually installs
├── requirements-dev.txt
├── sec_filings/
│   ├── config.py       # Settings + TARGET_COMPANIES registry
│   ├── errors.py       # PipelineError hierarchy
│   ├── models.py       # FilingMetadata (Pydantic)
│   ├── client.py       # SECClient — rate-limited, retrying, host-allowlisted HTTP
│   ├── resolver.py     # resolve_latest_10k()
│   ├── converter.py    # HTMLToPDFConverter
│   └── pipeline.py     # CLI entrypoint
├── tests/
│   ├── conftest.py              # shared test_settings fixture
│   ├── test_models.py
│   ├── test_client.py
│   ├── test_resolver.py
│   ├── test_converter.py        # mocked Playwright
│   ├── test_converter_browser.py  # real Chromium, no network (-m browser)
│   ├── test_pipeline.py
│   └── test_live.py             # real SEC API, opt-in (-m live)
└── output/              # generated PDFs, grouped by ticker (gitignored)
```

## Testing

```bash
pip install -r requirements-dev.txt

pytest tests/ -v              # unit tests only (default) — no network, no real browser
pytest -m browser              # + real Chromium, still no network
pytest -m live                  # + the real SEC API (requires SEC_USER_AGENT)

mypy sec_filings/ tests/
ruff check sec_filings/ tests/
```

`test_client.py` exercises retry/backoff/throttle/block-detection/host-allowlist logic
via `httpx.MockTransport`. `test_converter.py` mocks Playwright entirely, down to
invoking the request-route handler directly to test its branches.
`test_converter_browser.py` and `test_live.py` cover what mocks can't: actual pagination,
actual JS-disabled behavior, actual rendering of all 6 target companies' real filings —
excluded from the default run (slower, and `live` needs real internet) but run
explicitly before trusting a change to the parts the mocked tests can't fully verify.

## Design trade-offs

**Playwright over WeasyPrint.** 10-K filings (especially Workiva-generated iXBRL
documents) have wide, deeply nested financial tables and non-trivial CSS. WeasyPrint's
CSS support is closer to CSS 2.1 and has no JS execution; it struggles with modern table
layouts and gets the pagination of wide tables wrong. Playwright drives real Chromium, so
the PDF matches what a person would see opening the filing in a browser and printing it —
at the cost of shipping a full Chromium binary and a slower cold start.

**Letter portrait with a scale ladder, not a fixed custom page size.** An earlier version
forced A3 landscape at a fixed 0.8 scale, reasoning that wider pages would fit wide
tables better. Measured against all 6 target companies, that reasoning didn't hold up:
Letter portrait at scale 1.0 actually overflows (each source page is slightly taller than
the printable area, so content spills onto extra sheets — Goldman Sachs went from 253
expected pages to 509 rendered, with 234 of them nearly empty). The actual fix is a scale
ladder (`pdf_scales = (0.9, 0.8, 0.7)` by default): render at the largest scale, count
pages, and accept the first one that stays within tolerance of the source's own
page-break-marker count. 5 of 6 target companies land on scale 0.9 (~10pt text); Goldman
Sachs's longer filing needs 0.8. This is validated against measured output, not assumed
the way the original A3 choice was.

**Sync Playwright API over async.** The pipeline processes tickers one at a time
regardless, because SEC's rate cap is shared across all of them — there's no concurrent
fetching to gain from an async rewrite. The sync API keeps `pipeline.py` a plain
sequential loop instead of introducing an event loop for no throughput benefit.

**Manual retry/backoff over a library (e.g. tenacity).** `SECClient`'s retry logic
handles five status codes, an optional `Retry-After` header (numeric or HTTP-date,
capped, jittered), and a narrow set of actually-transient transport errors, in about 40
lines total. A decorator-based retry library would add a dependency for less code than
the decorator's own configuration would take.

**Rate limiter has no lock.** `SECClient`'s throttle is a plain check-then-sleep with no
`threading.Lock` — correct for what this actually is: a single-process, sequential CLI
with no concurrent callers. A lock would be dead code today. If this pipeline becomes a
worker pool (multiple processes sharing one egress IP against SEC's *per-IP* limit), the
fix is a shared limiter (e.g. Redis-backed), not a thread lock scoped to one process —
so that's the thing to build if/when concurrency is actually added, not preemptively now.

**Exact-pinned `requirements.lock`, range-pinned `requirements.txt`/`pyproject.toml`.**
The Docker image installs from `requirements.lock` (`pip install --require-hashes`),
generated via `pip-compile --generate-hashes pyproject.toml -o requirements.lock`, so a
build is reproducible down to transitive dependency versions and content hashes, not just
the four direct dependencies. `requirements.txt` and `pyproject.toml` stay range-pinned
for local development flexibility.

**No default `SEC_USER_AGENT`.** An earlier version hardcoded a personal email as the
fallback User-Agent — meaning anyone running the tool without configuring it would
silently identify as that person to SEC. `user_agent` is now a required setting with no
default, validated to contain a contact email address; a missing or malformed value
fails at startup.
