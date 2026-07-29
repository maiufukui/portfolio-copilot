"""
Production-safe helpers shared between app/tools.py (the live server) and
the eval harnesses (test_q2.py, test_q5.py, test_q8.py).

Why this file exists (real incident, 2026-07-29): app/tools.py -- a hard
production import, loaded at server startup via app/graph.py -> server.py
-- was importing search_tavily/format_results directly from test_q2.py,
fetch_insider_transactions/CODE_LABELS/within_window from test_q5.py, and
fetch_recommendation_trends/format_recommendation_trends from test_q8.py.
All three of those eval files also do `from ragas.messages import
ToolCall` at module level, for their own run_case() scoring functions.
requirements-server.txt deliberately excludes ragas (it's only needed by
the eval harness, not by the deployed server) -- so importing anything at
all from test_q2/test_q5/test_q8 in production meant `import ragas`
executed at server startup regardless of which name you actually needed,
and the deploy crashed with `ModuleNotFoundError: No module named
'ragas'` the moment ragas was correctly left out of the server's
dependency file.

This was NOT a missing-pin problem (that was the PREVIOUS incident, same
day: langgraph-checkpoint-postgres missing from requirements-server.txt,
fixed by adding the pin). This one is different and can't be fixed by
adding ragas to requirements-server.txt without defeating the entire
point of that file's split from requirements.txt -- the deployed image
would permanently carry ragas and everything it pulls in (pandas,
datasets, etc.), just to satisfy an import of a function that itself
never touches ragas.

The actual root cause is architectural: production code was importing
from files whose name and primary purpose is "eval harness for question
N," which happen to also import a heavy eval-only dependency for a
DIFFERENT function in the same file (their own run_case()). The durable
fix is this file: every function app/tools.py actually needs, extracted
to a module with zero ragas/pytest/yfinance dependency and zero
dependency on app.graph/app.tools (removing, as a side effect, the
circular-import risk test_q2.py/test_q5.py/test_q8.py's own comments
already flagged and worked around with deferred `from app.graph import
ask` imports inside run_case()).

test_q2.py, test_q5.py, and test_q8.py still import these same functions
FROM this module (rather than redefining them), so their own CLI
behavior (`python test_q2.py --ticker ...`) and existing external
importers (fetch_transcripts.py's `from test_q2 import search_tavily`,
run_scorecard.py's `from test_q2 import load_q2, run_case`, etc.) are
unaffected -- only app/tools.py's import source changed, to point here
directly instead of through an eval file that happens to re-export it.

test_q1.py and test_q7.py are NOT part of this file: verified directly
(grep, 2026-07-29) that neither imports ragas, pytest, or yfinance
anywhere, so app/tools.py's existing `from test_q1 import
load_ticker_documents` and `from test_q7 import find_hits` carry no such
risk and don't need to move. Moving them here too would be unnecessary
surface area with no bug to fix.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

import requests

# --- News / Tavily (moved from test_q2.py) ---------------------------------

TAVILY_URL = "https://api.tavily.com/search"


def search_tavily(
    query: str,
    api_key: str,
    time_range: str = "week",
    max_results: int = 8,
    topic: str = "news",
) -> list[dict]:
    """Hit Tavily's /search endpoint. topic='news' + time_range filters to
    recent news rather than general web results."""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "query": query,
        "topic": topic,
        "time_range": time_range,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_answer": False,
    }
    resp = requests.post(TAVILY_URL, headers=headers, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


DATE_IN_URL_RE = re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})/")


def extract_date_from_url(url: str) -> str | None:
    """Fallback only: Tavily's 'general' topic often omits published_date, but
    many financial news URLs embed a YYYY/MM/DD path segment. Not an
    authoritative source -- always labeled 'inferred from URL' so it's never
    mistaken for a verified publish date."""
    if not url:
        return None
    match = DATE_IN_URL_RE.search(url)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d} (inferred from URL)"


def display_date(r: dict) -> str:
    return r.get("published_date") or extract_date_from_url(r.get("url", "")) or "date unknown"


def format_results(results: list[dict]) -> str:
    lines = []
    for r in results:
        title = r.get("title", "untitled")
        url = r.get("url", "")
        score = r.get("score")
        content = (r.get("content") or "")[:500]
        lines.append(
            f"Title: {title}\nDate: {display_date(r)}\nRelevance score: {score}\nURL: {url}\nExcerpt: {content}"
        )
    return "\n\n".join(lines)


# --- Insider transactions (moved from test_q5.py) ---------------------------

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

# Caching added 2026-07-27 (Maiu, explicit call, same pattern as
# app/tools.py's quote/news/earnings-date caches): this is called from
# get_market_data on essentially every chat question, uncached, one of
# four real contributors found in a caching audit to hitting Finnhub's
# rate limit. 24h TTL -- Maiu's explicit call, accepting that a fresh
# Form 4 filed today could sit unsurfaced for up to a day in exchange
# for the call-volume reduction; flagged once as a real signal-latency
# tradeoff for this specific one before applying it, not a silent
# default.
INSIDER_TTL_SECONDS = 86400  # 24 hours
_INSIDER_CACHE: dict[str, tuple[float, list[dict]]] = {}


def fetch_insider_transactions(symbol: str, api_key: str) -> list[dict]:
    symbol = symbol.upper()
    now = time.monotonic()
    cached = _INSIDER_CACHE.get(symbol)
    if cached and now - cached[0] < INSIDER_TTL_SECONDS:
        return cached[1]

    result = _fetch_insider_transactions_uncached(symbol, api_key)
    _INSIDER_CACHE[symbol] = (now, result)
    return result


def _fetch_insider_transactions_uncached(symbol: str, api_key: str) -> list[dict]:
    url = "https://finnhub.io/api/v1/stock/insider-transactions"
    resp = requests.get(url, params={"symbol": symbol, "token": api_key}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", [])


def within_window(transaction_date: str, cutoff: datetime) -> bool:
    try:
        dt = datetime.strptime(transaction_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return dt >= cutoff


# --- Institutional recommendation trends (moved from test_q8.py) -----------

FINNHUB_RECOMMENDATION_URL = "https://finnhub.io/api/v1/stock/recommendation"

# Caching added 2026-07-27 (Maiu, explicit call) -- same audit/pattern as
# fetch_insider_transactions above. 24h TTL is a lower-risk call than
# insider transactions specifically: Finnhub's own recommendation data is
# an aggregated monthly-cadence consensus, not something that actually
# updates intraday, so a day-old cache isn't giving up real freshness the
# source itself doesn't already lack.
RECOMMENDATION_TTL_SECONDS = 86400  # 24 hours
_RECOMMENDATION_CACHE: dict[str, tuple[float, list[dict]]] = {}


def fetch_recommendation_trends(ticker: str, api_key: str) -> list[dict]:
    """Real institutional analyst consensus (aggregated buy/hold/sell counts
    from actual sell-side coverage) -- not scraped from the open web. Free
    tier. Most recent period first. Cached per ticker for
    RECOMMENDATION_TTL_SECONDS -- see comment above."""
    ticker = ticker.upper()
    now = time.monotonic()
    cached = _RECOMMENDATION_CACHE.get(ticker)
    if cached and now - cached[0] < RECOMMENDATION_TTL_SECONDS:
        return cached[1]

    result = _fetch_recommendation_trends_uncached(ticker, api_key)
    _RECOMMENDATION_CACHE[ticker] = (now, result)
    return result


def _fetch_recommendation_trends_uncached(ticker: str, api_key: str) -> list[dict]:
    resp = requests.get(FINNHUB_RECOMMENDATION_URL, params={"symbol": ticker, "token": api_key}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def format_recommendation_trends(trends: list[dict]) -> str:
    if not trends:
        return "No institutional recommendation-trend data available for this ticker."
    lines = []
    for t in trends[:2]:  # most recent + one prior period, for trend direction
        total = (
            t.get("strongBuy", 0)
            + t.get("buy", 0)
            + t.get("hold", 0)
            + t.get("sell", 0)
            + t.get("strongSell", 0)
        )
        lines.append(
            f"Period {t.get('period')}: {total} analyst(s) covering -- "
            f"Strong Buy: {t.get('strongBuy', 0)}, Buy: {t.get('buy', 0)}, "
            f"Hold: {t.get('hold', 0)}, Sell: {t.get('sell', 0)}, "
            f"Strong Sell: {t.get('strongSell', 0)}"
        )
    return "\n".join(lines)
