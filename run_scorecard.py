"""
Single consolidated eval harness -- runs every question that has REAL
automated scoring somewhere in this repo and persists one scorecard
(JSON + a printed summary table), instead of the score living only in
whichever of the 8 separate test_qN.py scripts' stdout happened to be
run most recently.

IMPORTANT SCOPE DISCLOSURE, found while building this (not assumed):
of the 9 "built" questions in eval_dataset.json, only 5 have ANY real
automated scoring at all -- the rest are argparse scripts that print an
LLM-generated answer for manual human review, with no judge, no metric,
no PASS/FAIL, nothing to aggregate:

  Scored, aggregated here:
    Q1  (rag)          -- RAGAS triad (Faithfulness/LLMContextRecall/FactualCorrectness), run_eval.py's run_rag_q1
    Q5  (rag)           -- same RAGAS triad, run_eval.py's run_rag_q5
    Q7  (tool_calling)  -- custom PASS/FAIL judge only, test_q7_grounding.py's run_case
    Q9  (tool_calling)  -- custom judge + real RAGAS ToolCallAccuracy + AgentGoalAccuracyWithReference, test_q9.py's run_case
    Q11 (tool_calling)  -- same as Q9, test_q11.py's run_case

  NOT scored anywhere, not aggregated here (disclosed, not silently
  dropped -- each shows up in the scorecard with status="not_scored"
  and why):
    Q2 (test_q2.py)          -- prints an LLM relevance list (High/Medium/Low), no judge, no metric
    Q4 (test_q5.py)          -- prints insider-transaction records, no judge, no metric
    Q6 (test_q8.py --mode reaction)      -- prints an LLM analyst-reaction summary, no judge, no metric
    Q8 (test_q8.py --mode rating_change) -- narrates a deterministic Python delta, but nothing checks the narration against it (no judge, no metric)

  Not built at all (eval_dataset.json status="not_built"): Q3, Q12.

  Removed 2026-07-27: Q13 (hybrid) and its scoring script test_q13.py
  were deleted along with the since-purchase-comparison use case they
  tested -- see eval_dataset.json and the PRD's Task 1 §4.

This is a DELIBERATELY additive wrapper, not a rewrite: every score
here comes from calling the existing, already-verified functions in
run_eval.py / test_q3.py / test_q5.py / test_q7_grounding.py /
test_q8.py / test_q9.py / test_q10.py directly (same pattern
compare_retrievers.py already uses for run_eval.py's Q1 runner) --
none of their internal scoring logic was touched or reimplemented, so
nothing that was already confirmed working via a real run this session
(Q9's RAGAS scores) is at risk of regressing here.

UPDATED 2026-07-28 (Maiu, explicit call: "build and automate all 10"):
Q2 (test_q2.py), Q3 (new, test_q3.py), Q4 (test_q5.py), Q6 (test_q8.py),
and Q8 (test_q8.py) all now have real scorers wired in below -- closing
the gap this module's docstring previously flagged. Q11 (old id 12, the
whole-portfolio digest) is explicitly NOT tested (see eval_dataset.json
id 11's own reuses field) -- it stays status="not_built" and is skipped
by build_scorecard()'s own not-built branch, same as before. The old
id-11 question (earnings date + Fundamentals Health Score, previously
scored via test_q11.py) was DROPPED from the dataset entirely when Q10
replaced it -- test_q11.py itself still exists in this repo but is no
longer dispatched to by anything here, since no dataset question maps
to it anymore. Flagged as an orphaned script, not deleted without an
explicit go-ahead.

  Scored, aggregated here (10 of 11 total questions):
    Q1  (rag)          -- RAGAS triad, run_eval.py's run_rag_q1
    Q2  (tool_calling)  -- custom judge + real RAGAS ToolCallAccuracy + AgentGoalAccuracyWithReference, test_q2.py's run_case
    Q3  (tool_calling)  -- custom PASS/FAIL judge only, test_q3.py's run_case
    Q4  (tool_calling)  -- custom judge + real RAGAS ToolCallAccuracy + AgentGoalAccuracyWithReference, test_q5.py's run_case
    Q5  (rag)           -- same RAGAS triad, run_eval.py's run_rag_q5
    Q6  (tool_calling)  -- custom judge + real RAGAS ToolCallAccuracy + AgentGoalAccuracyWithReference, test_q8.py's run_case
    Q7  (tool_calling)  -- custom PASS/FAIL judge only, test_q7_grounding.py's run_case
    Q8  (tool_calling)  -- deterministic check_narration_matches_deltas against a real computed diff, test_q8.py's run_case (Q8-specific, single-arg)
    Q9  (tool_calling)  -- custom judge + real RAGAS ToolCallAccuracy + AgentGoalAccuracyWithReference, test_q9.py's run_case
    Q10 (tool_calling)  -- custom PASS/FAIL judge only, test_q10.py's run_case

  NOT scored (disclosed, not silently dropped):
    Q11 -- status="not_built" in eval_dataset.json, explicitly not tested (no product support for portfolio-wide queries)

Cannot be executed from this dev sandbox (no live OPENAI_API_KEY/
FINNHUB_API_KEY/TAVILY_API_KEY/PORTKEY_API_KEY/DATABASE_URL calls
attempted -- see each test_qN.py's own module docstring for what was
and wasn't verified) -- only `py_compile` and import-chain checked.
Needs a real run to confirm it actually produces the scorecard as
designed end to end.

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

# Empty as of 2026-07-28 -- Q2/Q4/Q6/Q8 all have real scorers wired in
# below now (see module docstring for what changed and why). Kept as a
# named dict, not deleted, so a future not-yet-scored question has an
# obvious place to be disclosed rather than silently dropped.
NOT_SCORED: dict[int, str] = {}

evaluator_llm = LangchainLLMWrapper(ChatOpenAI(model="gpt-4.1-mini", temperature=0))


def _score_rag_question(question: dict) -> dict:
    """Mirrors run_eval.py's score_rag_question, but returns a structured
    result instead of only printing -- same runner functions
    (RAG_RUNNERS), same RAGAS metrics, not reimplemented."""
    qid = question["id"]
    runner = RAG_RUNNERS[qid]

    samples, labels, responses, has_ref_flags = [], [], [], []
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
        responses.append(result["response"])
        has_ref_flags.append(has_reference)

    metrics = [Faithfulness(llm=evaluator_llm)]
    if any(has_ref_flags):
        metrics += [LLMContextRecall(llm=evaluator_llm), FactualCorrectness(llm=evaluator_llm)]

    dataset = EvaluationDataset(samples=samples)
    scores = evaluate(dataset, metrics=metrics)
    df = scores.to_pandas()
    metric_cols = [c for c in df.columns if c not in ("user_input", "retrieved_contexts", "response", "reference")]

    # "response" added 2026-07-28 (Maiu, explicit call): the full generated
    # answer text used to only exist in stdout, not in eval_scorecard.json --
    # real gap, surfaced while diagnosing Q1's factual_correctness score,
    # where seeing the actual answer text (not just the numeric metric) is
    # what real diagnosis needs.
    cases = []
    for i, label in enumerate(labels):
        row = df.iloc[i]
        cases.append({"case": label, "response": responses[i], **{c: float(row[c]) for c in metric_cols if c in row}})

    means = {c: float(df[c].mean()) for c in metric_cols if c in df.columns}

    return {
        "id": qid,
        "category": question["category"],
        "scored_by": f"RAGAS ({', '.join(m.name for m in metrics)})",
        "cases": cases,
        "mean": means,
    }


def _score_tool_question_2_4_6_9(question: dict) -> dict:
    """Q2/Q4/Q6/Q9 share the same shape: a build_graph() + run_case(graph,
    case, judge_llm) that already returns judgment text + real
    ragas_tool_call_accuracy + ragas_goal_accuracy (the latter scored
    against each module's own outcome-voiced GOAL_REFERENCE, not
    eval_dataset.json's expected_behavior -- see test_q9.py's
    GOAL_REFERENCE comment for why that swap was necessary). Imported
    directly from each question's own module -- not reimplemented.

    Renamed 2026-07-28 from _score_tool_question_9_11 (Q11 dropped this
    shape -- old id-11 was removed from the dataset entirely when Q10
    replaced it, see eval_dataset.json id 10's reuses field and this
    module's docstring; test_q11.py still exists but nothing here
    dispatches to it anymore). Q2/Q4/Q6 added the same day, same reason
    ("build and automate all 10") -- each module's load_qN() already
    returns cases in the {"ticker", "company", ...} shape this loop
    expects (test_q5.py's load_q4() normalizes Q4's portfolio-wide
    dataset entry into per-ticker cases at load time -- see its own
    docstring for why)."""
    qid = question["id"]
    from app.graph import build_graph

    if qid == 2:
        from test_q2 import load_q2, run_case
        q = load_q2()
        cases = q["test_cases"]
    elif qid == 4:
        from test_q5 import load_q4, run_case
        q = load_q4()
        cases = q["test_cases"]
    elif qid == 6:
        from test_q8 import load_q6, run_case
        q = load_q6()
        cases = q["test_cases"]
    elif qid == 9:
        from test_q9 import load_q9, run_case
        q = load_q9()
        cases = q["test_cases"]
    else:
        raise ValueError(qid)

    graph = build_graph()
    judge_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    results = []
    for case in cases:
        r = run_case(graph, case, judge_llm)
        results.append(
            {
                "case": r["ticker"],
                "response": r.get("response"),
                "ragas_tool_call_accuracy": r.get("ragas_tool_call_accuracy"),
                "ragas_goal_accuracy": r.get("ragas_goal_accuracy"),
                "judgment": r.get("judgment"),
                "coverage": r.get("coverage"),
            }
        )

    tca_vals = [c["ragas_tool_call_accuracy"] for c in results if c["ragas_tool_call_accuracy"] is not None]
    ga_vals = [c["ragas_goal_accuracy"] for c in results if c["ragas_goal_accuracy"] is not None]
    mean = {}
    if tca_vals:
        mean["ragas_tool_call_accuracy"] = sum(tca_vals) / len(tca_vals)
    if ga_vals:
        mean["ragas_goal_accuracy"] = sum(ga_vals) / len(ga_vals)

    return {
        "id": qid,
        "category": question["category"],
        "scored_by": "custom PASS/FAIL judge + real RAGAS ToolCallAccuracy + AgentGoalAccuracyWithReference",
        "cases": results,
        "mean": mean,
    }


def _score_grounding_question(question: dict) -> dict:
    """Q3/Q7/Q10 share the same shape: a build_graph() + run_case(graph,
    case, judge_llm) from a "GROUNDING_JUDGE_PROMPT"-style module (does
    the response actually check real data instead of mirroring the
    question's own framing) -- custom PASS/FAIL judge only, no RAGAS
    metric wired in for any of these three yet, reported as judgment
    text per case rather than a numeric mean.

    Generalized 2026-07-28 from the original Q7-only _score_q7 (Maiu,
    "build and automate all 10") -- test_q3.py and test_q10.py were
    both built directly off test_q7_grounding.py's template, so they
    share its run_case(graph, case, judge_llm) -> {"ticker",
    "any_tool_called", "judgment", ...} return shape exactly."""
    qid = question["id"]
    from app.graph import build_graph

    if qid == 3:
        from test_q3 import load_q3_cases, run_case
        cases = load_q3_cases()
    elif qid == 7:
        from test_q7_grounding import load_q7_cases, run_case
        cases = load_q7_cases()
    elif qid == 10:
        from test_q10 import load_q10_cases, run_case
        cases = load_q10_cases()
    else:
        raise ValueError(qid)

    graph = build_graph()
    judge_llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    results = []
    for case in cases:
        r = run_case(graph, case, judge_llm)
        label = r["ticker"] if "move_pct" not in r else f"{r['ticker']} ({r['move_pct']}%)"
        results.append(
            {
                "case": label,
                "response": r.get("response"),
                "any_tool_called": r["any_tool_called"],
                "judgment": r["judgment"],
            }
        )

    return {
        "id": qid,
        "category": question["category"],
        "scored_by": "custom PASS/FAIL judge only (no RAGAS metric wired in for this question yet)",
        "cases": results,
        "mean": {},
    }


def _score_q8(question: dict) -> dict:
    """Q8 is a genuinely different shape from every other tool_calling
    question here: no LangGraph agent involved at all (test_q8.py's own
    Q8 run_case docstring explains why -- it's direct Finnhub + a
    deterministic Python diff + narration, not a tool-calling question in
    the live-agent sense), so it needs no graph/judge_llm, and it's
    scored by a deterministic pass/fail against real computed deltas
    (check_narration_matches_deltas), not an LLM judge or a RAGAS metric.
    Added 2026-07-28 (Maiu, "build and automate all 10") -- previously
    listed in NOT_SCORED."""
    from test_q8 import load_q8, run_rating_change_case

    q = load_q8()
    results = []
    for case in q["test_cases"]:
        r = run_rating_change_case(case)
        results.append(
            {
                "case": r["ticker"],
                "passed": r["passed"],
                "reason": r["reason"],
                "narration": r.get("narration"),
            }
        )

    scored = [r for r in results if r["passed"] is not None]
    mean = {"pass_rate": sum(1 for r in scored if r["passed"]) / len(scored)} if scored else {}

    return {
        "id": 8,
        "category": question["category"],
        "scored_by": "deterministic check_narration_matches_deltas against a real computed Finnhub trend diff (no LLM judge, no RAGAS metric)",
        "cases": results,
        "mean": mean,
    }


SCORERS = {
    1: _score_rag_question,
    2: _score_tool_question_2_4_6_9,
    3: _score_grounding_question,
    4: _score_tool_question_2_4_6_9,
    5: _score_rag_question,
    6: _score_tool_question_2_4_6_9,
    7: _score_grounding_question,
    8: _score_q8,
    9: _score_tool_question_2_4_6_9,
    10: _score_grounding_question,
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

    # Merge into any existing scorecard file rather than overwriting it
    # wholesale -- real bug, found 2026-07-28 via a real two-command
    # sequence (--question 1, then --question 8): the old code below did
    # a plain open(args.out, "w") every time, truncating the file and
    # writing ONLY the question(s) just requested -- the second command
    # silently discarded Q1's results that were sitting in the file from
    # the first. --question exists specifically for narrow, single-
    # question re-runs (this project's own standing convention -- see
    # CLAUDE.md's "default to narrowest scope" rule), so the file it
    # writes needs to behave like a durable, accumulating scorecard, not
    # get reset by every partial run. Only merges when --question was
    # actually used and a prior file exists -- a full run (no --question)
    # still does a clean full overwrite, which is correct for that case.
    if question_ids is not None and os.path.exists(args.out):
        with open(args.out) as f:
            existing = json.load(f)
        existing.setdefault("questions", {}).update(scorecard["questions"])
        existing["generated_at"] = scorecard["generated_at"]
        scorecard = existing

    with open(args.out, "w") as f:
        json.dump(scorecard, f, indent=2, default=str)
    print(f"\nScorecard written to {args.out}")

    print_summary(scorecard)


if __name__ == "__main__":
    main()
