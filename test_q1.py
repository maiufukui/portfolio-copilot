"""
Test harness for Eval Question 1 (Task 1):
"Has anything in <Ticker>'s latest earnings call contradicted my thesis
that margin expansion is driven by software mix shift?"

Adapted from 10_LLM_Servers/app/rag.py:
- Fireworks -> OpenAI (text-embedding-3-small / gpt-4.1-mini)
- PDF-only loader -> PDF + HTM + TXT (filings are .htm, transcripts are .txt/.pdf)
- Fixed-size chunking per Task 3: 512 tokens, 50-token overlap
- In-memory Qdrant, same as course pattern

Usage:
    pip install -r requirements.txt
    cp .env.example .env   # then add your real OPENAI_API_KEY
    python test_q1.py --ticker ALAB --thesis "margin expansion is driven by software mix shift"
"""

from __future__ import annotations

import argparse
import glob
import os
import re

import tiktoken
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain_community.document_loaders import (
    PyMuPDFLoader,
    TextLoader,
)
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

from llm_gateway import build_embeddings

load_dotenv()


def _tiktoken_len(text: str) -> int:
    tokens = tiktoken.encoding_for_model("gpt-4o").encode(text)
    return len(tokens)


# Real bug, found against Data/ALAB/10-Q_2026-05-06.htm (2026-07-25): every
# SEC filing since ~2019 uses Inline XBRL, which embeds machine-readable tag
# data alongside the visible filing text inside <ix:header>/<ix:hidden>
# elements and other elements styled display:none -- none of it meant to be
# read by a human viewing the rendered page. BSHTMLLoader's default
# soup.get_text() doesn't respect CSS visibility, so that hidden tag soup
# was getting pulled into the "visible" text right alongside real prose
# (confirmed: this exact file's parsed Item 1 started with raw XBRL fact
# IDs -- "alab-202603310001736297false2026Q1--12-311P7Yxbrli:shares..." --
# instead of real business-description text). Replaces BSHTMLLoader with a
# thin wrapper that strips this content before text extraction; everything
# else (encoding, metadata shape) matches BSHTMLLoader's own behavior so
# nothing downstream needs to change.
_HIDDEN_TAG_NAMES = ("ix:header", "ix:hidden")
_DISPLAY_NONE_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)


def _load_filing_html(path: str) -> Document:
    with open(path, encoding="utf-8") as f:
        soup = BeautifulSoup(f, "html.parser")

    for tag in soup.find_all(_HIDDEN_TAG_NAMES):
        tag.decompose()
    for tag in soup.find_all(style=_DISPLAY_NONE_RE):
        tag.decompose()

    text = soup.get_text()
    title = str(soup.title.string) if soup.title else ""
    return Document(page_content=text, metadata={"source": path, "title": title})


def load_ticker_documents(ticker: str, data_dir: str = "Data"):
    """Load every .htm (SEC filing), .txt (transcript), and .pdf (transcript,
    if that's all we have) file for a given ticker."""
    ticker_dir = os.path.join(data_dir, ticker.upper())
    if not os.path.isdir(ticker_dir):
        raise FileNotFoundError(f"No data folder found at {ticker_dir}")

    documents = []

    for path in glob.glob(os.path.join(ticker_dir, "*.htm")):
        documents.append(_load_filing_html(path))

    for path in glob.glob(os.path.join(ticker_dir, "*.txt")):
        documents.extend(TextLoader(path).load())

    for path in glob.glob(os.path.join(ticker_dir, "*.pdf")):
        documents.extend(PyMuPDFLoader(path).load())

    if not documents:
        raise FileNotFoundError(f"No .htm/.txt/.pdf documents found in {ticker_dir}")

    return documents


def build_retriever(documents):
    # Task 3 decision: fixed-size chunking, 512 tokens, 50-token overlap
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=512, chunk_overlap=50, length_function=_tiktoken_len
    )
    chunks = splitter.split_documents(documents)

    embedding_model = build_embeddings(model="text-embedding-3-small")
    vectorstore = QdrantVectorStore.from_documents(
        documents=chunks,
        embedding=embedding_model,
        location=":memory:",
        collection_name="portfolio_copilot_eval",
    )
    return vectorstore.as_retriever(search_kwargs={"k": 10})


PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are a portfolio thesis-monitoring assistant. The user holds "
            "{ticker} and their stated investment thesis is:\n\n"
            '"{thesis}"\n\n'
            "Using ONLY the context below (pulled from {ticker}'s SEC filings "
            "and latest earnings call transcript), answer: has anything in "
            "the source material CONFIRMED, been NEUTRAL to, or CONTRADICTED "
            "this thesis? Cite the specific numbers or quotes you're relying "
            "on. If the context doesn't address the thesis at all, say so "
            "explicitly rather than guessing.\n\n"
            "CONTEXT:\n{context}\n\n"
            "Respond in this format:\n"
            "Verdict: [Confirms / Neutral / Contradicts / Not addressed]\n"
            "Evidence: ...\n"
            "Explanation: ...",
        )
    ]
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--thesis", required=True)
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    print(f"Loading documents for {args.ticker}...")
    documents = load_ticker_documents(args.ticker)
    print(f"Loaded {len(documents)} document(s). Chunking + embedding...")
    retriever = build_retriever(documents)

    query = (
        f"Has anything in {args.ticker}'s latest earnings call or filings "
        f"contradicted the thesis that {args.thesis}?"
    )
    retrieved_docs = retriever.invoke(query)

    print(f"\nRetrieved {len(retrieved_docs)} chunks:")
    for i, doc in enumerate(retrieved_docs, 1):
        source = doc.metadata.get("source", "unknown")
        preview = doc.page_content[:120].replace("\n", " ")
        print(f"  [{i}] {source} -- {preview}...")

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
    chain = PROMPT | llm | StrOutputParser()
    answer = chain.invoke(
        {
            "ticker": args.ticker,
            "thesis": args.thesis,
            "context": "\n\n---\n\n".join(d.page_content for d in retrieved_docs),
        }
    )

    print("\n" + "=" * 60)
    print(answer)
    print("=" * 60)


if __name__ == "__main__":
    main()
