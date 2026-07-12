"""
One-off diagnostic: does the parent-child retriever's case-1 result set
(Q1 test case "this quarter's gross margin change") actually contain the
correct non-GAAP transcript sentence (76.4%, 70bps, "lower mix of
hardware sales within signal conditioning" -- the eval reference,
verbatim, from the earnings call TAKEAWAYS), even though the synthesized
response quoted the 10-Q's GAAP figure (76.3%, 136bps YoY) instead?

Retrieval only -- no answer-generation call, no RAGAS judge call. Just
embeddings + a similarity search, to see what's actually in the context
window handed to the LLM.

Usage:
    python inspect_retrieval.py
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from parent_child_retriever import build_parent_child_retriever
from test_q1 import load_ticker_documents

load_dotenv()

QUERY = "What did ALAB's management identify as the specific driver behind this quarter's gross margin change?"


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    print("Loading ALAB documents...")
    documents = load_ticker_documents("ALAB")

    print("Building parent-child retriever...")
    retrieve = build_parent_child_retriever(documents)

    print(f"\nQuery: {QUERY}\n")
    parents = retrieve(QUERY, k=5)

    for i, p in enumerate(parents, 1):
        text = p.page_content
        has_correct = "76.4" in text and "70 basis" in text
        has_wrong = "136 bps" in text or "74.9" in text
        print(f"[{i}] {p.metadata.get('source')} :: {p.metadata.get('label')}  ({len(text)} chars)")
        print(f"    contains correct (non-GAAP, 76.4%/70bps) sentence: {has_correct}")
        print(f"    contains the GAAP (136bps/74.9%) sentence instead: {has_wrong}")
        if has_correct:
            idx = text.find("76.4")
            print(f"    excerpt: ...{text[max(0,idx-100):idx+150]!r}...")


if __name__ == "__main__":
    main()
