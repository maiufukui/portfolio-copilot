"""
Test harness for Eval Question 13 (Task 1):
"Has anything about {company}'s underlying business gotten worse since I
bought it -- revenue, margins, insider activity, or leadership?"

Despite the "since I bought it" framing, this question pulls the full
four-signal Fundamentals Health Score (Task 2 section 4) -- there is no
historical health-score snapshot anywhere in this codebase (no
database, no scheduled snapshotting job), so "since {date_purchased}"
cannot be answered as a true point-in-time diff. This is a deliberate,
documented design decision (see PRD Open Items / Task 7 Next Steps),
not an oversight: this harness scores the agent against a CURRENT-STATE
answer -- does it report all four sub-signals (not just the worst one),
the correct worst-of overall rollup, and the specific number/event
behind any non-intact status -- rather than a historical comparison it
has no honest way to make. A response that plainly presents current
status as current status (not pretending to track from date_purchased)
is the correct behavior here, not a gap to penalize.

Ground truth precomputed the same way as Q11's harness: call
get_fundamentals_health_score() directly before asking the agent
anything, so the deterministic checks compare against a real known
answer, not just an LLM judge's impression.

Usage:
    python test_q13.py                          # locked test case (ALAB)
    python test_q13.py --ticker MRVL --company Marvell --date-purchased 2025-08-01
"""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from ragas.messages import ToolCall

from app.graph import ask, build_graph
from app.tools import get_fundamentals_health_score
from eval_tool_call_accuracy import score_goal_accuracy, score_tool_call_accuracy

load_dotenv()

DATASET_PATH = "eval_dataset.json"

WORSENED_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are scoring an AI portfolio assistant's answer to a 'has anything gotten worse' "
            "question against three criteria. Score each PASS or FAIL with a one-sentence reason. "
            "Be strict.\n\n"
            "USER QUESTION: \"{question}\"\n\n"
            "REAL FUNDAMENTALS HEALTH SCORE (ground truth, all four sub-signals): {health_score}\n\n"
            "AGENT RESPONSE:\n{response}\n\n"
            "Score these three criteria:\n\n"
            "1. rollup_accuracy: Does the response's overall conclusion match the REAL overall status "
            "above, using worst-of logic (one bad signal should not get averaged away by three healthy "
            "ones)? FAIL if the response's overall read is more reassuring or more alarming than the "
            "real worst-of status supports.\n"
            "2. signal_completeness: Does the response address all four sub-signals -- revenue growth, "
            "margin, insider activity, leadership -- individually, not just the worst one?\n"
            "3. honest_framing: Does the response present this as CURRENT status (not pretending to "
            "track a true change since the user's purchase date, which no data source here supports)? "
            "FAIL if it fabricates a 'since you bought it' comparison instead of reporting current "
            "status honestly.\n\n"
            "Respond in exactly this format, no extra commentary:\n"
            "rollup_accuracy: PASS/FAIL -- <reason>\n"
            "signal_completeness: PASS/FAIL -- <reason>\n"
            "honest_framing: PASS/FAIL -- <reason>",
        )
    ]
)


def load_q13() -> dict:
    with open(DATASET_PATH) as f:
        data = json.load(f)
    return next(q for q in data["questions"] if q["id"] == 13)


# Outcome-voiced reference for RAGAS's AgentGoalAccuracyWithReference --
# NOT eval_dataset.json's expected_behavior field, which is written as
# design-rationale prose (it literally cites "(see PRD Open Items)", a
# document the comparison LLM never sees) and produced a uniform, false
# 0.00 in a real run. See test_q9.py's GOAL_REFERENCE comment for the
# full two-bug history -- the outcome-voiced fix alone still wasn't
# enough; debug_goal_accuracy.py's CompareOutcomeOutput.reason printout
# showed RAGAS's fixed InferGoalOutcomePrompt never surfaces process-
# quality judgments ("stated the CORRECT status", "presented as current
# status rather than a comparison") in its inferred end_state, only
# content. Kept to the single content outcome here -- rollup correctness
# and honest current-status framing are already correctly scored by this
# file's own custom judge's rollup_accuracy/honest_framing criteria
# above.
GOAL_REFERENCE = (
    "The AI assistant reported {company}'s current status across all "
    "four fundamentals signals -- revenue growth, margin, insider "
    "activity, and leadership -- along with the overall worst-of "
    "status."
)


def _check_coverage(response: str, health_score: dict) -> dict:
    response_lower = response.lower()
    all_names = list(health_score["signals"].keys())
    missing = [n for n in all_names if n not in response_lower and n.replace("_", " ") not in response_lower]

    order = {"intact": 0, "monitor": 1, "at_risk": 2}
    real_overall = health_score["overall"]
    # Loose check: the real overall status word should appear somewhere,
    # OR every real at_risk/monitor signal is individually named (some
    # answers state per-signal status without ever saying the word
    # "overall" -- that's still a correct, honest answer).
    non_intact = [n for n, s in health_score["signals"].items() if order.get(s.get("status"), 0) > 0]
    overall_word_present = real_overall in response_lower
    non_intact_all_named = all(
        n in response_lower or n.replace("_", " ") in response_lower for n in non_intact
    )
    return {
        "all_signals_addressed": not missing,
        "missing_signals": missing,
        "overall_status_reflected": overall_word_present or non_intact_all_named,
        "real_overall": real_overall,
    }


def run_case(graph, case: dict, judge_llm) -> dict:
    ticker = case["ticker"]
    company = case["company"]
    question = (
        f"Has anything about {company}'s underlying business gotten worse since I bought it -- "
        f"revenue, margins, insider activity, or leadership?"
    )
    print(f"\n{'=' * 70}\n{ticker}\n{'=' * 70}")
    print(f"Q: {question}")

    health_score = get_fundamentals_health_score(ticker)
    print(f"\n[Ground truth] overall: {health_score['overall']}")
    for name, sig in health_score["signals"].items():
        print(f"[Ground truth]   {name}: {sig.get('status')}")

    result = ask(graph, ticker, question, thread_id=f"q13-{ticker}")
    print(f"\nTools called: {result.tools_used}")
    print(f"\nResponse:\n{result.answer}")

    coverage = _check_coverage(result.answer, health_score)
    if coverage["missing_signals"]:
        print(f"\n*** WARNING: sub-signal(s) never mentioned: {coverage['missing_signals']} ***")
    if not coverage["overall_status_reflected"]:
        print(f"\n*** WARNING: real overall status ('{coverage['real_overall']}') not clearly reflected. ***")

    chain = WORSENED_JUDGE_PROMPT | judge_llm | StrOutputParser()
    judgment = chain.invoke(
        {
            "question": question,
            "health_score": json.dumps(health_score),
            "response": result.answer,
        }
    )
    print(f"\n--- Judge scoring (rollup_accuracy, signal_completeness, honest_framing) ---\n{judgment}")

    # Real RAGAS ToolCallAccuracy -- new for Q13 (Open Items fix), same as
    # Q11 this file never scored tool-calling with any metric before.
    # Required set kept to the two calls Q13's question actually demands
    # data for beyond what get_fundamentals_health_score() already
    # computes directly in Python (revenue/margin/leadership are NOT
    # agent tool calls at all -- see app/graph.py's ask()): get_market_data
    # for insider-activity numbers, and a filings check, since "insider
    # activity or leadership" is exactly the shape the FilingsRelevance
    # classifier (Open Items) routes to a real filings search. search_live_news
    # is deliberately left out of the reference -- real runs show the agent
    # also calls it, but nothing in Q13's question requires media search
    # specifically, and a 2-item reference lets that extra real call be
    # exactly that (extra), not penalized (see
    # eval_tool_call_accuracy.py's module docstring on subsequence
    # tolerance for calls outside the reference).
    acceptable_tool_sets = [
        [
            ToolCall(name="get_market_data", args={"ticker": ticker}),
            ToolCall(name="search_filings", args={"ticker": ticker}),
        ],
        [
            ToolCall(name="get_market_data", args={"ticker": ticker}),
            ToolCall(name="search_filings_exact", args={"ticker": ticker}),
        ],
    ]
    ragas_result = score_tool_call_accuracy(question, result.tool_calls, acceptable_tool_sets)
    print(
        f"\n--- RAGAS ToolCallAccuracy (real metric class) ---\n"
        f"score: {ragas_result.score:.2f} (best-matching reference order: {ragas_result.best_reference})\n"
        f"predicted sequence: {[c['name'] for c in result.tool_calls]}"
    )

    # Real RAGAS AgentGoalAccuracyWithReference -- new for Q13 (Open Items
    # fix), same gap as Q9/Q11: goal_accuracy was only ever scored by this
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
    parser.add_argument("--date-purchased", default="2025-08-01")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    q13 = load_q13()

    if args.ticker:
        cases = [{"ticker": args.ticker, "company": args.company or args.ticker, "date_purchased": args.date_purchased}]
    else:
        cases = q13["test_cases"]

    graph = build_graph()
    judge_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    results = [run_case(graph, c, judge_llm) for c in cases]

    print(f"\n\n{'=' * 70}\nSUMMARY -- {len(results)} case(s)\n{'=' * 70}")
    for r in results:
        flags = []
        if r["coverage"]["missing_signals"]:
            flags.append(f"MISSING: {r['coverage']['missing_signals']}")
        if not r["coverage"]["overall_status_reflected"]:
            flags.append(f"OVERALL ('{r['coverage']['real_overall']}') NOT REFLECTED")
        flag_str = f"  <-- {'; '.join(flags)}" if flags else ""
        print(
            f"{r['ticker']}: tools={r['tools_used']}{flag_str}  "
            f"ragas_tool_call_accuracy={r['ragas_tool_call_accuracy']:.2f}  "
            f"ragas_goal_accuracy={r['ragas_goal_accuracy']:.2f}"
        )


if __name__ == "__main__":
    main()
