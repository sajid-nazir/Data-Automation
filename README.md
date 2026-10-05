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
| `src/converter.py` | `HTMLToPDFConverter` — renders already-fetched HTML to PDF via Playwright |
| `src/pipeline.py` | CLI entrypoint: fetch → resolve → convert → save, per ticker |

### Fetching and rendering are deliberately decoupled

`SECClient` is the only thing that talks to sec.gov. It enforces a strict 10 req/s
throttle, sends the configured User-Agent, and retries with exponential backoff on
`429`/`503`. Both the submissions JSON (`data.sec.gov`) and the filing HTML itself
(`www.sec.gov/Archives/...`) go through it, so every request to SEC is governed by the
same policy.

`HTMLToPDFConverter` never makes a network request. It takes HTML that's already been
fetched, writes it to a local temp file, and points Playwright's headless Chromium at
`file://` to render it.

This split exists because of a real failure mode, not just layering for its own sake:
pointing Playwright directly at a `sec.gov` URL gets served SEC's "undeclared automated
tool" block page, even with a fully compliant User-Agent string. SEC's edge layer
fingerprints the headless-browser signal itself (`navigator.webdriver = true`), which a
User-Agent header can't paper over. Routing the fetch through `httpx` (which has no such
tell) and handing Playwright only the resulting markup sidesteps the block entirely, and
keeps that fetch inside the one rate-limited client instead of Chromium making an
ungoverned second request per filing.

### Error handling

`pipeline.run()` processes tickers sequentially and wraps each one in its own
`try/except`. A bad ticker, a SEC outage, or a Playwright timeout logs an error and moves
on to the next company — one failure never aborts the batch. A summary line reports
succeeded/skipped/failed counts at the end.

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
docker run --rm -v "$(pwd)/output:/app/output" --user "$(id -u):$(id -g)" sec-10k-fetcher:latest

# Run — specific tickers
docker run --rm -v "$(pwd)/output:/app/output" --user "$(id -u):$(id -g)" sec-10k-fetcher:latest AAPL META
```

Or via compose (mounts `./output` and matches your host UID/GID automatically):

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
│   ├── resolver.py      # resolve_latest_10k()
│   ├── converter.py    # HTMLToPDFConverter
│   └── pipeline.py     # CLI entrypoint
├── tests/
│   ├── test_models.py
│   ├── test_resolver.py
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

All client/converter/pipeline tests run against mocks — no network calls or real
browser launches in CI. The resolver tests use a mock EDGAR-shaped JSON fixture with a
10-K/A ahead of the real 10-K, to confirm amendments are skipped.

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
~15 lines and only needs to handle two status codes (`429`, `503`) plus transport
errors. A decorator-based retry library would add a dependency for less code than the
decorator's own configuration would take.

**HTML fetched separately from rendering.** Covered above — this isn't a stylistic
preference, it's the fix for SEC's anti-bot block on direct Playwright navigation.
