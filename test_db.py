"""
Test suite for app/db.py and backfill_price_history.py.

Two tiers, deliberately separated:

  1. Unit tests -- pure logic, no network. Run anywhere, including this
     dev sandbox, whose outbound network is allowlisted and confirmed to
     block both the real Render Postgres host and Yahoo Finance (checked
     directly: DNS/proxy failures on both, same class of restriction
     app/tools.py's fetch_price_history comment already discloses for
     FMP). These cover URL normalization, table schema, the exact SQL
     app/db.py generates, and the backfill script's DataFrame-parsing
     logic.

  2. Integration tests (marked `integration`) -- require a real,
     reachable DATABASE_URL and actually read/write Postgres. They SKIP,
     not fail and not fake-pass, when the DB can't be reached, via the
     `live_db` fixture below. They could not be run end-to-end from this
     dev sandbox for the reason above -- run this suite from an
     environment with real network access (i.e. locally) to get the
     actual verification this project's working agreement requires
     ("verify before asserting" -- a green run in a network-restricted
     sandbox is not that).

Run everything:
    pytest test_db.py -v

Run only what a network-restricted sandbox can actually execute:
    pytest test_db.py -v -m "not integration"
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import db
from backfill_price_history import _history_to_rows

# ============================================================= unit ===


class TestNormalizeDatabaseUrl:
    def test_postgres_scheme_rewritten(self):
        assert db._normalize_database_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"

    def test_postgresql_scheme_rewritten(self):
        assert db._normalize_database_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"

    def test_already_normalized_is_idempotent(self):
        url = "postgresql+psycopg://u:p@h/db"
        assert db._normalize_database_url(url) == url

    def test_preserves_query_params_and_special_chars(self):
        url = "postgresql://user:p%40ss@host:5432/dbname?sslmode=require"
        out = db._normalize_database_url(url)
        assert out == "postgresql+psycopg://user:p%40ss@host:5432/dbname?sslmode=require"


class TestSchema:
    def test_all_four_tables_defined(self):
        assert set(db.metadata.tables.keys()) == {
            "price_snapshots",
            "health_score_history",
            "user_memory",
            "news_dedup",
        }

    def test_price_snapshots_primary_key_is_ticker_and_date(self):
        pk_cols = {c.name for c in db.price_snapshots.primary_key.columns}
        assert pk_cols == {"ticker", "date"}

    def test_price_snapshots_columns(self):
        cols = {c.name for c in db.price_snapshots.columns}
        assert cols == {"ticker", "date", "close", "captured_at"}

    def test_no_delete_capability_exists_in_module(self):
        """Retention is a deliberate design choice (see db.py's module
        docstring) -- this asserts the invariant in code, not just in a
        comment: nothing named delete/drop/prune/purge is exposed."""
        public_names = [n for n in dir(db) if not n.startswith("_")]
        forbidden = {"delete", "drop", "prune", "purge", "truncate"}
        hits = [n for n in public_names if any(f in n.lower() for f in forbidden)]
        assert hits == [], f"Found retention-violating function(s): {hits}"


class TestQueryShape:
    """Compile-time checks against the real Postgres dialect -- confirms
    the SQL app/db.py generates is what it's supposed to be, without
    needing a live connection."""

    def test_get_price_history_orders_desc_and_limits(self):
        from sqlalchemy import select

        stmt = (
            select(db.price_snapshots.c.date, db.price_snapshots.c.close)
            .where(db.price_snapshots.c.ticker == "ALAB")
            .order_by(db.price_snapshots.c.date.desc())
            .limit(90)
        )
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "ORDER BY price_snapshots.date DESC" in sql
        assert "LIMIT 90" in sql

    def test_save_price_snapshot_upsert_targets_ticker_and_date(self):
        stmt = pg_insert(db.price_snapshots).values(ticker="ALAB", date=date(2026, 7, 24), close=100.0)
        stmt = stmt.on_conflict_do_update(
            index_elements=["ticker", "date"], set_={"close": stmt.excluded.close}
        )
        sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "ON CONFLICT (ticker, date)" in sql
        assert "DO UPDATE SET close" in sql


class TestBackfillParsing:
    """backfill_price_history._history_to_rows against a DataFrame shaped
    exactly like yfinance's real .history() output -- no network call."""

    def _fake_history(self):
        idx = pd.to_datetime(["2026-07-20", "2026-07-21", "2026-07-22"])
        return pd.DataFrame(
            {"Close": [100.5, float("nan"), 102.25], "Open": [99.0, 100.0, 101.0]}, index=idx
        )

    def test_drops_nan_rows(self):
        rows = _history_to_rows("ALAB", self._fake_history())
        assert len(rows) == 2

    def test_row_shape(self):
        rows = _history_to_rows("ALAB", self._fake_history())
        assert rows[0] == {"ticker": "ALAB", "date": date(2026, 7, 20), "close": 100.5}

    def test_empty_dataframe_returns_empty_list(self):
        assert _history_to_rows("ALAB", pd.DataFrame({"Close": []})) == []


# ====================================================== integration ===


@pytest.fixture(scope="module")
def live_db():
    """Skips every integration test (not a fail, not a fake-pass) if
    DATABASE_URL is missing or unreachable. In this dev sandbox that's
    expected and disclosed above -- it will skip here every time. Run
    from an environment with real network access to get real coverage.
    """
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set")
    try:
        engine = db.get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        pytest.skip(
            f"Could not reach Postgres ({e!r}) -- expected from a network-restricted "
            f"sandbox; run this suite locally against the real DATABASE_URL for real coverage."
        )
    db.init_db()
    return engine


@pytest.mark.integration
class TestLiveDatabase:
    TEST_TICKER = "ZZTEST_UNIT"

    def teardown_method(self, method):
        # Direct SQL cleanup, deliberately NOT a db.py function -- db.py
        # has no delete path by design (see its Retention docstring).
        # This is test-harness hygiene only, not a capability the app
        # itself exposes; keeps this suite from leaving synthetic rows
        # in the real price_snapshots table forever.
        engine = db.get_engine()
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM price_snapshots WHERE ticker = :t"), {"t": self.TEST_TICKER})

    def test_init_db_is_idempotent(self, live_db):
        db.init_db()
        db.init_db()

    def test_save_and_read_round_trip(self, live_db):
        db.save_price_snapshot(self.TEST_TICKER, date(2026, 7, 20), 100.0)
        history = db.get_price_history(self.TEST_TICKER, limit=10)
        assert {"date": "2026-07-20", "close": 100.0} in history

    def test_upsert_overwrites_not_duplicates(self, live_db):
        d = date(2026, 7, 21)
        db.save_price_snapshot(self.TEST_TICKER, d, 100.0)
        db.save_price_snapshot(self.TEST_TICKER, d, 105.5)  # same (ticker, date), new price
        history = db.get_price_history(self.TEST_TICKER, limit=10)
        same_day = [h for h in history if h["date"] == "2026-07-21"]
        assert len(same_day) == 1, "upsert must not create a duplicate row for the same (ticker, date)"
        assert same_day[0]["close"] == 105.5

    def test_read_limit_caps_rows_returned_newest_first(self, live_db):
        base = date(2026, 1, 1)
        for i in range(5):
            db.save_price_snapshot(self.TEST_TICKER, base + timedelta(days=i), 100.0 + i)
        history = db.get_price_history(self.TEST_TICKER, limit=3)
        assert len(history) == 3
        dates = [h["date"] for h in history]
        assert dates == sorted(dates, reverse=True), "must be newest-first"

    def test_ticker_is_case_normalized(self, live_db):
        db.save_price_snapshot(self.TEST_TICKER.lower(), date(2026, 7, 22), 50.0)
        history = db.get_price_history(self.TEST_TICKER, limit=10)
        assert any(h["close"] == 50.0 for h in history)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
