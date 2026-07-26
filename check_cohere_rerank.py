"""
Live verification probe -- item 4, step 1. NOT wired into the app.

Before touching parent_child_retriever.py's real retrieve() logic (step 2 of
item 4's plan), confirm the Cohere trial key actually works end-to-end --
same "test live before building" discipline this project now applies to
every new external dependency (FMP and Finnhub's price-target endpoints
both turned out gated after being assumed to work; don't repeat that here
with Cohere).

Deliberately does NOT need OPENAI_API_KEY or any embeddings -- parent
splitting (parent_child_retriever.split_into_parents) is pure regex/text
logic, no vector search involved. This isolates the one new thing being
verified (does the Cohere rerank call work) from everything else.

Usage:
    python check_cohere_rerank.py --ticker ALAB --query "What drove this quarter's gross margin change?"
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv
from langchain_cohere import CohereRerank
from langchain_core.documents import Document

from parent_child_retriever import split_into_parents
from test_q1 import load_ticker_documents

load_dotenv()

RERANK_MODEL = "rerank-v3.5"  # current Cohere model at time of writing; check
                               # https://docs.cohere.com/docs/rerank for the
                               # latest if this 404s or errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="ALAB")
    parser.add_argument(
        "--query", default="What drove this quarter's gross margin change?"
    )
    parser.add_argument("--top-n", type=int, default=5)
    args = parser.parse_args()

    print(f"Loading {args.ticker} documents and splitting into parents (no embeddings needed)...")
    documents = load_ticker_documents(args.ticker)
    parents = split_into_parents(documents)
    print(f"  {len(parents)} parent sections/turns found.")

    # Cap how many parents get sent to Cohere in this smoke test -- real
    # usage (step 2) reranks a bounded candidate set (post-dedup, pre-k
    # truncation), not every parent in the corpus. 20 is plenty to confirm
    # the API call itself works.
    candidates = parents[:20]
    docs = [
        Document(page_content=p["text"][:2000], metadata={"label": p["label"], "source": p["source"]})
        for p in candidates
    ]

    print(f"\nSending {len(docs)} candidates to Cohere ({RERANK_MODEL}) for query:")
    print(f'  "{args.query}"\n')

    compressor = CohereRerank(model=RERANK_MODEL, top_n=args.top_n)
    reranked = compressor.compress_documents(documents=docs, query=args.query)

    print(f"Top {len(reranked)} reranked results:")
    for i, doc in enumerate(reranked, 1):
        score = doc.metadata.get("relevance_score")
        label = doc.metadata.get("label", "?")
        print(f"  {i}. [{score:.4f}] {label} -- {doc.page_content[:120].strip()}...")

    print("\nIf you see real scores and sensible-looking top results above, the Cohere trial key works.")


if __name__ == "__main__":
    main()
