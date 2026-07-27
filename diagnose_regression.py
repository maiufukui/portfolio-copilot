"""
Diagnostic: after Item 8 (content-type exclusion), ALAB's and PANW's Q1
cases are STILL at context_recall 0.0 (real RAGAS run, 2026-07-26), and
MRVL's "this quarter's data center revenue growth" case regressed from
1.0 to 0.0 in that same run. This checks, per case, at every stage of
retrieve()'s pipeline -- dense-only, BM25-only, RRF-fused -- whether the
parent containing the real answer is present at all, and at what rank.

It also tests a second, previously-unexamined variable: run_eval.py's
Q1 query embeds a long instructional suffix ("...Prefer the exact
verbatim sentence...") added for the PANW query-wording fix. That same
long query is used for DENSE retrieval too, not just reranking -- this
compares dense/BM25/fused results using that full augmented query
against the bare core question, to see whether the added instructional
text is itself hurting retrieval, not just failing to help reranking.

Reuses the EXISTING on-disk embedding cache (parent_child_retriever's
EMBEDDING_CACHE_DIR) for each ticker -- one real OpenAI call per query
variant (embedding a few dozen words), zero corpus re-embedding. The
BM25 index and content-type filtering are rebuilt locally (free,
CPU-only) to exactly mirror what build_parent_child_retriever does
internally, since that function doesn't expose its internal
dense/BM25/fused state for inspection.

Usage:
    python3 diagnose_regression.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi

from parent_child_retriever import (
    CHILD_CHUNK_OVERLAP,
    CHILD_CHUNK_SIZE,
    CHILD_SEARCH_K,
    EMBEDDING_CACHE_DIR,
    EMBEDDING_MODEL,
    EXCLUDED_CONTENT_TYPES,
    QDRANT_COLLECTION_NAME,
    _reciprocal_rank_fusion,
    _tiktoken_len,
    split_into_parents,
)
from test_q1 import load_ticker_documents

load_dotenv()

# One case per row we need to explain: ALAB's and PANW's still-broken
# cases, plus MRVL's new regression. target_signatures are exact,
# distinctive substrings pulled directly from each case's real
# eval_dataset.json reference -- a parent "contains the answer" if ANY
# of its signatures appear in that parent's raw text.
CASES = [
    ("ALAB", "this quarter's gross margin change", ["signal conditioning"]),
    ("ALAB", "next quarter's gross margin guidance", ["one-time customer agreement", "73%"]),
    ("PANW", "this quarter's gross margin change", ["78.8", "cloud-hosting"]),
    ("PANW", "fiscal Q4 2026 revenue and margin outlook", ["3.345", "3.355", "28.9"]),
    ("MRVL", "this quarter's data center revenue growth", ["1.8 billion", "optical interconnect"]),
]

# Exact match to run_eval.py's run_rag_q1 query construction.
FULL_QUERY_TEMPLATE = (
    "What did {ticker}'s management identify as the specific driver behind {metric}, "
    "for the most recently reported quarter, not the full fiscal year? Prefer the exact verbatim "
    "sentence from management's spoken remarks over any bullet-point summary, headline takeaway, "
    "or restated figure elsewhere in the source."
)
SHORT_QUERY_TEMPLATE = "What did {ticker}'s management identify as the specific driver behind {metric}?"


def _find_rank(hits: list[Document], parents_by_id: dict, signatures: list[str]):
    """1-indexed rank of the first hit whose PARENT text contains any
    signature, plus that parent's source file and content_type, or
    (None, None, None) if absent from this hit list entirely."""
    for rank, hit in enumerate(hits, start=1):
        pid = hit.metadata.get("parent_id", "")
        parent = parents_by_id.get(pid, {})
        parent_text = parent.get("text", "")
        if any(sig in parent_text for sig in signatures):
            source = os.path.basename(hit.metadata.get("source", "unknown"))
            return rank, source, parent.get("content_type", "?")
    return None, None, None


def _build_local_child_docs(ticker: str):
    """Mirrors build_parent_child_retriever's child_docs construction and
    content-type exclusion exactly (same splitter config, same
    EXCLUDED_CONTENT_TYPES filter) without needing OpenAI -- pure local
    text processing, so this stays in sync with what's actually indexed
    on disk without duplicating logic by hand."""
    documents = load_ticker_documents(ticker)
    parents = split_into_parents(documents)
    parents_by_id = {p["parent_id"]: p for p in parents}
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHILD_CHUNK_SIZE, chunk_overlap=CHILD_CHUNK_OVERLAP, length_function=_tiktoken_len
    )
    child_docs = []
    for parent in parents:
        if parent.get("content_type") in EXCLUDED_CONTENT_TYPES:
            continue
        for chunk_text in splitter.split_text(parent["text"]):
            child_docs.append(
                Document(
                    page_content=chunk_text,
                    metadata={"parent_id": parent["parent_id"], "source": parent.get("source", "unknown")},
                )
            )
    return parents_by_id, child_docs


def main():
    by_ticker_cache: dict[str, tuple] = {}
    rows = []

    for ticker, metric, signatures in CASES:
        print("=" * 70)
        print(f"{ticker}: {metric}")
        print("=" * 70)

        collection_dir = os.path.join(EMBEDDING_CACHE_DIR, ticker)
        if not os.path.isdir(collection_dir):
            print(f"!! No on-disk cache at {collection_dir} -- run run_eval.py --question 1 first.")
            continue

        if ticker not in by_ticker_cache:
            parents_by_id, child_docs = _build_local_child_docs(ticker)
            bm25_index = BM25Okapi([d.page_content.lower().split() for d in child_docs])
            embedding_model = OpenAIEmbeddings(model=EMBEDDING_MODEL)
            client = QdrantClient(path=collection_dir)
            vectorstore = QdrantVectorStore(client=client, collection_name=QDRANT_COLLECTION_NAME, embedding=embedding_model)
            child_retriever = vectorstore.as_retriever(search_kwargs={"k": CHILD_SEARCH_K})
            by_ticker_cache[ticker] = (parents_by_id, child_docs, bm25_index, child_retriever, client)
        parents_by_id, child_docs, bm25_index, child_retriever, _client = by_ticker_cache[ticker]

        for label, query in [
            ("FULL (augmented)", FULL_QUERY_TEMPLATE.format(ticker=ticker, metric=metric)),
            ("SHORT (bare question)", SHORT_QUERY_TEMPLATE.format(ticker=ticker, metric=metric)),
        ]:
            dense_hits = child_retriever.invoke(query)  # 1 real embedding call
            bm25_scores = bm25_index.get_scores(query.lower().split())  # free, local
            bm25_ranked_idx = sorted(range(len(child_docs)), key=lambda i: bm25_scores[i], reverse=True)
            bm25_hits = [child_docs[i] for i in bm25_ranked_idx[:CHILD_SEARCH_K]]
            fused = _reciprocal_rank_fusion(
                [dense_hits, bm25_hits], key_fn=lambda d: (d.metadata.get("parent_id", ""), d.page_content)
            )

            dense_rank, dense_src, dense_ct = _find_rank(dense_hits, parents_by_id, signatures)
            bm25_rank, bm25_src, bm25_ct = _find_rank(bm25_hits, parents_by_id, signatures)
            fused_rank, fused_src, fused_ct = _find_rank(fused, parents_by_id, signatures)

            print(f"\n  Query variant: {label}")
            print(f"    dense-only top-{CHILD_SEARCH_K}: " + (f"rank {dense_rank} ({dense_src}, {dense_ct})" if dense_rank else "NOT FOUND"))
            print(f"    BM25-only  top-{CHILD_SEARCH_K}: " + (f"rank {bm25_rank} ({bm25_src}, {bm25_ct})" if bm25_rank else "NOT FOUND"))
            print(f"    RRF-fused  pre-rerank:   " + (f"rank {fused_rank} ({fused_src}, {fused_ct})" if fused_rank else "NOT FOUND"))

            rows.append(
                {
                    "ticker": ticker,
                    "metric": metric[:40],
                    "query": label,
                    "dense_rank": dense_rank,
                    "bm25_rank": bm25_rank,
                    "fused_rank": fused_rank,
                }
            )
        print()

    for cache_entry in by_ticker_cache.values():
        cache_entry[-1].close()

    print("=" * 70)
    print(f"SUMMARY (rank = 1-indexed position where the answer's parent was found; blank = not in top-{CHILD_SEARCH_K} at that stage)")
    print("=" * 70)
    import pandas as pd

    pd.set_option("display.width", 160)
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
