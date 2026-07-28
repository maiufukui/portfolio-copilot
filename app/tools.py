"""
Tool belt for the Portfolio Copilot LangGraph agent.

Four tools, bound to one agent node -- matches Session 9
(09_Agent_Servers/simple_agent.py)'s verified pattern: get_tool_belt()
returns a list of @tool-decorated functions, passed straight into
create_react_agent(model, tools=tool_belt, ...). No custom planner /
fan-out node -- Send() has zero precedent anywhere in the course
(confirmed by directly reading notebook code across 8+ sessions, not
just READMEs).

Reuses existing, already-tested logic from the root-level test_q*.py /
fetch_*.py scripts rather than re-implementing it:
    - RAG retrieval           <- test_q1.load_ticker_documents / build_retriever
    - Keyword / exact search  <- test_q7.find_hits
    - Live news search        <- test_q2.search_tavily / format_results
    - Market data              <- test_q5 (insider tx) + test_q8 (recommendation
                                  trends) + Finnhub /quote (new, small addition)

The Fundamentals Health Score itself (XBRL revenue/margin + 8-K
leadership + insider-activity thresholds, Task 2 section 4) is NOT one
of these four bound tools. Per the PRD's Agent Workflow diagram (Task 2
section 3), D1-D4 tool calls feed into a synthesis step that compares
findings against the score -- the score is computed once per query as a
deterministic pre-step (get_fundamentals_health_score, below) and
injected into the system prompt as ground truth by app/graph.py.
Exposing it as a fifth callable tool would let the model choose not to
check it, or restate an exact number imprecisely; keeping it outside
the tool loop keeps those numbers exact.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections import OrderedDict
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from langchain_core.tools import tool

# app/ is a subpackage -- make sure the repo root (where test_q*.py and
# fetch_*.py live) is importable regardless of how this module is run.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fetch_leadership_events import (  # noqa: E402
    classify_departure,
    fetch_filing_text,
    fetch_submissions,
    find_8k_with_item,
)
from fetch_xbrl_financials import (  # noqa: E402
    GROSS_PROFIT_TAG,
    TICKER_TO_CIK,
    classify_margin_trend,
    classify_revenue_trend,
    fetch_concept,
    fetch_revenue,
    quarterly_series,
)
from parent_child_retriever import build_parent_child_retriever  # noqa: E402
from test_q1 import load_ticker_documents  # noqa: E402
from test_q2 import format_results, search_tavily  # noqa: E402
from test_q5 import CODE_LABELS, fetch_insider_transactions, within_window  # noqa: E402
from test_q7 import find_hits  # noqa: E402
from test_q8 import fetch_recommendation_trends, format_recommendation_trends  # noqa: E402

from app import db  # noqa: E402

load_dotenv()

FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
FINNHUB_EARNINGS_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"

# Full company names -- Tavily search quality degrades with ticker symbols
# and generic suffixes mixed in (confirmed directly in test_q2.py's own
# testing notes: diluted queries missed real, current news a plain
# company-name search found immediately). Used by the dashboard's news
# section, not by the chat agent's search_live_news tool (that tool takes
# a query the model writes itself).
TICKER_TO_COMPANY = {
    "ALAB": "Astera Labs",
    "AAPL": "Apple",
    "MRVL": "Marvell",
    "NBIS": "Nebius",
    "PANW": "Palo Alto Networks",
    "DELL": "Dell Technologies",
}

# ---------------------------------------------------------------------
# Per-process artifact caches -- avoid re-loading/re-embedding the same
# ticker's documents on every tool call within a conversation. This is
# the same category of cache as Session 12's CachingEmbedder
# (02_Cat_Health_Agent_Caching.ipynb, Task 5): deterministic, repeated
# work, keyed and reused rather than redone. Two things borrowed
# directly from that pattern:
#   - hit/miss counters, so cache behavior is auditable in logs, not a
#     silent guess (the notebook's own framing: "a cache you cannot
#     audit is a cache you cannot debug").
#   - a bound on size (LRU eviction via OrderedDict), so a long-lived
#     process (the eventual FastAPI server, not just this CLI) can't
#     grow unbounded as more tickers get queried over a session.
# No TTL here, unlike the health-score cache below -- these are static
# filing documents, not live data. Once a filing is loaded there's no
# staleness question, only a size question.
# ---------------------------------------------------------------------
_MAX_CACHED_TICKERS = 20
_DOC_CACHE: "OrderedDict[str, list]" = OrderedDict()
_RETRIEVER_CACHE: "OrderedDict[str, object]" = OrderedDict()
cache_stats = {"doc_hits": 0, "doc_misses": 0, "retriever_hits": 0, "retriever_misses": 0}


def _bounded_cache_set(cache: "OrderedDict", key: str, value) -> None:
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _MAX_CACHED_TICKERS:
        cache.popitem(last=False)  # evict least-recently-used ticker


def _get_documents(ticker: str) -> list:
    ticker = ticker.upper()
    if ticker in _DOC_CACHE:
        cache_stats["doc_hits"] += 1
        _DOC_CACHE.move_to_end(ticker)
        return _DOC_CACHE[ticker]
    cache_stats["doc_misses"] += 1
    documents = load_ticker_documents(ticker)
    _bounded_cache_set(_DOC_CACHE, ticker, documents)
    return documents


def _get_retriever(ticker: str):
    ticker = ticker.upper()
    if ticker in _RETRIEVER_CACHE:
        cache_stats["retriever_hits"] += 1
        _RETRIEVER_CACHE.move_to_end(ticker)
        return _RETRIEVER_CACHE[ticker]
    cache_stats["retriever_misses"] += 1
    # Item 4: parent-child + Cohere rerank, replacing the flat baseline
    # (test_q1.build_retriever). Returns a callable (retrieve(question,
    # k=5) -> list[Document]), not a LangChain retriever object -- see
    # search_filings below for the call-site shape this requires.
    #
    # cache_key=ticker (2026-07-26): shares the same on-disk embedding
    # cache as run_eval.py -- if that script (or a prior server process)
    # already embedded this ticker's current corpus, this process reuses
    # those vectors instead of re-hitting OpenAI. This process's own
    # _RETRIEVER_CACHE above still matters for repeat calls within one
    # running server, same as before.
    retriever = build_parent_child_retriever(_get_documents(ticker), cache_key=ticker)
    _bounded_cache_set(_RETRIEVER_CACHE, ticker, retriever)
    return retriever


# ------------------------------------------------------------- D1 ---
@tool
def search_filings(ticker: str, query: str) -> str:
    """Semantic (vector) search over a ticker's indexed SEC filings
    (10-K/10-Q/8-K) and earnings call transcript. Use for open-ended
    questions about what a company said or reported -- drivers behind a
    number, guidance language, qualitative commentary. NOT reliable for
    questions that need exhaustive/verbatim recall of every mention of
    a term (top-k similarity search can silently miss hits) -- use
    search_filings_exact for those instead.

    For a question scoped to a specific recent period ("this quarter",
    "latest", "recent"), phrase your query to explicitly ask for the
    MOST RECENTLY REPORTED QUARTER, distinguishing it from the full
    fiscal year -- e.g. "...for the most recently reported quarter, not
    the full fiscal year." Do not try to guess or state an exact date;
    you likely don't know this company's specific fiscal calendar, and
    the phrasing above works without one. Confirmed necessary against a
    real case (item 4, 2026-07-25): without this, a 10-K's annual MD&A
    section -- a different period, sometimes citing a change in the
    opposite direction from the actual quarter -- outranked the correct
    quarterly source.

    When asking for a driver behind a number or a guidance figure, also
    ask for the exact verbatim sentence from management's own spoken
    remarks, rather than a bullet-point summary, headline takeaway, or
    restated figure elsewhere in the source. Confirmed necessary against
    a real case (item 7, 2026-07-26): a bullet-point "TAKEAWAYS" summary
    and a metadata/summary preamble both outranked the actual quoted
    guidance sentence, even though the real sentence was present and
    retrievable -- the reranker needs this explicit signal to prefer it.
    """
    retriever = _get_retriever(ticker)
    docs = retriever(query, k=5)
    if not docs:
        return f"No relevant passages found in {ticker}'s indexed filings for: {query}"
    parts = []
    for i, d in enumerate(docs, 1):
        source = os.path.basename(d.metadata.get("source", "unknown"))
        parts.append(f"[{i}] Source: {source}\n{d.page_content}")
    return "\n\n---\n\n".join(parts)


# ------------------------------------------------------------- D2 ---
@tool
def search_filings_exact(ticker: str, keywords: list[str]) -> str:
    """Exhaustive, deterministic keyword/exact-phrase search across a
    ticker's full filing + transcript text (not top-k vector search).
    Use whenever the question demands COMPLETE recall -- 'has X been
    disclosed', 'any mentions of Y', customer-concentration or
    capacity/demand risk language -- where a missed hit is a real
    failure, not a minor gap. Tolerates hyphen/space variants
    ('capacity-constrained' also matches 'capacity constrained').
    """
    documents = _get_documents(ticker)
    hits = find_hits(documents, keywords)
    if not hits:
        return f"No verbatim matches found for {keywords} in {ticker}'s filings/transcripts."
    parts = [f"{len(hits)} match(es) found:"]
    for h in hits:
        source = os.path.basename(h["source"])
        page = f" (page {h['page']})" if h.get("page") is not None else ""
        parts.append(f"- [{source}{page}] matched '{h['keyword']}': ...{h['snippet']}...")
    return "\n".join(parts)


# ------------------------------------------------------------- D3 ---
@tool
def search_live_news(query: str, time_range: str = "week") -> str:
    """Live web/news search (Tavily) for what's happening right now --
    recent headlines, guidance changes, analyst reactions. The filings
    tools above are static/indexed and cannot answer 'what's the latest
    news' -- always use this tool for that. time_range: 'day', 'week',
    'month', or 'year'.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return "TAVILY_API_KEY not configured -- live news search unavailable."
    results = search_tavily(query, api_key, time_range=time_range, topic="news")
    if not results:
        return f"No recent news results found for: {query}"
    return format_results(results)


# Caching added 2026-07-27 (Maiu, explicit call): fetch_quote and
# fetch_next_earnings_date are called from BOTH get_dashboard_data
# (every dashboard load, per tracked ticker) and get_market_data (the
# chat tool, most demo questions), with zero reuse between those call
# sites -- a real contributor to hitting Finnhub's rate limit, same
# category of bug as the news-caching fix above. Price gets the
# shortest TTL (15 min, same as the health score) since a stale price
# is a real, different kind of wrong an older version of this file's
# comment specifically called out; the earnings date changes far less
# often (announced once, weeks out) so a 24h TTL costs nothing real.
QUOTE_TTL_SECONDS = 900  # 15 minutes
_QUOTE_CACHE: dict[str, tuple[float, dict | None]] = {}
EARNINGS_DATE_TTL_SECONDS = 86400  # 24 hours
_EARNINGS_DATE_CACHE: dict[str, tuple[float, str | None]] = {}


def fetch_quote(ticker: str, api_key: str) -> dict | None:
    """Raw Finnhub /quote call, returning the fields the dashboard needs
    (price, % change) as plain data. Extracted out of get_market_data so
    both the chat tool and the dashboard endpoint (server.py) share one
    implementation instead of two copies of the same request. Cached per
    ticker for QUOTE_TTL_SECONDS -- see comment above."""
    ticker = ticker.upper()
    now = time.monotonic()
    cached = _QUOTE_CACHE.get(ticker)
    if cached and now - cached[0] < QUOTE_TTL_SECONDS:
        return cached[1]

    result = _fetch_quote_uncached(ticker, api_key)
    _QUOTE_CACHE[ticker] = (now, result)
    return result


def _fetch_quote_uncached(ticker: str, api_key: str) -> dict | None:
    resp = requests.get(FINNHUB_QUOTE_URL, params={"symbol": ticker, "token": api_key}, timeout=15)
    if not resp.ok:
        return None
    q = resp.json()
    return {
        "price": q.get("c"),
        "change_pct": q.get("dp"),
        "prev_close": q.get("pc"),
        "day_low": q.get("l"),
        "day_high": q.get("h"),
    }


def fetch_next_earnings_date(ticker: str, api_key: str) -> str | None:
    """Finnhub's earnings calendar, filtered to this ticker's next
    upcoming report date within a 1-year lookahead window. Returns None
    if nothing is scheduled yet (common -- companies often don't
    announce next quarter's date until close to it). Cached per ticker
    for EARNINGS_DATE_TTL_SECONDS -- see comment above."""
    ticker = ticker.upper()
    now = time.monotonic()
    cached = _EARNINGS_DATE_CACHE.get(ticker)
    if cached and now - cached[0] < EARNINGS_DATE_TTL_SECONDS:
        return cached[1]

    result = _fetch_next_earnings_date_uncached(ticker, api_key)
    _EARNINGS_DATE_CACHE[ticker] = (now, result)
    return result


def _fetch_next_earnings_date_uncached(ticker: str, api_key: str) -> str | None:
    today = datetime.now().date()
    resp = requests.get(
        FINNHUB_EARNINGS_CALENDAR_URL,
        params={
            "from": today.isoformat(),
            "to": (today + timedelta(days=365)).isoformat(),
            "symbol": ticker,
            "token": api_key,
        },
        timeout=15,
    )
    if not resp.ok:
        return None
    entries = resp.json().get("earningsCalendar", [])
    dated = sorted(e["date"] for e in entries if e.get("date"))
    return dated[0] if dated else None


# Real bug, found live: a user asked "ALAB dropped 8% last week, should I
# sell?" and the agent never actually answered the question -- it fell
# back to reciting the deterministic health-score block and generic
# supporting detail instead. Root cause, confirmed by reading this file,
# not guessed: get_market_data's only price signal was fetch_quote's
# Finnhub /quote call, which exposes exactly one number -- `dp`, percent
# change TODAY. There was no tool anywhere that could confirm or deny a
# claim about last week, last month, or any other lookback -- the model
# had zero real data to ground an answer in, for that entire shape of
# question. This is the same class of gap the PRD's NBIS test case
# happened to catch (a described "12% drop" didn't match the real quote,
# and the agent called that out) -- that worked only because the claim
# was about TODAY, the one thing the tool could check. Anything else was
# unverifiable by design.
#
# Finnhub's own /stock/candle endpoint is the obvious first choice for
# historical price and was deliberately NOT used here: multiple free-tier
# users report a hard "you don't have access to this resource" error on
# US-stock candles (finnhub-io/Finnhub-API GitHub issues #546 and #349,
# checked directly, not assumed) -- not something to build a live demo
# feature on.
#
# FMP was the first fix attempted here (FMP_API_KEY was already sitting
# in .env, unused) and was CONFIRMED GATED for real tickers once actually
# tested live -- a 402 Payment Required on ALAB, not a hypothetical -- so
# it never became a durable source of truth, and would have needed a live
# key on every single call regardless.
#
# Current source: app/db.py's price_snapshots table (Render Postgres),
# backed by two things instead of one live API call --
#   1. backfill_price_history.py, a one-time yfinance pull of ~1 year of
#      real daily closes per ticker, run once by hand and verified
#      against a known source before anything depended on it.
#   2. get_market_data below, which upserts today's Finnhub quote price
#      into price_snapshots on every call -- the permanent, ongoing
#      mechanism, zero new API calls.
# No TTL cache needed here the way FMP's fetch_price_history had one --
# this is a local DB read, not a rate-limited external call.
def fetch_price_history(ticker: str, max_days: int = 90) -> list[dict] | None:
    """Reads real historical daily closes for `ticker` from Postgres
    (app/db.py's price_snapshots), newest-first -- same shape
    compute_price_change_over already expects ({'date', 'close'}).
    Returns None on any DB failure (e.g. DATABASE_URL unset, connection
    drop) so callers degrade gracefully instead of crashing the agent
    turn, same pattern every other optional data source in this file
    follows.
    """
    try:
        history = db.get_price_history(ticker, limit=max_days)
    except Exception:
        return None
    return history or None


def compute_price_change_over(history: list[dict], trading_days_ago: int) -> dict | None:
    """Percent change from `trading_days_ago` trading days back to the most
    recent close in `history` (expects newest-first, as fetch_price_history
    returns it). Trading days, not calendar days -- 5 trading days is "last
    week", 21 is "last month", matching how these questions actually get
    asked, rather than a naive 7/30-calendar-day offset that could land on
    a weekend with no trading data at all."""
    if not history or len(history) <= trading_days_ago:
        return None
    latest, past = history[0], history[trading_days_ago]
    latest_close, past_close = latest.get("close"), past.get("close")
    if latest_close is None or past_close is None or past_close == 0:
        return None
    return {
        "from_date": past.get("date"),
        "to_date": latest.get("date"),
        "from_close": past_close,
        "to_close": latest_close,
        "pct_change": round((latest_close - past_close) / past_close * 100, 2),
    }


# ------------------------------------------------------------- D4 ---
@tool
def get_market_data(ticker: str) -> str:
    """Market data for a ticker: live quote (price + % change TODAY only),
    price change over the last ~week and ~month (real historical closes
    from Postgres -- use this to verify or refute any claim a question
    makes about a price move over a period, e.g. "dropped 8% last week"
    -- never take the user's stated percentage at face value), next
    scheduled earnings date, insider transactions (Form 3/4/5) in the
    last 30 days, and institutional analyst recommendation trends
    (current vs. prior period). Use for price/valuation questions,
    "when does X report next" questions, insider-selling questions, and
    analyst-rating questions.
    """
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        return "FINNHUB_API_KEY not configured -- market data unavailable."

    parts = []

    q = fetch_quote(ticker, api_key)
    if q:
        parts.append(
            f"Quote: ${q['price']} ({q['change_pct']}% today), "
            f"prev close ${q['prev_close']}, day range ${q['day_low']}-${q['day_high']}"
        )

        # The permanent self-snapshot mechanism (see fetch_price_history's
        # comment above): every real get_market_data call writes today's
        # price into price_snapshots, zero new API calls (reuses the quote
        # already fetched above). This is what keeps price history current
        # going forward, after backfill_price_history.py's one-time seed.
        # Wrapped defensively -- a DB hiccup here must not break the quote
        # the user is actually asking for; same "degrade gracefully"
        # pattern as every other optional data source in this file.
        if q.get("price") is not None:
            try:
                db.save_price_snapshot(ticker, datetime.now().date(), q["price"])
            except Exception as e:
                print(f"!! save_price_snapshot failed for {ticker}: {e}", file=sys.stderr)

    # Historical price change (week/month) -- see the long comment above
    # fetch_price_history for why this exists: get_market_data previously
    # exposed ONLY today's %% change, so a question claiming a move over
    # any other period (e.g. "dropped 8% last week") had no real data to
    # be checked against at all.
    history = fetch_price_history(ticker)
    if history:
        change_lines = []
        for label, trading_days in (("~1 week", 5), ("~1 month", 21)):
            change = compute_price_change_over(history, trading_days)
            if change:
                change_lines.append(
                    f"  {label} ({change['from_date']} -> {change['to_date']}): "
                    f"{change['pct_change']:+.2f}% (${change['from_close']} -> ${change['to_close']})"
                )
        parts.append(
            "Price change over time (verify any claimed % move against this -- do not take the "
            "user's stated number at face value):\n" + "\n".join(change_lines)
            if change_lines
            else "Price change over time: not enough historical data yet to compute."
        )
    else:
        parts.append("Price change over time: unavailable (database read failed).")

    # Added for eval Q11 ("when does X report next, what should I watch
    # for") -- fetch_next_earnings_date already existed and was already
    # used by the dashboard endpoint (get_dashboard_data below), but was
    # never actually surfaced to the chat agent itself. That gap, not a
    # missing Finnhub integration, was Q11's real blocker.
    next_earnings = fetch_next_earnings_date(ticker, api_key)
    parts.append(
        f"Next scheduled earnings date: {next_earnings}"
        if next_earnings
        else "Next scheduled earnings date: not yet announced."
    )

    transactions = fetch_insider_transactions(ticker, api_key)
    cutoff = datetime.now() - timedelta(days=30)
    recent = [t for t in transactions if within_window(t.get("transactionDate"), cutoff)]
    if recent:
        lines = []
        for t in recent[:15]:
            code = t.get("transactionCode", "?")
            change = t.get("change")
            lines.append(
                f"  {t.get('name', 'unknown')} -- {CODE_LABELS.get(code, code)} -- "
                f"{abs(change) if change is not None else '?'} shares @ ${t.get('transactionPrice')} "
                f"on {t.get('transactionDate')} (filed {t.get('filingDate')})"
            )
        parts.append("Insider transactions (last 30 days):\n" + "\n".join(lines))
    else:
        parts.append("Insider transactions (last 30 days): none found.")

    trends = fetch_recommendation_trends(ticker, api_key)
    parts.append("Analyst recommendation trends:\n" + format_recommendation_trends(trends))

    return "\n\n".join(parts)


TOOL_BELT = [search_filings, search_filings_exact, search_live_news, get_market_data]


# -----------------------------------------------------------------
# Fundamentals Health Score -- deterministic pre-step, not a bound
# tool. See module docstring.
#
# TTL cache applied per Session 12's tool-result-caching pattern
# (02_Cat_Health_Agent_Caching.ipynb, Task 5, the TOOL_CACHE / TTL
# example): this makes 4-6 live SEC/Finnhub calls per computation --
# the exact "slow backend" shape that pattern targets. The TTL is,
# in that notebook's own words, "the honesty knob": how stale a
# result we're willing to serve. XBRL filings and 8-Ks are filed on
# a quarterly/event cadence, never intraday, so 15 minutes of
# staleness here is not a real accuracy risk.
#
# 2026-07-27 update: this comment originally said live quotes stay
# permanently uncached, because a 15-minute-old price is a real,
# different kind of staleness than a quarterly filing. That reasoning
# was correct for accuracy, but it didn't account for call volume --
# fetch_quote turned out to be a real contributor to hitting Finnhub's
# rate limit (same category of bug as the news-caching fix, see
# get_dashboard_news above). Maiu's explicit call: quotes now get the
# same 15-minute TTL as this cache, accepting that specific staleness
# tradeoff deliberately rather than leaving the call uncached by
# default. See QUOTE_TTL_SECONDS below fetch_next_earnings_date's
# original spot in this file.
# -----------------------------------------------------------------
HEALTH_SCORE_TTL_SECONDS = 900  # 15 minutes
_HEALTH_SCORE_CACHE: dict[str, tuple[float, dict]] = {}


def get_fundamentals_health_score(ticker: str, force_refresh: bool = False) -> dict:
    """Computes the four-signal Fundamentals Health Score (PRD Task 2
    section 4), cached per ticker for HEALTH_SCORE_TTL_SECONDS (see
    note above). Worst-of rollup, not averaged -- a healthy signal
    elsewhere should never dilute away a genuine red flag.

    Known, disclosed limitation: the insider-activity signal cannot yet
    distinguish a routine 10b5-1 plan sale (Finnhub's insider-
    transactions endpoint doesn't expose plan status) from a
    discretionary one. This classifier applies the dollar/count
    thresholds as a conservative signal and labels every result with
    that caveat rather than silently overstating precision -- a real
    plan-vs-discretionary filter is a pending follow-up.
    """
    ticker = ticker.upper()
    now = time.monotonic()
    if not force_refresh:
        cached = _HEALTH_SCORE_CACHE.get(ticker)
        if cached and now - cached[0] < HEALTH_SCORE_TTL_SECONDS:
            return cached[1]

    result = _compute_fundamentals_health_score(ticker)
    _HEALTH_SCORE_CACHE[ticker] = (now, result)
    return result


def _compute_fundamentals_health_score(ticker: str) -> dict:
    signals: dict[str, dict] = {}

    if ticker in TICKER_TO_CIK:
        cik = TICKER_TO_CIK[ticker]

        try:
            _, revenue_raw = fetch_revenue(cik)
            revenue_q = quarterly_series(revenue_raw)
            signals["revenue_growth"] = classify_revenue_trend(revenue_q)

            gross_profit_raw = fetch_concept(cik, GROSS_PROFIT_TAG)
            if gross_profit_raw:
                gross_profit_q = quarterly_series(gross_profit_raw)
                signals["margin"] = classify_margin_trend(revenue_q, gross_profit_q)
            else:
                signals["margin"] = {"status": "insufficient_data", "reason": "no GrossProfit tag reported"}
        except Exception as e:  # noqa: BLE001 -- surface as a signal status, don't crash the agent turn
            signals["revenue_growth"] = {"status": "insufficient_data", "reason": str(e)}
            signals["margin"] = {"status": "insufficient_data", "reason": str(e)}

        try:
            submissions = fetch_submissions(cik)
            matches = find_8k_with_item(submissions, lookback=90)
            if not matches:
                signals["leadership"] = {"status": "intact", "reason": "no 8-K Item 5.02 in last 90 days"}
            else:
                results = []
                for m in matches:
                    if not m["primary_doc"]:
                        continue
                    text = fetch_filing_text(cik, m["accession"], m["primary_doc"])
                    c = classify_departure(text)
                    c["filed"] = m["filed"]
                    results.append(c)
                order = {"intact": 0, "monitor": 1, "at_risk": 2}
                ceo_cfo_departures = sum(1 for r in results if r.get("is_ceo_or_cfo") and r["status"] != "intact")
                worst = max(results, key=lambda r: order.get(r["status"], 0)) if results else {"status": "intact"}
                overall = worst["status"]
                if ceo_cfo_departures >= 2:
                    overall = "at_risk"
                signals["leadership"] = {"status": overall, "departures": results}
        except Exception as e:  # noqa: BLE001
            signals["leadership"] = {"status": "insufficient_data", "reason": str(e)}
    else:
        no_cik = {"status": "insufficient_data", "reason": f"no CIK mapped for {ticker}"}
        signals["revenue_growth"] = no_cik
        signals["margin"] = no_cik
        signals["leadership"] = no_cik

    api_key = os.environ.get("FINNHUB_API_KEY")
    if api_key:
        try:
            transactions = fetch_insider_transactions(ticker, api_key)
            cutoff = datetime.now() - timedelta(days=30)
            sells = [
                t
                for t in transactions
                if t.get("transactionCode") == "S" and within_window(t.get("transactionDate"), cutoff)
            ]
            total_value = sum(
                abs(t["change"]) * t["transactionPrice"]
                for t in sells
                if t.get("change") is not None and t.get("transactionPrice")
            )
            distinct_sellers = len({t.get("name") for t in sells})
            ceo_cfo_big_sale = any(
                abs(t.get("change") or 0) * (t.get("transactionPrice") or 0) > 5_000_000
                and re.search(r"\b(CEO|CFO|chief executive|chief financial)\b", t.get("name", ""), re.IGNORECASE)
                for t in sells
            )
            if ceo_cfo_big_sale or (distinct_sellers >= 2 and total_value > 25_000_000):
                status = "at_risk"
            elif total_value > 25_000_000 or distinct_sellers >= 2:
                status = "monitor"
            else:
                status = "intact"
            signals["insider_activity"] = {
                "status": status,
                "total_sell_value_30d": round(total_value),
                "distinct_sellers_30d": distinct_sellers,
                "caveat": "cannot distinguish 10b5-1 plan sales from discretionary yet -- see docstring",
            }
        except Exception as e:  # noqa: BLE001
            signals["insider_activity"] = {"status": "insufficient_data", "reason": str(e)}
    else:
        signals["insider_activity"] = {"status": "insufficient_data", "reason": "FINNHUB_API_KEY not configured"}

    order = {"intact": 0, "monitor": 1, "at_risk": 2}
    real_statuses = [s.get("status") for s in signals.values() if s.get("status") in order]
    overall = max(real_statuses, key=lambda s: order[s]) if real_statuses else "insufficient_data"

    return {"ticker": ticker, "overall": overall, "signals": signals}


# -----------------------------------------------------------------
# Dashboard data -- composes the above with a live quote, next
# earnings date, and recent news into one payload for the frontend's
# per-ticker card + chart + news section. Deliberately does NOT include
# cost basis, shares held, $ gain/loss, or % of portfolio -- no data
# source for any of those exists anywhere in this codebase (no
# database, no onboarding form; the PRD's Task 3 Tables B/C describe
# them conceptually but nothing was ever built to capture them).
# Fabricating those numbers for a nicer-looking dashboard would violate
# this project's own grounded-in-real-data principle, so they're
# omitted entirely rather than mocked.
# -----------------------------------------------------------------

# Real bug, found 2026-07-27: this had NO caching at all, unlike
# get_fundamentals_health_score right above it. Every dashboard load
# re-hit Tavily fresh for all 6 tracked tickers, and every reload during
# testing burned 6 more calls -- almost certainly the actual cause of
# repeatedly hitting Tavily's plan usage limit (HTTP 432), not one
# unlucky spike. Same cache pattern as the health score, 30 minutes
# instead of 15 -- news genuinely changes slower than a health score
# that's partly driven by same-day insider-activity data, so a longer
# TTL is a real reduction in call volume, not just consistency for its
# own sake.
NEWS_TTL_SECONDS = 86400  # 24 hours (2026-07-28, Maiu: was 30 min -- widened
# to once-a-day since news doesn't move fast enough to justify burning
# Tavily calls every half hour, and the 30-min version was still going to
# hit the same usage-limit wall over a full day of testing/demoing.
_NEWS_CACHE: dict[str, tuple[float, list[dict]]] = {}


def get_dashboard_news(ticker: str, force_refresh: bool = False) -> list[dict]:
    """Recent, relevance-filtered news for one ticker (Supporting Evidence
    panel). Cached per ticker for NEWS_TTL_SECONDS -- see comment above."""
    ticker = ticker.upper()
    now = time.monotonic()
    if not force_refresh:
        cached = _NEWS_CACHE.get(ticker)
        if cached and now - cached[0] < NEWS_TTL_SECONDS:
            return cached[1]

    news = _fetch_dashboard_news_uncached(ticker)
    _NEWS_CACHE[ticker] = (now, news)
    return news


def _fetch_dashboard_news_uncached(ticker: str) -> list[dict]:
    news: list[dict] = []
    tavily_key = os.environ.get("TAVILY_API_KEY")
    company = TICKER_TO_COMPANY.get(ticker, ticker)
    if not tavily_key:
        return news

    try:
        # A real, observed failure (not hypothetical): a plain "week"
        # search for a smaller-cap ticker returned 5 results, none of
        # them actually about this company -- Tavily backfilled with
        # loosely-related filler rather than returning fewer, real
        # hits. Two independent, cheap defenses against showing that
        # as if it were real news: (1) widen to "month" -- test_q2.py's
        # own established finding for thin-coverage tickers -- and
        # (2) require the company name to actually appear in the
        # title or content, a deterministic sanity check that doesn't
        # depend on trusting Tavily's own relevance score alone.
        results = search_tavily(company, tavily_key, time_range="month", max_results=8, topic="news")
        company_lower = company.lower()
        # Widened 2026-07-27 (Maiu: ALAB showing zero articles, real
        # bug) -- the original filter only matched the full company
        # name ("astera labs") as a literal substring. Financial
        # headlines routinely use the bare ticker instead
        # ("ALAB soars on AI demand..."), especially for a name like
        # Astera Labs that isn't a common dictionary word the way
        # "Apple" is -- those headlines were being silently dropped
        # even when genuinely about this company. Ticker match uses a
        # word-boundary regex, not a plain substring check, so a
        # 3-5 letter ticker can't accidentally match inside an
        # unrelated longer word.
        ticker_pattern = re.compile(rf"\b{re.escape(ticker.lower())}\b")

        def _is_relevant(r: dict) -> bool:
            title = (r.get("title") or "").lower()
            content = (r.get("content") or "").lower()
            return (
                company_lower in title
                or company_lower in content
                or bool(ticker_pattern.search(title))
                or bool(ticker_pattern.search(content))
            )

        relevant = [r for r in results if _is_relevant(r)]
        news = [
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "date": r.get("published_date"),
                "excerpt": (r.get("content") or "")[:280],
            }
            for r in relevant[:5]
        ]
        # Deliberately no synthetic fallback text here if `news` ends up
        # empty -- an honest "nothing found" is correct output when
        # nothing relevant clears the bar, not a bug to paper over.
    except Exception as e:  # noqa: BLE001 -- news is a nice-to-have on this endpoint, never worth a 500
        # Logged (2026-07-27), not silently swallowed -- an empty `news`
        # list caused by a real API/network failure was previously
        # indistinguishable from an empty list caused by "nothing
        # relevant found," which made a real bug harder to diagnose than
        # it needed to be.
        print(f"get_dashboard_news: search failed for {ticker!r}: {e!r}", file=sys.stderr)
        news = []

    return news


def get_dashboard_data(ticker: str) -> dict:
    ticker = ticker.upper()
    # Real bug, found 2026-07-28 while making an unrelated change: the news
    # refactor (get_dashboard_news / _fetch_dashboard_news_uncached) moved
    # this line's only other definition into that new inner function, but
    # left this function still referencing `company` in its return dict.
    # That's a NameError on every single call -- this function could not
    # have returned successfully since that refactor landed. Restoring the
    # lookup here, independent of whatever _fetch_dashboard_news_uncached
    # does internally.
    company = TICKER_TO_COMPANY.get(ticker, ticker)
    health_score = get_fundamentals_health_score(ticker)

    api_key = os.environ.get("FINNHUB_API_KEY")
    quote = fetch_quote(ticker, api_key) if api_key else None
    next_earnings_date = fetch_next_earnings_date(ticker, api_key) if api_key else None

    news = get_dashboard_news(ticker)

    return {
        "ticker": ticker,
        "company": company,
        "health_score": health_score,
        "quote": quote,
        "next_earnings_date": next_earnings_date,
        "news": news,
    }
