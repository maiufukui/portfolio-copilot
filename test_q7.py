"""
Test harness for Eval Question 7 (Task 1):
"Show me every time Company X has mentioned '<keyword>' in the last N
filings, verbatim."

This deliberately does NOT use the vector store. Top-k embedding
similarity is approximate by design (it returns the k most-similar
chunks, with no completeness guarantee, and no guarantee those chunks
even contain the literal phrase) — wrong tool for a query that demands
exhaustive, verbatim recall. This script does plain keyword/regex
search over the raw document text instead, which is deterministic and
complete for the literal phrase(s) given.

Usage:
    python test_q7.py --ticker ALAB --keyword "supply chain"
    python test_q7.py --ticker ALAB --keyword bottleneck --keyword "capacity constrained"
    python test_q7.py --ticker ALAB --keyword "supply chain" --summarize
"""

from __future__ import annotations

import argparse
import os
import re

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from test_q1 import load_ticker_documents

load_dotenv()

CONTEXT_CHARS = 200  # characters of surrounding context on each side of a hit


def build_pattern(keyword: str) -> re.Pattern:
    """Build a case-insensitive pattern that tolerates a space OR hyphen
    between words in a multi-word keyword (so 'capacity constrained' also
    catches 'capacity-constrained')."""
    words = [re.escape(w) for w in keyword.split()]
    pattern = r"[\s-]+".join(words)
    return re.compile(pattern, re.IGNORECASE)


def find_hits(documents, keywords: list[str]):
    """Return every verbatim match of any keyword across all documents,
    with source, (page if available), and surrounding context."""
    hits = []
    for keyword in keywords:
        pattern = build_pattern(keyword)
        for doc in documents:
            text = doc.page_content
            source = doc.metadata.get("source", "unknown")
            page = doc.metadata.get("page")
            for match in pattern.finditer(text):
                start = max(0, match.start() - CONTEXT_CHARS)
                end = min(len(text), match.end() + CONTEXT_CHARS)
                snippet = text[start:end].replace("\n", " ").strip()
                hits.append(
                    {
                        "keyword": keyword,
                        "source": source,
                        "page": page,
                        "snippet": snippet,
                    }
                )
    return hits


SUMMARY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "Below are every verbatim, keyword-matched excerpt found across "
            "{ticker}'s filings for the term(s): {keywords}. Do not add, "
            "infer, or paraphrase beyond what's here — just organize these "
            "into a clean, cited list grouped by source document. If a hit "
            "looks like boilerplate/legal disclaimer language rather than a "
            "substantive mention, note that.\n\n"
            "HITS:\n{hits}",
        )
    ]
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument(
        "--keyword",
        action="append",
        required=True,
        help="Repeatable. Exact phrase to search for, e.g. --keyword \"supply chain\"",
    )
    parser.add_argument(
        "--summarize",
        action="store_true",
        help="Run an LLM pass to organize the raw hits into a cited summary.",
    )
    args = parser.parse_args()

    print(f"Loading documents for {args.ticker}...")
    documents = load_ticker_documents(args.ticker)
    print(f"Loaded {len(documents)} document(s). Searching for: {args.keyword}")

    hits = find_hits(documents, args.keyword)

    if not hits:
        print("No verbatim matches found.")
        return

    by_source: dict[str, int] = {}
    for h in hits:
        by_source[h["source"]] = by_source.get(h["source"], 0) + 1

    print(f"\n{len(hits)} total match(es):")
    for source, count in by_source.items():
        print(f"  {source}: {count}")

    print("\n" + "-" * 60)
    for i, h in enumerate(hits, 1):
        page_str = f" (page {h['page']})" if h.get("page") is not None else ""
        print(f"[{i}] {h['source']}{page_str} -- matched '{h['keyword']}'")
        print(f"    ...{h['snippet']}...")
    print("-" * 60)

    if args.summarize:
        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY not set — required for --summarize.")
        llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
        chain = SUMMARY_PROMPT | llm | StrOutputParser()
        hits_text = "\n\n".join(
            f"Source: {h['source']}"
            + (f" (page {h['page']})" if h.get("page") is not None else "")
            + f"\nMatched: {h['keyword']}\nExcerpt: ...{h['snippet']}..."
            for h in hits
        )
        summary = chain.invoke(
            {"ticker": args.ticker, "keywords": ", ".join(args.keyword), "hits": hits_text}
        )
        print("\n" + "=" * 60)
        print(summary)
        print("=" * 60)


if __name__ == "__main__":
    main()
