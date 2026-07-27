"""
Parent-child retrieval -- Task 6 advanced-retriever upgrade.
(Rubric: "Choose and implement an advanced retrieval technique that you
believe will improve your application's ability to retrieve the most
appropriate context.")

Follows the hand-rolled pattern from Session 7
(07_Advanced_Retrievers/01_Cat_Health_Advanced_Retrieval.ipynb, Task 5:
"Parent-Child Retrieval"), not LangChain's ParentDocumentRetriever class:
child chunks are embedded and searched for precision; each child's
metadata carries a parent_id; hits are deduped back to unique, larger
parent documents via a lookup dict (recover_parent_documents in the
notebook, mirrored here), giving the answer model full section/turn
context instead of an isolated 512-token fragment that may cut a fact
off from the reasoning around it.

Why this, specifically: PRD Task 3's chunking-strategy writeup already
names the concrete failure this targets -- at k=6, naive dense search on
512-token chunks missed the one chunk containing an exact quote a
thesis-check question needed, because it scored lower similarity than
surrounding boilerplate. Parent-child retrieval doesn't fix the child
chunk's similarity ranking; it makes a correct-but-narrow child hit
recover its FULL parent section regardless, so a partial match still
surfaces the complete context instead of a fragment.

Parent unit, per PRD Task 6's design note ("Item-based for filings,
turn-based for transcripts"), confirmed against this project's real data
before writing any splitting logic:

  - SEC filings (.htm, all 4 tickers): the full "Item N. <Title>"
    section (e.g. "Item 1A. Risk Factors"). Item headings are real,
    regex-detectable text in the parsed filing (confirmed against
    Data/ALAB/10-K_2026-02-20.htm), but each "Item N." occurrence needs
    two checks, not one: (1) it must be followed by that item's real
    Reg S-K title (ITEM_TITLE_KEYWORDS below) -- plain prose
    cross-references like "...appearing under Item 9A. Our
    responsibility is..." also match "Item 9A." followed by a period,
    and confirmed to otherwise get mistaken for the real heading; (2) of
    the remaining candidates (typically two: the Table of Contents line
    and the real section), split_filing_into_items keeps whichever has
    the longer following text, since a ToC line is one sentence and the
    real section runs pages.

  - Earnings transcripts (.txt, all 4 tickers): the full speaker turn,
    split on exact speaker names extracted from the transcript's own
    "Call participants:" header -- not a generic "Capitalized Word:"
    regex, which would also match "Source:", "Call date:", "Published:"
    in the same file (confirmed against Data/ALAB/transcript_Q1_2026.txt).

  - Non-.txt fallback (currently unused): PyMuPDFLoader gives one
    Document per page, used as one parent per page, for any future
    source file this project ingests that isn't structured filing HTML
    or transcript text. Dead code against this project's actual data --
    all 4 tracked tickers have clean, fully verbatim .txt transcripts
    fetched directly from source -- kept only as a defensive fallback
    for a future source format.

This coexists with test_q1.py's build_retriever (the MVP baseline)
rather than replacing it -- nothing in app/tools.py or run_eval.py's
existing Q1 runner is touched. compare_retrievers.py runs both side by
side for the required before/after table.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time

import tiktoken
from langchain_cohere import CohereRerank
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from rank_bm25 import BM25Okapi

CHILD_CHUNK_SIZE = 512
CHILD_CHUNK_OVERLAP = 50
CHILD_SEARCH_K = 15  # search wider than the final k, then dedupe down to unique parents
EMBEDDING_MODEL = "text-embedding-3-small"

# Persistent/shared embedding cache (added 2026-07-26). Root cause this
# fixes: every separate process that calls build_parent_child_retriever
# (run_eval.py, app/tools.py's live agent, one-off dev scripts) used to
# call QdrantVectorStore.from_documents(..., location=":memory:"), which
# re-embeds a ticker's ENTIRE corpus via OpenAI from scratch, every single
# time, with zero persistence between runs. Two short-lived processes
# embedding the same static corpus within the same rolling 1-minute window
# is exactly what pushed real runs into OpenAI's embeddings TPM rate limit
# (429) three separate times this project (see run_eval.py's per-process
# _retriever_cache comment for the first incident; that fix only stopped
# REDUNDANT embedding calls within one process, not across processes).
#
# Fix: cache_key (a ticker) makes build_parent_child_retriever persist its
# child-chunk embeddings to a local on-disk Qdrant collection instead of
# :memory:, keyed by a content fingerprint (see _child_docs_fingerprint) so
# a genuinely changed corpus (new filing added, chunking logic edited)
# still triggers a real rebuild rather than silently serving stale
# embeddings. Without a cache_key, behavior is unchanged (in-memory, no
# persistence) -- compare_retrievers.py and check_cohere_rerank.py call
# this without one and don't need cross-process reuse.
#
# Disclosed limitation, not silently papered over: Qdrant's local
# (path=) mode takes an exclusive file lock on that directory -- only ONE
# process can have a given ticker's cache open at a time. If a second
# process tries to open the same ticker's cache while the first still has
# it (e.g. running run_eval.py against ALAB while the dev server is also
# mid-request for ALAB), that second process cannot get the lock. Handled
# by falling back to a one-off in-memory build for that process (see the
# try/except in _load_or_build_vectorstore) rather than crashing --
# correct, but that process pays a real OpenAI re-embed cost that one
# time. Acceptable for this project's actual usage pattern (sequential
# local dev/eval runs, not concurrent same-ticker traffic), not a fix for
# true concurrent multi-process access -- that would need a real Qdrant
# server (Docker) instead of local file mode, which is more
# infrastructure than a capstone needs right now.
#
# This is LOCAL DISK persistence -- it solves the actual, observed pain
# (repeated rate limits across separate local dev-script invocations). It
# does NOT by itself solve cold-start re-embedding on Render after a
# server restart/redeploy, because Render's paid plans (including
# Starter) do NOT include a persistent filesystem automatically -- a
# separate "Disk" resource must be explicitly attached to the service and
# mounted at a specific path (confirmed directly against Render's own
# docs, render.com/docs/disks, 2026-07-26; see chat for the full
# citation). EMBEDDING_CACHE_DIR below is overridable via env var
# specifically so that a Disk can be pointed at it later without a code
# change -- but no Disk is attached yet, and doing so is a separate,
# not-yet-taken action (would cost $0.25/GB/month and also disables
# zero-downtime deploys on that service, per Render's docs). Tracked as a
# follow-up, not done here.
EMBEDDING_CACHE_DIR = os.environ.get(
    "EMBEDDING_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".embedding_cache", "qdrant"),
)
QDRANT_COLLECTION_NAME = "parent_child"

# Item 4: replaces the retired prefer_source_suffixes hand-coded rule
# (below, kept only in git history) with a real per-query Cohere rerank
# pass -- same langchain_cohere.CohereRerank pattern verified working
# against this project's real data via check_cohere_rerank.py, itself
# following Session 07's rerank_parent_candidates.
RERANK_MODEL = "rerank-v3.5"
RERANK_MAX_RETRIES = 1  # one retry on a transient failure before falling back -- most
                          # real-world 429s/network blips clear within a second

# Bound on how much of one parent's raw text gets returned. Confirmed
# necessary against the eval harness's own Q1 case 1 ("this quarter's
# gross margin change"): the correct answer (a 3,120-char transcript
# turn, matching the eval reference verbatim) was retrieved and ranked
# 3rd of 5 parents -- ranking wasn't the problem, similarity search
# correctly found it -- but it was concatenated into the LLM's context
# alongside two much larger filing sections (23K and 40K chars) that
# also matched (a different, GAAP-basis gross-margin figure), for
# 230K+ total context chars. The synthesis step picked the GAAP figure
# that appeared first and was repeated across the two large sections,
# missing the smaller, later, correct one. Returning a full 40K-char
# Item 7 section for one matching sentence defeats the purpose of
# parent-child retrieval -- it's supposed to stop a fact from being cut
# off mid-context, not bury a precise, correct excerpt inside 25,000
# characters of surrounding text it doesn't need. Small parents
# (transcript turns, short Items) are returned whole, unaffected.
MAX_PARENT_CHARS = 6000
PARENT_WINDOW_MARGIN = 3000  # chars of context kept on each side of the matching child chunk, when a parent exceeds MAX_PARENT_CHARS

ITEM_PATTERN = re.compile(r"Item\s+(\d{1,2}[A-Z]?)\.\s", re.IGNORECASE)
SPEAKER_LIST_PATTERN = re.compile(r"Call participants:\s*\n((?:-[^\n]+\n?)+)", re.IGNORECASE)

# Canonical item titles, covering both 10-K item numbering and 10-Q
# item numbering (10-Qs reuse the SAME item numbers for two unrelated
# sections -- e.g. "Item 1" is "Financial Statements" in Part I but
# "Legal Proceedings" in Part II -- confirmed against all three
# domestic tickers' real 10-Qs). Each item number maps to a list of
# possible titles rather than one; a match is validated if ANY listed
# title appears in the text right after it, and which one matched
# becomes part of that parent's key (see split_filing_into_items) so
# Part I's "Item 1" and Part II's "Item 1" don't collapse into one
# parent and silently drop one of the two.
#
# This also rejects matches where "Item N." is followed by ordinary
# prose rather than a real heading -- e.g. a cross-reference like
# "...appearing under Item 9A. Our responsibility is to express
# opinions..." -- confirmed necessary against
# Data/ALAB/10-K_2026-02-20.htm, where such a cross-reference happened
# to fall right before a long, heading-free stretch of text, so a bare
# "keep the longest occurrence" rule picked it over the real (much
# shorter, but genuine) Item 9A heading.
#
# Filing types with a different item scheme entirely (20-F, 8-K, 6-K)
# aren't covered here -- see the coverage check in
# split_filing_into_items, which falls back to a whole-document parent
# rather than silently dropping the content this map doesn't recognize.
ITEM_TITLE_KEYWORDS: dict[str, list[str]] = {
    "1": ["business", "financial statements", "legal proceedings"],
    "1A": ["risk factors"],
    "1B": ["unresolved staff comments"],
    "1C": ["cybersecurity"],
    "2": ["properties", "management's discussion", "unregistered sales"],
    "3": ["legal proceedings", "quantitative and qualitative", "defaults upon senior"],
    "4": ["mine safety", "controls and procedures"],
    "5": ["market for", "other information"],
    "6": ["reserved", "exhibits"],
    "7": ["management's discussion"],
    "7A": ["quantitative and qualitative"],
    "8": ["financial statements"],
    "9": ["changes in and disagreements"],
    "9A": ["controls and procedures"],
    "9B": ["other information"],
    "9C": ["foreign jurisdiction"],
    "10": ["directors, executive officers"],
    "11": ["executive compensation"],
    "12": ["security ownership"],
    "13": ["certain relationships"],
    "14": ["principal accountant"],
    "15": ["exhibits"],
    "16": ["form 10-k summary"],
}
TITLE_CHECK_WINDOW = 60  # chars of text after "Item N. " to search for a keyword

# If validated items don't collectively cover at least this fraction of
# the document, or too few distinct items validated at all,
# ITEM_TITLE_KEYWORDS doesn't match this filing's actual item scheme
# (e.g. a 20-F's items 1-19 have entirely different titles from a
# 10-K/10-Q's) -- fall back to one whole-document parent rather than
# silently mislabeling or dropping the content. Both checks are needed:
# coverage alone isn't enough, since a single lone validated match (the
# real failure mode against Data/NBIS/20-F_2026-04-30.htm) starts at
# position 0 with no next match to bound it, so it captures 100% of the
# document under one wrong item label rather than actually splitting it.
# A 10-Q has at least 4 Part I items alone; MIN_VALID_ITEMS = 3 is below
# every real filing type this project uses and above NBIS's 1-match
# false positive.
MIN_COVERAGE_FRACTION = 0.85
MIN_VALID_ITEMS = 3

# Item 8: content-type tagging, added after confirming a real, measured
# regression from Item 7's RRF fusion (run_eval.py --question 1
# --verbose, 2026-07-26). Root cause, confirmed against the actual
# retrieved-context blocks from that run: split_filing_into_items and
# split_transcript_into_turns both glue structural "header" content --
# a filing's cover page / checkbox front matter, and a transcript's
# TAKEAWAYS/SUMMARY/INDUSTRY GLOSSARY preamble -- onto whichever Item or
# speaker turn happens to come first, rather than splitting it off on
# its own. That combined parent is large, generic, and financial-term-
# dense, so it out-competes the actual answer under both dense and BM25
# search. Confirmed directly: ALAB's two Q1 cases retrieved ZERO
# transcript content in the top 5 (all 10-Q front matter/financial
# tables); PANW's two cases retrieved the TAKEAWAYS block plus four
# different filings' front matter, never the real transcript body.
# Every ticker where this doesn't happen (DELL, MRVL, NBIS) scored
# context_recall 1.0 in that same run.
#
# Fix: each parent now carries a content_type; the two structural-header
# types below are excluded from indexing entirely (never chunked,
# embedded, or added to the BM25 index -- see build_parent_child_retriever)
# rather than just downranked. Everything else (Item 1A risk factors,
# Item 5, MD&A, financial statement notes, all real transcript turns)
# is untouched and still competes normally -- this targets the two
# specific structural-header spots confirmed responsible, not a general
# boilerplate classifier.
EXCLUDED_CONTENT_TYPES = {"transcript_preamble", "filing_frontmatter"}


def _normalize(text: str) -> str:
    return text.replace("’", "'").lower()


def _tiktoken_len(text: str) -> int:
    return len(tiktoken.encoding_for_model("gpt-4o").encode(text))


def split_filing_into_items(doc: Document) -> list[dict]:
    """Split one SEC filing Document into its Item sections (see module
    docstring for the title-validation + ToC-vs-real-heading
    disambiguation logic)."""
    text = doc.page_content
    source = doc.metadata.get("source", "unknown")
    raw_matches = list(ITEM_PATTERN.finditer(text))
    whole_doc_fallback = [
        {
            "parent_id": source,
            "text": text,
            "label": "(whole document, no Item headings found)",
            "source": source,
            "content_type": "filing_item",  # unclassified fallback -- not excluded, safer than risking a false-positive drop
        }
    ]

    # Validate each match against that item number's known titles, and
    # keep track of WHICH title matched -- for item numbers reused across
    # 10-Q Part I/Part II (e.g. "1"), the matched title is what tells
    # apart "Item 1: Financial Statements" from "Item 1: Legal
    # Proceedings" so they don't collapse into one key.
    validated = []
    for m in raw_matches:
        item_num = m.group(1).upper()
        window = _normalize(text[m.end() : m.end() + TITLE_CHECK_WINDOW])
        for keyword in ITEM_TITLE_KEYWORDS.get(item_num, []):
            if keyword in window:
                validated.append((m, item_num, keyword))
                break
    if not validated:
        return whole_doc_fallback

    # Item 8: the cover page / checkbox front matter before the FIRST
    # real Item heading is now its own parent (content_type
    # "filing_frontmatter", excluded from indexing -- see
    # EXCLUDED_CONTENT_TYPES) instead of being glued onto whichever Item
    # comes first. Confirmed necessary: for a 10-Q, Item 1 is "Financial
    # Statements", so the old behavior made "Item 1" = cover page + table
    # of contents + the entire balance sheet/income statement/notes, one
    # giant generic parent that out-competed the real MD&A answer under
    # both dense and BM25 search (see EXCLUDED_CONTENT_TYPES comment for
    # the confirmed real-run evidence).
    frontmatter_text = text[: validated[0][0].start()]

    segments: dict[tuple[str, str], list[str]] = {}
    for i, (m, item_num, keyword) in enumerate(validated):
        start = m.start()  # always the item's own heading now -- frontmatter is split off above, not glued to item 0
        end = validated[i + 1][0].start() if i + 1 < len(validated) else len(text)
        segments.setdefault((item_num, keyword), []).append(text[start:end])

    parents = []
    total_captured = len(frontmatter_text)  # counted here so the coverage check below stays equivalent to the old behavior
    if frontmatter_text.strip():
        parents.append(
            {
                "parent_id": f"{source}::frontmatter",
                "text": frontmatter_text,
                "label": "(cover page / front matter)",
                "source": source,
                "content_type": "filing_frontmatter",
            }
        )
    for (item_num, keyword), candidates in segments.items():
        # Keep only the longest occurrence -- the real section body, not
        # the one-line Table of Contents mention of the same item.
        segment_text = max(candidates, key=len)
        total_captured += len(segment_text)
        label = f"Item {item_num}"
        parents.append(
            {
                "parent_id": f"{source}::{label}::{keyword}",
                "text": segment_text,
                "label": label,
                "source": source,
                "content_type": "filing_item",
            }
        )

    # ITEM_TITLE_KEYWORDS only covers 10-K/10-Q item schemes. If it
    # doesn't recognize enough of this filing's real headings (e.g. a
    # 20-F, which uses a completely different Item 1-19 scheme), a
    # partial split would silently drop or mislabel content -- a single
    # accurate whole-document parent is safer than a fast but wrong one.
    if len(segments) < MIN_VALID_ITEMS:
        return whole_doc_fallback
    if len(text) and total_captured / len(text) < MIN_COVERAGE_FRACTION:
        return whole_doc_fallback
    return parents


def _extract_speaker_names(text: str) -> list[str]:
    """Pull real speaker names out of a transcript's own "Call
    participants:" header (format: "- Title — Name" per line)."""
    header_match = SPEAKER_LIST_PATTERN.search(text)
    if not header_match:
        return []
    names = []
    for line in header_match.group(1).splitlines():
        line = line.strip().lstrip("-").strip()
        if not line:
            continue
        parts = re.split(r"[—-]\s*", line)  # "Title — Name" or "Title - Name"
        if len(parts) >= 2 and parts[-1].strip():
            names.append(parts[-1].strip())
    return names


def split_transcript_into_turns(doc: Document) -> list[dict]:
    """Split a .txt earnings-call transcript into speaker turns, one
    parent per contiguous block of one speaker's remarks. Only reliable
    for plaintext transcripts with this project's "Call participants:"
    header format (see split_into_parents for the .pdf fallback)."""
    text = doc.page_content
    source = doc.metadata.get("source", "unknown")
    names = _extract_speaker_names(text)
    if not names:
        return [
            {
                "parent_id": source,
                "text": text,
                "label": "(whole transcript, no speaker list found)",
                "source": source,
                "content_type": "transcript_body",  # unclassified fallback -- not excluded
            }
        ]

    escaped = sorted((re.escape(n) for n in names), key=len, reverse=True)
    turn_pattern = re.compile(rf"^({'|'.join(escaped)}):\s", re.MULTILINE)
    matches = list(turn_pattern.finditer(text))
    if not matches:
        return [
            {
                "parent_id": source,
                "text": text,
                "label": "(whole transcript, no turn markers matched)",
                "source": source,
                "content_type": "transcript_body",
            }
        ]

    parents = []
    # Item 8: the header/date/TAKEAWAYS/SUMMARY/INDUSTRY GLOSSARY preamble
    # before the first recognized speaker line is now its own parent
    # (content_type "transcript_preamble", excluded from indexing -- see
    # EXCLUDED_CONTENT_TYPES) instead of being glued onto turn 0. Confirmed
    # necessary: this preamble is exactly the bullet-point TAKEAWAYS
    # summary that a real run showed outranking the actual verbatim
    # transcript sentence for PANW (see EXCLUDED_CONTENT_TYPES comment for
    # the confirmed evidence). Previously noted (before this fix) that
    # ALAB's preamble alone was 34% of the file -- that's how much
    # summary/glossary text was riding along with turn 0's real remarks.
    preamble_text = text[: matches[0].start()]
    if preamble_text.strip():
        parents.append(
            {
                "parent_id": f"{source}::preamble",
                "text": preamble_text,
                "label": "(preamble: takeaways/summary/glossary)",
                "source": source,
                "content_type": "transcript_preamble",
            }
        )

    for i, m in enumerate(matches):
        start = m.start()  # always this speaker's own turn now -- preamble is split off above, not glued to turn 0
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        speaker = m.group(1)
        parents.append(
            {
                "parent_id": f"{source}::turn{i}::{speaker}",
                "text": text[start:end],
                "label": f"{speaker} (turn {i})",
                "source": source,
                "content_type": "transcript_body",
            }
        )
    return parents


def split_into_parents(documents: list[Document]) -> list[dict]:
    """Dispatch each loaded Document to the right parent-splitting
    strategy based on its source file type."""
    parents = []
    for doc in documents:
        source = doc.metadata.get("source", "")
        if source.endswith(".htm"):
            parents.extend(split_filing_into_items(doc))
        elif source.endswith(".txt"):
            parents.extend(split_transcript_into_turns(doc))
        else:
            # .pdf transcripts -- one parent per PDF page (PyMuPDFLoader
            # already gives one Document per page), same page-level
            # parent unit Session 7's own notebook uses.
            page = doc.metadata.get("page")
            parents.append(
                {
                    "parent_id": f"{source}::page{page}" if page is not None else source,
                    "text": doc.page_content,
                    "label": f"page {page}" if page is not None else "(whole document)",
                    "source": source,
                    "content_type": "filing_item",  # unclassified fallback -- not excluded; dead code against current data
                }
            )
    return parents


def _child_docs_fingerprint(child_docs: list[Document]) -> str:
    """Content fingerprint over exactly what would be embedded --
    catches a genuinely changed corpus (new/edited filing) OR a changed
    splitting/chunking rule (different child_docs even from the same raw
    documents), either of which must invalidate the cache. Sorted first
    so load-order nondeterminism (e.g. filesystem glob order) can't cause
    a spurious cache miss/rebuild for an unchanged corpus."""
    hasher = hashlib.sha256()
    hasher.update(EMBEDDING_MODEL.encode())
    for doc in sorted(child_docs, key=lambda d: (d.metadata.get("parent_id", ""), d.page_content)):
        hasher.update(doc.metadata.get("parent_id", "").encode())
        hasher.update(doc.page_content.encode("utf-8", errors="ignore"))
    return hasher.hexdigest()


def _load_or_build_vectorstore(child_docs: list[Document], embedding_model: OpenAIEmbeddings, cache_key: str | None):
    """No cache_key: original behavior, unchanged (in-memory, no
    persistence, always re-embeds). With a cache_key: reuse an on-disk
    Qdrant collection if its fingerprint matches this exact child_docs
    content; otherwise rebuild and persist. Any failure (most likely:
    another process already holds this ticker's on-disk lock -- see the
    module docstring above) falls back to the original in-memory build
    rather than crashing, so this is strictly additive."""
    if cache_key is None:
        return QdrantVectorStore.from_documents(
            documents=child_docs, embedding=embedding_model, location=":memory:", collection_name="parent_child_eval"
        )

    fingerprint = _child_docs_fingerprint(child_docs)
    collection_dir = os.path.join(EMBEDDING_CACHE_DIR, cache_key)
    fingerprint_path = os.path.join(EMBEDDING_CACHE_DIR, f"{cache_key}.fingerprint")

    try:
        cached_fingerprint = None
        if os.path.exists(fingerprint_path):
            with open(fingerprint_path) as f:
                cached_fingerprint = f.read().strip()

        if cached_fingerprint == fingerprint and os.path.isdir(collection_dir):
            client = QdrantClient(path=collection_dir)
            if client.collection_exists(QDRANT_COLLECTION_NAME):
                print(f"[embedding cache] HIT for {cache_key} -- reusing on-disk vectors, no OpenAI embedding calls made.")
                return QdrantVectorStore(client=client, collection_name=QDRANT_COLLECTION_NAME, embedding=embedding_model)
            client.close()

        # Cache miss (first time), or stale (corpus/chunking changed) --
        # wipe any old collection dir before rebuilding so it can't mix
        # old vectors with the new fingerprint file.
        os.makedirs(EMBEDDING_CACHE_DIR, exist_ok=True)
        if os.path.isdir(collection_dir):
            shutil.rmtree(collection_dir)
        print(f"[embedding cache] MISS for {cache_key} -- embedding via OpenAI and persisting to disk for next time.")
        vectorstore = QdrantVectorStore.from_documents(
            documents=child_docs,
            embedding=embedding_model,
            path=collection_dir,
            collection_name=QDRANT_COLLECTION_NAME,
        )
        with open(fingerprint_path, "w") as f:
            f.write(fingerprint)
        return vectorstore
    except Exception as e:  # noqa: BLE001 -- e.g. another process holds this ticker's on-disk lock right now
        print(
            f"!! WARNING: on-disk embedding cache unavailable for {cache_key} ({e}) -- "
            f"falling back to an in-memory build for this process (will re-embed via OpenAI). "
            f"Common cause: another process already has this ticker's cache directory open "
            f"(Qdrant local file mode allows only one process at a time per collection)."
        )
        return QdrantVectorStore.from_documents(
            documents=child_docs, embedding=embedding_model, location=":memory:", collection_name="parent_child_eval"
        )


def build_parent_child_retriever(documents: list[Document], cache_key: str | None = None):
    """Build a parent-child retriever over a ticker's already-loaded
    documents (see test_q1.load_ticker_documents).

    cache_key: pass the ticker (e.g. "ALAB") to persist/reuse this
    ticker's child-chunk embeddings on local disk across separate process
    runs -- see the EMBEDDING_CACHE_DIR block above for why this exists
    and its disclosed limitations. Omit it (default) for the original
    in-memory, no-persistence behavior.

    Returns a callable: query(question: str, k: int = 5) -> list[Document],
    each returned Document being a full parent (Item / speaker turn / PDF
    page), not a raw 512-token fragment."""
    parents = split_into_parents(documents)
    parents_by_id = {p["parent_id"]: p for p in parents}

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHILD_CHUNK_SIZE, chunk_overlap=CHILD_CHUNK_OVERLAP, length_function=_tiktoken_len
    )
    child_docs = []
    excluded_parent_count = 0
    for parent in parents:
        # Item 8: hard-exclude at the indexing stage, not just at rerank
        # time -- these parents are never chunked or embedded, so they
        # can't consume any of the CHILD_SEARCH_K candidate slots under
        # either dense or BM25 search, and no embedding cost is spent on
        # them. See EXCLUDED_CONTENT_TYPES above for what's excluded and why.
        if parent.get("content_type") in EXCLUDED_CONTENT_TYPES:
            excluded_parent_count += 1
            continue
        for chunk_text in splitter.split_text(parent["text"]):
            child_docs.append(
                Document(
                    page_content=chunk_text,
                    metadata={"parent_id": parent["parent_id"], "source": parent.get("source", "unknown")},
                )
            )
    if excluded_parent_count:
        print(
            f"[content-type filter] excluded {excluded_parent_count} parent(s) "
            f"(transcript_preamble/filing_frontmatter) from indexing."
        )

    embedding_model = OpenAIEmbeddings(model=EMBEDDING_MODEL)
    vectorstore = _load_or_build_vectorstore(child_docs, embedding_model, cache_key)
    child_retriever = vectorstore.as_retriever(search_kwargs={"k": CHILD_SEARCH_K})

    # Item 7: RRF first-stage fusion, built once per ticker alongside the
    # dense vectorstore above -- NOT rebuilt per query (see
    # _bm25_rank_children below). Root cause this fixes, confirmed live
    # against DELL's "this quarter's gross margin change" (2026-07-26,
    # check_dell_panw_retrieval.py): the exact answer sentence
    # ("Gross margin dollars increased 18% to $6,800,000,000... 20.5%...")
    # exists verbatim in Data/DELL/transcript_latest.txt, but dense
    # embedding search's own top 15 never included it at all -- Cohere
    # rerank never got the chance to rank it, because it was never in the
    # candidate pool to begin with. Exact numeric figures like this are
    # exactly what BM25's lexical/term-frequency scoring is built to
    # catch and dense embeddings can blur among topically-similar
    # passages. This is a genuinely different failure mode from PANW's
    # (confirmed same day: PANW's target chunk WAS in dense search's top
    # 15, at position 4, but Cohere's rerank still didn't put it in the
    # final top 5 -- a reranking problem, not a retrieval one, tracked
    # separately via a query-wording fix, not RRF).
    bm25_corpus_tokens = [doc.page_content.lower().split() for doc in child_docs]
    bm25_index = BM25Okapi(bm25_corpus_tokens)

    def _bounded_parent_text(parent_text: str, matched_child_text: str) -> str:
        """Return parent_text unchanged if it's under MAX_PARENT_CHARS.
        Otherwise, return a PARENT_WINDOW_MARGIN-sized window centered on
        wherever matched_child_text actually occurs in it -- the specific
        passage that made this parent match at all -- rather than the
        full section (see MAX_PARENT_CHARS above for why)."""
        if len(parent_text) <= MAX_PARENT_CHARS:
            return parent_text
        match_pos = parent_text.find(matched_child_text)
        if match_pos == -1:
            # Exact substring not found (can happen if the splitter's
            # child text doesn't line up byte-for-byte, e.g. a subtle
            # whitespace difference) -- fall back to the start of the
            # section rather than guessing where the match was.
            return parent_text[:MAX_PARENT_CHARS] + " [...truncated, showing start of section...]"
        start = max(0, match_pos - PARENT_WINDOW_MARGIN)
        end = min(len(parent_text), match_pos + len(matched_child_text) + PARENT_WINDOW_MARGIN)
        prefix = "[...truncated...] " if start > 0 else ""
        suffix = " [...truncated...]" if end < len(parent_text) else ""
        return prefix + parent_text[start:end] + suffix

    def _bm25_rank_children(question: str, k: int) -> list[Document]:
        """First-stage sparse/lexical search over the FULL child corpus
        for this ticker, using the bm25_index built once above (not
        rebuilt per query -- only the query-vs-index scoring is redone
        each call, which is cheap, local, CPU-only arithmetic). NOT the
        same thing as _bm25_fallback_rerank below, which only reranks an
        already-retrieved candidate set as a Cohere-outage fallback --
        this runs independently over every child chunk, exactly parallel
        to child_retriever's dense search, so the two can be fused."""
        scores = bm25_index.get_scores(question.lower().split())
        ranked_indices = sorted(range(len(child_docs)), key=lambda i: scores[i], reverse=True)
        return [child_docs[i] for i in ranked_indices[:k]]

    def retrieve(question: str, k: int = 5) -> list[Document]:
        """Item 7: fuses two independently-ranked first-stage searches --
        dense/semantic (child_retriever) and sparse/lexical (BM25, via
        _bm25_rank_children) -- via Reciprocal Rank Fusion (RRF) before
        deduping to unique parents and ranking those parents against the
        actual query with Cohere Rerank. RRF doesn't replace Cohere's
        reranking; it fixes what Cohere gets to choose FROM -- a
        candidate a document that dense search alone would never surface
        (confirmed live for DELL, see the bm25_index comment above) gets
        a real chance to reach Cohere instead of being silently absent
        from the pool the whole time.

        This replaces the retired prefer_source_suffixes hand-coded rule
        (see git history), which only reordered by source-file type and
        was, in the PRD's own words, "set by hand for one known question"
        (Q1 case 1, ALAB's gross-margin driver). This generalizes past
        that one case instead of encoding it."""
        dense_hits = child_retriever.invoke(question)
        bm25_hits = _bm25_rank_children(question, CHILD_SEARCH_K)
        fused_children = _reciprocal_rank_fusion(
            [dense_hits, bm25_hits],
            key_fn=lambda d: (d.metadata.get("parent_id", ""), d.page_content),
        )

        seen: set[str] = set()
        candidates = []
        for child in fused_children:
            pid = child.metadata["parent_id"]
            if pid in seen:
                continue
            seen.add(pid)
            parent = parents_by_id[pid]
            candidates.append(
                Document(
                    page_content=_bounded_parent_text(parent["text"], child.page_content),
                    metadata={
                        "source": parent.get("source", "unknown"),
                        "label": parent.get("label", ""),
                        "parent_id": pid,
                        "content_type": parent.get("content_type", ""),
                    },
                )
            )

        return _rerank(question, candidates, k)

    return retrieve


RRF_K = 60  # standard constant from the original RRF paper (Cormack, Clarke & Buettcher 2009) --
            # not tuned for this project specifically, used as published.


def _reciprocal_rank_fusion(ranked_lists: list[list[Document]], key_fn, k: int = RRF_K) -> list[Document]:
    """Fuses N independently-ranked lists into one combined ranking:
    score(doc) = sum, over every list it appears in, of 1 / (k + rank),
    rank being 1-indexed position in that list. A document near the top
    of ANY one list scores highly even if it's absent from every other
    list -- this is what lets a BM25-only hit surface into the final
    candidate pool without dense search ever needing to find it itself
    (and vice versa). key_fn identifies "the same document" across
    lists -- object identity isn't reliable here since dense and BM25
    search return separate Document instances over the same underlying
    text, so retrieve() keys on (parent_id, page_content) instead."""
    scores: dict = {}
    doc_by_key: dict = {}
    for ranked_list in ranked_lists:
        for rank, doc in enumerate(ranked_list, start=1):
            key = key_fn(doc)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            doc_by_key.setdefault(key, doc)
    fused_keys = sorted(scores, key=lambda kk: scores[kk], reverse=True)
    return [doc_by_key[kk] for kk in fused_keys]


def _bm25_fallback_rerank(question: str, candidates: list[Document], k: int) -> list[Document]:
    """Free, local, no-network reranking fallback -- the same rank_bm25
    library Session 07's own notebook uses for lexical retrieval,
    repurposed here as a fallback SCORER over an already-retrieved
    candidate set rather than a first-stage retriever. Used only when
    Cohere is unavailable, so search_filings still returns
    relevance-ranked results during a Cohere outage instead of silently
    reverting to raw similarity order -- the exact ranking behavior
    already found broken on a real case (ALAB's gross-margin question,
    see retrieve()'s docstring)."""
    tokenized_corpus = [doc.page_content.lower().split() for doc in candidates]
    bm25 = BM25Okapi(tokenized_corpus)
    scores = bm25.get_scores(question.lower().split())
    ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
    return [doc for doc, _ in ranked[:k]]


def _rerank(question: str, candidates: list[Document], k: int) -> list[Document]:
    """Cohere Rerank as the primary ranking signal. One retry on failure
    (network blip, rate limit) before falling back to a local BM25
    rerank (_bm25_fallback_rerank) rather than raw similarity order --
    fails open (search_filings still returns something) but doesn't
    silently regress to the pre-item-4 ranking quality. Any Cohere/
    network exception is caught here, not raised, so one flaky rerank
    call can't take down the whole tool."""
    if not candidates:
        return candidates
    compressor = CohereRerank(model=RERANK_MODEL, top_n=k)
    last_error: Exception | None = None
    for attempt in range(RERANK_MAX_RETRIES + 1):
        try:
            return list(compressor.compress_documents(documents=candidates, query=question))
        except Exception as e:  # noqa: BLE001 -- any failure here falls back, doesn't crash the tool
            last_error = e
            if attempt < RERANK_MAX_RETRIES:
                time.sleep(1)
    print(
        f"!! WARNING: Cohere rerank failed after {RERANK_MAX_RETRIES + 1} attempt(s) "
        f"({last_error}) -- falling back to local BM25 rerank for this query."
    )
    return _bm25_fallback_rerank(question, candidates, k)
