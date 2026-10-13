"""One exception hierarchy for every expected failure mode in this pipeline.

Before this, failures surfaced as a mix of unrelated types - a bare
httpx.HTTPStatusError for a 404, a raw json.JSONDecodeError for a malformed body,
SECRequestError for exhausted retries - with no common base a caller could catch to
mean "this is an operational failure, not a bug in our code." pipeline.py's
per-ticker error handling depends on that distinction existing.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base for every expected, operational failure this pipeline can raise.

    Callers (chiefly pipeline.py's per-ticker loop) catch this to mean "something
    about this ticker's filing failed in an anticipated way" - as opposed to an
    uncaught exception of any other type, which means the code itself has a bug.
    """


class SECError(PipelineError):
    """Base for failures talking to SEC EDGAR."""


class SECRequestError(SECError):
    """A request to SEC failed (after retries, or outright for a non-retryable error).

    `status_code` is None when the failure never got a response at all (a transport
    error, or a host refused before any request was sent).
    """

    def __init__(self, message: str, *, url: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class SECBlockedError(SECRequestError):
    """SEC served its automated-traffic block page instead of the requested document."""


class DisallowedHostError(SECRequestError):
    """A URL (direct or via redirect) pointed outside the configured host allowlist."""


class SECResponseError(SECError):
    """SEC returned a 2xx response whose body isn't usable (bad JSON, wrong shape)."""


class FilingNotFoundError(PipelineError):
    """No matching 10-K could be resolved for a ticker."""


class StaleFilingError(FilingNotFoundError):
    """The latest resolvable 10-K is older than the configured staleness window."""


class ConversionError(PipelineError):
    """Playwright failed to render HTML content as a PDF."""


class PDFValidationError(ConversionError):
    """A rendered PDF failed validation (e.g. page count far below the source's own markers)."""
