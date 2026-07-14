"""
One-shot isolated diagnostic for the Q9 temporal-comparison misroute bug
(Open Items / app/graph.py's _question_invites_temporal_comparison).

Real, repeated evidence (test_q9.py, two full runs) shows Q9's response
carries the "(structured data)" fingerprint that only _compose_grounded_
narrative() can produce -- meaning _question_invites_temporal_comparison
is returning True for a question that is a VERBATIM, exact-string match
to a False-labeled few-shot example already in its own system prompt.
Static analysis (source-string mangling, duplicate/shadowed definitions,
question-variable mutation) has ruled out every code-level explanation
that doesn't require actually running the classifier.

This script isolates just the classifier call -- no graph, no tools, no
health score -- against Q9's exact real question text, run 5x each:
  (a) through build_chat_llm (Portkey-routed, same as production)
  (b) through plain ChatOpenAI (bypasses Portkey entirely)

If (a) shows True some/all of the time and (b) shows False consistently,
Portkey's gateway is corrupting the forced-tool-call structured-output
mechanism specifically -- a new, distinct Portkey defect beyond the
already-known inline_provider_blocked flakiness.

If BOTH (a) and (b) show True some of the time, it's a real model
reliability issue independent of Portkey, and the fix is a stronger
classifier design (e.g. deterministic keyword pre-check before the LLM
call, or a cheaper/more literal exact-match short-circuit for this kind
of case) rather than a gateway fix.

Usage:
    python debug_temporal_classifier.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from llm_gateway import build_chat_llm

load_dotenv()

# Copied verbatim from app/graph.py -- not re-imported from app.graph on
# purpose, so this script has zero dependency on app/graph.py's heavier
# imports (Qdrant, Finnhub, etc.) and can run fast and in isolation.
class TemporalComparisonQuestion(BaseModel):
    """Classification of whether a question invites a since-purchase /
    change-over-time comparison this app has no historical data to
    support."""

    invites_temporal_comparison: bool = Field(
        description="True if the question asks whether something has changed, gotten worse/better, "
        "or otherwise implies a before/after comparison against a PRIOR reference point (e.g. 'since "
        "I bought it', 'has this changed', 'is this still a good hold'). False if the question only "
        "asks what's notable/current within a recency window (e.g. 'this week', 'lately') without "
        "comparing to a past state -- a time window is not the same as a before/after comparison."
    )


_TEMPORAL_QUESTION_PROMPT = """Classify whether this question about a stock holding asks for a \
comparison over time (since purchase, since a date, whether something has changed) rather than \
asking only about current status or what's notable within a recency window.

A question mentioning a TIME WINDOW ("this week", "recently", "lately") is NOT automatically a \
comparison question -- it's asking what's new/notable within that window, not whether anything has \
changed relative to a past state. Only classify True if the question explicitly or implicitly asks \
to compare against a PRIOR reference point (a purchase date, "has this changed", "gotten worse/\
better", "is this still worth holding").

Examples:
- "Summarize everything notable about Astera Labs this week -- filings, media, and analyst \
activity." -> False (asks what's notable within a window, not a comparison against the past)
- "Has anything about Astera Labs's underlying business gotten worse since I bought it -- revenue, \
margins, insider activity, or leadership?" -> True (explicit since-purchase comparison)
- "When does Marvell report next, and what should I watch for based on its current Fundamentals \
Health Score?" -> False (asks about current status and what to watch, not whether anything changed)
- "Is Nebius still a good hold given what's happened since Q1?" -> True (implies a before/after \
comparison)"""

# Q9's REAL, exact question text (from test_q9.py's run_case), not
# retyped by hand -- so any mismatch is a bug in this script, not a red
# herring.
Q9_QUESTION = (
    "Summarize everything notable about Astera Labs this week -- "
    "filings, media, and analyst activity."
)

N_RUNS = 5


def run_trials(label: str, llm) -> list[bool]:
    classifier = llm.with_structured_output(TemporalComparisonQuestion)
    results = []
    for i in range(N_RUNS):
        verdict = classifier.invoke(
            [("system", _TEMPORAL_QUESTION_PROMPT), ("human", Q9_QUESTION)]
        )
        results.append(verdict.invites_temporal_comparison)
        print(f"  [{label}] run {i + 1}/{N_RUNS}: invites_temporal_comparison = {verdict.invites_temporal_comparison}")
    return results


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    print("Q9 question under test:")
    print(f"  {Q9_QUESTION!r}\n")

    print("=" * 70)
    print("(a) Through build_chat_llm -- Portkey-routed, same as production")
    print("=" * 70)
    portkey_llm = build_chat_llm(model="gpt-4.1-mini", temperature=0)
    portkey_results = run_trials("portkey", portkey_llm)

    print("\n" + "=" * 70)
    print("(b) Plain ChatOpenAI -- bypasses Portkey entirely")
    print("=" * 70)
    direct_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0, api_key=os.environ.get("OPENAI_API_KEY"))
    direct_results = run_trials("direct", direct_llm)

    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    print(f"Portkey-routed: {portkey_results.count(True)}/{N_RUNS} misclassified as True (should be False every time)")
    print(f"Direct OpenAI:  {direct_results.count(True)}/{N_RUNS} misclassified as True (should be False every time)")

    if any(portkey_results) and not any(direct_results):
        print("\n-> Portkey-specific: gateway is corrupting the forced structured-output call. "
              "Fix: bypass Portkey for this one classifier call, or file this as a distinct Portkey defect.")
    elif any(direct_results):
        print("\n-> Model-level: misclassifies even with no gateway involved. "
              "Fix: don't trust the LLM alone for this distinction -- add a deterministic pre-check "
              "(e.g. if the question contains 'since ' + a date/purchase reference, force True; "
              "if it contains a bare recency window word with no comparison language, force False; "
              "only fall through to the LLM for genuinely ambiguous phrasing).")
    else:
        print("\n-> Did not reproduce in 5/5 trials either way. The bug may be intermittent, or "
              "something in the full graph.invoke() path (not the classifier itself) is the real "
              "cause -- worth re-checking whether _question_invites_temporal_comparison is really "
              "the function being hit, e.g. via the print-statement approach on a real test_q9.py run.")


if __name__ == "__main__":
    main()
