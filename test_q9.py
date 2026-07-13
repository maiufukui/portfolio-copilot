"""
Test harness for Eval Question 9 (Task 1):
"Summarize everything notable about {company} this week -- filings,
media, and analyst activity."

Tool-calling/hybrid question (Task 5 scoring: tool-call accuracy, goal
accuracy, topic adherence from a LangGraph trace) -- there's no single
retrievable passage to score with RAGAS, the thing being tested is
ORCHESTRATION: can the agent pull from all three signal categories
(filings/keyword checks, live media search, institutional/analyst
consensus) and produce one coherent digest with citations, instead of
answering from just one tool or blending sources without attribution.

Deliberately calls the REAL deployed agent (app.graph.ask), the same way
test_q7_grounding.py does, rather than hand-orchestrating the three tool
calls directly -- all three tools (search_filings_exact, search_live_news,
get_market_data) were already bound to the live agent before this file
existed. What Q9 actually tests is whether the agent RELIABLY reaches for
all three on its own for a "summarize everything" question, not whether
the tools individually work (already proven elsewhere). If it doesn't,
that's a real, useful finding -- not a reason to fake it with a scripted
pipeline instead.

Usage:
    python test_q9.py                          # locked test case (ALAB)
    python test_q9.py --ticker MRVL --company Marvell
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

# The three source categories Q9's expected_behavior names explicitly.
# Not all three tools need to fire -- e.g. search_filings and
# search_filings_exact both count as "filings" -- but at least one from
# each category should, or a whole signal type was silently skipped.
FILINGS_TOOLS = {"search_filings", "search_filings_exact"}
NEWS_TOOLS = {"search_live_news"}
MARKET_TOOLS = {"get_market_data"}

DIGEST_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are scoring an AI portfolio assistant's weekly-digest "
            "response against three criteria. Score each PASS or FAIL with "
            "a one-sentence reason. Be strict.\n\n"
            "USER QUESTION: \"{question}\"\n\n"
            "TOOLS THE AGENT CALLED: {tools_used}\n\n"
            "AGENT RESPONSE:\n{response}\n\n"
            "Score these three criteria:\n\n"
            "1. source_coverage: Does the response address all three named "
            "categories -- filings, media, and analyst activity -- either "
            "with real findings or an explicit 'nothing notable found' for "
            "that category? FAIL if a category is silently omitted rather "
            "than addressed one way or the other.\n"
            "2. citation_quality: Is each concrete claim attributed to a "
            "specific source and date (a filing name, a news URL/date, or "
            "explicitly labeled institutional data), not just asserted?\n"
            "3. tool_call_accuracy: Given the tools called (listed above), "
            "did the agent actually check something in each of the three "
            "categories (a filings tool, a news tool, a market-data tool), "
            "rather than answering from only one or two?\n\n"
            "Respond in exactly this format, no extra commentary:\n"
            "source_coverage: PASS/FAIL -- <reason>\n"
            "citation_quality: PASS/FAIL -- <reason>\n"
            "tool_call_accuracy: PASS/FAIL -- <reason>",
        )
    ]
)


def load_q9_cases() -> list[dict]:
    with open(DATASET_PATH) as f:
        data = json.load(f)
    q9 = next(q for q in data["questions"] if q["id"] == 9)
    return q9["test_cases"]


def _category_coverage(tools_used: list[str]) -> dict[str, bool]:
    used = set(tools_used)
    return {
        "filings": bool(used & FILINGS_TOOLS),
        "media": bool(used & NEWS_TOOLS),
        "analyst": bool(used & MARKET_TOOLS),
    }


def run_case(graph, case: dict, judge_llm) -> dict:
    # Named categories included explicitly, matching eval_dataset.json's
    # own question_template -- this is a fair test of orchestration, not
    # a trick question phrased to withhold the hint a real user's
    # question would also carry ("filings, media, and analyst activity"
    # is literally the eval's own wording, not an artificial nudge).
    question = (
        f"Summarize everything notable about {case['company']} this week -- "
        f"filings, media, and analyst activity."
    )
    print(f"\n{'=' * 70}\n{case['ticker']}\n{'=' * 70}")
    print(f"Q: {question}")

    result = ask(graph, case["ticker"], question, thread_id=f"q9-{case['ticker']}")
    print(f"\nTools called: {result.tools_used}")
    print(f"\nResponse:\n{result.answer}")

    coverage = _category_coverage(result.tools_used)
    missing = [k for k, v in coverage.items() if not v]
    if missing:
        print(f"\n*** WARNING: no tool called for categor{'y' if len(missing) == 1 else 'ies'}: {missing} ***")

    chain = DIGEST_JUDGE_PROMPT | judge_llm | StrOutputParser()
    judgment = chain.invoke(
        {
            "question": question,
            "tools_used": result.tools_used or "(none)",
            "response": result.answer,
        }
    )
    print(f"\n--- Judge scoring ---\n{judgment}")

    return {
        "ticker": case["ticker"],
        "tools_used": result.tools_used,
        "coverage": coverage,
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
        cases = load_q9_cases()

    graph = build_graph()
    judge_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    results = [run_case(graph, c, judge_llm) for c in cases]

    print(f"\n\n{'=' * 70}\nSUMMARY -- {len(results)} case(s)\n{'=' * 70}")
    for r in results:
        missing = [k for k, v in r["coverage"].items() if not v]
        flag = "" if not missing else f"  <-- MISSING: {missing}"
        print(f"{r['ticker']}: tools={r['tools_used']}{flag}")


if __name__ == "__main__":
    main()
