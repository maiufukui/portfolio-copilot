"""
Persistence layer -- Render Postgres.

Fixes the ephemeral-filesystem gap that broke price history across every
free-tier restart: get_market_data previously had zero durable price
data to compute a week-over-week or month-over-month change from,
because the only source (an FMP live call) was cache-only and lost
everything on restart. See Portoflow Copilot Demo.md, Section 1, for
the full incident writeup and the plan this module implements.

SQLAlchemy Core (not the ORM) -- same "fewer moving parts" bias
llm_gateway.py already applies (hand-rolled headers instead of a new
package). psycopg (v3), not psycopg2 -- deliberately: psycopg2 isn't
installed anywhere in this project, and mixing drivers is its own class
of bug. SQLAlchemy's default "postgresql://" scheme resolves to
psycopg2, so _normalize_database_url below rewrites the scheme to
"postgresql+psycopg://" explicitly rather than relying on a fallback.

Four tables, created together by init_db(), but only price_snapshots is
wired to anything in this pass:
  - price_snapshots      -- built AND wired (this module's actual job)
  - health_score_history -- schema only; wiring is Q13's since-purchase-
                             comparison work, a separate item
  - user_memory          -- schema only; wiring is the guardrails/memory
                             item, a separate item
  - news_dedup           -- schema only; unused until the memory item

Creating all four now, in one migration, rather than three separate
schema changes later, was a deliberate call (see the doc) -- cheap
either way, but worth naming as a choice.

Retention: nothing in this module ever deletes a row. price_snapshots
keeps every snapshot indefinitely -- the 1-year backfill and every
daily self-snapshot after it -- by deliberate choice, not omission.
There is no delete/prune function anywhere in this file.
"""

from __future__ import annotations

import os
from datetime import date as date_type
from datetime import datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    func,
    select,
)
from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

# Every other env-var-reading entry point in this repo calls load_dotenv()
# itself rather than assuming some other already-imported module did it
# first (app/tools.py, every test_q*.py, every fetch_*.py -- checked
# directly via grep, not assumed). This module reads DATABASE_URL
# directly in get_engine(), so it follows the same convention: harmless
# to call redundantly, but wrong to skip, since backfill_price_history.py
# and test_db.py both import this module without going through
# app/tools.py (which is the only other place DATABASE_URL would
# otherwise get loaded).
load_dotenv()

metadata = MetaData()

price_snapshots = Table(
    "price_snapshots",
    metadata,
    Column("ticker", String, primary_key=True),
    Column("date", Date, primary_key=True),
    Column("close", Float, nullable=False),
    Column("captured_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

# Schema only this pass -- see module docstring. Composite PK
# (ticker, computed_at) since a ticker can have multiple scores over
# time and both fields together identify one snapshot.
health_score_history = Table(
    "health_score_history",
    metadata,
    Column("ticker", String, primary_key=True),
    Column("computed_at", DateTime(timezone=True), primary_key=True, server_default=func.now()),
    Column("overall", String, nullable=False),
    Column("signals_json", Text, nullable=False),
)

# Schema only this pass -- see module docstring.
user_memory = Table(
    "user_memory",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", Text, nullable=False),
    Column("memory_type", String, nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False),
)

# Schema only this pass -- see module docstring.
news_dedup = Table(
    "news_dedup",
    metadata,
    Column("url_hash", String, primary_key=True),
    Column("ticker", String, nullable=False),
    Column("first_seen_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)


def _normalize_database_url(raw_url: str) -> str:
    """Rewrite whatever scheme the host handed us into
    'postgresql+psycopg://', explicitly selecting the psycopg (v3)
    driver rather than relying on SQLAlchemy's default dialect
    resolution (which would try psycopg2 -- not installed here).

    Handles the two schemes actually seen in the wild:
      - 'postgres://...'    (Heroku-style, some hosts still emit this)
      - 'postgresql://...'  (what Render's connection strings use)

    Idempotent -- calling it on an already-normalized URL is a no-op,
    so this is safe to apply unconditionally rather than needing the
    caller to know which case it's in.
    """
    if raw_url.startswith("postgres://"):
        raw_url = "postgresql://" + raw_url[len("postgres://"):]
    if raw_url.startswith("postgresql://"):
        raw_url = "postgresql+psycopg://" + raw_url[len("postgresql://"):]
    return raw_url


_engine: Engine | None = None


def get_engine() -> Engine:
    """Singleton engine, built once per process. Pool sized for Render
    Postgres Basic-256mb (small instance, small pool -- 5 connections
    exhausts a meaningful fraction of that plan's connection limit if
    set too high). pool_pre_ping guards against Render silently
    dropping idle connections between requests, which would otherwise
    surface as a confusing mid-query error instead of a clean retry.
    """
    global _engine
    if _engine is not None:
        return _engine

    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        raise RuntimeError(
            "DATABASE_URL is not set. This module requires a real Postgres "
            "connection string (see .env.example)."
        )

    _engine = create_engine(
        _normalize_database_url(raw_url),
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    return _engine


def init_db(engine: Engine | None = None) -> None:
    """Create all four tables if they don't already exist. Safe to call
    on every app startup -- checkfirst=True makes this a no-op against
    an already-initialized database rather than an error."""
    metadata.create_all(engine or get_engine(), checkfirst=True)


def save_price_snapshot(ticker: str, snapshot_date: date_type | str, close: float) -> None:
    """Upsert one (ticker, date) -> close row. Upsert, not insert, so
    this is safe to call every time get_market_data runs (potentially
    multiple times a day) without erroring on the second call for the
    same day -- 'close' just gets overwritten with the latest value
    seen for that date, which is what you want for an intraday quote
    that hasn't settled to a final EOD close yet.

    No delete path exists anywhere in this module -- see the module
    docstring's Retention note.
    """
    if isinstance(snapshot_date, str):
        snapshot_date = datetime.fromisoformat(snapshot_date).date()

    ticker = ticker.upper()
    engine = get_engine()
    stmt = pg_insert(price_snapshots).values(
        ticker=ticker, date=snapshot_date, close=close
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "date"],
        set_={"close": stmt.excluded.close},
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def get_price_history(ticker: str, limit: int = 90) -> list[dict]:
    """Most recent `limit` snapshots for `ticker`, newest-first --
    same shape and ordering the old FMP-based fetch_price_history
    returned ({'date': 'YYYY-MM-DD', 'close': float, ...}), so
    compute_price_change_over (app/tools.py) needed zero changes to
    keep working against this new source.

    `limit` is a READ limit -- how many rows this one query returns --
    not a retention limit. Every row ever written stays in the table
    regardless of what's requested here; see the module docstring.
    """
    ticker = ticker.upper()
    engine = get_engine()
    stmt = (
        select(price_snapshots.c.date, price_snapshots.c.close)
        .where(price_snapshots.c.ticker == ticker)
        .order_by(price_snapshots.c.date.desc())
        .limit(limit)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [{"date": r.date.isoformat(), "close": r.close} for r in rows]
