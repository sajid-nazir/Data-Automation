"""
Validate SEC EDGAR Submissions API and resolves latest 10-K URLs.
"""

from dataclasses import dataclass
import time
from typing import Dict, List, Optional
import httpx
from rich.console import Console
from rich.table import Table

console = Console()

# Target Companies & SEC Identifiers (CIK)
# SEC requires 10-digit zero-padded CIK strings for submissions.
TARGET_COMPANIES: Dict[str, str] = {
    "Apple": "0000320193",
    "Meta": "0001326801",
    "Alphabet": "0001652044",
    "Amazon": "0001018724",
    "Netflix": "0001065280",
    "Goldman Sachs": "0000886982",
}


@dataclass
class FilingMetadata:
    company_name: str
    cik: str
    form: str
    filing_date: str
    report_date: str
    accession_number: str
    primary_doc_name: str
    document_url: str


class SECClient:
    BASE_URL = "https://data.sec.gov/submissions"
    ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"

    def __init__(self, user_identity: str):
        """
        user_identity: Format must be 'SampleName sample@domain.com'
        """
        self.headers = {
            "User-Agent": user_identity,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }
        self.client = httpx.Client(headers=self.headers, timeout=15.0)
        self._last_request_time = 0.0
        self._min_interval = 0.12  # ~8 req/sec, safely below SEC's 10 req/sec cap

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def get_submissions(self, cik: str) -> dict:
        self._throttle()
        url = f"{self.BASE_URL}/CIK{cik.zfill(10)}.json"
        response = self.client.get(url)

        if response.status_code == 429:
            raise RuntimeError("SEC Rate limit exceeded. Throttle needs adjustment.")
        response.raise_for_status()
        return response.json()

    def resolve_latest_10k(self, company_name: str, cik: str) -> Optional[FilingMetadata]:
        data = self.get_submissions(cik)
        recent = data.get("filings", {}).get("recent", {})

        if not recent:
            return None

        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])

        # Traverse columnar arrays to find the first (latest) 10-K
        for i, form in enumerate(forms):
            # Target standard annual 10-Ks (skip 10-K/A amendments unless desired)
            if form == "10-K":
                accession = accessions[i]
                primary_doc = primary_docs[i]
                accession_clean = accession.replace("-", "")
                cik_unpadded = str(int(cik))  # Archive URL requires unpadded CIK

                doc_url = (
                    f"{self.ARCHIVE_URL}/{cik_unpadded}/"
                    f"{accession_clean}/{primary_doc}"
                )

                return FilingMetadata(
                    company_name=company_name,
                    cik=cik,
                    form=form,
                    filing_date=filing_dates[i],
                    report_date=report_dates[i],
                    accession_number=accession,
                    primary_doc_name=primary_doc,
                    document_url=doc_url,
                )
        return None

    def close(self):
        self.client.close()


def main():
    """
    Main function to validate SEC EDGAR 10-K metadata.
    """
    USER_IDENTITY = "Sajid sajidchnazir@gmail.com"
    client = SECClient(user_identity=USER_IDENTITY)

    table = Table(title="SEC EDGAR 10-K Metadata Validation", show_header=True)
    table.add_column("Company", style="bold cyan")
    table.add_column("CIK", style="dim")
    table.add_column("Filing Date", style="green")
    table.add_column("Period End", style="yellow")
    table.add_column("Document URL", style="magenta")

    results: List[FilingMetadata] = []

    try:
        for company, cik in TARGET_COMPANIES.items():
            console.print(f"Resolving metadata for [bold]{company}[/]...", style="dim")
            filing = client.resolve_latest_10k(company, cik)
            if filing:
                results.append(filing)
                table.add_row(
                    filing.company_name,
                    filing.cik,
                    filing.filing_date,
                    filing.report_date,
                    filing.document_url,
                )
            else:
                table.add_row(company, cik, "N/A", "N/A", "No 10-K found")

        console.print("\n")
        console.print(table)
    finally:
        client.close()


if __name__ == "__main__":
    main()