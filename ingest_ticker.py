"""
Single-entrypoint ticker onboarding — Portfolio Tracker Assistant (item 3, step 5)

Before this script: adding a ticker meant three separately-remembered
manual steps (run fetch_edgar_filings.py, hand-build a transcript, run
fetch_xbrl_financials.py). This wires filings + transcript into one
call. XBRL is deliberately NOT a fetch-and-save step here -- see below.

Scope boundary, stated directly rather than left implicit: this does
NOT touch fetch_xbrl_financials.py's TICKER_TO_CIK or app/tools.py's
TICKER_TO_COMPANY. Both are small, static, hand-maintained dicts
imported directly into the live agent's per-query path
(get_fundamentals_health_score, called on every single chat turn) --
refactoring either into something dynamic under time pressure, without
a real test pass against that live path, would trade a small avoided
dict edit for real risk to a tested, load-bearing piece of the app.
Out of scope for this item. Instead, this script resolves the new
ticker's real CIK from SEC's own company_tickers.json (the same source
fetch_edgar_filings.py already uses, not guessed) and prints the exact
line to paste into each dict -- a copy-paste, not a lookup task.

XBRL itself needs no local fetch/save step at all: fetch_xbrl_financials's
fetch_revenue/fetch_concept hit SEC's live XBRL API at query time, every
time -- there's no local cache file for a transcript-style pipeline to
produce. "Ingesting" XBRL for a new ticker is entirely the CIK-mapping
step above, not a data-fetch step.

Usage:
    python ingest_ticker.py --ticker PANW --company "Palo Alto Networks"
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from fetch_edgar_filings import get_cik_map, ingest_filings_for_ticker
from fetch_transcripts import ingest_transcript

load_dotenv()


def resolve_cik(ticker: str) -> str | None:
    """Same source and format fetch_edgar_filings.py's get_cik_map()
    already uses -- one real lookup, not a second guess at SEC's data."""
    cik_map = get_cik_map()
    return cik_map.get(ticker.upper())


def ingest_ticker(ticker: str, company: str) -> dict:
    """Runs both real fetch/save steps for one new ticker and reports
    what still needs a manual one-line dict edit. Returns a summary dict
    rather than just printing, so a caller (or a future onboarding-form
    endpoint, doc item 5) can check success programmatically instead of
    scraping stdout.
    """
    ticker = ticker.upper()
    print(f"=== Onboarding {ticker} ({company}) ===\n")

    print("[1/3] EDGAR filings (10-K/10-Q/8-K)...")
    cik_map = get_cik_map()
    filings_ok = ingest_filings_for_ticker(ticker, cik_map)

    print("\n[2/3] Earnings-call transcript...")
    transcript_path = ingest_transcript(ticker)

    print("\n[3/3] XBRL fundamentals -- no fetch/save step needed (live-queried at query time).")
    cik = cik_map.get(ticker)
    if cik:
        print(f"    Real CIK resolved from SEC: {cik}")
        print(f'    Paste into fetch_xbrl_financials.py\'s TICKER_TO_CIK: "{ticker}": "{cik}",')
    else:
        print(f"    !! Could not resolve a CIK for {ticker} -- check the ticker symbol.")

    print(f'\n    Paste into app/tools.py\'s TICKER_TO_COMPANY: "{ticker}": "{company}",')
    print("    (Required for the frontend/chat to recognize this ticker at all --")
    print("     server.py's /tickers endpoint and search_filings both key off that dict.)")

    return {
        "ticker": ticker,
        "filings_ok": filings_ok,
        "transcript_path": transcript_path,
        "cik": cik,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company", required=True, help='Full company name, e.g. "Palo Alto Networks"')
    args = parser.parse_args()

    result = ingest_ticker(args.ticker, args.company)

    print("\n=== Summary ===")
    print(result)
    if not result["filings_ok"] or not result["transcript_path"] or not result["cik"]:
        print("\n!! One or more steps failed -- do NOT add this ticker to TICKER_TO_COMPANY "
              "until every step above is confirmed working. A half-onboarded ticker (visible in "
              "the UI but missing filings or transcript data) is worse than not onboarding it at "
              "all -- see fetch_transcripts.py's QA-gate reasoning, same principle applies here.")


if __name__ == "__main__":
    main()
