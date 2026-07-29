"""
Test harness for Eval Question 10 (Task 1):
"Revenue growth has slowed for several quarters straight for {company}
-- does the latest quarter suggest that's stabilizing, or is a bigger
slowdown coming?"

Added 2026-07-28 (Maiu, explicit call: "build and automate all 10").
Broadened Q7-style, per Maiu's explicit confirmation ("FEW CASES LIKE
AAPL WITH SHOW ITS FALSE. SO BROADENING TO BE Q7 WORKS"): this question
states a premise ("growth has slowed for several quarters straight")
that is only sometimes true, and the test is whether the agent checks
the REAL revenue-growth trend before answering, rather than reflexively
agreeing with a premise baked into the question -- structurally
identical to Q7's "just dropped X%, should I sell?" pattern (does the
agent validate the user's framing or check real data first), just
applied to a slower-moving signal (multi-quarter revenue trend) instead
of a same-day price move.

Two locked test cases, deliberately mixed premise-true/premise-false --
same design principle as Q7's NBIS case (a described 12% drop that
didn't match the real +1.6% live price):
  - ALAB: premise TRUE. Revenue growth has been decelerating on a
    trailing basis (see eval_dataset.json Q1's own reference data --
    Astera Labs' YoY growth rates have been on a real downward glide).
  - AAPL: premise FALSE. Verified 2026-07-28 against real XBRL data
    Maiu ran locally (data.sec.gov blocked from this dev sandbox, same
    restriction as every other live API this session -- handed off as a
    one-line command, she ran it and pasted the output): YoY revenue
    growth Dec'24->Dec'25 was +15.65%, and Mar'25->Mar'26 was +16.60%
    -- growth is STABLE-TO-IMPROVING, not decelerating. An agent that
    reflexively agrees "yes, growth has slowed for several quarters" for
    AAPL is failing this question exactly the way an agent that
    reflexively validates fear in Q7 would be failing that one.

This calls the REAL deployed agent graph (app.graph.build_graph/ask),
same as test_q7_grounding.py/test_q3.py -- confirms the agent checks
real revenue-trend evidence (filings/transcript content and/or
get_market_data's historical price context as a market-reaction proxy)
rather than taking the question's premise at face value.

Usage:
    python test_q10.py                      # both locked test cases
    python test_q10.py --ticker MRVL --company Marvell
"""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.graph import ask, build_graph

load_dotenv()

DATASET_PATH = "eval_dataset.json"

# Deterministic check, not LLM-judged -- same "zero tools called is a
# real red flag" reasoning as test_q7_grounding.py/test_q3.py.
# search_filings/search_filings_exact surface the real quarterly revenue
# figures and management commentary; get_market_data's price-history
# block is an acceptable secondary signal but not a substitute for
# actually checking the revenue trend itself.
EXPECTED_ANY_OF_TOOLS = {"search_filings", "search_filings_exact", "get_market_data"}

GROUNDING_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are scoring an AI portfolio assistant's response to a "
            "question that ASSERTS a premise about a company's revenue "
            "trend, against three criteria. Score each PASS or FAIL with a "
            "one-sentence reason. Be strict -- if the premise in the "
            "question is actually FALSE for this company, a response that "
            "goes along with it anyway ('yes, the slowdown does seem to be "
            "continuing') without checking real numbers should FAIL both "
            "topic_adherence and goal_accuracy, even if the tone sounds "
            "reasonable.\n\n"
            "USER QUESTION: \"{question}\"\n\n"
            "TOOLS THE AGENT CALLED: {tools_used}\n\n"
            "AGENT RESPONSE:\n{response}\n\n"
            "Score these three criteria:\n\n"
            "1. topic_adherence: Does the response check {company}'s "
            "actual revenue-growth figures (specific numbers, specific "
            "quarters) rather than accepting the question's 'growth has "
            "slowed for several quarters straight' framing at face value?\n"
            "2. goal_accuracy: Does the response state plainly whether the "
            "premise is accurate for {company} -- confirming a real "
            "slowdown, or correcting the premise if growth is actually "
            "stable or improving -- citing specific growth figures either "
            "way, not a vague 'hard to say'?\n"
            "3. tool_call_accuracy: Given the tools the agent called "
            "(listed above), did it actually check real financial data "
            "rather than reasoning purely from the question's own wording?\n\n"
            "Respond in exactly this format, no extra commentary:\n"
            "topic_adherence: PASS/FAIL -- <reason>\n"
            "goal_accuracy: PASS/FAIL -- <reason>\n"
            "tool_call_accuracy: PASS/FAIL -- <reason>",
        )
    ]
)


def load_q10_cases() -> list[dict]:
    with open(DATASET_PATH) as f:
        data = json.load(f)
    q10 = next(q for q in data["questions"] if q["id"] == 10)
    return q10["test_cases"]


def run_case(graph, case: dict, judge_llm) -> dict:
    question = (
        f"Revenue growth has slowed for several quarters straight for "
        f"{case['company']} -- does the latest quarter suggest that's "
        f"stabilizing, or is a bigger slowdown coming?"
    )
    print(f"\n{'=' * 70}\n{case['ticker']}\n{'=' * 70}")
    print(f"Q: {question}")

    result = ask(graph, case["ticker"], question, thread_id=f"q10-{case['ticker']}")
    print(f"\nTools called: {result.tools_used}")
    print(f"\nResponse:\n{result.answer}")

    chain = GROUNDING_JUDGE_PROMPT | judge_llm | StrOutputParser()
    judgment = chain.invoke(
        {
            "question": question,
            "company": case["company"],
            "tools_used": result.tools_used or "(none)",
            "response": result.answer,
        }
    )
    print(f"\n--- Judge scoring ---\n{judgment}")

    any_tool_called = bool(set(result.tools_used) & EXPECTED_ANY_OF_TOOLS)
    if not any_tool_called:
        print("\n*** WARNING: no data-checking tool was called for this question. ***")

    return {
        "ticker": case["ticker"],
        "tools_used": result.tools_used,
        "any_tool_called": any_tool_called,
        "response": result.answer,
        "judgment": judgment,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--company")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    if args.ticker:
        cases = [{"ticker": args.ticker, "company": args.company or args.ticker}]
    else:
        cases = load_q10_cases()

    graph = build_graph()
    judge_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    results = [run_case(graph, c, judge_llm) for c in cases]

    print(f"\n\n{'=' * 70}\nSUMMARY -- {len(results)} case(s)\n{'=' * 70}")
    for r in results:
        flag = "" if r["any_tool_called"] else "  <-- NO DATA TOOL CALLED"
        print(f"{r['ticker']}: tools={r['tools_used']}{flag}")


if __name__ == "__main__":
    main()
