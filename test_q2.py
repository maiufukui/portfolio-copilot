"""
Test harness for Eval Question 2 (Task 1):
"What's the latest news on Company X, and does it affect my position?"

Calls Tavily's live search API for recent news on a ticker, then checks
each result against the user's stored thesis, flagging relevance as
High/Medium/Low with dated source links -- matching the eval's expected
behavior spec.

Usage:
    python test_q2.py --ticker ALAB --company "Astera Labs" \
        --thesis "margin expansion is driven by software mix shift"
    python test_q2.py --ticker ALAB --company "Astera Labs" \
        --thesis "..." --days month
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

# search_tavily/extract_date_from_url/display_date/format_results moved to
# shared_helpers.py 2026-07-29: app/tools.py (production) imported
# format_results/search_tavily from this file, and this file imports ragas
# (below) for its own run_case() scoring -- so any production import from
# this file pulled in ragas at server startup, crashing the deploy the
# moment ragas was correctly excluded from requirements-server.txt. See
# shared_helpers.py's module docstring for the full incident writeup.
# Re-imported here (not redefined) so this file's own CLI (`python
# test_q2.py --ticker ...`) and existing external importers
# (fetch_transcripts.py, run_scorecard.py) see no change in behavior.
from shared_helpers import display_date, format_results, search_tavily  # noqa: F401

load_dotenv()

DATASET_PATH = "eval_dataset.json"


RELEVANCE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "The user holds {ticker} ({company}). Their stated investment thesis is:\n\n"
            '"{thesis}"\n\n'
            "Below are recent news search results for {ticker}. For EACH item, "
            "decide if it's relevant to the thesis above and rate relevance "
            "High / Medium / Low. Only rely on what's in the excerpt -- don't "
            "invent details you can't see. Routine price moves or generic "
            "analyst-target chatter unrelated to the thesis should be rated Low.\n\n"
            "Respond as a list, one entry per article, in this format:\n"
            "[Relevance] Title (date) -- one-sentence reason -- URL\n\n"
            "NEWS RESULTS:\n{results}",
        )
    ]
)


# --- Real automated scoring for Q2, added 2026-07-27 (Maiu, explicit call:
# "build and automate all 10"). eval_dataset.json's own scoring_method for
# id 2 is "tool_call_goal_topic", the same method Q7/Q9/Q11 use, and its
# own _meta description says that trace should come from LangGraph, not a
# standalone script -- so this calls the REAL deployed agent (app.graph.ask),
# same pattern as test_q9.py, not the standalone --thesis CLI above (which
# stays as-is, it's a different, older exploration path, not the scored
# harness). The live agent's actual question is eval_dataset.json's own
# template, "does it affect my position", cross-referenced against the
# Fundamentals Health Score computed inside ask() itself, not a free-text
# --thesis argument (that concept is retired, see eval_dataset.json's
# _meta.description).
NEWS_JUDGE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are scoring an AI portfolio assistant's news-relevance response "
            "against three criteria. Score each PASS or FAIL with a one-sentence "
            "reason. Be strict.\n\n"
            "USER QUESTION: \"{question}\"\n\n"
            "TOOLS THE AGENT CALLED: {tools_used}\n\n"
            "AGENT RESPONSE:\n{response}\n\n"
            "Score these three criteria:\n\n"
            "1. live_search_used: Did the agent actually search for current news "
            "(not just answer from the Fundamentals Health Score alone)? FAIL if "
            "the response reads like it never checked live news.\n"
            "2. relevance_assessment: Does the response actually assess whether "
            "the news matters to the user's position -- not just list headlines? "
            "FAIL if it's a bare list with no judgment of relevance or impact.\n"
            "3. citation_quality: Is each news item attributed to a source and "
            "date? FAIL if claims are asserted without a source.\n\n"
            "Respond in exactly this format, no extra commentary:\n"
            "live_search_used: PASS/FAIL -- <reason>\n"
            "relevance_assessment: PASS/FAIL -- <reason>\n"
            "citation_quality: PASS/FAIL -- <reason>",
        )
    ]
)

GOAL_REFERENCE_Q2 = (
    "The AI assistant reported recent news for {company} and assessed whether "
    "it's relevant to the user's position."
)


def load_q2() -> dict:
    with open(DATASET_PATH) as f:
        data = json.load(f)
    return next(q for q in data["questions"] if q["id"] == 2)


def run_case(graph, case: dict, judge_llm) -> dict:
    """Calls the real live agent with eval_dataset.json's own Q2 wording --
    same shape as test_q9.py's run_case, adapted for Q2's single-tool
    (search_live_news) expectation instead of Q9's three-category one.

    ask() is imported HERE, deliberately, not at module level -- app/tools.py
    imports format_results/search_tavily from this file, so a top-level
    `from app.graph import ask` here would be a real circular import
    (app.tools -> test_q2 -> app.graph -> app.tools), not a hypothetical
    one. Deferred to call time, after app.tools has already finished
    loading, breaks the cycle."""
    from app.graph import ask
    from eval_tool_call_accuracy import score_goal_accuracy, score_tool_call_accuracy

    question = f"What's the latest news on {case['company']}, and does it affect my position?"
    print(f"\n{'=' * 70}\n{case['ticker']}\n{'=' * 70}\nQ: {question}")

    result = ask(graph, case["ticker"], question, thread_id=f"q2-{case['ticker']}")
    print(f"\nTools called: {result.tools_used}\n\nResponse:\n{result.answer}")

    chain = NEWS_JUDGE_PROMPT | judge_llm | StrOutputParser()
    judgment = chain.invoke(
        {"question": question, "tools_used": result.tools_used or "(none)", "response": result.answer}
    )
    print(f"\n--- Judge scoring ---\n{judgment}")

    t = case["ticker"]
    acceptable_tool_sets = [[ToolCall(name="search_live_news", args={})]]
    ragas_result = score_tool_call_accuracy(question, result.tool_calls, acceptable_tool_sets)
    goal_score = score_goal_accuracy(
        question, result.tool_calls, result.answer, GOAL_REFERENCE_Q2.format(company=case["company"])
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
    parser.add_argument("--company", required=True, help="Full company name, improves search quality")
    parser.add_argument("--thesis", required=True)
    parser.add_argument(
        "--days", dest="time_range", default="week", choices=["day", "week", "month", "year"]
    )
    parser.add_argument(
        "--topic",
        default="news",
        choices=["news", "general"],
        help="Tavily's 'news' vertical may have thin coverage for smaller-cap "
        "tickers -- try 'general' if scores stay low.",
    )
    args = parser.parse_args()

    tavily_key = os.environ.get("TAVILY_API_KEY")
    if not tavily_key:
        raise SystemExit("TAVILY_API_KEY not set in .env")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set in .env")

    # Company name alone, no ticker/boilerplate suffix -- ticker symbols and
    # generic words like "stock news" were diluting the match and pulling in
    # unrelated results (verified: real ALAB news existed this week that a
    # plain web search found immediately, but the diluted query missed it).
    query = args.company
    print(f"Searching Tavily for: {query} (time_range={args.time_range}, topic={args.topic})")
    results = search_tavily(query, tavily_key, time_range=args.time_range, topic=args.topic)

    if not results:
        print("No news results found.")
        return

    print(f"\n{len(results)} result(s) found:")
    for r in results:
        print(f"  - [{r.get('score')}] {r.get('title')} ({display_date(r)}) -- {r.get('url')}")

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
    chain = RELEVANCE_PROMPT | llm | StrOutputParser()
    output = chain.invoke(
        {
            "ticker": args.ticker,
            "company": args.company,
            "thesis": args.thesis,
            "results": format_results(results),
        }
    )

    print("\n" + "=" * 60)
    print(output)
    print("=" * 60)


if __name__ == "__main__":
    main()
