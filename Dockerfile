# syntax=docker/dockerfile:1
FROM python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

LABEL description="SEC 10-K fetch-and-convert pipeline"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

WORKDIR /app

# Hash-pinned, not the loose requirements.txt, so the build is reproducible down to
# transitive dependency versions and content hashes. Regenerate with:
#   pip-compile --generate-hashes pyproject.toml -o requirements.lock
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    # Installs Chromium plus every OS-level package it needs (fonts, codecs, etc.)
    # via Playwright's own dependency resolver, rather than hand-maintaining an apt list.
    && playwright install --with-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Application code stays root-owned and read-only to the runtime user below - appuser
# only ever needs to execute it, never write to it.
COPY sec_filings/ sec_filings/

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/output \
    && chown appuser:appuser /app/output

USER appuser

ENTRYPOINT ["python", "-m", "sec_filings.pipeline"]
CMD []
