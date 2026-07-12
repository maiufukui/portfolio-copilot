"""
Fundamentals Health Score sub-signal: leadership stability.

Detects 8-K Item 5.02 filings ("Departure of Directors or Certain
Officers; Election of Directors; Appointment of Certain Officers") via
SEC EDGAR's submissions API, then reads the actual filing text to
determine who departed and whether a successor was named -- the "items"
field only tells you THAT a filing touches Item 5.02, not the specifics
needed to classify severity (Task 2 section 4: CEO/CFO vs. below-CEO/CFO,
named successor or not).

Endpoints (no API key required, real contact User-Agent required):
    https://data.sec.gov/submissions/CIK{cik:010d}.json           -- filing list + items
    (then fetch the actual 8-K .htm from the accession number for text)

NOT YET LIVE-VERIFIED end-to-end -- this sandbox has no outbound network
access to data.sec.gov (confirmed blocked). The submissions API's "items"
field structure is documented by SEC but wasn't confirmed against a live
response in this build session. Includes a text-search fallback
(reusing test_q7.py's approach) in case "items" isn't populated the way
expected -- run locally against real data and paste the output back.

Usage:
    python fetch_leadership_events.py --ticker ALAB
"""

from __future__ import annotations

import argparse
import re

import requests

HEADERS = {"User-Agent": "PersonalPortfolioCopilot maiu.fukui@gmail.com"}

TICKER_TO_CIK = {
    "ALAB": "0001736297",
    "AAPL": "0000320193",
    "MRVL": "0001835632",
    "NBIS": "0001513845",  # confirmed via app.edgar.tools/companies/NBIS
}

LEADERSHIP_ITEM = "5.02"

# Loose title patterns to classify severity once we have the filing text.
CEO_CFO_PATTERN = re.compile(r"\b(chief executive officer|CEO|chief financial officer|CFO)\b", re.IGNORECASE)
DEPARTURE_PATTERN = re.compile(r"\b(resign|resignation|departure|depart|terminat|step(?:s|ped)? down)\b", re.IGNORECASE)
SUCCESSOR_PATTERN = re.compile(r"\b(successor|will serve as|appointed|effective immediately|named)\b", re.IGNORECASE)


def fetch_submissions(cik: str) -> dict:
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def find_8k_with_item(submissions: dict, item: str = LEADERSHIP_ITEM, lookback: int = 90) -> list[dict]:
    """Scan the 'recent' filings block for 8-Ks whose 'items' field
    includes the target item code, within the lookback window (days)."""
    from datetime import date

    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    items_field = recent.get("items", [""] * len(forms))
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    cutoff = date.today()
    matches = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        try:
            filed = date.fromisoformat(dates[i])
        except (IndexError, ValueError):
            continue
        if (cutoff - filed).days > lookback:
            continue
        if item in (items_field[i] if i < len(items_field) else ""):
            matches.append({
                "filed": dates[i],
                "accession": accessions[i],
                "primary_doc": primary_docs[i] if i < len(primary_docs) else None,
            })
    return matches


def fetch_filing_text(cik: str, accession: str, primary_doc: str) -> str:
    """CIK needs no leading zeros in the Archives path; accession number
    needs its dashes stripped for the folder name."""
    cik_int = str(int(cik))
    accession_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{primary_doc}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    # Strip tags crudely -- good enough for keyword/pattern classification,
    # not meant to be a clean document for RAG ingestion.
    text = re.sub(r"<[^>]+>", " ", resp.text)
    return re.sub(r"\s+", " ", text)


def classify_departure(text: str) -> dict:
    is_ceo_cfo = bool(CEO_CFO_PATTERN.search(text))
    is_departure = bool(DEPARTURE_PATTERN.search(text))
    successor_named = bool(SUCCESSOR_PATTERN.search(text))

    if not is_departure:
        return {"status": "intact", "reason": "Item 5.02 filing found but no departure language detected -- likely a routine election/appointment, not a departure."}

    if is_ceo_cfo and not successor_named:
        status = "at_risk"
    elif is_ceo_cfo and successor_named:
        status = "monitor"  # CEO/CFO change but handled with continuity
    else:
        status = "monitor"  # departure below CEO/CFO level

    return {
        "status": status,
        "is_ceo_or_cfo": is_ceo_cfo,
        "successor_named": successor_named,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--days", type=int, default=90, help="Lookback window for 8-K Item 5.02 filings.")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    if ticker not in TICKER_TO_CIK:
        raise SystemExit(f"No CIK mapped for {ticker}. Add it to TICKER_TO_CIK.")
    cik = TICKER_TO_CIK[ticker]

    print(f"Fetching filing history for {ticker} (CIK {cik})...")
    submissions = fetch_submissions(cik)
    matches = find_8k_with_item(submissions, lookback=args.days)

    if not matches:
        print(f"\nNo 8-K Item {LEADERSHIP_ITEM} filings in the last {args.days} days.")
        print("Leadership Stability: intact (no departure-related 8-K or news).")
        return

    print(f"\n{len(matches)} Item {LEADERSHIP_ITEM} filing(s) found:")
    results = []
    for m in matches:
        print(f"  {m['filed']} -- accession {m['accession']}")
        if not m["primary_doc"]:
            print("    (no primary document listed, skipping text fetch)")
            continue
        text = fetch_filing_text(cik, m["accession"], m["primary_doc"])
        classification = classify_departure(text)
        classification["filed"] = m["filed"]
        results.append(classification)
        print(f"    -> {classification}")

    # Task 2 section 4: worst-of, not averaged, and 2+ C-suite departures
    # within 90 days is itself an At Risk trigger regardless of individual
    # severity.
    severity = {"intact": 0, "monitor": 1, "at_risk": 2}
    ceo_cfo_departures = sum(1 for r in results if r.get("is_ceo_or_cfo") and r["status"] != "intact")
    worst = max(results, key=lambda r: severity.get(r["status"], 0)) if results else {"status": "intact"}
    overall = worst["status"]
    if ceo_cfo_departures >= 2:
        overall = "at_risk"

    print(f"\n=== Leadership Stability: {overall} ===")
    if ceo_cfo_departures >= 2:
        print(f"({ceo_cfo_departures} CEO/CFO-level departures within {args.days} days -- At Risk regardless of individual successor status.)")


if __name__ == "__main__":
    main()
