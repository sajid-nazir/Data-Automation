# SEC 10-K Fetcher

Fetches the latest 10-K filing for a set of public companies from SEC EDGAR and converts
each one to PDF.

## Quickstart

### Local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

export SEC_USER_AGENT="Your Company your-contact@example.com"  # required

python -m sec_filings.pipeline          # all target companies
python -m sec_filings.pipeline AAPL META  # specific tickers
```

PDFs land in `./output/{TICKER}/{TICKER}_10K_{filing_date}.pdf`. Exit code is `0` only
if every requested ticker succeeded.

### Docker

```bash
docker build -t sec-10k-fetcher:latest .

docker run --rm --shm-size=1gb \
  -e SEC_USER_AGENT="Your Company your-contact@example.com" \
  -v "$(pwd)/output:/app/output" --user "$(id -u):$(id -g)" \
  sec-10k-fetcher:latest AAPL META
```

Or via compose (`export SEC_USER_AGENT=...` first):

```bash
docker compose build
docker compose run --rm sec-10k-fetcher AAPL META
```

## Project layout

```
sec_filings/
├── config.py      # Settings + TARGET_COMPANIES registry
├── errors.py      # exception hierarchy
├── models.py      # FilingMetadata (Pydantic)
├── client.py      # rate-limited, retrying SEC HTTP client
├── resolver.py     # resolves the latest 10-K from submissions JSON
├── converter.py   # Playwright HTML-to-PDF conversion
└── pipeline.py    # CLI entrypoint
tests/
```

## Testing

```bash
pip install -r requirements-dev.txt

pytest tests/ -v          # unit tests (default) — no network, no real browser
pytest -m browser          # + real Chromium, still no network
pytest -m live               # + the real SEC API

mypy sec_filings/ tests/
ruff check sec_filings/ tests/
```

## Configuration

All settings are overridable via `SEC_`-prefixed environment variables (see
`sec_filings/config.py`). `SEC_USER_AGENT` is required — no default.
