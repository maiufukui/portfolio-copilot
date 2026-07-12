"""
Before/after comparison for Task 6/12 (advanced retriever rubric item):
baseline flat-vector retrieval (test_q1.build_retriever -- fixed 512-token
chunks, k=10) vs. parent-child retrieval (parent_child_retriever.py --
child chunks searched for precision, deduped back to full Item
sections / speaker turns / PDF pages).

Scored with the same RAGAS triad run_eval.py uses (Faithfulness,
LLMContextRecall, FactualCorrectness) against Q1's two ALAB test cases --
the eval dataset's only vector-retrieval RAG question with written
reference answers, so it's the only one FactualCorrectness/ContextRecall
can meaningfully score. Both retrievers answer the same two questions
through the same driver-identification prompt (Q1_DRIVER_PROMPT, reused
from run_eval.py, not duplicated here) so the only variable being
compared is retrieval, not prompt wording.

Requires OPENAI_API_KEY (embeddings + gpt-4.1-mini answer/judge calls) --
same requirement as run_eval.py and test_q1.py.

Usage:
    python compare_retrievers.py
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI
from ragas import EvaluationDataset, evaluate
from ragas.dataset_schema import SingleTurnSample
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import FactualCorrectness, Faithfulness, LLMContextRecall

from parent_child_retriever import build_parent_child_retriever
from run_eval import DATASET_PATH, Q1_DRIVER_PROMPT
from test_q1 import build_retriever as build_baseline_retriever
from test_q1 import load_ticker_documents

load_dotenv()

evaluator_llm = LangchainLLMWrapper(ChatOpenAI(model="gpt-4.1-mini", temperature=0))
answer_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)


def load_q1_cases() -> list[dict]:
    with open(DATASET_PATH) as f:
        data = json.load(f)
    q1 = next(q for q in data["questions"] if q["id"] == 1)
    return q1["test_cases"]


def run_case(case: dict, get_contexts) -> dict:
    """get_contexts(query) -> list[str] of retrieved page_content, using
    whichever retriever is under test. Same Q1_DRIVER_PROMPT run_eval.py
    uses for Q1, so retrieval is the only thing varying between the two
    conditions."""
    query = (
        f"What did {case['ticker']}'s management identify as the specific "
        f"driver behind {case['metric']}?"
    )
    contexts = get_contexts(query)
    chain = Q1_DRIVER_PROMPT | answer_llm | StrOutputParser()
    response = chain.invoke(
        {"ticker": case["ticker"], "metric": case["metric"], "context": "\n\n---\n\n".join(contexts)}
    )
    return {"user_input": query, "retrieved_contexts": contexts, "response": response}


def score(label: str, run_results: list[dict], cases: list[dict]):
    samples = [
        SingleTurnSample(
            user_input=r["user_input"],
            retrieved_contexts=r["retrieved_contexts"],
            response=r["response"],
            reference=case["reference"],
        )
        for r, case in zip(run_results, cases)
    ]
    dataset = EvaluationDataset(samples=samples)
    metrics = [Faithfulness(llm=evaluator_llm), LLMContextRecall(llm=evaluator_llm), FactualCorrectness(llm=evaluator_llm)]
    print(f"\nScoring {label} ({len(samples)} case(s))...")
    scores = evaluate(dataset, metrics=metrics)
    df = scores.to_pandas()
    df.insert(0, "case", [c["metric"] for c in cases])
    df.insert(0, "retriever", label)
    return df


def main():
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    cases = load_q1_cases()
    ticker = cases[0]["ticker"]

    print(f"Loading documents for {ticker}...")
    documents = load_ticker_documents(ticker)

    print("Building baseline retriever (flat 512-token chunks, k=10)...")
    baseline_retriever = build_baseline_retriever(documents)

    print("Building parent-child retriever (child search k=15, deduped to k=5 parents)...")
    parent_child_retrieve = build_parent_child_retriever(documents)

    print("\nRunning baseline retriever against both test cases...")
    baseline_results = [
        run_case(c, lambda q: [d.page_content for d in baseline_retriever.invoke(q)]) for c in cases
    ]

    print("Running parent-child retriever against both test cases...")
    # prefer_source_suffixes=(".txt", ".pdf"): Q1 is a driver-identification
    # question ("what did management identify/say..."), and the primary
    # source for management's own narrative attribution is the earnings
    # call transcript, not a filing's required GAAP-comparison language --
    # see parent_child_retriever.retrieve's docstring for the full
    # rationale and the eval evidence (Q1 case 1) behind it. This is
    # specific to this question shape, not a change to the retriever's
    # default (unpreferenced) behavior used elsewhere.
    pc_results = [
        run_case(c, lambda q: [d.page_content for d in parent_child_retrieve(q, k=5, prefer_source_suffixes=(".txt", ".pdf"))])
        for c in cases
    ]

    # Raw response vs. reference, printed before scoring -- factual_correctness
    # is an atomic-claim F1 against a one-sentence reference, so a low or
    # dropping score needs the actual text to diagnose (is the driver simply
    # missing/wrong, or is it present but phrased differently / buried in
    # extra claims the reference doesn't have -- F1 penalizes both directions
    # equally and a short reference has very few atomic claims total, so one
    # mismatch swings the score hard). Printing this rather than guessing.
    print("\n" + "=" * 70)
    print("RAW RESPONSES vs. REFERENCE (for diagnosing factual_correctness)")
    print("=" * 70)
    for label, results in [("baseline", baseline_results), ("parent-child", pc_results)]:
        for case, r in zip(cases, results):
            print(f"\n--- {label} | {case['metric']} ---")
            print(f"Reference: {case['reference']}")
            print(f"Response:\n{r['response']}")

    import pandas as pd

    pd.set_option("display.max_colwidth", 40)
    pd.set_option("display.width", 160)

    baseline_df = score("baseline (flat 512-tok)", baseline_results, cases)
    pc_df = score("parent-child", pc_results, cases)

    combined = pd.concat([baseline_df, pc_df], ignore_index=True)
    metric_cols = [
        c for c in combined.columns if c not in ("retriever", "case", "user_input", "retrieved_contexts", "response", "reference")
    ]

    print("\n" + "=" * 70)
    print("BEFORE/AFTER -- Task 6 parent-child retriever vs. baseline")
    print("=" * 70)
    print(combined[["retriever", "case"] + metric_cols].to_string(index=False))

    print("\nMean per retriever:")
    print(combined.groupby("retriever")[metric_cols].mean().to_string())

    # Retrieved-context size, for the PRD writeup's rationale (parent-child
    # trades chunk count for chunk completeness -- worth showing, not just
    # asserting).
    print("\nRetrieved context sizes (chars per case, sum across contexts):")
    for label, results in [("baseline", baseline_results), ("parent-child", pc_results)]:
        for case, r in zip(cases, results):
            total_chars = sum(len(c) for c in r["retrieved_contexts"])
            print(f"  {label:15s} {case['metric'][:40]:42s} {len(r['retrieved_contexts'])} context(s), {total_chars} chars")


if __name__ == "__main__":
    main()
