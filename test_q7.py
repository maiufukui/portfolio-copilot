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
from collections import OrderedDict

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


# Cap applied after dedup, before the synthesis call -- a real fix for a
# recurring RAGAS-judge TimeoutError on 3/3 Q5 eval runs (never on Q1),
# tied to one case matching up to 87 raw snippets in a single synthesis
# call (see eval_dataset.json Q5's deferred_reason). Deduping usually
# gets well under this on its own since risk-factor boilerplate repeats
# near-verbatim across a 10-K/10-Q/8-K; this is the backstop for
# whatever's left after that.
MAX_SUMMARY_EXCERPTS = 40


def dedupe_hits(hits: list[dict]) -> list[dict]:
    """Collapse hits with identical (whitespace-normalized) snippet text
    into one entry, recording every (source, page) location it appears
    at.

    This directly targets the other half of the same eval_dataset.json
    Q5 bug: the customer-concentration case scored faithfulness 0.0
    because SUMMARY_PROMPT called a boilerplate sentence "substantive" --
    but the written reference for that case says the identical sentence
    appears once in the 10-K and once in the 10-Q. Raw find_hits() output
    sends that sentence to the LLM twice with no signal they're the same
    text; deduping surfaces the repeat count explicitly, which is itself
    evidence of boilerplate (see SUMMARY_PROMPT below)."""
    grouped: "OrderedDict[str, dict]" = OrderedDict()
    for h in hits:
        key = " ".join(h["snippet"].split())
        if key not in grouped:
            grouped[key] = {"keyword": h["keyword"], "snippet": h["snippet"], "locations": []}
        grouped[key]["locations"].append({"source": h["source"], "page": h.get("page")})
    return list(grouped.values())[:MAX_SUMMARY_EXCERPTS]


def count_raw_hits_by_keyword(hits: list[dict], keywords: list[str]) -> str:
    """Total raw (pre-dedup) hit count per keyword, formatted for the
    synthesis prompt.

    Written reference answers in eval_dataset.json are authored from raw
    filing counts (e.g. "72 'demand' mentions"), not deduped ones. The
    dedup fix (above) is correct for boilerplate judgment -- an LLM
    should see one copy of a repeated sentence, not five -- but it also
    silently changed how many mentions the model itself was reporting,
    which tanked factual_correctness on the capacity/demand case (0.13)
    even though nothing the model said was actually wrong: it was just
    counting the deduped set, not the real one.

    Rather than rewrite the reference to match deduped counts (that would
    make the reference describe the pipeline's internal representation
    instead of what's actually in the filings -- the wrong thing for a
    reference answer to track), this keeps the reference as real ground
    truth and has the response state the same raw totals explicitly,
    ahead of its deduped qualitative breakdown."""
    counts = {kw: 0 for kw in keywords}
    for h in hits:
        if h["keyword"] in counts:
            counts[h["keyword"]] += 1
    return ", ".join(f"{n} raw mention(s) of '{kw}'" for kw, n in counts.items())


def format_single_hit(g: dict) -> str:
    """Format one deduped excerpt group, including its location metadata
    (which filing(s)/page(s) it appears in) inline in the text -- not
    just the snippet.

    This is used both as one entry of hits_text (what the synthesis LLM
    reads) AND, unchanged, as one entry of the retrieved_contexts list
    handed to RAGAS for faithfulness scoring in run_eval.py. Those two
    were previously different: the LLM saw location info (via the old
    inline format_grouped_hits loop) but retrieved_contexts only ever got
    the bare snippet text. That mismatch is exactly why the
    customer-concentration case's faithfulness kept coming in low across
    multiple runs -- the response correctly states which filings an
    excerpt appears in (a true claim, visible to the LLM), but RAGAS's
    judge has no way to verify that claim against context that never
    contained it, so a true statement scored as unfaithful. Giving RAGAS
    the exact same text the LLM was given makes every grounded claim
    actually checkable."""
    locs = ", ".join(
        f"{loc['source']}" + (f" p.{loc['page']}" if loc.get("page") is not None else "")
        for loc in g["locations"]
    )
    return (
        f"Excerpt (appears verbatim in {len(g['locations'])} filing location(s): {locs})\n"
        f"Matched: {g['keyword']}\nExcerpt: ...{g['snippet']}..."
    )


def format_grouped_hits(grouped: list[dict]) -> str:
    """Shared hits_text formatting for the synthesis prompt -- used by
    both this script's --summarize path and run_eval.py's run_rag_q5, so
    manual CLI testing and the eval harness send the LLM the same shape
    of input rather than two silently-different formats of the same
    prompt."""
    return "\n\n".join(format_single_hit(g) for g in grouped)


SUMMARY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "Below are deduplicated, verbatim, keyword-matched excerpts found "
            "across {ticker}'s filings for the term(s): {keywords}. Do not "
            "add, infer, or paraphrase beyond what's here — organize these "
            "into a clean, cited list grouped by source document.\n\n"
            "Start your response with one line stating the RAW total mention "
            "count(s), exactly as given here: {raw_counts}. This is the true "
            "total occurrence count across all filings, counted before the "
            "excerpts below were deduplicated for classification -- state it "
            "verbatim, don't recompute it from the deduplicated excerpt list "
            "below, which intentionally undercounts repeats.\n\n"
            "Then classify each excerpt as BOILERPLATE or SUBSTANTIVE using these "
            "concrete rules, not a general impression:\n"
            "- BOILERPLATE: appears verbatim in 2 or more filing locations "
            "(shown for each excerpt below), uses hedge language ('may', "
            "'could', 'might', 'depend on'), and names no specific customer, "
            "percentage, dollar figure, or date. Standard risk-factor "
            "language a company repeats filing after filing counts as "
            "boilerplate even the first time you see it, if it's generic.\n"
            "- SUBSTANTIVE: names a specific figure (a dollar amount, "
            "percentage, customer, or date) or describes something as a "
            "current-period event or result rather than a standing, "
            "hypothetical risk — even if it only appears once.\n\n"
            "The verbatim-repeat count given for each excerpt is strong "
            "evidence toward BOILERPLATE, but a single-location excerpt "
            "with a concrete figure is still SUBSTANTIVE — judge by content, "
            "the repeat count is a signal, not the only rule.\n\n"
            # A prior version of this prompt required an explicit
            # present/absent justification for every individual excerpt.
            # Reverted -- verified via run_eval.py --question 5 --verbose
            # that it made the model over-classify generic marketing/
            # industry-description language as SUBSTANTIVE (crashed
            # capacity/demand's factual_correctness 0.98 -> 0.11), a
            # worse regression than the faithfulness gap it was meant to
            # close. The real faithfulness gap is fixed differently now,
            # via format_single_hit above (RAGAS's retrieved_contexts and
            # the LLM's input are the same text, not two different views).
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
        grouped = dedupe_hits(hits)
        print(f"\n{len(hits)} raw match(es) -> {len(grouped)} unique excerpt(s) after dedup "
              f"(sent to the synthesis call, capped at {MAX_SUMMARY_EXCERPTS}).")
        hits_text = format_grouped_hits(grouped)
        raw_counts = count_raw_hits_by_keyword(hits, args.keyword)
        summary = chain.invoke(
            {
                "ticker": args.ticker,
                "keywords": ", ".join(args.keyword),
                "hits": hits_text,
                "raw_counts": raw_counts,
            }
        )
        print("\n" + "=" * 60)
        print(summary)
        print("=" * 60)


if __name__ == "__main__":
    main()
