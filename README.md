# SEC 10-K Fetcher

Fetches the latest 10-K filing for a set of public companies from SEC EDGAR and converts
each one to a paginated PDF.

## Architecture

The pipeline is split into four single-responsibility modules, wired together by a thin
CLI orchestrator:

| Module | Responsibility |
|---|---|
| `src/config.py` | SEC endpoints, compliant User-Agent, rate limit, output paths, target company registry |
| `src/client.py` | `SECClient` — all HTTP traffic to sec.gov |
| `src/resolver.py` | Parses `filings.recent`'s parallel arrays into a typed `FilingMetadata` |
| `src/sanitizer.py` | Prepares fetched HTML for offline rendering (strips scripts, inlines images) |
| `src/converter.py` | `HTMLToPDFConverter` — renders already-fetched HTML to PDF via Playwright |
| `src/pipeline.py` | CLI entrypoint: fetch → resolve → convert → save, per ticker |

### Fetching and rendering are deliberately decoupled

`SECClient` is the only thing that talks to sec.gov. It enforces a strict 10 req/s
throttle, sends the configured User-Agent, and retries with exponential backoff on
`429`/`500`/`502`/`503`/`504` (honoring a server `Retry-After` header when present).
The submissions JSON, the filing HTML, and every image embedded in it all go through
it, so every request to SEC — not just the first one — is governed by the same policy.

`HTMLToPDFConverter` never makes a network request. It takes HTML that's already been
fetched and fully self-contained, writes it to a local temp file, and points
Playwright's headless Chromium at `file://` to render it.

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
earlier version of this document attributed the block to `navigator.webdriver`; that
was disproven by patching it to `false` via `page.add_init_script` and confirming the
block persisted regardless. The header above is the one piece of evidence that actually
explains an immediate block.) No User-Agent string changes that header, so the only
reliable fix is to keep Chromium off the network entirely: fetch through `httpx` (no
such header), hand Playwright only the resulting markup.

Routing the fetch through `SECClient` surfaced a second problem: the fetched HTML
references images by paths relative to the SEC archive folder and embeds a
bot-detection sensor script at an absolute path — neither resolves once the document is
rendered from a local temp file. `src/sanitizer.py` fixes both before the HTML ever
reaches the renderer: `inline_images()` fetches each relative image through the same
rate-limited client and inlines it as a base64 data URI; `strip_scripts()` removes all
`<script>` tags, since nothing about a static PDF render depends on JS execution.

### Error handling

`pipeline.run()` validates every requested ticker against `TARGET_COMPANIES` up front —
before a client or browser is ever started — then processes the rest sequentially,
wrapping each one in its own `try/except`. A SEC outage or a Playwright timeout logs an
error and moves on to the next company; one failure never aborts the batch. `run()`
returns whether every ticker succeeded, and `main()` turns that into the process exit
code, so a fully-failed batch run from cron, CI, or a worker queue is visibly a failure,
not a silent success.

Two specific failure modes get dedicated handling rather than being left to show up as
a generic exception:
- `SECClient.get_document()` raises `SECBlockedError` if SEC's block-page text shows up
  in a `200` response body — a non-error status code that isn't actually the document.
- `HTMLToPDFConverter` logs a warning (not a hard failure — the heuristic isn't proven
  reliable enough to fail a batch on) if a generated PDF has far fewer pages than the
  source HTML's `page-break-after` marker count suggests it should. This exists because
  a PDF was observed mid-development to render only 8 of 61 actual pages with no error
  of any kind — Chromium returned successfully with a plausible-looking PDF that was
  silently missing 87% of the filing. The specific cause was investigated (a dangling
  script reference was the leading hypothesis) but never conclusively reproduced after
  the initial occurrence, so this is a detection net, not a confirmed fix.

## Quickstart

### Local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Defaults to every company in TARGET_COMPANIES
python -m src.pipeline

# Or specific tickers
python -m src.pipeline AAPL META
```

PDFs land in `./output/{TICKER}/{TICKER}_10K_{filing_date}.pdf`.

### Docker

```bash
# Build
docker build -t sec-10k-fetcher:latest .

# Run — all target companies, PDFs persisted to ./output
# --shm-size=1gb: Chromium uses /dev/shm for rendering; Docker's 64MB default is too
# small for image-heavy filings and fails as a crash or silent degradation, not a
# clear error.
docker run --rm --shm-size=1gb -v "$(pwd)/output:/app/output" --user "$(id -u):$(id -g)" sec-10k-fetcher:latest

# Run — specific tickers
docker run --rm --shm-size=1gb -v "$(pwd)/output:/app/output" --user "$(id -u):$(id -g)" sec-10k-fetcher:latest AAPL META
```

Or via compose (mounts `./output`, sets `shm_size`, and matches your host UID/GID
automatically):

```bash
docker compose build
docker compose run --rm sec-10k-fetcher            # all companies
docker compose run --rm sec-10k-fetcher AAPL META  # specific tickers
```

### Configuration

All settings are overridable via `SEC_`-prefixed environment variables (see
`src/config.py`), most notably:

```bash
export SEC_USER_AGENT="Your Company your-contact@example.com"
```

## Project layout

```
.
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── src/
│   ├── config.py       # Settings + TARGET_COMPANIES registry
│   ├── models.py       # FilingMetadata (Pydantic)
│   ├── client.py       # SECClient — rate-limited, retrying HTTP
│   ├── resolver.py     # resolve_latest_10k()
│   ├── sanitizer.py    # strip_scripts(), inline_images()
│   ├── converter.py    # HTMLToPDFConverter
│   └── pipeline.py     # CLI entrypoint
├── tests/
│   ├── test_models.py
│   ├── test_client.py
│   ├── test_resolver.py
│   ├── test_sanitizer.py
│   ├── test_converter.py
│   └── test_pipeline.py
└── output/              # generated PDFs, grouped by ticker (gitignored)
```

## Testing

```bash
pip install -r requirements-dev.txt

pytest tests/ -v
mypy src/
```

All tests run against mocks — no network calls or real browser launches in CI.
`test_client.py` exercises the retry/backoff/throttle/block-detection logic via
`httpx.MockTransport` rather than a live server. The resolver tests use a mock
EDGAR-shaped JSON fixture with a 10-K/A ahead of the real 10-K, to confirm amendments
are skipped.

## Design trade-offs

**Playwright over WeasyPrint.** 10-K filings (especially Workiva-generated iXBRL
documents) are single HTML files with wide, deeply nested financial tables and
non-trivial CSS. WeasyPrint's CSS support is closer to CSS 2.1 and has no JS execution;
it struggles with modern table layouts and gets the pagination of wide tables wrong.
Playwright drives real Chromium, so the PDF matches what a person would see opening the
filing in a browser and printing it — at the cost of shipping a full Chromium binary and
a slower cold start. For this use case, rendering fidelity on financial tables mattered
more than image size.

**Sync Playwright API over async.** The pipeline processes tickers one at a time
regardless, because SEC's 10 req/s cap is shared across all of them — there's no
concurrent fetching to gain from an async rewrite. The sync API keeps `pipeline.py` a
plain sequential loop instead of introducing an event loop for no throughput benefit.

**Manual retry/backoff over a library (e.g. tenacity).** `SECClient`'s retry logic is
~20 lines and only needs to handle five status codes (`429`, `500`, `502`, `503`,
`504`), an optional `Retry-After` header, and transport errors. A decorator-based retry
library would add a dependency for less code than the decorator's own configuration
would take.

**HTML fetched separately from rendering.** Covered above — this isn't a stylistic
preference, it's the fix for SEC's anti-bot block on direct Playwright navigation.

**Exact version pins in `requirements.txt`, range pins in `pyproject.toml`.** The
Docker image is built from `requirements.txt`, so that file pins the exact versions
actually tested (`httpx==0.28.1`, `playwright==1.63.0`, etc.) for reproducible builds.
`pyproject.toml`'s ranges stay loose, since that file describes what this package is
compatible with if installed alongside other dependencies, not what's deployed.
