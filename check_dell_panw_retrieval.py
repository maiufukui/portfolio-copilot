"""
Diagnostic: is the DELL/PANW gross-margin retrieval bug a first-stage
(dense embedding) miss, or a Cohere rerank miss?

Context: run_eval.py --question 1 --verbose showed DELL's "this quarter's
gross margin change" and PANW's "fiscal Q4 2026 revenue and margin
outlook" cases both citing the wrong figures, even though the correct
sentence is verbatim in Data/DELL/transcript_latest.txt and
Data/PANW/transcript_latest.txt (confirmed by direct grep, 2026-07-26).
The post-rerank top 5 shown to the LLM never included transcript_latest.txt
for DELL at all. This script checks the PRE-rerank candidate set (the
top CHILD_SEARCH_K=15 dense hits, before Cohere ever sees them) to
determine which stage is actually losing the correct passage:

  - If transcript_latest.txt's chunk isn't even in the top 15 here,
    dense embedding similarity is the failure point -- supports moving
    to item 7 (RRF, blending dense + BM25 keyword search), since exact
    numeric figures like "$3.345 billion" or "20.5%" are exactly what
    lexical search is good at and dense embeddings can blur among
    topically-similar passages.
  - If it IS in the top 15 here but wasn't in the post-rerank top 5,
    the problem is Cohere's reranking, not retrieval -- a different,
    likely cheaper fix (rerank prompt/model tuning).

Reuses the EXISTING on-disk embedding cache (parent_child_retriever's
EMBEDDING_CACHE_DIR) for DELL/PANW -- already built by prior run_eval.py
runs, so this makes ONE real OpenAI call per query (embedding the
question text itself, a few tokens) and zero corpus re-embedding calls.

Usage:
    python3 check_dell_panw_retrieval.py
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient

from parent_child_retriever import (
    CHILD_SEARCH_K,
    EMBEDDING_CACHE_DIR,
    EMBEDDING_MODEL,
    QDRANT_COLLECTION_NAME,
    split_into_parents,
)
from langchain_qdrant import QdrantVectorStore
from test_q1 import load_ticker_documents

load_dotenv()

CASES = [
    ("DELL", "What did DELL's management identify as the specific driver behind this quarter's gross margin change, for the most recently reported quarter, not the full fiscal year?"),
    ("PANW", "What did PANW's management identify as the specific driver behind fiscal Q4 2026 revenue and margin outlook, for the most recently reported quarter, not the full fiscal year?"),
]


def main():
    for ticker, query in CASES:
        print("=" * 70)
        print(f"{ticker}: {query}")
        print("=" * 70)

        collection_dir = os.path.join(EMBEDDING_CACHE_DIR, ticker)
        if not os.path.isdir(collection_dir):
            print(f"!! No on-disk cache found at {collection_dir} -- run run_eval.py --question 1 "
                  f"at least once first so this reuses the existing cache instead of rebuilding.")
            continue

        # parents_by_id -- just for a readable label per hit, not re-embedded (pure local parsing).
        documents = load_ticker_documents(ticker)
        parents = split_into_parents(documents)
        parents_by_id = {p["parent_id"]: p for p in parents}

        embedding_model = OpenAIEmbeddings(model=EMBEDDING_MODEL)
        client = QdrantClient(path=collection_dir)
        vectorstore = QdrantVectorStore(client=client, collection_name=QDRANT_COLLECTION_NAME, embedding=embedding_model)
        child_retriever = vectorstore.as_retriever(search_kwargs={"k": CHILD_SEARCH_K})

        hits = child_retriever.invoke(query)  # PRE-rerank -- raw dense similarity order, top 15
        transcript_hits = []
        print(f"\nTop {len(hits)} PRE-RERANK dense hits (source, then a 100-char preview):\n")
        for i, hit in enumerate(hits, 1):
            source = os.path.basename(hit.metadata.get("source", "unknown"))
            is_transcript = "transcript" in source.lower()
            if is_transcript:
                transcript_hits.append((i, hit))
            marker = " <-- TRANSCRIPT" if is_transcript else ""
            preview = hit.page_content[:100].replace("\n", " ")
            print(f"  [{i}] {source}{marker}\n      {preview}...")

        print()
        if transcript_hits:
            print(f"RESULT: transcript_latest.txt WAS in the pre-rerank top {CHILD_SEARCH_K}, "
                  f"at position(s) {[i for i, _ in transcript_hits]}. Dense retrieval found it; "
                  f"check whether Cohere's rerank is what dropped it.")
            for i, hit in transcript_hits:
                pid = hit.metadata.get("parent_id", "")
                parent_text = parents_by_id.get(pid, {}).get("text", "")
                has_key_figure = any(fig in parent_text for fig in ["6,800,000,000", "20.5%", "3.345", "3.355"])
                print(f"  -- hit [{i}]'s full parent contains the exact target figure: {has_key_figure}")
        else:
            print(f"RESULT: transcript_latest.txt was NOT in the pre-rerank top {CHILD_SEARCH_K} at all. "
                  f"This is a first-stage dense-embedding miss, not a reranking problem -- "
                  f"supports moving to item 7 (RRF / BM25 fusion).")
        print()

        client.close()


if __name__ == "__main__":
    main()
