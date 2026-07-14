"""
Diagnostic for AgentGoalAccuracyWithReference (Priority 3).

Round 1 (resolved): a uniform 0.00 across every case turned out to be
GOAL_REFERENCE bundling a QUALITY criterion (citation completeness,
rollup correctness, honest framing) into the desired outcome -- a
dimension RAGAS's fixed InferGoalOutcomePrompt structurally never
surfaces in its inferred end_state (content only, not quality). Fixing
GOAL_REFERENCE down to a single content-outcome statement in test_q9.py/
test_q11.py/test_q13.py flipped Q9/ALAB and Q11/MRVL to 1.00 in a real
run. Confirmed via this script's own reason-field printout.

Round 2 (this file, now): Q11/NBIS and Q13/ALAB STILL score 0.00 with
the same fixed, content-only GOAL_REFERENCE. Since the fix demonstrably
works on other cases with the same reference template, this is
case-specific, not a repeat of the same root cause -- printing the
reason field again, this time for the two real failing cases (captured
verbatim from an actual run), to see what's different about them.

Usage:
    python debug_goal_accuracy.py
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from ragas.llms import LangchainLLMWrapper
from ragas.metrics._goal_accuracy import (
    CompareOutcomeInput,
    CompareOutcomePrompt,
    InferGoalOutcomePrompt,
    WorkflowInput,
)
from ragas.messages import AIMessage, HumanMessage, ToolCall
from ragas.dataset_schema import MultiTurnSample

load_dotenv()

CASES = [
    {
        "label": "Q9/ALAB -- control, confirmed 1.00 in real run",
        "question": "Summarize everything notable about Astera Labs this week -- filings, media, and analyst activity.",
        "tool_calls": [
            {"name": "search_live_news", "args": {}},
            {"name": "search_filings_exact", "args": {"ticker": "ALAB"}},
            {"name": "get_market_data", "args": {"ticker": "ALAB"}},
        ],
        "reference": (
            "The AI assistant produced a weekly digest for Astera Labs covering "
            "filings, media coverage, and analyst activity."
        ),
        "answer": """**Current Fundamentals Health Score for ALAB -- today's snapshot only, not a comparison to any prior date:**
- Overall: AT RISK
- Revenue Growth: intact
- Margin: intact
- Leadership: intact -- no 8-K Item 5.02 in last 90 days
- Insider Activity: at risk -- $87,123,837 sold across 3 insider(s) in the last 30 days

Here's a summary of notable information about Astera Labs (ticker ALAB) this week from filings, media, and analyst activity:

Filings:
- The latest SEC filings include a Form 8-K dated June 8, 2026, and a 10-Q quarterly report for the period ended March 31, 2026. These filings provide detailed financial statements and business updates.
- The 10-Q shows strong revenue growth with Q1 2026 revenue of $308 million, up 93% year-over-year and 14% sequentially.
- No recent 8-K filings indicate leadership changes in the last 90 days.

Media:
- No major news headlines specifically about Astera Labs appeared in the last week from the live news search.
- The Q1 2026 earnings call transcript highlights strong execution, expanding product portfolio, and growing customer partnerships in AI fabrics and optical connectivity.

Analyst and Market Data:
- The stock price recently closed at $362.05, down 12.33% on the day.
- Next earnings date is scheduled for August 4, 2026.
- Insider selling activity is notable with multiple open market sales by insiders in early July 2026.
- Analyst coverage remains stable with 30 analysts: 7 Strong Buy, 14 Buy, 9 Hold, and no Sell ratings as of July 1, 2026.

In summary, Astera Labs is showing strong financial performance and market positioning in AI connectivity, but recent insider selling activity is a caution signal. No new filings or news materially change the current fundamentals health score, which remains overall "at risk" primarily due to insider activity. Analyst sentiment remains generally positive.""",
    },
    {
        "label": "Q11/NBIS -- real run, still 0.00",
        "question": "When does Nebius report next, and what should I watch for based on its current Fundamentals Health Score?",
        "tool_calls": [
            {"name": "get_market_data", "args": {"ticker": "NBIS"}},
            {"name": "search_filings", "args": {"ticker": "NBIS"}},
            {"name": "search_filings_exact", "args": {"ticker": "NBIS"}},
        ],
        "reference": (
            "The AI assistant reported Nebius's next earnings date and named "
            "the fundamentals sub-signals currently at Monitor or At Risk."
        ),
        "answer": """**Current Fundamentals Health Score for NBIS -- today's snapshot only, not a comparison to any prior date:**
- Overall: MONITOR
- Revenue Growth: insufficient data
- Margin: insufficient data -- no GrossProfit tag reported
- Leadership: intact -- no 8-K Item 5.02 in last 90 days
- Insider Activity: monitor -- $24,413,164 sold across 4 insider(s) in the last 30 days

Nebius (NBIS) is scheduled to report its next earnings on July 29, 2026.

Based on the current Fundamentals Health Score and recent filings and transcripts, here are key points to watch for:

1. Revenue Growth: Nebius has shown very strong revenue growth recently, with Nebius AI cloud business revenue increasing 603% from $68.3 million in 2024 to $480.3 million in 2025. In Q1 2026, group revenue grew 684% year-over-year to $399 million, with Nebius AI business revenue up 841% year-over-year to $390 million. Watch for continued strong top-line growth and how the company manages capacity to meet demand. (Source: 20-F_2026-04-30.htm, transcript_Q1_2026.txt)

2. Margin Expansion: The company reported significant margin expansion, with Nebius AI business adjusted EBITDA margin increasing to 45% in Q1 2026 from 24% in Q4 2025. Group adjusted EBITDA margin was 32% in Q1 2026. Margin progression will be influenced by utilization, pricing, and capacity. (Source: transcript_Q1_2026.txt)

3. Capacity and Capital Spending: Capacity constraints are a key factor limiting growth. The company is investing heavily in expanding data center infrastructure and GPUs, with CapEx expectations raised to $20-$25 billion for 2026. Watch for updates on capacity expansion projects and financing plans. (Source: transcript_Q1_2026.txt, 20-F_2026-04-30.htm)

4. Insider Activity: There has been notable insider selling in the last 30 days, with four distinct insiders selling shares in open market transactions. The total sell value was about $24.4 million. It is not yet clear if these sales are part of pre-arranged plans or discretionary. (Source: Market data)

5. Leadership: No recent 8-K filings indicating leadership changes have been found, so leadership is considered intact. (Source: search_filings_exact for 8-K Item 5.02)

6. Analyst Sentiment: Analysts are generally positive with a mix of Strong Buy, Buy, and Hold ratings, and no Sell ratings currently.

In summary, watch for the upcoming earnings report on July 29, 2026, for updates on revenue growth, margin trends, capacity expansion progress, and any commentary on managing supply constraints. Insider selling activity and financing plans for capital expenditures are also important to monitor. These factors align with the current Fundamentals Health Score signals of "monitor" for insider activity and insufficient data for revenue growth and margin signals.""",
    },
    {
        "label": "Q13/ALAB -- real run, still 0.00",
        "question": "Has anything about Astera Labs's underlying business gotten worse since I bought it -- revenue, margins, insider activity, or leadership?",
        "tool_calls": [
            {"name": "search_live_news", "args": {}},
            {"name": "get_market_data", "args": {"ticker": "ALAB"}},
            {"name": "search_filings", "args": {"ticker": "ALAB"}},
        ],
        "reference": (
            "The AI assistant reported Astera Labs's current status across all "
            "four fundamentals signals -- revenue growth, margin, insider "
            "activity, and leadership -- along with the overall worst-of "
            "status."
        ),
        "answer": """**Current Fundamentals Health Score for ALAB -- today's snapshot only, not a comparison to any prior date:**
- Overall: AT RISK
- Revenue Growth: intact
- Margin: intact
- Leadership: intact -- no 8-K Item 5.02 in last 90 days
- Insider Activity: at risk -- $87,123,837 sold across 3 insider(s) in the last 30 days

Revenue Growth
The company reported year-over-year revenue growth of 149.7% for the quarter ended June 30, 2025, followed by 103.9% for the quarter ended September 30, 2025. Growth continued at 91.8% and 93.4% for the quarters ended December 31, 2025, and March 31, 2026, respectively.

Margin
Gross margin percentages for the last four quarters were consistently strong, with 75.84% for Q2 2025, 76.25% for Q3 2025, 75.57% for Q4 2025, and 76.26% for Q1 2026.

Insider Activity
Insider selling activity over the past 30 days totaled $87,123,837 in value, involving three distinct sellers. Notable transactions include Stefan A. Dyckerhoff selling 8,499 shares at $450 per share on July 1, 2026, and Manuel Alba selling multiple blocks on July 1, 2026.

Leadership
Recent news highlights ongoing business development and strategic initiatives. On June 30, 2026, 64x Bio launched the LV Apex Suite, expanding its VectorSelect platform into lentiviral vector production (BioSpace, 2026-06-30). Additionally, on July 6, 2026, Lexaria Bioscience reported positive meetings with potential pharmaceutical partners and collaborators, signaling active leadership engagement in growth opportunities (TheNewswire, 2026-07-06).""",
    },
]


async def run_case(case: dict, llm) -> None:
    print(f"\n{'=' * 70}\n{case['label']}\n{'=' * 70}")

    ai_tool_message = AIMessage(
        content="", tool_calls=[ToolCall(name=c["name"], args=c["args"]) for c in case["tool_calls"]]
    )
    final_message = AIMessage(content=case["answer"])
    sample = MultiTurnSample(
        user_input=[HumanMessage(content=case["question"]), ai_tool_message, final_message],
        reference=case["reference"],
    )

    workflow_prompt = InferGoalOutcomePrompt()
    compare_prompt = CompareOutcomePrompt()

    workflow_result = await workflow_prompt.generate(
        data=WorkflowInput(workflow=sample.pretty_repr()), llm=llm, callbacks=None
    )
    print(f"INFERRED end_state:  {workflow_result.end_state}")

    compare_result = await compare_prompt.generate(
        data=CompareOutcomeInput(
            desired_outcome=sample.reference, arrived_outcome=workflow_result.end_state
        ),
        llm=llm,
        callbacks=None,
    )
    print(f"\nDESIRED (GOAL_REFERENCE): {sample.reference}")
    print(f"VERDICT: {compare_result.verdict}")
    print(f"REASON: {compare_result.reason}")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    llm = LangchainLLMWrapper(ChatOpenAI(model="gpt-4.1-mini", temperature=0, api_key=os.environ.get("OPENAI_API_KEY")))
    for case in CASES:
        await run_case(case, llm)


if __name__ == "__main__":
    asyncio.run(main())
