"""
One-time backfill -- Portfolio Tracker Assistant

Seeds price_snapshots (app/db.py) with ~1 year of real daily closes per
ticker via yfinance, so get_market_data has durable history to compute
week-over-week / month-over-month price changes from the moment this
runs, instead of waiting a year for the daily self-snapshot (app/tools.py's
get_market_data, after this) to accumulate it one day at a time.

Run ONCE, by hand, and verified before anything depends on it -- same
standard already applied to FMP and Twelve Data earlier in this project
(both got assumed-working, then confirmed broken, the hard way). This
script is never called by the running app itself.

Pulls a full year (not just the 90-day read-limit app/db.py's
get_price_history uses) because it's a single free yfinance call either
way -- grabbing more now avoids a second backfill run if a longer-lookback
feature (e.g. "since your last earnings call") gets built later.

Usage:
    pip install -r requirements.txt   # pulls in yfinance, SQLAlchemy, psycopg
    python backfill_price_history.py
"""

from __future__ import annotations

import time

import yfinance as yf
from dotenv import load_dotenv

from app import db

load_dotenv()  # redundant with app/db.py's own load_dotenv() call, but matches
                # every other standalone entry point in this repo (test_q*.py,
                # fetch_*.py) calling it themselves rather than relying on an
                # import side effect.

# Mirrors fetch_edgar_filings.py's own TICKERS list and app/tools.py's
# TICKER_TO_COMPANY keys -- deliberately a plain local list, not an
# import of app.tools (which would drag in the full LangChain/Qdrant
# import chain for a one-time script that has nothing to do with any
# of that). This is the same "one ticker list per ingestion script"
# debt already flagged for the XBRL CIK dict, not a new decision --
# update all three together when PANW/DELL land (doc item 6).
TICKERS = ["ALAB", "AAPL", "MRVL", "NBIS"]

PERIOD = "1y"


def _history_to_rows(ticker: str, hist) -> list[dict]:
    """Pure transform: a yfinance history DataFrame (indexed by
    Timestamp, with a 'Close' column) -> a list of plain
    {'ticker', 'date', 'close'} dicts ready for db.save_price_snapshot.

    Split out from fetch_and_save so this parsing logic is unit-testable
    without a real network call -- feed it a fake DataFrame shaped like
    yfinance's real output, check what comes out.
    """
    rows = []
    for ts, row in hist.iterrows():
        close = row.get("Close")
        if close is None:
            continue
        try:
            close = float(close)
        except (TypeError, ValueError):
            continue
        if close != close:  # NaN check without importing math/numpy for one comparison
            continue
        rows.append({"ticker": ticker, "date": ts.date(), "close": close})
    return rows


def fetch_and_save(ticker: str) -> int:
    """Fetch PERIOD of daily history for `ticker` from yfinance and
    upsert every row into price_snapshots via db.save_price_snapshot.
    Returns the row count written so main() can report real per-ticker
    counts instead of just 'it ran without crashing'."""
    hist = yf.Ticker(ticker).history(period=PERIOD, interval="1d")
    if hist is None or hist.empty:
        print(f"!! No data returned for {ticker} -- yfinance gave back nothing")
        return 0

    rows = _history_to_rows(ticker, hist)
    for r in rows:
        db.save_price_snapshot(r["ticker"], r["date"], r["close"])
    return len(rows)


def main():
    db.init_db()
    print(f"Backfilling {len(TICKERS)} tickers: {', '.join(TICKERS)}")
    results = {}
    for ticker in TICKERS:
        count = fetch_and_save(ticker)
        results[ticker] = count
        print(f"  {ticker}: {count} rows written")
        time.sleep(0.5)  # no documented yfinance rate limit, no reason to hammer it anyway

    print()
    print("Summary:", results)
    zero = [t for t, c in results.items() if c == 0]
    if zero:
        print(f"!! WARNING: no data written for {zero} -- do not trust get_market_data for "
              f"these tickers until this is investigated.")


if __name__ == "__main__":
    main()
