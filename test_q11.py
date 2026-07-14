"""
Test harness for Eval Question 11 (Task 1):
"When does {company} report next, and what should I watch for based on
its current Fundamentals Health Score?"

Tool-calling/hybrid question (Task 5 scoring: tool-call accuracy, goal
accuracy, topic adherence from a LangGraph trace) -- but unlike Q7/Q9,
the ground truth here is fully computable in Python BEFORE the agent is
ever asked anything: get_fundamentals_health_score() already returns
every sub-signal's real status, and fetch_next_earnings_date() is
already wired into get_market_data (the Q11 tool-exposure fix from
earlier this session). So this harness checks the agent's answer
against real precomputed ground truth deterministically, not just an
LLM judge's impression -- did it cite the actual date, did it name
every sub-signal that's actually monitor/at_risk, did it avoid treating
an intact signal as a concern.

Two locked test cases deliberately exercise different paths:
  - MRVL currently has a real monitor/at_risk sub-signal (decelerating
    revenue growth) -- tests that the agent actually surfaces it.
  - NBIS's revenue/margin are insufficient_data (20-F filer, no
    quarterly XBRL ever filed with the SEC -- see PRD Open Items) --
    tests that the agent reports that honestly instead of inventing a
    number or silently dropping the signal.

Usage:
    python test_q11.py                          # locked test cases (MRVL, NBIS)
    python test_q11.py --ticker ALAB --company "Astera Labs"
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from ragas.messages import ToolCall

from app.graph import ask, build_graph
from app.tools import fetch_next_earnings_date, get_fundamentals_health_score
from eval_tool_call_accuracy import score_goal_accuracy, score_tool_call_accuracy

load_dotenv()

DATASET_PATH = "eval_dataset.json"

WATCH_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are scoring an AI portfolio assistant's answer to an earnings-date/what-to-watch "
            "question against three criteria. Score each PASS or FAIL with a one-sentence reason. "
            "Be strict.\n\n"
            "USER QUESTION: \"{question}\"\n\n"
            "REAL NEXT EARNINGS DATE: {next_earnings}\n"
            "REAL FLAGGED SUB-SIGNALS (monitor/at_risk): {flagged_signals}\n\n"
            "AGENT RESPONSE:\n{response}\n\n"
            "Score these three criteria:\n\n"
            "1. goal_accuracy: For each flagged sub-signal listed above, does the response name the "
            "SPECIFIC number or event driving it (not just the word 'monitor' or 'at risk')? FAIL if "
            "it lists a signal name with no concrete reason behind it.\n"
            "2. topic_adherence: Does the response stay grounded in this ticker's actual health-score "
            "data rather than generic 'watch the earnings call for surprises' filler that would apply "
            "to any company?\n"
            "3. no_overclaiming: Does the response avoid describing an intact signal (one NOT listed "
            "above) as a concern, and avoid inventing a number that isn't in the real data above?\n\n"
            "Respond in exactly this format, no extra commentary:\n"
            "goal_accuracy: PASS/FAIL -- <reason>\n"
            "topic_adherence: PASS/FAIL -- <reason>\n"
            "no_overclaiming: PASS/FAIL -- <reason>",
        )
    ]
)


def load_q11() -> dict:
    with open(DATASET_PATH) as f:
        data = json.load(f)
    return next(q for q in data["questions"] if q["id"] == 11)


# Outcome-voiced reference for RAGAS's AgentGoalAccuracyWithReference --
# NOT eval_dataset.json's expected_behavior field. See test_q9.py's
# GOAL_REFERENCE comment for the full two-bug history (rubric-voiced
# prose, then a still-too-checklist-shaped outcome statement) confirmed
# via debug_goal_accuracy.py's CompareOutcomeOutput.reason printout.
# Kept to the single content outcome here too -- "citing the specific
# number or event behind each flagged signal" is process quality, not
# outcome content, and is already correctly scored by this file's own
# custom judge's goal_accuracy criterion above.
GOAL_REFERENCE = (
    "The AI assistant reported {company}'s next earnings date and named "
    "the fundamentals sub-signals currently at Monitor or At Risk."
)


def get_ground_truth(ticker: str) -> dict:
    """Real, precomputed answer -- same functions the live agent's own
    tools call, invoked directly here so the harness has something
    known-correct to check the agent's answer against."""
    api_key = os.environ.get("FINNHUB_API_KEY")
    next_earnings = fetch_next_earnings_date(ticker, api_key) if api_key else None
    health_score = get_fundamentals_health_score(ticker)
    flagged = {
        name: sig
        for name, sig in health_score["signals"].items()
        if sig.get("status") in ("monitor", "at_risk")
    }
    return {"next_earnings": next_earnings, "health_score": health_score, "flagged_signals": flagged}


def _date_variants(iso_date: str) -> list[str]:
    """Reasonable prose renderings of an ISO date string. The agent
    correctly writes '2026-08-26' as 'August 26, 2026' rather than
    repeating the raw ISO string -- confirmed as a real false negative
    in testing (the original ISO-only substring check flagged both
    MRVL and NBIS's real, correct earnings dates as 'missing' purely
    because of format, not because the agent got anything wrong)."""
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return [iso_date]
    return [
        iso_date,                                      # "2026-08-26"
        f"{dt.strftime('%B')} {dt.day}, {dt.year}",     # "August 26, 2026"
        f"{dt.strftime('%b')} {dt.day}, {dt.year}",     # "Aug 26, 2026"
        f"{dt.month}/{dt.day}/{dt.year}",                # "8/26/2026"
    ]


def _check_coverage(response: str, ground_truth: dict) -> dict:
    response_lower = response.lower()
    ne = ground_truth["next_earnings"]
    earnings_date_cited = bool(ne) and any(v.lower() in response_lower for v in _date_variants(ne))

    missing_signals = [
        name
        for name in ground_truth["flagged_signals"]
        if name not in response_lower and name.replace("_", " ") not in response_lower
    ]
    return {
        "earnings_date_cited": earnings_date_cited,
        "all_flagged_signals_named": not missing_signals,
        "missing_signals": missing_signals,
    }


def run_case(graph, case: dict, judge_llm) -> dict:
    ticker = case["ticker"]
    company = case["company"]
    question = (
        f"When does {company} report next, and what should I watch for "
        f"based on its current Fundamentals Health Score?"
    )
    print(f"\n{'=' * 70}\n{ticker}\n{'=' * 70}")
    print(f"Q: {question}")

    ground_truth = get_ground_truth(ticker)
    print(f"\n[Ground truth] next earnings: {ground_truth['next_earnings']}")
    print(f"[Ground truth] flagged signals: {list(ground_truth['flagged_signals'].keys()) or '(none)'}")

    result = ask(graph, ticker, question, thread_id=f"q11-{ticker}")
    print(f"\nTools called: {result.tools_used}")
    print(f"\nResponse:\n{result.answer}")

    coverage = _check_coverage(result.answer, ground_truth)
    if not coverage["earnings_date_cited"]:
        print("\n*** WARNING: real next earnings date not found verbatim in response. ***")
    if coverage["missing_signals"]:
        print(f"\n*** WARNING: flagged signal(s) never mentioned: {coverage['missing_signals']} ***")

    chain = WATCH_JUDGE_PROMPT | judge_llm | StrOutputParser()
    judgment = chain.invoke(
        {
            "question": question,
            "next_earnings": ground_truth["next_earnings"] or "(not announced)",
            "flagged_signals": list(ground_truth["flagged_signals"].keys()) or "(none -- all signals intact)",
            "response": result.answer,
        }
    )
    print(f"\n--- Judge scoring (goal_accuracy, topic_adherence, no_overclaiming) ---\n{judgment}")

    # Real RAGAS ToolCallAccuracy -- new for Q11 (Open Items fix). This
    # file never scored tool-calling behavior with any metric before,
    # real or custom -- only printed result.tools_used for visibility.
    # The next-earnings date is surfaced by get_market_data
    # (fetch_next_earnings_date is wired into it -- see PRD Task 5 Table
    # E), so that's the one deterministic required call; a single-item
    # reference is maximally tolerant of any other real calls the agent
    # also makes (see eval_tool_call_accuracy.py -- is_sequence_aligned
    # returns True as soon as the one required call appears anywhere in
    # the predicted sequence).
    acceptable_tool_sets = [[ToolCall(name="get_market_data", args={"ticker": ticker})]]
    ragas_result = score_tool_call_accuracy(question, result.tool_calls, acceptable_tool_sets)
    print(
        f"\n--- RAGAS ToolCallAccuracy (real metric class) ---\n"
        f"score: {ragas_result.score:.2f}\n"
        f"predicted sequence: {[c['name'] for c in result.tool_calls]}"
    )

    # Real RAGAS AgentGoalAccuracyWithReference -- new for Q11 (Open Items
    # fix), same gap as Q9/Q13: goal_accuracy was only ever scored by this
    # file's own PASS/FAIL judge prompt. Uses GOAL_REFERENCE (outcome-
    # voiced), not expected_behavior (rubric-voiced) -- see comment above.
    goal_score = score_goal_accuracy(
        question, result.tool_calls, result.answer, GOAL_REFERENCE.format(company=company)
    )
    print(f"\n--- RAGAS AgentGoalAccuracyWithReference (real metric class) ---\nscore: {goal_score:.2f}")

    return {
        "ticker": ticker,
        "tools_used": result.tools_used,
        "coverage": coverage,
        "response": result.answer,
        "judgment": judgment,
        "ragas_tool_call_accuracy": ragas_result.score,
        "ragas_goal_accuracy": goal_score,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--company")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    q11 = load_q11()

    if args.ticker:
        cases = [{"ticker": args.ticker, "company": args.company or args.ticker}]
    else:
        cases = q11["test_cases"]

    graph = build_graph()
    judge_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    results = [run_case(graph, c, judge_llm) for c in cases]

    print(f"\n\n{'=' * 70}\nSUMMARY -- {len(results)} case(s)\n{'=' * 70}")
    for r in results:
        flags = []
        if not r["coverage"]["earnings_date_cited"]:
            flags.append("MISSING EARNINGS DATE")
        if r["coverage"]["missing_signals"]:
            flags.append(f"MISSING SIGNALS: {r['coverage']['missing_signals']}")
        flag_str = f"  <-- {'; '.join(flags)}" if flags else ""
        print(
            f"{r['ticker']}: tools={r['tools_used']}{flag_str}  "
            f"ragas_tool_call_accuracy={r['ragas_tool_call_accuracy']:.2f}  "
            f"ragas_goal_accuracy={r['ragas_goal_accuracy']:.2f}"
        )


if __name__ == "__main__":
    main()
