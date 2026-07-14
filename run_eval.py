"""
Eval harness runner for the Personal Portfolio Copilot capstone.

Reads eval_dataset.json (12 locked questions -- ids 1-9, 11-13; id 10 was
retired) and runs each one that's actually buildable today against real
pipelines, scoring with the two methods described in the PRD (Task 1 §4,
"Evaluation methodology"):

- RAG questions (category "rag"): scored with RAGAS -- Faithfulness always,
  plus LLMContextRecall and FactualCorrectness when a test case has a
  written `reference` answer to compare against. Pattern per
  Session 6 (Agentic RAG Evaluation) / Session 10 (LLM Servers run_eval.py):
  SingleTurnSample -> EvaluationDataset -> ragas.evaluate().

- Tool-calling questions (category "tool_calling" / "hybrid"): NOT scored
  by this file directly -- it prints a pointer to each question's own
  standalone test_qN.py script for manual review, rather than duplicating
  that scoring logic here. As of this session, three of those scripts
  (test_q9.py, test_q11.py, test_q13.py) score real RAGAS multi-turn
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
automated scoring (RAG questions here plus Q7/Q9/Q11/Q13's tool-calling
scoring), persisted to one JSON scorecard instead of scattered stdout
across 8 separate scripts, see run_scorecard.py -- it imports this
file's RAG_RUNNERS directly rather than duplicating this scoring logic.

Usage:
    python run_eval.py                        # run everything runnable
    python run_eval.py --question 1           # run just question id 1
    python run_eval.py --question 5 --verbose # + full input/context/response/reference per case
    python run_eval.py --list                 # print status of all 12, run nothing
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

from test_q1 import build_retriever, load_ticker_documents
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
            "call transcript), answer: what did {ticker}'s management "
            "specifically identify as the driver behind {metric}? Quote the "
            "exact figures/language they used. Clearly state whether this is "
            "a backward-looking result (something that already happened) or "
            "forward-looking guidance (something they're projecting). If the "
            "context doesn't address this, say so explicitly rather than "
            "guessing.\n\n"
            "CONTEXT:\n{context}\n\n"
            "Respond in this format:\n"
            "Driver: ...\n"
            "Type: [Backward-looking result / Forward-looking guidance / Not addressed]\n"
            "Source: ...",
        )
    ]
)


def run_rag_q1(case: dict) -> dict:
    """Q1: driver-identification (margin/guidance). Vector retrieval.
    Replaces the retired thesis-comparison version -- thesis is no longer
    a product concept, so this no longer uses test_q1.py's original
    Verdict/Evidence/Explanation prompt, only its generic load/retrieve
    utilities."""
    documents = load_ticker_documents(case["ticker"])
    retriever = build_retriever(documents)
    query = f"What did {case['ticker']}'s management identify as the specific driver behind {case['metric']}?"
    retrieved_docs = retriever.invoke(query)
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
# test_qN.py script rather than re-implementing its scoring here. Q9/Q11/
# Q13's scripts score real RAGAS ToolCallAccuracy + AgentGoalAccuracy
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
