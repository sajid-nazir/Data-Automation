"""Pydantic schema for resolved SEC filing metadata."""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, HttpUrl, field_validator

_CIK_PATTERN = re.compile(r"^\d{10}$")
_ACCESSION_PATTERN = re.compile(r"^\d{10}-\d{2}-\d{6}$")


class FilingMetadata(BaseModel):
    """A single filing resolved from the EDGAR submissions API's columnar arrays."""

    ticker: str
    company_name: str
    cik: str
    form_type: str
    filing_date: date
    report_date: date
    accession_number: str
    primary_document: str
    document_url: HttpUrl

    @field_validator("cik")
    @classmethod
    def _validate_cik(cls, value: str) -> str:
        padded = value.zfill(10)
        if not _CIK_PATTERN.match(padded):
            raise ValueError(f"CIK must be numeric, got {value!r}")
        return padded

    @field_validator("accession_number")
    @classmethod
    def _validate_accession_number(cls, value: str) -> str:
        if not _ACCESSION_PATTERN.match(value):
            raise ValueError(
                f"Accession number must match NNNNNNNNNN-NN-NNNNNN, got {value!r}"
            )
        return value

    @field_validator("form_type")
    @classmethod
    def _validate_form_type(cls, value: str) -> str:
        if not value.startswith("10-K"):
            raise ValueError(f"Expected a 10-K form type, got {value!r}")
        return value

    @property
    def accession_number_compact(self) -> str:
        """Accession number with dashes stripped, as used in archive URL paths."""
        return self.accession_number.replace("-", "")
