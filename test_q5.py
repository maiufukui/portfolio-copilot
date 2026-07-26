"""
Test harness for Eval Question 5 (Task 1):
"Is there any insider selling in my holdings this week?"

Calls Finnhub's insider-transactions endpoint (Form 3/4/5 sourced) for
each ticker in the portfolio, filters to the requested time window, and
separates open-market sells (transactionCode 'S') from other activity
(purchases 'P', awards/grants, etc.) so the eval question's specific
"selling" framing is answered directly while still showing full context.

Usage:
    python test_q5.py
    python test_q5.py --days 14
    python test_q5.py --ticker AAPL --ticker MRVL
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

# PANW/DELL added (item 6) -- real bug caught during Maiu's review: this
# stayed hardcoded at 4 tickers even after TICKER_TO_COMPANY (app/tools.py)
# grew to 6, so Q4's portfolio-wide "is there any insider selling in my
# holdings" check would have silently kept excluding both new tickers.
# Same "one ticker list per script" debt as everywhere else in this repo,
# not a new decision -- update alongside the others when a 7th lands.
DEFAULT_TICKERS = ["MRVL", "AAPL", "ALAB", "NBIS", "PANW", "DELL"]

# Common Form 4 transaction codes worth knowing:
# S = open-market sale, P = open-market purchase, A = grant/award,
# M = option exercise, F = tax withholding (shares withheld, not a real sale/buy)
CODE_LABELS = {
    "S": "SELL (open market)",
    "P": "BUY (open market)",
    "A": "Award/Grant",
    "M": "Option Exercise",
    "F": "Tax Withholding",
    "G": "Gift",
}


def fetch_insider_transactions(symbol: str, api_key: str) -> list[dict]:
    url = "https://finnhub.io/api/v1/stock/insider-transactions"
    resp = requests.get(url, params={"symbol": symbol, "token": api_key})
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def within_window(transaction_date: str, cutoff: datetime) -> bool:
    try:
        dt = datetime.strptime(transaction_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return dt >= cutoff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ticker", action="append", help="Repeatable. Defaults to the full portfolio."
    )
    parser.add_argument("--days", type=int, default=7, help="Lookback window, default 7 (this week).")
    args = parser.parse_args()

    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise SystemExit("FINNHUB_API_KEY not set in .env")

    tickers = args.ticker or DEFAULT_TICKERS
    cutoff = datetime.now() - timedelta(days=args.days)

    all_sells = []
    all_other = []

    for ticker in tickers:
        print(f"Fetching insider transactions for {ticker}...")
        transactions = fetch_insider_transactions(ticker, api_key)
        recent = [t for t in transactions if within_window(t.get("transactionDate"), cutoff)]

        for t in recent:
            code = t.get("transactionCode", "?")
            change = t.get("change")  # signed shares actually transacted; negative = sell
            record = {
                "ticker": t.get("symbol", ticker),
                "name": t.get("name", "unknown"),
                "code": code,
                "label": CODE_LABELS.get(code, code),
                "shares_transacted": abs(change) if change is not None else None,
                "post_transaction_holdings": t.get("share"),
                "price": t.get("transactionPrice"),
                "transaction_date": t.get("transactionDate"),
                "filing_date": t.get("filingDate"),
            }
            (all_sells if code == "S" else all_other).append(record)

    print(f"\n{'=' * 60}")
    print(f"Insider SELLING in the last {args.days} day(s) across {tickers}:")
    print("=" * 60)
    if not all_sells:
        print("None found.")
    else:
        for r in all_sells:
            shares = r.get("shares_transacted")
            value = (
                f"${shares * r['price']:,.0f}"
                if shares and r.get("price")
                else "n/a"
            )
            print(
                f"  [{r['ticker']}] {r['name']} — sold {shares} shares "
                f"@ ${r['price']} (~{value}) on {r['transaction_date']} "
                f"(filed {r['filing_date']}, {r['post_transaction_holdings']} shares held after)"
            )

    print(f"\nOther insider activity in the same window (not sells):")
    if not all_other:
        print("  None found.")
    else:
        for r in all_other:
            print(
                f"  [{r['ticker']}] {r['name']} — {r['label']} — "
                f"{r['shares_transacted']} shares on {r['transaction_date']} "
                f"(filed {r['filing_date']})"
            )
    print("=" * 60)


if __name__ == "__main__":
    main()
