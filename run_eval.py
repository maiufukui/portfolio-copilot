"""
Eval harness runner for the Personal Portfolio Copilot capstone.

Reads eval_dataset.json (11 locked questions -- ids 1-9, 11-12; id 10 was
retired, id 13 removed 2026-07-27 along with the since-purchase-comparison
use case it tested -- see the PRD's Task 1 §4) and runs each one that's
actually buildable today against real pipelines, scoring with the two
methods described in the PRD (Task 1 §4, "Evaluation methodology"):

- RAG questions (category "rag"): scored with RAGAS -- Faithfulness always,
  plus LLMContextRecall and FactualCorrectness when a test case has a
  written `reference` answer to compare against. Pattern per
  Session 6 (Agentic RAG Evaluation) / Session 10 (LLM Servers run_eval.py):
  SingleTurnSample -> EvaluationDataset -> ragas.evaluate().

- Tool-calling questions (category "tool_calling" / "hybrid"): NOT scored
  by this file directly -- it prints a pointer to each question's own
  standalone test_qN.py script for manual review, rather than duplicating
  that scoring logic here. As of this session, two of those scripts
  (test_q9.py, test_q11.py) score real RAGAS multi-turn
  metrics -- ToolCallAccuracy and AgentGoalAccuracyWithReference (see
  eval_tool_call_accuracy.py) -- against the real deployed LangGraph
  agent (app/graph.py), plus a custom PASS/FAIL LLM-judge prompt for
  criteria RAGAS doesn't cover (source coverage, citation quality, etc).
  The remaining built tool-calling questions (Q2, Q4, Q6, Q7, Q8) still
  use ONLY a hand-written PASS/FAIL judge prompt, not RAGAS's real
  metric classes -- a known gap, tracked in the PRD's Open Items, not
  yet closed for those five. Run the script named in each question's
  "reuses" field directly to see its actual scoring.

Only questions with status "built" in eval_dataset.json are run at all.
"not_built" / "partially_built" / "deferred" questions are listed but
skipped, with the reason printed -- see eval_dataset.json's "reuses" field
per question for exactly what's missing.

For a single consolidated run across every question that has ANY real
automated scoring (RAG questions here plus Q7/Q9/Q11's tool-calling
scoring), persisted to one JSON scorecard instead of scattered stdout
across 8 separate scripts, see run_scorecard.py -- it imports this
file's RAG_RUNNERS directly rather than duplicating this scoring logic.

Usage:
    python run_eval.py                        # run everything runnable
    python run_eval.py --question 1           # run just question id 1
    python run_eval.py --question 5 --verbose # + full input/context/response/reference per case
    python run_eval.py --list                 # print status of all 11, run nothing
"""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from ragas import EvaluationDataset, evaluate
from ragas.dataset_schema import SingleTurnSample
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import FactualCorrectness, Faithfulness, LLMContextRecall

from test_q1 import load_ticker_documents
from test_q7 import count_raw_hits_by_keyword, dedupe_hits, find_hits, format_grouped_hits, format_single_hit, SUMMARY_PROMPT

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()

DATASET_PATH = os.path.join(os.path.dirname(__file__), "eval_dataset.json")

evaluator_llm = LangchainLLMWrapper(ChatOpenAI(model="gpt-4.1-mini", temperature=0))
answer_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)


def load_dataset() -> dict:
    with open(DATASET_PATH) as f:
        return json.load(f)


# Per-ticker retriever cache, scoped to a single run_eval.py process. Q1 now
# has 2 test cases per ticker (8 -> 12 once PANW/DELL were added), and
# run_rag_q1 was calling build_retriever (full re-embed of that ticker's
# documents via text-embedding-3-small, no persistence) once per TEST CASE,
# not once per ticker -- so every ticker's corpus was being embedded twice in
# one run. That redundant 2x is what pushed a real run into OpenAI's
# embeddings TPM rate limit (429) for the first time after the new test
# cases were added. Cached only for the lifetime of this process (matches
# the underlying retriever's own in-memory/ephemeral design, doesn't add a
# new persistence layer or change what's tested).
#
# Item 4 (2026-07-25): swapped build_retriever (flat baseline) for
# build_parent_child_retriever -- this eval was silently still scoring the
# retired retriever after app/tools.py's search_filings moved to the new
# parent-child + Cohere rerank one. Without this, Q1's RAGAS scores would
# measure a retriever the live agent no longer uses.
#
# CHANGED 2026-07-28 (Maiu, real bug found in a live run_scorecard.py
# run): this used to maintain its own separate, process-lifetime
# _retriever_cache dict here, calling build_parent_child_retriever
# directly with cache_key=ticker -- a SECOND on-disk Qdrant cache,
# independent of app/tools.py's own _RETRIEVER_CACHE (used by the live
# agent's search_filings tool), even though both point at the exact same
# on-disk directory per ticker (parent_child_retriever.EMBEDDING_CACHE_DIR
# / ticker). Qdrant's local (path=) mode takes an exclusive file lock on
# that directory -- fine across two separate PROCESSES (falls back to an
# in-memory rebuild, by design, see parent_child_retriever.py's own
# comment), but this was two independent caches racing for the same lock
# WITHIN one process: run_scorecard.py runs Q1's RAG scoring first
# (populating this cache, never released), then any live-agent question
# (Q3/Q7/Q9/Q10) calls search_filings -> app.tools.get_retriever, which
# tried to open the SAME ticker's directory again and lost the race --
# confirmed via a real run's own printed warnings for every ticker
# touched after Q1 ("already accessed by another instance of Qdrant
# client... falling back to an in-memory build... will re-embed via
# OpenAI"), a real, avoidable extra OpenAI cost every time. Fixed by
# delegating to app.tools.get_retriever directly -- one retriever, one
# open Qdrant client, per ticker, per process, shared by RAG scoring and
# the live agent both. app.tools.get_retriever has its own bounded LRU
# cache (_RETRIEVER_CACHE, app/tools.py), so this still only builds each
# ticker's retriever once per process, same guarantee the old
# _retriever_cache gave, just via a single shared cache instead of two.
def _get_cached_retriever(ticker: str):
    from app.tools import get_retriever

    return get_retriever(ticker)


# ---------------------------------------------------------------------------
# RAG question runners -- one function per question id, each returns a
# SingleTurnSample-shaped dict: {"retrieved_contexts": [...], "response": str}
# ---------------------------------------------------------------------------

Q1_DRIVER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "human",
            "You are a portfolio-monitoring assistant. Using ONLY the context "
            "below (pulled from {ticker}'s SEC filings and latest earnings "
            "call transcript), answer: how does {ticker}'s {metric} guidance "
            "for next quarter compare to what they just reported?\n\n"
            "Lead with the exact figure management cited for the MOST RECENTLY "
            "REPORTED quarter -- percentage, basis points, or dollar amount, "
            "exactly as stated in the source -- then state the exact guidance "
            "figure(s) for next quarter, then explain in one sentence whether "
            "guidance represents an improvement, a step down, or roughly flat "
            "versus the reported result, and why (the driver management cited "
            "for that trajectory). Do not blend or substitute a different "
            "period's figures. If the context only addresses one of the two "
            "periods, say explicitly which one is missing rather than "
            "guessing.\n\n"
            "CONTEXT:\n{context}\n\n"
            "Respond in this exact format, with no extra commentary:\n"
            "This quarter: [exact figure(s) actually reported]\n"
            "Next quarter guidance: [exact figure(s) guided]\n"
            "Comparison: [one sentence: better / worse / flat, and the driver cited for the guidance]\n"
            "Source: [1-2 short quotes -- whichever sentence(s) actually support the answer]",
        )
    ]
)


def run_rag_q1(case: dict) -> dict:
    """Q1: comparison question (this quarter's actual vs. next quarter's
    guidance, same metric). Vector retrieval. Rewritten 2026-07-28 (Maiu,
    explicit call) from a single backward-OR-forward driver-identification
    question into a backward-AND-forward comparison -- see eval_dataset.json
    id 1's reuses field for the full rationale. Replaces the retired
    thesis-comparison version -- thesis is no longer a product concept, so
    this doesn't use test_q1.py's original Verdict/Evidence/Explanation
    prompt, only its generic load/retrieve utilities."""
    retriever = _get_cached_retriever(case["ticker"])
    # "...prefer the exact verbatim sentence..." (2026-07-26): confirmed
    # necessary against a real case (PANW's Q4 revenue/margin outlook) --
    # check_dell_panw_retrieval.py showed the correct chunk WAS retrieved
    # (dense search's top 15, position 4), but Cohere's rerank still
    # ranked a bullet-point "TAKEAWAYS" summary and a metadata/summary
    # preamble above it in the final top 5. Different failure mode from
    # DELL's (RRF fixes that one, see parent_child_retriever.py) -- this
    # one is Cohere ranking a paraphrased restatement over the actual
    # verbatim management quote, addressed here by giving the reranker an
    # explicit signal to prefer, not by widening top_n.
    #
    # Query and k both widened 2026-07-28 for the comparison rewrite: the
    # old query scoped retrieval to ONLY "the most recently reported
    # quarter, not the full fiscal year," which was correct for the old
    # single-period question but would actively work against this one --
    # the new question needs BOTH the reported-quarter figure AND the
    # guidance figure to come back, not just one. k raised 5 -> 6 to give
    # the second period's passage a real chance to make the cut alongside
    # the first, a modest increase weighed against MAX_PARENT_CHARS's own
    # over-stuffed-context concern (parent_child_retriever.py), not an
    # unbounded widening.
    query = (
        f"How does {case['ticker']}'s {case['metric']} guidance for next quarter compare to what they "
        f"just reported? Retrieve both: the actual {case['metric']} figure from the most recently "
        f"reported quarter, and any forward guidance for {case['metric']} in the next quarter. Prefer "
        f"the exact verbatim sentence from management's spoken remarks over any bullet-point summary, "
        f"headline takeaway, or restated figure elsewhere in the source."
    )
    retrieved_docs = retriever(query, k=6)
    contexts = [d.page_content for d in retrieved_docs]

    chain = Q1_DRIVER_PROMPT | answer_llm | StrOutputParser()
    response = chain.invoke(
        {
            "ticker": case["ticker"],
            "metric": case["metric"],
            "context": "\n\n---\n\n".join(contexts),
        }
    )
    return {"retrieved_contexts": contexts, "response": response, "user_input": query}


def run_rag_q5(case: dict) -> dict:
    """Q5: exhaustive-recall check, phrased as a real question ("has there
    been any recent X") rather than a literal "show me every mention
    verbatim" instruction -- the agent has to recognize on its own that
    the question demands complete recall and route to keyword/exact-match
    search rather than lossy top-k vector retrieval. Retrieval itself is
    still deterministic regex (find_hits), now supporting multiple
    keywords per case (e.g. "capacity" OR "demand" for one question).

    The RAW hit dump used to be handed straight to RAGAS as the
    "response", while references are dense, structured summaries (exact
    counts per source, grouped themes). That format mismatch was tanking
    factual_correctness even when nothing was factually wrong (see the
    0.39 "demand" score from the earlier version). Fixed by running the
    same LLM synthesis pass test_q7.py already exposes via --summarize
    (SUMMARY_PROMPT) before scoring, so response and reference are
    comparable in structure, not just in content.

    Raw hits are deduped (dedupe_hits) before both the synthesis call and
    the RAGAS retrieved_contexts -- fixes the two problems noted in
    eval_dataset.json Q5's deferred_reason: the customer-concentration
    case's 0.0 faithfulness (SUMMARY_PROMPT had no signal that its two
    hits were the same sentence repeated verbatim across the 10-K/10-Q)
    and the recurring judge timeout on 3/3 Q5 runs (tied to up to 87 raw
    snippets in one synthesis call -- deduping plus the dedupe cap in
    test_q7.py bounds that).

    A verified re-run of this fix (faithfulness 0.0 -> 0.667 on the
    customer-concentration case, no timeout) surfaced a second, distinct
    issue: factual_correctness on the capacity/demand case dropped to
    0.13, because the written reference states RAW mention counts ("72
    'demand' mentions") but the deduped response was implicitly counting
    the deduped set instead. count_raw_hits_by_keyword feeds the true raw
    count into the prompt so the response states the same total the
    reference does, while the deduped excerpts underneath are still used
    for the (correct) boilerplate/substantive judgment -- both numbers
    are now real, not one overwriting the other.

    retrieved_contexts uses format_single_hit (same text the LLM itself
    reads via format_grouped_hits/hits_text), not bare snippet text --
    an earlier version used [g["snippet"] for g in grouped], which
    stripped out the location metadata (which filing(s) an excerpt
    appears in). The response correctly states that metadata (it's true,
    the LLM was given it), but RAGAS's faithfulness judge had no way to
    verify a claim against context that never contained it, so a true
    statement scored as unfaithful. Now the judge sees exactly what the
    LLM saw."""
    documents = load_ticker_documents(case["ticker"])
    keywords = case.get("keywords") or [case.get("keyword")]
    hits = find_hits(documents, keywords)
    query = case.get("question") or f"Has there been any recent {' or '.join(keywords)} mentioned in {case['ticker']}'s filings?"

    if not hits:
        return {
            "retrieved_contexts": ["No verbatim matches found."],
            "response": "No matches found for any of the searched terms.",
            "user_input": query,
        }

    grouped = dedupe_hits(hits)
    contexts = [format_single_hit(g) for g in grouped]
    hits_text = format_grouped_hits(grouped)
    raw_counts = count_raw_hits_by_keyword(hits, keywords)
    chain = SUMMARY_PROMPT | answer_llm | StrOutputParser()
    response = chain.invoke(
        {
            "ticker": case["ticker"],
            "keywords": ", ".join(keywords),
            "hits": hits_text,
            "raw_counts": raw_counts,
        }
    )
    return {"retrieved_contexts": contexts, "response": response, "user_input": query}


RAG_RUNNERS = {
    1: run_rag_q1,
    5: run_rag_q5,
}


def score_rag_question(question: dict, verbose: bool = False) -> None:
    qid = question["id"]
    runner = RAG_RUNNERS.get(qid)
    if runner is None:
        print(f"  [Q{qid}] SKIPPED -- no runner wired up yet ({question['reuses']})")
        return

    samples = []
    labels = []  # parallel to samples -- lets us tell rows apart in the per-row breakdown
    metrics_note = []
    for case in question["test_cases"]:
        result = runner(case)
        reference = case.get("reference")
        has_reference = bool(reference) and reference != "TBD -- fill in against MRVL transcript."
        label = case.get("metric") or ", ".join(case.get("keywords", [])) or case.get("keyword") or case.get("ticker") or "case"

        if verbose:
            # Same shape as the coursework homework printouts (and
            # test_q1.py/test_q7.py elsewhere in this repo): full input,
            # every retrieved chunk, the full generated response, and the
            # reference it's being judged against -- not just a score.
            # This is what lets a bad score be diagnosed instead of just
            # trusted or distrusted blindly.
            print("\n" + "=" * 70)
            print(f"[Q{qid}] case: {label}")
            print("=" * 70)
            print(f"Input:\n{result['user_input']}\n")
            print(f"Retrieved context(s) ({len(result['retrieved_contexts'])}):")
            for i, ctx in enumerate(result["retrieved_contexts"], 1):
                print(f"  [{i}] {ctx}")
            print(f"\nResponse:\n{result['response']}\n")
            print(f"Reference{' (none written yet)' if not has_reference else ''}:\n{reference if has_reference else '(none -- response is compared to itself; only Faithfulness is meaningful)'}")
            print("=" * 70)

        samples.append(
            SingleTurnSample(
                user_input=result["user_input"],
                retrieved_contexts=result["retrieved_contexts"],
                response=result["response"],
                reference=reference if has_reference else result["response"],
                # ^ when no real reference exists yet, RAGAS still needs a
                # non-null reference field to not crash on LLMContextRecall/
                # FactualCorrectness -- see the has_reference gate below,
                # which is what actually controls whether those metrics run.
            )
        )
        labels.append(label)
        metrics_note.append(has_reference)

    metrics = [Faithfulness(llm=evaluator_llm)]
    if any(metrics_note):
        metrics += [LLMContextRecall(llm=evaluator_llm), FactualCorrectness(llm=evaluator_llm)]
    else:
        print(f"  [Q{qid}] note: no written `reference` in test_cases yet -- "
              f"only Faithfulness is meaningful; LLMContextRecall/FactualCorrectness "
              f"need a reference answer to compare against and are skipped.")

    dataset = EvaluationDataset(samples=samples)
    print(f"  [Q{qid}] scoring {len(samples)} test case(s) with RAGAS ({[m.name for m in metrics]})...")
    scores = evaluate(dataset, metrics=metrics)
    print(scores)

    # Per-row breakdown -- the aggregate print above is a mean across test
    # cases, which hides whether any individual sample errored (e.g. a
    # timed-out judge call) or came back as NaN. This shows exactly what
    # each test case scored on each metric, so a bad aggregate can be
    # traced back to a specific row instead of trusted blindly.
    import pandas as pd
    pd.set_option("display.max_colwidth", 40)
    pd.set_option("display.width", 160)
    df = scores.to_pandas()
    df.insert(0, "case", labels[: len(df)])
    metric_cols = [c for c in df.columns if c not in ("case", "user_input", "retrieved_contexts", "response", "reference")]
    print(f"\n  [Q{qid}] per-row breakdown:")
    print(df[["case"] + metric_cols].to_string(index=False))
    nan_rows = df[df[metric_cols].isna().any(axis=1)]
    if not nan_rows.empty:
        print(f"  [Q{qid}] WARNING: {len(nan_rows)} row(s) have a NaN metric -- "
              f"likely the timed-out job. That row is silently excluded from "
              f"the aggregate mean above, not counted as a failure or a zero.")


# ---------------------------------------------------------------------------
# Tool-calling questions -- not scored by this file directly. The real
# LangGraph agent (app/graph.py) exists and is what these questions run
# against, but this runner points to each question's own standalone
# test_qN.py script rather than re-implementing its scoring here. Q9/Q11's
# scripts score real RAGAS ToolCallAccuracy + AgentGoalAccuracy
# WithReference (eval_tool_call_accuracy.py) plus a custom judge; Q2/Q4/
# Q6/Q7/Q8's scripts still use only a custom PASS/FAIL judge prompt (Open
# Items gap, not yet closed for those five).
# ---------------------------------------------------------------------------

def run_tool_question(question: dict) -> None:
    qid = question["id"]
    print(f"  [Q{qid}] tool-calling/hybrid question -- not scored by run_eval.py "
          f"directly. Run its own standalone script for real scoring. "
          f"Reuses: {question['reuses']}")


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", type=int, help="Run only this question id.")
    parser.add_argument("--list", action="store_true", help="Print status of all questions, run nothing.")
    parser.add_argument("--verbose", action="store_true", help="Print full input/retrieved-context/response/reference per test case, not just scores.")
    args = parser.parse_args()

    data = load_dataset()
    questions = data["questions"]

    if args.list:
        print(f"{'ID':<4}{'Category':<18}{'Status':<18}Reuses")
        for q in questions:
            print(f"{q['id']:<4}{q['category']:<18}{q['status']:<18}{q['reuses']}")
        return

    if args.question:
        questions = [q for q in questions if q["id"] == args.question]
        if not questions:
            raise SystemExit(f"No question with id {args.question}")

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    print("=" * 70)
    print("Personal Portfolio Copilot -- Eval Harness")
    print("=" * 70)

    for q in questions:
        print(f"\nQ{q['id']}: {q['question_template']}")
        # --question N is an explicit, targeted request -- run it regardless
        # of status. The default "run everything runnable" pass still skips
        # deferred/not_built questions (no --question given), since those
        # aren't meant to run unattended. This is what lets a "deferred
        # pending a fix" question (e.g. Q5, see eval_dataset.json) actually
        # be re-tested via `--question 5 --verbose` once the fix is in,
        # without having to hand-edit the dataset's status field first just
        # to test whether the fix worked.
        if q["status"] not in ("built", "partially_built") and not args.question:
            print(f"  SKIPPED -- status='{q['status']}'. {q['reuses']}")
            continue
        elif q["status"] not in ("built", "partially_built"):
            print(f"  status='{q['status']}' but running anyway (--question explicitly given).")

        if q["category"] == "rag":
            score_rag_question(q, verbose=args.verbose)
        else:
            run_tool_question(q)

    print("\n" + "=" * 70)
    print("Done. RAG questions above have real RAGAS scores from this run. "
          "Tool-calling/hybrid questions are not scored by this file -- run "
          "each one's own test_qN.py script directly (see the reuses field "
          "printed above, or --list).")
    print("=" * 70)


if __name__ == "__main__":
    main()
