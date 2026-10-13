"""HTML transforms applied before handing a fetched SEC filing to the offline renderer.

SEC's filing HTML isn't rendering-ready as fetched: it embeds a third-party
bot-detection sensor script at an absolute path that resolves to nothing once
rendered from a local file, and its images are referenced by paths relative to
the SEC archive, which likewise don't resolve offline. Both are fixed here so
`converter.py` can stay a pure renderer with no knowledge of SEC's URL layout.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from urllib.parse import urljoin

from sec_filings.client import SECClient

_SCRIPT_TAG = re.compile(r"<script\b[^>]*?(?:/>|>.*?</script\s*>)", re.IGNORECASE | re.DOTALL)
_IMG_SRC = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.IGNORECASE)


def strip_scripts(html: str) -> str:
    """Remove all <script> tags.

    The renderer only needs a static visual layout for PDF conversion - no filing
    content depends on JS execution. Leaving scripts in place also means a dangling
    reference (e.g. SEC's bot-detection sensor script) attempts and fails to load
    on every render for no benefit.
    """
    return _SCRIPT_TAG.sub("", html)


def inline_images(html: str, source_url: str, client: SECClient) -> str:
    """Replace relative <img src> references with base64 data URIs.

    The document is rendered from a local temp file with no network access, so a
    path relative to the SEC archive (e.g. "aapl-20250927_g1.jpg") would otherwise
    render as a broken image. Each referenced image is fetched through the same
    rate-limited `SECClient` used for everything else, never by the browser itself.
    """

    def _replace(match: re.Match[str]) -> str:
        prefix, src, suffix = match.group(1), match.group(2), match.group(3)
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)

        image_url = urljoin(source_url, src)
        content = client.get_binary(image_url)
        mime_type, _ = mimetypes.guess_type(src)
        mime_type = mime_type or "application/octet-stream"
        encoded = base64.b64encode(content).decode("ascii")
        return f"{prefix}data:{mime_type};base64,{encoded}{suffix}"

    return _IMG_SRC.sub(_replace, html)
