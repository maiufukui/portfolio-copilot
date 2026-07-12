"""
EDGAR filing downloader — Portfolio Tracker Assistant

Downloads the most recent 10-K, 10-Q, and 8-K for each ticker (falling
back to 20-F/6-K for foreign private issuers like NBIS) and saves the
RAW filing document — not the Inline Viewer wrapper — into
Data/<TICKER>/.

No API key required. SEC EDGAR is public, but requires a real,
identifying User-Agent string per their fair-access policy — replace
the placeholder email below with your own before running this.

Usage:
    pip install requests
    python fetch_edgar_filings.py
"""

import os
import time

import requests

# --- SEC requires a real contact string here, not a placeholder ---
USER_AGENT = "Portfolio Tracker Assistant maiu.fukui@gmail.com"
HEADERS = {"User-Agent": USER_AGENT}

TICKERS = ["MRVL", "AAPL", "ALAB", "NBIS"]

# form_type -> how many of the most recent filings of that type to grab.
# 4 years of 10-Ks, and enough 10-Qs to cover those same ~4 years (3/year typical).
DOMESTIC_FORM_COUNTS = {"10-K": 4, "10-Q": 16, "8-K": 1}
FOREIGN_FORM_COUNTS = {"20-F": 4, "6-K": 16}  # used automatically if domestic forms aren't found

DATA_DIR = "Data"


def get_cik_map():
    """Fetch SEC's ticker -> CIK mapping (one file covers every public company)."""
    url = "https://www.sec.gov/files/company_tickers.json"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    raw = resp.json()
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in raw.values()}


def get_filings(cik, form_counts):
    """Return up to N most recent filings of each requested form type for this CIK.

    form_counts: dict like {"10-K": 4, "10-Q": 16} — how many of each form
    to collect, most recent first (EDGAR's 'recent' list is already
    newest-first).
    """
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()

    recent = data["filings"]["recent"]
    results = []
    counts_seen = {form: 0 for form in form_counts}

    for i, form in enumerate(recent["form"]):
        if form in form_counts and counts_seen[form] < form_counts[form]:
            accession = recent["accessionNumber"][i].replace("-", "")
            primary_doc = recent["primaryDocument"][i]
            filing_date = recent["filingDate"][i]
            results.append((form, filing_date, accession, primary_doc))
            counts_seen[form] += 1
        if all(counts_seen[form] >= form_counts[form] for form in form_counts):
            break

    return results, int(cik)


def download_filing(cik_int, accession, primary_doc, dest_path):
    """Download the raw filing document directly — this is the fix for the
    'saved only the cover page' problem: we hit the actual .htm file on
    sec.gov/Archives, never the /ix?doc= Inline Viewer wrapper."""
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{primary_doc}"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(resp.content)
    print(f"Saved {dest_path}  <-  {url}")


def main():
    cik_map = get_cik_map()

    for ticker in TICKERS:
        cik = cik_map.get(ticker.upper())
        if not cik:
            print(f"!! Could not find CIK for {ticker}, skipping")
            continue

        ticker_dir = os.path.join(DATA_DIR, ticker)
        os.makedirs(ticker_dir, exist_ok=True)

        filings, cik_int = get_filings(cik, DOMESTIC_FORM_COUNTS)

        if not filings:
            print(f"{ticker}: no 10-K/10-Q/8-K found — trying 20-F/6-K (foreign private issuer)")
            filings, cik_int = get_filings(cik, FOREIGN_FORM_COUNTS)

        if not filings:
            print(f"!! No filings found at all for {ticker}")
            continue

        for form, filing_date, accession, primary_doc in filings:
            ext = os.path.splitext(primary_doc)[1] or ".htm"
            filename = f"{form.replace('/', '-')}_{filing_date}{ext}"
            dest_path = os.path.join(ticker_dir, filename)
            download_filing(cik_int, accession, primary_doc, dest_path)
            time.sleep(0.3)  # stay well under SEC's rate limits


if __name__ == "__main__":
    main()
