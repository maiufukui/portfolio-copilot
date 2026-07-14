"""
Single consolidated eval harness -- runs every question that has REAL
automated scoring somewhere in this repo and persists one scorecard
(JSON + a printed summary table), instead of the score living only in
whichever of the 8 separate test_qN.py scripts' stdout happened to be
run most recently.

IMPORTANT SCOPE DISCLOSURE, found while building this (not assumed):
of the 10 "built" questions in eval_dataset.json, only 6 have ANY real
automated scoring at all -- the rest are argparse scripts that print an
LLM-generated answer for manual human review, with no judge, no metric,
no PASS/FAIL, nothing to aggregate:

  Scored, aggregated here:
    Q1  (rag)          -- RAGAS triad (Faithfulness/LLMContextRecall/FactualCorrectness), run_eval.py's run_rag_q1
    Q5  (rag)           -- same RAGAS triad, run_eval.py's run_rag_q5
    Q7  (tool_calling)  -- custom PASS/FAIL judge only, test_q7_grounding.py's run_case
    Q9  (tool_calling)  -- custom judge + real RAGAS ToolCallAccuracy + AgentGoalAccuracyWithReference, test_q9.py's run_case
    Q11 (tool_calling)  -- same as Q9, test_q11.py's run_case
    Q13 (hybrid)        -- same as Q9, test_q13.py's run_case

  NOT scored anywhere, not aggregated here (disclosed, not silently
  dropped -- each shows up in the scorecard with status="not_scored"
  and why):
    Q2 (test_q2.py)          -- prints an LLM relevance list (High/Medium/Low), no judge, no metric
    Q4 (test_q5.py)          -- prints insider-transaction records, no judge, no metric
    Q6 (test_q8.py --mode reaction)      -- prints an LLM analyst-reaction summary, no judge, no metric
    Q8 (test_q8.py --mode rating_change) -- narrates a deterministic Python delta, but nothing checks the narration against it (no judge, no metric)

  Not built at all (eval_dataset.json status="not_built"): Q3, Q12.

This is a DELIBERATELY additive wrapper, not a rewrite: every score
here comes from calling the existing, already-verified functions in
run_eval.py / test_q7_grounding.py / test_q9.py / test_q11.py /
test_q13.py directly (same pattern compare_retrievers.py already uses
for run_eval.py's Q1 runner) -- none of their internal scoring logic
was touched or reimplemented, so nothing that was already confirmed
working via a real run this session (Q9/Q11/Q13's RAGAS scores) is at
risk of regressing here. Building a NEW judge/metric for the four
not-yet-scored questions above is a materially bigger, riskier task
this file deliberately does not attempt -- flagged as a real gap, not
solved by this consolidation.

Cannot be executed from this dev sandbox (no `ragas` installed, no
network to install it, and several of the underlying scripts need live
OPENAI_API_KEY/FINNHUB_API_KEY/TAVILY_API_KEY/PORTKEY_API_KEY calls) --
only `py_compile` syntax-checked. Needs a real run to confirm it
actually produces the scorecard as designed.

Usage:
    python run_scorecard.py                  # run every scored question, write eval_scorecard.json
    python run_scorecard.py --question 9      # just one scored question
    python run_scorecard.py --out custom.json # write somewhere else
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from ragas import EvaluationDataset, evaluate
from ragas.dataset_schema import SingleTurnSample
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import FactualCorrectness, Faithfulness, LLMContextRecall

from run_eval import RAG_RUNNERS, load_dataset

load_dotenv()

DEFAULT_OUT_PATH = "eval_scorecard.json"

NOT_SCORED = {
    2: "test_q2.py prints an LLM relevance list (High/Medium/Low) for manual review -- no judge, no metric.",
    4: "test_q5.py prints insider-transaction records for manual review -- no judge, no metric.",
    6: "test_q8.py --mode reaction prints an LLM analyst-reaction summary for manual review -- no judge, no metric.",
    8: "test_q8.py --mode rating_change narrates a deterministic Python delta, but nothing checks the narration against it -- no judge, no metric.",
}

evaluator_llm = LangchainLLMWrapper(ChatOpenAI(model="gpt-4.1-mini", temperature=0))


def _score_rag_question(question: dict) -> dict:
    """Mirrors run_eval.py's score_rag_question, but returns a structured
    result instead of only printing -- same runner functions
    (RAG_RUNNERS), same RAGAS metrics, not reimplemented."""
    qid = question["id"]
    runner = RAG_RUNNERS[qid]

    samples, labels, has_ref_flags = [], [], []
    for case in question["test_cases"]:
        result = runner(case)
        reference = case.get("reference")
        has_reference = bool(reference) and reference != "TBD -- fill in against MRVL transcript."
        label = case.get("metric") or ", ".join(case.get("keywords", [])) or case.get("keyword") or case.get("ticker") or "case"
        samples.append(
            SingleTurnSample(
                user_input=result["user_input"],
                retrieved_contexts=result["retrieved_contexts"],
                response=result["response"],
                reference=reference if has_reference else result["response"],
            )
        )
        labels.append(label)
        has_ref_flags.append(has_reference)

    metrics = [Faithfulness(llm=evaluator_llm)]
    if any(has_ref_flags):
        metrics += [LLMContextRecall(llm=evaluator_llm), FactualCorrectness(llm=evaluator_llm)]

    dataset = EvaluationDataset(samples=samples)
    scores = evaluate(dataset, metrics=metrics)
    df = scores.to_pandas()
    metric_cols = [c for c in df.columns if c not in ("user_input", "retrieved_contexts", "response", "reference")]

    cases = []
    for i, label in enumerate(labels):
        row = df.iloc[i]
        cases.append({"case": label, **{c: float(row[c]) for c in metric_cols if c in row}})

    means = {c: float(df[c].mean()) for c in metric_cols if c in df.columns}

    return {
        "id": qid,
        "category": question["category"],
        "scored_by": f"RAGAS ({', '.join(m.name for m in metrics)})",
        "cases": cases,
        "mean": means,
    }


def _score_tool_question_9_11_13(question: dict) -> dict:
    """Q9/Q11/Q13 share the same shape: a build_graph() + run_case(graph,
    case, judge_llm) that already returns judgment text + real
    ragas_tool_call_accuracy + ragas_goal_accuracy (the latter scored
    against each module's own outcome-voiced GOAL_REFERENCE, not
    eval_dataset.json's expected_behavior -- see test_q9.py's
    GOAL_REFERENCE comment for why that swap was necessary). Imported
    directly from each question's own module -- not reimplemented."""
    qid = question["id"]
    from app.graph import build_graph

    if qid == 9:
        from test_q9 import load_q9, run_case
        q = load_q9()
    elif qid == 11:
        from test_q11 import load_q11, run_case
        q = load_q11()
    elif qid == 13:
        from test_q13 import load_q13, run_case
        q = load_q13()
    else:
        raise ValueError(qid)

    graph = build_graph()
    judge_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    cases = []
    for case in q["test_cases"]:
        r = run_case(graph, case, judge_llm)
        cases.append(
            {
                "case": r["ticker"],
                "ragas_tool_call_accuracy": r.get("ragas_tool_call_accuracy"),
                "ragas_goal_accuracy": r.get("ragas_goal_accuracy"),
                "judgment": r.get("judgment"),
                "coverage": r.get("coverage"),
            }
        )

    tca_vals = [c["ragas_tool_call_accuracy"] for c in cases if c["ragas_tool_call_accuracy"] is not None]
    ga_vals = [c["ragas_goal_accuracy"] for c in cases if c["ragas_goal_accuracy"] is not None]
    mean = {}
    if tca_vals:
        mean["ragas_tool_call_accuracy"] = sum(tca_vals) / len(tca_vals)
    if ga_vals:
        mean["ragas_goal_accuracy"] = sum(ga_vals) / len(ga_vals)

    return {
        "id": qid,
        "category": question["category"],
        "scored_by": "custom PASS/FAIL judge + real RAGAS ToolCallAccuracy + AgentGoalAccuracyWithReference",
        "cases": cases,
        "mean": mean,
    }


def _score_q7(question: dict) -> dict:
    """Q7 only has the custom PASS/FAIL judge (no RAGAS metric wired in
    yet) -- reported as judgment text per case, not a numeric mean,
    since nothing here parses the judge's PASS/FAIL text into a score."""
    from app.graph import build_graph
    from test_q7_grounding import load_q7_cases, run_case

    graph = build_graph()
    judge_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    cases = []
    for case in load_q7_cases():
        r = run_case(graph, case, judge_llm)
        cases.append(
            {
                "case": f"{r['ticker']} ({r['move_pct']}%)",
                "any_tool_called": r["any_tool_called"],
                "judgment": r["judgment"],
            }
        )

    return {
        "id": 7,
        "category": question["category"],
        "scored_by": "custom PASS/FAIL judge only (no RAGAS metric wired in for this question yet)",
        "cases": cases,
        "mean": {},
    }


SCORERS = {
    1: _score_rag_question,
    5: _score_rag_question,
    7: _score_q7,
    9: _score_tool_question_9_11_13,
    11: _score_tool_question_9_11_13,
    13: _score_tool_question_9_11_13,
}


def build_scorecard(question_ids: list[int] | None = None) -> dict:
    data = load_dataset()
    questions = {q["id"]: q for q in data["questions"]}

    ids = question_ids or sorted(questions.keys())
    results = {}
    for qid in ids:
        q = questions.get(qid)
        if q is None:
            continue
        if q["status"] not in ("built", "partially_built"):
            results[str(qid)] = {"id": qid, "status": q["status"], "reason": "not built"}
            continue
        if qid in NOT_SCORED:
            results[str(qid)] = {"id": qid, "status": "not_scored", "reason": NOT_SCORED[qid]}
            continue
        scorer = SCORERS.get(qid)
        if scorer is None:
            results[str(qid)] = {"id": qid, "status": "not_scored", "reason": "no scorer wired into run_scorecard.py for this question"}
            continue
        print(f"\n{'=' * 70}\nScoring Q{qid}...\n{'=' * 70}")
        results[str(qid)] = {"status": "scored", **scorer(q)}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "questions": results,
    }


def print_summary(scorecard: dict) -> None:
    print(f"\n\n{'=' * 70}\nSCORECARD SUMMARY -- generated {scorecard['generated_at']}\n{'=' * 70}")
    for qid_str, r in scorecard["questions"].items():
        # Real bug, found on the first real run: build_scorecard() stores
        # q["status"] verbatim from eval_dataset.json ("not_built", with an
        # underscore, e.g. Q3/Q12) -- this used to check the literal string
        # "not built" (a space), which never matched, so any not_built
        # question fell through to the `else` branch below and crashed on
        # `r['scored_by']`, a key that dict shape never has. The scorecard
        # JSON itself was already written correctly by this point (that
        # write happens before this function is even called) -- only this
        # printed summary was ever affected.
        if r["status"] not in ("built", "partially_built", "not_scored", "scored"):
            print(f"Q{qid_str}: not built (status={r['status']!r})")
        elif r["status"] == "not_scored":
            print(f"Q{qid_str}: NOT SCORED -- {r['reason']}")
        else:
            mean_str = ", ".join(f"{k}={v:.2f}" for k, v in r.get("mean", {}).items()) or "(no numeric mean -- see per-case judgment text)"
            print(f"Q{qid_str}: scored via {r['scored_by']} -- {mean_str}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", type=int, help="Run only this question id.")
    parser.add_argument("--out", default=DEFAULT_OUT_PATH, help="Path to write the JSON scorecard to.")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    question_ids = [args.question] if args.question else None
    scorecard = build_scorecard(question_ids)

    with open(args.out, "w") as f:
        json.dump(scorecard, f, indent=2, default=str)
    print(f"\nScorecard written to {args.out}")

    print_summary(scorecard)


if __name__ == "__main__":
    main()
