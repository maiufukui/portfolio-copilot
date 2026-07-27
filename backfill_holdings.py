"""
One-time backfill -- Portfolio Tracker Assistant

Seeds the real `holdings` table (app/db.py, added 2026-07-27) with the
same 6 rows frontend/lib/mock-holdings.ts had been hardcoding client-side
-- so the demo isn't empty on first load once the frontend switches from
MOCK_HOLDINGS to the real /holdings API. The numbers themselves are still
fabricated (same caveat mock-holdings.ts's own header carried: "Shares
and cost basis are fabricated"); what changes is WHERE they live -- a
real Postgres row instead of a TS constant that resets on every reload.

Run ONCE, by hand. Idempotent (upsert_holding upserts on ticker), so a
second accidental run overwrites with the same values rather than
duplicating rows -- but there's still no reason to run this more than
once; after the frontend is wired to the real API (see
frontend/lib/api.ts), real edits should go through POST/PUT /holdings,
not another run of this script.

Usage:
    python backfill_holdings.py                # all 6 tickers
    python backfill_holdings.py --ticker ALAB   # just one, e.g. to reset
                                                 # a single row back to seed
"""

from __future__ import annotations

import argparse
from datetime import date

from dotenv import load_dotenv

from app import db

load_dotenv()  # redundant with app/db.py's own load_dotenv(), but matches every
                # other standalone entry point in this repo (test_q*.py, fetch_*.py,
                # backfill_price_history.py) calling it themselves.

# Mirrors frontend/lib/mock-holdings.ts's MOCK_HOLDINGS array exactly --
# same tickers, shares, cost basis, and purchase dates. Kept as a plain
# local list rather than trying to parse the .ts file from Python; this
# is a one-time seed, not an ongoing sync, so the duplication is a
# one-time cost, not an ongoing maintenance burden.
SEED_HOLDINGS = [
    {"ticker": "ALAB", "shares": 10, "cost_basis_avg": 320.0, "purchase_date": date(2026, 5, 14)},
    {"ticker": "AAPL", "shares": 5, "cost_basis_avg": 310.0, "purchase_date": date(2026, 4, 2)},
    {"ticker": "MRVL", "shares": 20, "cost_basis_avg": 180.0, "purchase_date": date(2026, 5, 20)},
    {"ticker": "NBIS", "shares": 15, "cost_basis_avg": 200.0, "purchase_date": date(2026, 5, 1)},
    {"ticker": "PANW", "shares": 8, "cost_basis_avg": 325.0, "purchase_date": date(2026, 6, 10)},
    {"ticker": "DELL", "shares": 12, "cost_basis_avg": 440.0, "purchase_date": date(2026, 5, 28)},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ticker",
        help="Seed/reset just this one ticker instead of all 6 -- e.g. to restore a single row "
        "back to its seed values after testing edits through the UI, without touching the other 5.",
    )
    args = parser.parse_args()
    targets = (
        [row for row in SEED_HOLDINGS if row["ticker"] == args.ticker.upper()]
        if args.ticker
        else SEED_HOLDINGS
    )
    if args.ticker and not targets:
        raise SystemExit(f"{args.ticker.upper()!r} is not in SEED_HOLDINGS -- typo?")

    db.init_db()
    print(f"Seeding {len(targets)} holding(s): {', '.join(r['ticker'] for r in targets)}")
    for row in targets:
        db.upsert_holding(row["ticker"], row["shares"], row["cost_basis_avg"], row["purchase_date"])
        print(f"  {row['ticker']}: {row['shares']} sh @ ${row['cost_basis_avg']} avg, "
              f"purchased {row['purchase_date'].isoformat()}")

    print()
    print("Done. Verify with: SELECT * FROM holdings; -- or GET /holdings once the server is running.")


if __name__ == "__main__":
    main()
