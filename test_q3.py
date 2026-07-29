"""
Test harness for Eval Question 3 (Task 1):
"Is there anything in {company}'s latest earnings I should be worried
about moving forward, especially around margin or guidance?"

Added 2026-07-28 (Maiu, explicit call: "build and automate all 10").
This REPLACES the old id-3 question ("Has {company}'s tone or substance
changed on {risk_or_opportunity} across its last 4 earnings calls?"),
which stayed status="not_built" -- blocked on data, not the prompt: only
1 quarter of transcript is ingested per ticker, and that question needed
4. This new wording only needs the most recent quarter (already
ingested for every tracked ticker), so it's runnable now.

Category: tool_calling (not rag) -- deliberately, even though the
underlying evidence lives in filings/transcripts. What's being tested is
not "can RAG retrieve the right passage" (that's Q1/Q5's job, already
scored via RAGAS against a fixed reference) but whether the agent
correctly IDENTIFIES a genuine forward-looking risk signal on its own
from that content, without either (a) fabricating a worry that isn't in
the filing, or (b) reflexively reassuring ("nothing to worry about")
without having actually checked. Structurally the same shape as Q7
(test_q7_grounding.py): an emotionally-framed question ("should I be
worried") scored on whether the response grounds itself in real evidence
rather than mirroring the user's framing either direction -- same
GROUNDING-style judge, adapted from margin/guidance-worry instead of a
price-drop.

This calls the REAL deployed agent graph (app.graph.build_graph/ask),
same as test_q7_grounding.py/test_q9.py/test_q11.py -- confirms the
agent reaches for search_filings/search_filings_exact (transcript +
10-Q/10-K content) on its own for this question, not that the RAG
pipeline individually works (already proven by Q1/Q5).

Usage:
    python test_q3.py                      # all locked test cases
    python test_q3.py --ticker ALAB --company "Astera Labs"
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

# Deterministic check, not LLM-judged: at minimum the agent should reach
# for a filings tool (the actual source of "latest earnings" content) --
# get_market_data is accepted too since the Fundamentals Health Score's
# margin sub-signal is also a legitimate way to ground a margin worry.
# Same "zero tools called is a real red flag" reasoning as
# test_q7_grounding.py's EXPECTED_ANY_OF_TOOLS.
EXPECTED_ANY_OF_TOOLS = {"search_filings", "search_filings_exact", "get_market_data"}

GROUNDING_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are scoring an AI portfolio assistant's response to a "
            "worried user question about a company's latest earnings, "
            "against three criteria. Score each PASS or FAIL with a "
            "one-sentence reason. Be strict -- a response that sounds "
            "reassuring or appropriately cautious but doesn't cite "
            "concrete evidence from the actual earnings materials should "
            "FAIL goal_accuracy.\n\n"
            "USER QUESTION: \"{question}\"\n\n"
            "TOOLS THE AGENT CALLED: {tools_used}\n\n"
            "AGENT RESPONSE:\n{response}\n\n"
            "Score these three criteria:\n\n"
            "1. topic_adherence: Does the response stay grounded in "
            "{company}'s actual latest-earnings content (margin trends, "
            "guidance language) rather than drifting into generic "
            "investing advice or just mirroring the user's worried tone "
            "('yes, that's concerning')? FAIL if it validates or dismisses "
            "the worry without citing anything from the real earnings "
            "material.\n"
            "2. goal_accuracy: Does the response explicitly state whether "
            "there IS or ISN'T a real margin/guidance concern, citing a "
            "specific number, quote, or disclosure from the latest "
            "quarter -- not just a vague 'keep an eye on margins'?\n"
            "3. tool_call_accuracy: Given the tools the agent called "
            "(listed above), did it actually check the company's real "
            "filings/transcript content (or the Fundamentals Health "
            "Score's margin signal) rather than answering from general "
            "knowledge?\n\n"
            "Respond in exactly this format, no extra commentary:\n"
            "topic_adherence: PASS/FAIL -- <reason>\n"
            "goal_accuracy: PASS/FAIL -- <reason>\n"
            "tool_call_accuracy: PASS/FAIL -- <reason>",
        )
    ]
)


def load_q3_cases() -> list[dict]:
    with open(DATASET_PATH) as f:
        data = json.load(f)
    q3 = next(q for q in data["questions"] if q["id"] == 3)
    return q3["test_cases"]


def run_case(graph, case: dict, judge_llm) -> dict:
    question = (
        f"Is there anything in {case['company']}'s latest earnings I "
        f"should be worried about moving forward, especially around "
        f"margin or guidance?"
    )
    print(f"\n{'=' * 70}\n{case['ticker']}\n{'=' * 70}")
    print(f"Q: {question}")

    result = ask(graph, case["ticker"], question, thread_id=f"q3-{case['ticker']}")
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
        cases = load_q3_cases()

    graph = build_graph()
    judge_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    results = [run_case(graph, c, judge_llm) for c in cases]

    print(f"\n\n{'=' * 70}\nSUMMARY -- {len(results)} case(s)\n{'=' * 70}")
    for r in results:
        flag = "" if r["any_tool_called"] else "  <-- NO DATA TOOL CALLED"
        print(f"{r['ticker']}: tools={r['tools_used']}{flag}")


if __name__ == "__main__":
    main()
