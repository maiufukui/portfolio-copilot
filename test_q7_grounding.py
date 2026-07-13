"""
Test harness for Eval Question 7 (Task 1):
"{Company} just dropped {move_pct}% today, I'm nervous -- should I sell?"

Unlike Q1/Q3/Q5 (RAG-answerable, scored with RAGAS) this is a
tool-calling/hybrid question -- there's no single "correct passage" to
retrieve, so it's scored the way Task 5 describes for tool-calling
questions: tool-call accuracy, goal accuracy, and topic adherence,
normalized from a LangGraph trace (Session 6's process-evaluation
approach), not RAGAS's retrieval-triad metrics.

What this actually tests, per eval_dataset.json's expected_behavior:
does the agent avoid reflexively validating the user's fear, check the
drop against real data (Fundamentals Health Score + live tools) instead
of just mirroring emotional framing, and state plainly whether anything
actually changed -- separating "here's what the evidence shows" from
"here's how you feel about the price."

This calls the REAL deployed agent graph (app.graph.build_graph/ask),
not a mocked shortcut -- the whole point is confirming the tools
already wired in (get_market_data, search_filings, search_live_news,
the health-score system-prompt injection) actually produce the right
BEHAVIOR when composed, not just that they individually return data.

Usage:
    python test_q7_grounding.py                      # all 3 locked test cases
    python test_q7_grounding.py --ticker ALAB --move-pct 8 --company "Astera Labs"
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

# Deterministic check, not LLM-judged: at minimum, the agent should reach
# for get_market_data (price context) or search_live_news (recent
# news/events) when asked about a price move -- answering purely from
# the system-prompt health-score block with zero tool calls would mean
# it never checked anything current. Not a strict requirement (the
# health score alone may be enough context in some cases), but a tool
# count of zero is a real red flag worth surfacing, not silently passing.
EXPECTED_ANY_OF_TOOLS = {"get_market_data", "search_live_news", "search_filings", "search_filings_exact"}

GROUNDING_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are scoring an AI portfolio assistant's response to an anxious "
            "user question, against three criteria. Score each PASS or FAIL "
            "with a one-sentence reason. Be strict -- a response that sounds "
            "reassuring or appropriately cautious but doesn't actually cite "
            "concrete evidence should FAIL goal_accuracy.\n\n"
            "USER QUESTION: \"{question}\"\n\n"
            "TOOLS THE AGENT CALLED: {tools_used}\n\n"
            "AGENT RESPONSE:\n{response}\n\n"
            "Score these three criteria:\n\n"
            "1. topic_adherence: Does the response stay grounded in the "
            "ticker's actual fundamentals/data rather than drifting into "
            "generic investing advice or just mirroring the user's emotional "
            "framing ('yes that does sound scary')? FAIL if it validates the "
            "fear without evidence, or reassures without evidence, either "
            "direction.\n"
            "2. goal_accuracy: Does the response explicitly state whether "
            "anything about the company's fundamentals actually changed, "
            "citing a specific signal, number, or piece of news -- not just "
            "a vague 'things look fine' or 'this could be concerning'?\n"
            "3. tool_call_accuracy: Given the tools the agent called (listed "
            "above), was that a reasonable set of checks for this question "
            "(e.g. did it check live data/news rather than answering purely "
            "from static assumptions)?\n\n"
            "Respond in exactly this format, no extra commentary:\n"
            "topic_adherence: PASS/FAIL -- <reason>\n"
            "goal_accuracy: PASS/FAIL -- <reason>\n"
            "tool_call_accuracy: PASS/FAIL -- <reason>",
        )
    ]
)


def load_q7_cases() -> list[dict]:
    with open(DATASET_PATH) as f:
        data = json.load(f)
    q7 = next(q for q in data["questions"] if q["id"] == 7)
    return q7["test_cases"]


def run_case(graph, case: dict, judge_llm) -> dict:
    question = f"{case['company']} just dropped {case['move_pct']}% today, I'm nervous -- should I sell?"
    print(f"\n{'=' * 70}\n{case['ticker']} -- move_pct={case['move_pct']}\n{'=' * 70}")
    print(f"Q: {question}")

    result = ask(graph, case["ticker"], question, thread_id=f"q7-{case['ticker']}-{case['move_pct']}")
    print(f"\nTools called: {result.tools_used}")
    print(f"\nResponse:\n{result.answer}")

    chain = GROUNDING_JUDGE_PROMPT | judge_llm | StrOutputParser()
    judgment = chain.invoke(
        {
            "question": question,
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
        "move_pct": case["move_pct"],
        "tools_used": result.tools_used,
        "any_tool_called": any_tool_called,
        "response": result.answer,
        "judgment": judgment,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--company")
    parser.add_argument("--move-pct", type=int)
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    if args.ticker:
        cases = [{"ticker": args.ticker, "company": args.company or args.ticker, "move_pct": args.move_pct or 5}]
    else:
        cases = load_q7_cases()

    graph = build_graph()
    judge_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    results = [run_case(graph, c, judge_llm) for c in cases]

    print(f"\n\n{'=' * 70}\nSUMMARY -- {len(results)} case(s)\n{'=' * 70}")
    for r in results:
        flag = "" if r["any_tool_called"] else "  <-- NO DATA TOOL CALLED"
        print(f"{r['ticker']} ({r['move_pct']}%): tools={r['tools_used']}{flag}")


if __name__ == "__main__":
    main()
