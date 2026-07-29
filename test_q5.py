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
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from ragas.messages import ToolCall

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


# --- Real automated scoring for Q4, added 2026-07-28 (Maiu, explicit
# call: "build and automate all 10"). eval_dataset.json's own
# scoring_method for id 4 is "tool_call_goal_topic" -- the same method
# Q2/Q9/Q11 use, whose own _meta description says the trace should come
# from LangGraph, not a standalone script -- so this calls the REAL
# deployed agent (app.graph.ask), same pattern as test_q2.py/test_q9.py,
# not the direct-Finnhub main() above (that stays as-is, it's a useful
# manual-review CLI, not the scored harness).
INSIDER_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are scoring an AI portfolio assistant's insider-selling "
            "check against three criteria. Score each PASS or FAIL with a "
            "one-sentence reason. Be strict.\n\n"
            'USER QUESTION: "{question}" (asked inside the user\'s {ticker} '
            "position thread)\n\n"
            "TOOLS THE AGENT CALLED: {tools_used}\n\n"
            "AGENT RESPONSE:\n{response}\n\n"
            "Score these three criteria:\n\n"
            "1. real_data_checked: Did the agent actually query real "
            "insider-transaction data (not just answer generically or from "
            "memory)? FAIL if the response reads like it never checked.\n"
            "2. accurate_reporting: If insider selling occurred in the "
            "window, does the response name the transaction with concrete "
            "detail (who, how many shares, when)? If none occurred, does "
            "the response say so plainly rather than being vague? FAIL if "
            "it hedges without a clear answer either way.\n"
            "3. scoped_correctly: Does the response stay scoped to {ticker} "
            "-- the user's actual holding in this thread -- rather than "
            "fabricating data for tickers not held or asked about?\n\n"
            "Respond in exactly this format, no extra commentary:\n"
            "real_data_checked: PASS/FAIL -- <reason>\n"
            "accurate_reporting: PASS/FAIL -- <reason>\n"
            "scoped_correctly: PASS/FAIL -- <reason>",
        )
    ]
)

GOAL_REFERENCE_Q4 = (
    "The AI assistant checked real insider-transaction data for {company} "
    "and reported whether any insider selling occurred in the past week."
)


def load_q4() -> dict:
    """Loads Q4's test cases from eval_dataset.json (id 4).

    Normalized here 2026-07-28: the dataset's original Q4 entry holds ONE
    case with a plural "tickers" list -- the only tool_calling question
    shaped that way (every other one uses singular "ticker"/"company").
    The real product has no portfolio-wide query surface: LangGraph is one
    thread per ticker, and get_market_data (app/tools.py) itself only ever
    takes a single ticker -- the same structural constraint that keeps the
    old id-12/new id-11 "whole portfolio" question explicitly untested.
    Rather than silently ask the live agent a question it cannot answer as
    literally posed, this expands the tickers list into one case per
    ticker at load time -- matching Q2/Q9/Q11's {"ticker", "company"}
    shape -- and each case gets asked inside THAT ticker's own thread,
    exactly like a real user would ask it from their ALAB (or AAPL, MRVL,
    NBIS) position view."""
    import json

    from app.tools import TICKER_TO_COMPANY

    with open("eval_dataset.json") as f:
        data = json.load(f)
    q4 = next(q for q in data["questions"] if q["id"] == 4)
    tickers = q4["test_cases"][0]["tickers"]
    q4 = dict(q4)
    q4["test_cases"] = [{"ticker": t, "company": TICKER_TO_COMPANY.get(t, t)} for t in tickers]
    return q4


def run_case(graph, case: dict, judge_llm) -> dict:
    """Calls the real live agent with eval_dataset.json's own Q4 wording,
    asked inside case['ticker']'s own thread -- see load_q4()'s docstring
    for why the dataset's portfolio-wide framing gets asked per-ticker
    rather than as one literal multi-ticker query.

    ask() is imported HERE, deliberately, not at module level -- app/
    tools.py imports fetch_insider_transactions/CODE_LABELS/within_window
    from THIS file, so a top-level `from app.graph import ask` here would
    be a real circular import (app.tools -> test_q5 -> app.graph ->
    app.tools), same class of bug already caught and fixed in test_q2.py.
    Deferred to call time, after app.tools has already finished loading,
    breaks the cycle."""
    from app.graph import ask
    from eval_tool_call_accuracy import score_goal_accuracy, score_tool_call_accuracy

    question = "Is there any insider selling in my holdings this week?"
    print(f"\n{'=' * 70}\n{case['ticker']}\n{'=' * 70}\nQ: {question}")

    result = ask(graph, case["ticker"], question, thread_id=f"q4-{case['ticker']}")
    print(f"\nTools called: {result.tools_used}\n\nResponse:\n{result.answer}")

    chain = INSIDER_JUDGE_PROMPT | judge_llm | StrOutputParser()
    judgment = chain.invoke(
        {
            "question": question,
            "ticker": case["ticker"],
            "tools_used": result.tools_used or "(none)",
            "response": result.answer,
        }
    )
    print(f"\n--- Judge scoring ---\n{judgment}")

    t = case["ticker"]
    acceptable_tool_sets = [[ToolCall(name="get_market_data", args={"ticker": t})]]
    ragas_result = score_tool_call_accuracy(question, result.tool_calls, acceptable_tool_sets)
    goal_score = score_goal_accuracy(
        question, result.tool_calls, result.answer, GOAL_REFERENCE_Q4.format(company=case["company"])
    )
    print(
        f"\n--- RAGAS ---\ntool_call_accuracy: {ragas_result.score:.2f}\n"
        f"goal_accuracy: {goal_score:.2f}"
    )

    return {
        "ticker": t,
        "tools_used": result.tools_used,
        "response": result.answer,
        "judgment": judgment,
        "ragas_tool_call_accuracy": ragas_result.score,
        "ragas_goal_accuracy": goal_score,
    }


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
