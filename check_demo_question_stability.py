"""
Repeated-run stability check for the 4 live-demo questions (PRD's "Demo
Success Criteria" section) -- NOT part of the graded eval_dataset.json
harness (run_eval.py / run_scorecard.py), and deliberately so: those score
against a written reference for a locked 11-question set. This script asks
a narrower, different question -- given the same exact wording, does the
live agent produce the SAME deterministic signals (which tools got called,
which answer-guards fired) run after run, or does it vary?

Built 2026-07-27 after two real, confirmed cases of run-to-run variance on
identical question wording this session: Q2's customer-concentration
answer flipped between "no majority" and "one end customer over 70%" on
back-to-back runs, and Q3 stated unverified filing claims on one run that
the newly added `filings_claim` guard (app/graph.py's ANSWER_GUARDS) then
caught on a later run. Both are now guarded against structurally, but
"we fixed the two we happened to catch by hand" is not the same claim as
"this is stable" -- this script exists to make that claim checkable
instead of assumed.

Deliberately does NOT judge whether an answer is CORRECT -- that's exactly
the open, hard "does this answer address the real question" problem this
project already decided not to build a general classifier for (see
app/graph.py's ANSWER_GUARDS comment, 2026-07-27). What this DOES check,
because it's checkable without another LLM call: did the same tools get
called across runs, and did the same answer-guards fire across runs. A
run that suddenly needs a guard's correction where an identical prior run
didn't is itself a real, reportable signal, whether or not the final text
happens to still land on a correct conclusion.

Cannot run inside a network-sandboxed environment without OPENAI_API_KEY
access -- same requirement every other live-agent script in this repo has.

Usage:
    python check_demo_question_stability.py                    # all 4 questions, 3 runs each
    python check_demo_question_stability.py --runs 5            # 5 runs each
    python check_demo_question_stability.py --question 3        # just Q3, default run count
    python check_demo_question_stability.py --out stability.json
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

from app.graph import ask, build_graph

load_dotenv()

# Current wording as of 2026-07-27 -- Q3 here is the revenue-deceleration
# version that superseded the margin-bouncing wording (see PRD Task 1 §4 /
# Demo Success Criteria); keep this in sync with the PRD if either changes,
# there is no single shared source for these 4 today.
DEMO_QUESTIONS = {
    1: "ALAB dropped 12% this week. I'm getting nervous—should I sell?",
    2: "Does ALAB rely heavily on any single customer for revenue -- is any one customer a majority?",
    3: (
        "Revenue growth has slowed for several quarters straight -- does the latest quarter "
        "suggest that's stabilizing, or is a bigger slowdown coming?"
    ),
    4: (
        "Is there anything in ALAB's latest earnings I should be worried about moving forward, "
        "especially around margin or guidance?"
    ),
}

_GUARD_FIRE_PATTERN = re.compile(r"^\[(?P<name>[\w_]+) guard\] firing", re.MULTILINE)


def _run_once(graph, ticker: str, question: str) -> dict:
    """One real call to the live agent, verbose=True so guard firings print
    to stdout -- captured here rather than parsed from a redirected file,
    so this script stays self-contained and doesn't touch app/graph.py's
    ChatResult shape just to expose guard state for this one script."""
    thread_id = f"stability-check-{uuid.uuid4().hex[:8]}"  # fresh per run,
    # deliberately -- reusing a thread_id would let MemorySaver's
    # checkpointer carry conversation history from one "repeat" into the
    # next, which would no longer be testing the same identical turn twice.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = ask(graph, ticker, question, thread_id=thread_id, verbose=True)
    stdout_text = buf.getvalue()
    guards_fired = sorted(set(m.group("name") for m in _GUARD_FIRE_PATTERN.finditer(stdout_text)))
    return {
        "answer": result.answer,
        "tools_used": result.tools_used,
        "guards_fired": guards_fired,
    }


def check_question(graph, ticker: str, qid: int, question: str, runs: int) -> dict:
    print(f"\n{'=' * 70}\nQ{qid}: {question}\n{'=' * 70}")
    run_results = []
    for i in range(1, runs + 1):
        print(f"  run {i}/{runs}...", end=" ", flush=True)
        r = _run_once(graph, ticker, question)
        print(f"tools={r['tools_used']} guards_fired={r['guards_fired']}")
        run_results.append(r)

    tool_sets = [tuple(sorted(r["tools_used"])) for r in run_results]
    guard_sets = [tuple(r["guards_fired"]) for r in run_results]
    tools_stable = len(set(tool_sets)) == 1
    guards_stable = len(set(guard_sets)) == 1

    if not tools_stable:
        print(f"  !! TOOLS VARIED across runs: {set(tool_sets)}")
    if not guards_stable:
        print(f"  !! GUARD FIRINGS VARIED across runs: {set(guard_sets)}")
    if tools_stable and guards_stable:
        print("  stable: same tools and same guard firings across every run.")

    return {
        "question": question,
        "runs": run_results,
        "tools_stable": tools_stable,
        "guards_stable": guards_stable,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="ALAB")
    parser.add_argument("--runs", type=int, default=3, help="How many times to run each question.")
    parser.add_argument(
        "--question", type=int, choices=sorted(DEMO_QUESTIONS), default=None,
        help="Run just this question's number (1-4). Default: all 4.",
    )
    parser.add_argument("--out", default="demo_question_stability.json")
    args = parser.parse_args()

    graph = build_graph()
    qids = [args.question] if args.question else sorted(DEMO_QUESTIONS)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs_per_question": args.runs,
        "ticker": args.ticker,
        "questions": {},
    }
    for qid in qids:
        report["questions"][str(qid)] = check_question(
            graph, args.ticker, qid, DEMO_QUESTIONS[qid], args.runs
        )

    with open(args.out, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'=' * 70}\nSUMMARY -- written to {args.out}\n{'=' * 70}")
    any_unstable = False
    for qid_str, r in report["questions"].items():
        status = "STABLE" if r["tools_stable"] and r["guards_stable"] else "VARIED -- review answers directly"
        if status != "STABLE":
            any_unstable = True
        print(f"Q{qid_str}: {status}")
    if any_unstable:
        print(
            "\nAt least one question showed varying tool calls or guard firings across "
            "identical runs. That's a real signal to read the raw answers in the JSON "
            "output side by side -- this script doesn't judge whether the CONCLUSIONS "
            "differ, only whether the deterministic signals did."
        )


if __name__ == "__main__":
    main()
