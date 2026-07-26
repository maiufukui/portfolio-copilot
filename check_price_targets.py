"""
Live verification probe -- NOT wired into the app, NOT an eval harness.

Item 6's "analyst estimates/price targets" step starts by checking which of
Finnhub's or FMP's price-target endpoint actually returns usable data across
all 6 tickers, before writing any get_market_data integration against either
one. This project already got burned once by trusting a vendor's docs page
over a live call (FMP's historical-price endpoint turned out to be gated on
the free tier after it was already built against -- see app/tools.py's FMP
comments and Portoflio Copilot Demo.md item 6's caveats). Don't repeat that:
run this, look at the real output, then decide.

Run ONCE, by hand, read the output yourself -- this script makes no decision
and writes nothing to the DB or app code. Network calls are outbound only
(GET requests to Finnhub/FMP); cannot be run from this sandbox (both hosts
are outside the sandbox's network allowlist), so this has not been executed
or verified by me -- only the endpoint shapes/params are grounded in each
vendor's current docs (checked live today, not from training-data memory):
  - Finnhub: https://finnhub.io/docs/api/price-target (path/param names
    confirmed via search of finnhub's own field docs: symbol, token)
  - FMP: https://site.financialmodelingprep.com/developer/docs/stable/price-target-summary
    (fetched today -- endpoint is https://financialmodelingprep.com/stable/
    price-target-summary?symbol=X, "stable" tier, not the legacy v3/v4 paths
    FMP is deprecating)

IMPORTANT -- FMP_API_KEY is not currently in .env or .env.example. It was
deliberately removed (render.yaml, .env.example) when FMP's historical-price
endpoint was confirmed gated and its dead code path was deleted. To test FMP
here, add FMP_API_KEY back to your local .env (sign up:
https://site.financialmodelingprep.com/register, free tier). If you'd rather
not bother re-adding an FMP key just to test a path that may get dropped
anyway, run with --skip-fmp and this only checks Finnhub.

This prints raw JSON per ticker per vendor plus a plain PASS/GATED/ERROR
verdict line, so you can see the real shape of what each free tier actually
returns (or doesn't) rather than trusting either vendor's marketing copy.

Usage:
    python check_price_targets.py                  # all 6 tickers, both vendors
    python check_price_targets.py --skip-fmp        # Finnhub only
    python check_price_targets.py --ticker PANW --ticker DELL   # just these 2
"""

from __future__ import annotations

import argparse
import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

ALL_TICKERS = ["ALAB", "AAPL", "MRVL", "NBIS", "PANW", "DELL"]

FINNHUB_PRICE_TARGET_URL = "https://finnhub.io/api/v1/stock/price-target"
FMP_PRICE_TARGET_SUMMARY_URL = "https://financialmodelingprep.com/stable/price-target-summary"


def check_finnhub(ticker: str, api_key: str) -> dict:
    """Returns {'status': 'PASS'|'EMPTY'|'ERROR', 'http_status': int, 'body': ...}.
    PASS = got a 200 with what looks like real target data (targetMean etc
    present and non-null). EMPTY = 200 but no usable numbers (likely means
    Finnhub has no coverage for this ticker, not necessarily a tier-gating
    issue). ERROR = non-200 (401/403 = auth/tier gating; anything else =
    something worth reading the raw body for)."""
    resp = requests.get(FINNHUB_PRICE_TARGET_URL, params={"symbol": ticker, "token": api_key})
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    if resp.status_code != 200:
        return {"status": "ERROR", "http_status": resp.status_code, "body": body}
    if isinstance(body, dict) and body.get("targetMean"):
        return {"status": "PASS", "http_status": resp.status_code, "body": body}
    return {"status": "EMPTY", "http_status": resp.status_code, "body": body}


def check_fmp(ticker: str, api_key: str) -> dict:
    """Same verdict shape as check_finnhub. FMP's stable-tier response is a
    list (possibly empty) of summary objects rather than a single dict --
    handled separately here, not assumed to match Finnhub's shape."""
    resp = requests.get(FMP_PRICE_TARGET_SUMMARY_URL, params={"symbol": ticker, "apikey": api_key})
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    if resp.status_code != 200:
        return {"status": "ERROR", "http_status": resp.status_code, "body": body}
    # FMP returns {"Error Message": "..."} with a 200 status for some
    # gating/plan-limit cases instead of a real error status code -- check
    # for that explicitly rather than trusting the HTTP status alone.
    if isinstance(body, dict) and "Error Message" in body:
        return {"status": "ERROR", "http_status": resp.status_code, "body": body}
    if isinstance(body, list) and len(body) > 0:
        return {"status": "PASS", "http_status": resp.status_code, "body": body}
    return {"status": "EMPTY", "http_status": resp.status_code, "body": body}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ticker", action="append", help="Repeatable. Defaults to all 6 tracked tickers."
    )
    parser.add_argument(
        "--skip-fmp", action="store_true", help="Only check Finnhub (skip if you haven't added FMP_API_KEY)."
    )
    args = parser.parse_args()
    tickers = args.ticker or ALL_TICKERS

    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    if not finnhub_key:
        raise SystemExit("FINNHUB_API_KEY not set in .env")

    fmp_key = None
    if not args.skip_fmp:
        fmp_key = os.environ.get("FMP_API_KEY")
        if not fmp_key:
            print(
                "FMP_API_KEY not set in .env -- FMP was skipped. Add it to .env to test FMP, "
                "or pass --skip-fmp to silence this. See this script's docstring for signup link.\n"
            )

    results = {}

    for ticker in tickers:
        print(f"\n{'=' * 70}\n{ticker}\n{'=' * 70}")

        print(f"[Finnhub] GET {FINNHUB_PRICE_TARGET_URL}?symbol={ticker}")
        fh = check_finnhub(ticker, finnhub_key)
        print(f"  HTTP {fh['http_status']} -> {fh['status']}")
        print(f"  {json.dumps(fh['body'], indent=2)[:500]}")

        fmp = None
        if fmp_key:
            print(f"\n[FMP] GET {FMP_PRICE_TARGET_SUMMARY_URL}?symbol={ticker}")
            fmp = check_fmp(ticker, fmp_key)
            print(f"  HTTP {fmp['http_status']} -> {fmp['status']}")
            print(f"  {json.dumps(fmp['body'], indent=2)[:500]}")

        results[ticker] = {"finnhub": fh["status"], "fmp": fmp["status"] if fmp else "SKIPPED"}

    print(f"\n\n{'=' * 70}\nSUMMARY -- {len(tickers)} ticker(s)\n{'=' * 70}")
    for ticker, r in results.items():
        print(f"  {ticker:6s}  Finnhub: {r['finnhub']:8s}  FMP: {r['fmp']}")

    finnhub_pass = sum(1 for r in results.values() if r["finnhub"] == "PASS")
    fmp_pass = sum(1 for r in results.values() if r["fmp"] == "PASS")
    print(f"\nFinnhub PASS on {finnhub_pass}/{len(tickers)} tickers.")
    if fmp_key:
        print(f"FMP PASS on {fmp_pass}/{len(tickers)} tickers.")
    print(
        "\nRead the raw bodies above, not just the verdict counts -- ERROR on a 401/403 means "
        "the endpoint is plan-gated (same failure mode as the old FMP historical-price bug); "
        "EMPTY on 200 likely just means no analyst coverage for that specific ticker."
    )


if __name__ == "__main__":
    main()
