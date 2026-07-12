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

import requests
from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from test_q2 import display_date, format_results, search_tavily

load_dotenv()

FINNHUB_RECOMMENDATION_URL = "https://finnhub.io/api/v1/stock/recommendation"


def fetch_recommendation_trends(ticker: str, api_key: str) -> list[dict]:
    """Real institutional analyst consensus (aggregated buy/hold/sell counts
    from actual sell-side coverage) -- not scraped from the open web. Free
    tier. Most recent period first."""
    resp = requests.get(FINNHUB_RECOMMENDATION_URL, params={"symbol": ticker, "token": api_key})
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--event", required=True, help='e.g. "guidance cut", "earnings miss"')
    parser.add_argument(
        "--days", dest="time_range", default="week", choices=["day", "week", "month", "year"]
    )
    parser.add_argument("--topic", default="news", choices=["news", "general"])
    args = parser.parse_args()

    tavily_key = os.environ.get("TAVILY_API_KEY")
    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    if not tavily_key:
        raise SystemExit("TAVILY_API_KEY not set in .env")
    if not finnhub_key:
        raise SystemExit("FINNHUB_API_KEY not set in .env")
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set in .env")

    print(f"Fetching institutional recommendation trends for {args.ticker}...")
    trends = fetch_recommendation_trends(args.ticker, finnhub_key)
    trends_text = format_recommendation_trends(trends)
    print(f"\n{trends_text}\n")

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
