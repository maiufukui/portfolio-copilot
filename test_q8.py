"""
Test harness for Eval Question 8 (Task 1):
"What did analysts say after today's guidance cut?"

Searches Tavily for recent news tied to a specific event (e.g. a
guidance cut) for a ticker, then synthesizes an answer that explicitly
separates company statements from analyst commentary, each with dated,
sourced citations -- matching the eval's expected behavior spec.

Reuses search_tavily/format_results from test_q2.py rather than
duplicating the Tavily call.

Usage:
    python test_q8.py --ticker ALAB --company "Astera Labs" --event "guidance cut"
    python test_q8.py --ticker MRVL --company Marvell --event "earnings miss"
"""

from __future__ import annotations

import argparse
import os
import time

import requests
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from ragas.messages import ToolCall

from test_q2 import display_date, format_results, search_tavily

load_dotenv()

FINNHUB_RECOMMENDATION_URL = "https://finnhub.io/api/v1/stock/recommendation"


# Caching added 2026-07-27 (Maiu, explicit call) -- same audit/pattern as
# test_q5.py's fetch_insider_transactions. 24h TTL is a lower-risk call
# than insider transactions specifically: Finnhub's own recommendation
# data is an aggregated monthly-cadence consensus, not something that
# actually updates intraday, so a day-old cache isn't giving up real
# freshness the source itself doesn't already lack.
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


# --- Eval Q8: "Have analysts changed their rating on {company} recently?" ---
# Distinct from the Q6 driver above: no event, no Tavily search -- just Finnhub
# recommendation-trends, diffed period-over-period. The diff itself is computed
# here in plain Python (deterministic, exact) rather than asked of the LLM --
# same reasoning as Q10's deterministic-assertion pieces elsewhere in this
# eval set: don't trust an LLM to do subtraction when Python already can, and
# score-check it exactly instead of hoping a judge notices a wrong diff.
RATING_CATEGORIES = ["strongBuy", "buy", "hold", "sell", "strongSell"]


def compute_trend_deltas(trends: list[dict]) -> dict | None:
    """Deterministic diff between the two most recent periods' recommendation
    trends. Returns None if fewer than 2 periods of data exist -- there's
    nothing to diff against, and the prompt needs to say that plainly rather
    than fabricate a comparison."""
    if len(trends) < 2:
        return None
    current, prior = trends[0], trends[1]
    return {
        "current_period": current.get("period"),
        "prior_period": prior.get("period"),
        "current_counts": {c: current.get(c, 0) for c in RATING_CATEGORIES},
        "prior_counts": {c: prior.get(c, 0) for c in RATING_CATEGORIES},
        "deltas": {c: current.get(c, 0) - prior.get(c, 0) for c in RATING_CATEGORIES},
    }


RATING_CHANGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            'The user asked: "Have analysts changed their rating on {company} '
            'recently?"\n\n'
            "Below is real institutional recommendation-trend data (Finnhub, "
            "aggregated sell-side coverage counts) for the two most recent "
            "periods, plus the exact deltas already computed between them in "
            "Python -- these numbers are authoritative, do not recompute or "
            "second-guess them.\n\n"
            "Current period ({current_period}): {current_counts}\n"
            "Prior period ({prior_period}): {prior_counts}\n"
            "Computed deltas, current minus prior ({current_period} vs "
            "{prior_period}): {deltas}\n\n"
            "State plainly whether the buy/hold/sell distribution shifted and "
            "by how much, citing both period dates explicitly -- write both "
            "dates EXACTLY as given above, in YYYY-MM-DD format (e.g. "
            "'2026-07-01'), not reworded into a different date style (e.g. "
            "'July 1, 2026'). If every delta is zero, say directly that the "
            "distribution hasn't changed rather than describing a shift that "
            "didn't happen. Keep the answer to 3-4 sentences.",
        )
    ]
)


# Real automated scoring for Q8, added 2026-07-27 (Maiu, explicit call --
# "build and automate all 10"). Previously this mode had a real computed
# ground truth (compute_trend_deltas) but nothing checked the LLM's
# narration against it -- disclosed as a real gap in run_scorecard.py's
# NOT_SCORED dict. This closes it the same way test_q11.py's deterministic
# checks work: don't trust an LLM judge to notice a wrong number, check
# the actual numbers appear correctly instead.
#
# FALSE NEGATIVE FOUND AND FIXED 2026-07-28, via a real run_scorecard.py
# run against MRVL: check_narration_matches_deltas failed the case with
# "missing current period date '2026-07-01'; missing prior period date
# '2026-06-01'" even though the narration correctly said "between June 1,
# 2026, and July 1, 2026" -- the dates were right, just written in prose
# instead of the literal ISO string this check does a substring match
# against. RATING_CHANGE_PROMPT never told the model to preserve ISO
# formatting, so a good, natural-language answer failed a check that was
# too brittle, not a real narration bug. Fixed at the prompt (below),
# not by loosening the check: RATING_CHANGE_PROMPT now explicitly
# requires both dates be written EXACTLY as given, in YYYY-MM-DD format
# -- keeps the check's literal match meaningful (it still verifies the
# real date value appears, not just a fuzzy "some date-like text"
# match) instead of trading precision for leniency.
def check_narration_matches_deltas(narration: str, deltas: dict) -> tuple[bool, str]:
    """Deterministically verifies the LLM's narration actually reflects the
    real computed deltas, not just plausible-sounding prose. Checks: both
    period dates are cited, every category with a real nonzero delta has
    both its name and its exact magnitude present in the text, and if
    every delta is zero, the narration says so rather than describing a
    shift that didn't happen (the exact failure mode RATING_CHANGE_PROMPT
    explicitly warns against, now actually verified instead of trusted)."""
    lowered = narration.lower()
    problems = []

    if str(deltas["current_period"]) not in narration:
        problems.append(f"missing current period date {deltas['current_period']!r}")
    if str(deltas["prior_period"]) not in narration:
        problems.append(f"missing prior period date {deltas['prior_period']!r}")

    nonzero = {c: d for c, d in deltas["deltas"].items() if d != 0}
    if not nonzero:
        # All deltas are zero -- narration must say so, not fabricate a shift.
        no_change_markers = ("no change", "hasn't changed", "has not changed", "unchanged", "no shift", "remained the same")
        if not any(m in lowered for m in no_change_markers):
            problems.append("all deltas are zero but narration doesn't clearly state nothing changed")
    else:
        for category, delta in nonzero.items():
            if category.lower() not in lowered:
                problems.append(f"real delta in {category!r} ({delta:+d}) but category name not mentioned")
                continue
            if str(abs(delta)) not in narration:
                problems.append(f"real delta in {category!r} is {delta:+d} but that magnitude isn't in the text")

    return (len(problems) == 0, "; ".join(problems) if problems else "narration matches computed deltas")


def run_rating_change(company: str, trends: list[dict]) -> str:
    """Q8's actual check: no Tavily, no --event -- just the Finnhub trend
    data, diffed deterministically and narrated by the LLM from the computed
    numbers rather than the model's own arithmetic."""
    deltas = compute_trend_deltas(trends)
    if deltas is None:
        return (
            "Only one period of institutional recommendation-trend data is "
            "available for this ticker -- there's nothing to compare it "
            "against yet, so no period-over-period shift can be reported."
        )
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
    chain = RATING_CHANGE_PROMPT | llm | StrOutputParser()
    return chain.invoke(
        {
            "company": company,
            "current_period": deltas["current_period"],
            "current_counts": deltas["current_counts"],
            "prior_period": deltas["prior_period"],
            "prior_counts": deltas["prior_counts"],
            "deltas": deltas["deltas"],
        }
    )

def load_q8() -> dict:
    """Loads Q8's test cases straight from eval_dataset.json (id 8) --
    same convention as load_q9()/load_q11(), so run_scorecard.py doesn't
    need a special case for how Q8's cases get sourced."""
    import json

    with open("eval_dataset.json") as f:
        data = json.load(f)
    return next(q for q in data["questions"] if q["id"] == 8)


def run_rating_change_case(case: dict) -> dict:
    """Q8's real automated check -- renamed from run_case 2026-07-28: a
    real, verified bug found during this session's live eval run. This
    function and Q6's run_case(graph, case, judge_llm) below were BOTH
    named run_case at module level -- Python doesn't overload by arity,
    so the second definition (Q6's) silently shadowed this one in the
    module namespace. Any `from test_q8 import run_case` (including
    run_scorecard.py's _score_q8) was actually getting Q6's 3-arg
    function, not this one -- confirmed via a real run:
    `TypeError: run_case() missing 2 required positional arguments:
    'case' and 'judge_llm'`. Neither py_compile nor the earlier
    hasattr(test_q8, 'run_case') check caught this, since both are
    satisfied either way -- only actually calling it surfaced the bug.
    Fetch real Finnhub trends, compute the
    real deltas, get the LLM's narration, verify the narration actually
    matches the computed numbers. No LangGraph agent involved here (Q8
    never goes through app.graph.ask() -- it's direct Finnhub + a
    narration prompt, not a tool-calling question in the live-agent
    sense), so this doesn't take a `graph` argument the way test_q9.py/
    test_q11.py's run_case does."""
    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    ticker = case["ticker"]
    company = case["company"]

    trends = fetch_recommendation_trends(ticker, finnhub_key)
    deltas = compute_trend_deltas(trends)
    if deltas is None:
        return {
            "ticker": ticker,
            "passed": None,
            "reason": "fewer than 2 periods of data available -- nothing to check",
            "narration": None,
        }

    narration = run_rating_change(company, trends)
    passed, reason = check_narration_matches_deltas(narration, deltas)
    return {
        "ticker": ticker,
        "passed": passed,
        "reason": reason,
        "narration": narration,
        "deltas": deltas["deltas"],
    }


ANALYST_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            'The user asked: "What did analysts say after {ticker}\'s {event}?"\n\n'
            "Organize your answer into three clearly separated sections:\n\n"
            "1. COMPANY STATEMENTS -- anything from {company} itself (press "
            "releases, management quotes, official guidance language, SEC filings), "
            "sourced from the search results below.\n\n"
            "2. INSTITUTIONAL ANALYST COMMENTARY -- start with the aggregated "
            "consensus data below (this is real sell-side coverage data, not "
            "scraped from the web -- report it as-is, don't editorialize on "
            "whether the counts seem high or low). Then, ONLY if the search "
            "results separately name a specific analyst and/or firm (e.g. "
            '"Jane Doe, Morgan Stanley") attached to a rating change or price '
            "target, add that as supporting detail. Do not infer institutional "
            "status for a source just because it sounds authoritative or "
            "discusses analyst opinions secondhand -- if no named analyst "
            "appears in the search results, just report the consensus numbers "
            "alone and say no additional named commentary was found.\n\n"
            "3. MEDIA & COMMENTARY -- everything else from the search results: "
            "financial news outlets, blogs, YouTube/video commentary, and "
            "aggregator sites (e.g. Motley Fool, Seeking Alpha, TIKR, "
            "StockTitan, BigGo Finance) reacting to or summarizing the event "
            "without being the bank's own research.\n\n"
            "For each item in sections 1 and 3, cite the date and source URL. "
            "If a section has no qualifying items, say so explicitly rather "
            "than leaving it blank or stretching an item to fill it.\n\n"
            "INSTITUTIONAL RECOMMENDATION TRENDS (Finnhub, aggregated real "
            "analyst coverage):\n{recommendation_trends}\n\n"
            "SEARCH RESULTS:\n{results}",
        )
    ]
)


# --- Real automated scoring for Q6, added 2026-07-28 (Maiu, explicit
# call: "build and automate all 10"). eval_dataset.json's own
# scoring_method for id 6 is "tool_call_goal_topic" -- same method as
# Q2/Q4/Q9/Q11, whose _meta description says the trace should come from
# LangGraph -- so this calls the REAL deployed agent (app.graph.ask),
# not the standalone --mode reaction CLI above (that stays as-is, a
# useful manual-review path built directly on Tavily + Finnhub, not the
# scored harness). get_market_data (app/tools.py) already folds in real
# institutional recommendation-trend data via fetch_recommendation_trends
# from this file, so the live agent has both a media-search tool
# (search_live_news) and a real-institutional-data tool (get_market_data)
# available to answer this without needing a new tool binding.
REACTION_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are scoring an AI portfolio assistant's analyst-reaction "
            "response against three criteria. Score each PASS or FAIL with "
            "a one-sentence reason. Be strict.\n\n"
            'USER QUESTION: "{question}"\n\n'
            "TOOLS THE AGENT CALLED: {tools_used}\n\n"
            "AGENT RESPONSE:\n{response}\n\n"
            "Score these three criteria:\n\n"
            "1. company_vs_analyst_distinction: Does the response keep "
            "company statements (press releases, management quotes, "
            "official guidance) separate from analyst/institutional "
            "commentary, rather than blending the two? FAIL if it's not "
            "possible to tell which claims are the company's own words vs. "
            "outside commentary.\n"
            "2. institutional_data_used: Does the response reference real "
            "institutional recommendation-trend data (buy/hold/sell "
            "counts or a stated shift/no-shift), not just generic news "
            "chatter about analyst sentiment? FAIL if no real consensus "
            "data is cited.\n"
            "3. citation_quality: Is each media/news claim attributed to a "
            "dated source? FAIL if claims are asserted without a source.\n\n"
            "Respond in exactly this format, no extra commentary:\n"
            "company_vs_analyst_distinction: PASS/FAIL -- <reason>\n"
            "institutional_data_used: PASS/FAIL -- <reason>\n"
            "citation_quality: PASS/FAIL -- <reason>",
        )
    ]
)

GOAL_REFERENCE_Q6 = (
    "The AI assistant reported analyst and market reaction to {company}'s "
    "{event}, distinguishing company statements from institutional/"
    "analyst commentary."
)


def load_q6() -> dict:
    """Loads Q6's test cases from eval_dataset.json (id 6) -- same
    convention as load_q8()/load_q9()/load_q11()."""
    import json

    with open("eval_dataset.json") as f:
        data = json.load(f)
    return next(q for q in data["questions"] if q["id"] == 6)


def run_case(graph, case: dict, judge_llm) -> dict:
    """Calls the real live agent with eval_dataset.json's own Q6 wording.
    Named run_case (not run_case_q6) to match the load_qN()/run_case(graph,
    case, judge_llm) convention run_scorecard.py already dispatches on for
    Q2/Q4/Q9/Q11 -- this module's Q8 run_case (single-arg, no graph) stays
    as-is above since it's a genuinely different shape (no LangGraph agent
    involved, see its own docstring).

    ask() is imported HERE, deliberately, not at module level -- app/
    tools.py imports fetch_recommendation_trends/format_recommendation_trends
    from THIS file, so a top-level `from app.graph import ask` here would
    be a real circular import (app.tools -> test_q8 -> app.graph ->
    app.tools), same class of bug already caught and fixed in
    test_q2.py/test_q5.py."""
    from app.graph import ask
    from eval_tool_call_accuracy import score_goal_accuracy, score_tool_call_accuracy

    question = f"What did analysts say after today's {case['event']}?"
    print(f"\n{'=' * 70}\n{case['ticker']}\n{'=' * 70}\nQ: {question}")

    result = ask(graph, case["ticker"], question, thread_id=f"q6-{case['ticker']}")
    print(f"\nTools called: {result.tools_used}\n\nResponse:\n{result.answer}")

    chain = REACTION_JUDGE_PROMPT | judge_llm | StrOutputParser()
    judgment = chain.invoke(
        {"question": question, "tools_used": result.tools_used or "(none)", "response": result.answer}
    )
    print(f"\n--- Judge scoring ---\n{judgment}")

    t = case["ticker"]
    acceptable_tool_sets = [
        [
            ToolCall(name="search_live_news", args={}),
            ToolCall(name="get_market_data", args={"ticker": t}),
        ]
    ]
    ragas_result = score_tool_call_accuracy(question, result.tool_calls, acceptable_tool_sets)
    goal_score = score_goal_accuracy(
        question,
        result.tool_calls,
        result.answer,
        GOAL_REFERENCE_Q6.format(company=case["company"], event=case["event"]),
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
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument(
        "--mode",
        default="reaction",
        choices=["reaction", "rating_change"],
        help=(
            "reaction (default, eval Q6): Tavily + Finnhub, 'what did analysts "
            "say after {event}'. rating_change (eval Q8): Finnhub only, "
            "period-over-period rating shift, no --event needed."
        ),
    )
    parser.add_argument(
        "--event", required=False, help='e.g. "guidance cut", "earnings miss" -- required for --mode reaction'
    )
    parser.add_argument(
        "--days", dest="time_range", default="week", choices=["day", "week", "month", "year"]
    )
    parser.add_argument("--topic", default="news", choices=["news", "general"])
    args = parser.parse_args()

    if args.mode == "reaction" and not args.event:
        raise SystemExit("--event is required for --mode reaction (default mode)")

    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    if not finnhub_key:
        raise SystemExit("FINNHUB_API_KEY not set in .env")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set in .env")

    print(f"Fetching institutional recommendation trends for {args.ticker}...")
    trends = fetch_recommendation_trends(args.ticker, finnhub_key)
    trends_text = format_recommendation_trends(trends)
    print(f"\n{trends_text}\n")

    if args.mode == "rating_change":
        print("Comparing current period to prior period (deterministic diff, no Tavily)...")
        output = run_rating_change(args.company, trends)
        print("\n" + "=" * 60)
        print(output)
        print("=" * 60)
        return

    tavily_key = os.environ.get("TAVILY_API_KEY")
    if not tavily_key:
        raise SystemExit("TAVILY_API_KEY not set in .env")

    # Company name + event only -- dropping the ticker symbol and generic
    # "analyst reaction" wording, which diluted the match in testing (see
    # test_q2.py for the same fix and why).
    query = f"{args.company} {args.event}"
    print(f"Searching Tavily for: {query} (time_range={args.time_range}, topic={args.topic})")
    results = search_tavily(query, tavily_key, time_range=args.time_range, topic=args.topic)

    if not results:
        print("No Tavily results found -- continuing with institutional trends only.")
        results_text = "No media/company search results found."
    else:
        print(f"\n{len(results)} result(s) found:")
        for r in results:
            print(f"  - [{r.get('score')}] {r.get('title')} ({display_date(r)}) -- {r.get('url')}")
        results_text = format_results(results)

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
    chain = ANALYST_PROMPT | llm | StrOutputParser()
    output = chain.invoke(
        {
            "ticker": args.ticker,
            "company": args.company,
            "event": args.event,
            "recommendation_trends": trends_text,
            "results": results_text,
        }
    )

    print("\n" + "=" * 60)
    print(output)
    print("=" * 60)


if __name__ == "__main__":
    main()
