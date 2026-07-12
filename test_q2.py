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
import os
import re

import requests
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

load_dotenv()

TAVILY_URL = "https://api.tavily.com/search"


def search_tavily(
    query: str,
    api_key: str,
    time_range: str = "week",
    max_results: int = 8,
    topic: str = "news",
) -> list[dict]:
    """Hit Tavily's /search endpoint. topic='news' + time_range filters to
    recent news rather than general web results."""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "query": query,
        "topic": topic,
        "time_range": time_range,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_answer": False,
    }
    resp = requests.post(TAVILY_URL, headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


DATE_IN_URL_RE = re.compile(r"/(\d{4})/(\d{1,2})/(\d{1,2})/")


def extract_date_from_url(url: str) -> str | None:
    """Fallback only: Tavily's 'general' topic often omits published_date, but
    many financial news URLs embed a YYYY/MM/DD path segment. Not an
    authoritative source -- always labeled 'inferred from URL' so it's never
    mistaken for a verified publish date."""
    if not url:
        return None
    match = DATE_IN_URL_RE.search(url)
    if not match:
        return None
    year, month, day = match.groups()
    return f"{year}-{int(month):02d}-{int(day):02d} (inferred from URL)"


def display_date(r: dict) -> str:
    return r.get("published_date") or extract_date_from_url(r.get("url", "")) or "date unknown"


def format_results(results: list[dict]) -> str:
    lines = []
    for r in results:
        title = r.get("title", "untitled")
        url = r.get("url", "")
        score = r.get("score")
        content = (r.get("content") or "")[:500]
        lines.append(
            f"Title: {title}\nDate: {display_date(r)}\nRelevance score: {score}\nURL: {url}\nExcerpt: {content}"
        )
    return "\n\n".join(lines)


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
