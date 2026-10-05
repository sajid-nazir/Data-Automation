# syntax=docker/dockerfile:1
FROM python:3.11-slim

LABEL description="SEC 10-K fetch-and-convert pipeline"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

WORKDIR /app

# Install Python deps first so this layer only invalidates when requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # Installs Chromium plus every OS-level package it needs (fonts, codecs, etc.)
    # via Playwright's own dependency resolver, rather than hand-maintaining an apt list.
    && playwright install --with-deps chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY src/ src/

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/output \
    && chown -R appuser:appuser /app /opt/playwright-browsers

USER appuser

ENTRYPOINT ["python", "-m", "src.pipeline"]
CMD []
