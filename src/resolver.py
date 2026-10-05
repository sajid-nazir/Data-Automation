"""Resolves the latest 10-K filing metadata from EDGAR submissions JSON columnar arrays.

Scaffold only — should port and type the columnar-array traversal validated in
validate_sec_api.py's `resolve_latest_10k`, returning `models.FilingMetadata`. Not yet
implemented.
"""

from __future__ import annotations

from src.config import Settings, settings
from src.models import FilingMetadata


def resolve_latest_10k(
    submissions: dict,
    *,
    ticker: str,
    company_name: str,
    cik: str,
    settings: Settings = settings,
) -> FilingMetadata | None:
    """Return the most recent 10-K filing's metadata, or None if none is found."""
    raise NotImplementedError
