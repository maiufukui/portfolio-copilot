"""
Shared helper for scoring RAGAS's real `ToolCallAccuracy` and
`AgentGoalAccuracyWithReference` metrics against this project's live
agent (Q9, Q11, Q13) -- Open Items / Task 5 finding: these three eval
harnesses previously scored "tool_call_accuracy" and "goal_accuracy"
(where they scored either at all) with hand-written LLM-judge PASS/FAIL
prompts, not RAGAS's actual metric classes. This module wires in the
real things.

API verified against this project's actual pinned dependency
(`ragas==0.2.15` in requirements.txt), fetched directly from that exact
tag on GitHub -- NOT assumed from a newer ragas version. This matters:
the course's own Session 6 notebook environment runs a much newer ragas
dev build (0.4.4.dev8) with a different API (`ragas.metrics.collections`,
a `strict_order` kwarg, `.ascore(user_input=..., reference_tool_calls=...)`
as an async method). That newer surface does NOT exist in 0.2.15. This
project's real `ToolCallAccuracy` (`ragas.metrics.ToolCallAccuracy`, the
same one `run_eval.py` already imports RAGAS metrics from) has:
  - no `strict_order` parameter at all,
  - a synchronous public entrypoint: `.multi_turn_score(MultiTurnSample)`,
  - sequence "alignment" checked as an IN-ORDER SUBSEQUENCE match (every
    reference tool call must appear in the predicted sequence in the same
    RELATIVE order, though not necessarily adjacent -- extra/interleaved
    predicted calls are fine, out-of-order ones are not).

Two real, disclosed limitations of applying this metric honestly to this
project's tools, not hidden by tuning the reference to whatever the agent
happened to do:

1. Several of this project's tools (search_filings's `query`,
   search_filings_exact's `keywords`, search_live_news's `query`) take
   free-text arguments the agent composes itself -- there is no fixed
   "correct" string to check exact-match against, unlike the Session 6
   metal-price agent's single deterministic `metal_name` argument. Only
   `ticker` (where a tool takes one) is included in reference args here;
   free-text args are left out of the reference on purpose. A side
   effect: get_market_data's argument accuracy can be checked properly
   (ticker is its only arg), but search_live_news has no deterministic
   arg at all -- its argument-accuracy component will read 0.0 by
   construction, which is a property of the metric applied to a
   free-text-only tool, not a defect in this implementation.
2. This project's design (Task 2's Infrastructure table) treats
   search_filings and search_filings_exact as interchangeable for
   "did the agent check filings," and doesn't require Q9/Q13's three
   tool categories to fire in any particular order. RAGAS 0.2.15's
   ToolCallAccuracy supports neither "either of these tool names" nor
   true unordered matching directly (no strict_order toggle exists in
   this version). Worked around here by scoring every acceptable
   tool-name variant, in every order permutation, against the real
   predicted sequence, and reporting the best-scoring one -- a max over
   orderings of an order-sensitive containment check is the correct way
   to express "these calls, in any order," not a way of inflating the
   score.

`AgentGoalAccuracyWithReference` added alongside `ToolCallAccuracy` for
the same reason (Open Items: PRD cited the Session 6 metal-price-agent
notebook as precedent for using RAGAS's real agentic metric classes,
but Q9/Q11/Q13 only ever used a custom PASS/FAIL judge prompt for goal
accuracy too). API verified the same way -- fetched
`ragas/metrics/_goal_accuracy.py` directly from the `v0.2.15` tag, not
assumed from the newer course venv:
  - dataclass `MetricWithLLM` + `MultiTurnMetric`; requires `llm` set
    (a `BaseRagasLLM`, e.g. `LangchainLLMWrapper(ChatOpenAI(...))`).
  - `MultiTurnSample` needs `reference: str` here, NOT
    `reference_tool_calls` -- a plain-English description of the
    desired outcome, not a tool-call list.
  - scoring flow: `InferGoalOutcomePrompt` infers `user_goal`/
    `end_state` from `sample.pretty_repr()` (every message's own
    `.pretty_repr()`, concatenated), then `CompareOutcomePrompt`
    compares `reference` (desired outcome) against the inferred
    `end_state` (arrived outcome), returning a binary "0"/"1" cast to
    float -- so this metric is coarser than ToolCallAccuracy by
    construction (one LLM-judged binary call, not a deterministic
    per-arg score), but it is RAGAS's real class, not a hand-written
    prompt.
  - same sync entrypoint as ToolCallAccuracy: `.multi_turn_score
    (sample, callbacks=None)`.

CONFIRMED BUG, FOUND AND FIXED VIA A REAL RUN: the first version of this
function's callers passed `eval_dataset.json`'s `expected_behavior` field
straight through as `reference`. Real runs against Q9/Q11/Q13 (4 cases
total) all scored exactly 0.00 -- including Q13/ALAB, where the custom
judge PASSed all three criteria and ToolCallAccuracy scored 1.00, so the
agent was not actually failing its goal. Root cause, confirmed by
re-fetching `_goal_accuracy.py`'s real source: `CompareOutcomePrompt`
expects `desired_outcome` and `arrived_outcome` to be short, symmetric,
OUTCOME-voiced statements (RAGAS's own example: "A table is successfully
booked at any Chinese restaurant for 8:00pm." vs "...at Jade Palace...").
`expected_behavior` is written as rubric/spec prose, not an outcome
statement -- Q13's is the worst case, literally containing "(see PRD
Open Items)", a citation to a document the comparison LLM never sees.
That structural/lexical mismatch reads as "different" to the judge
regardless of whether the agent actually succeeded. Fixed by having each
test_qN.py build its own short, outcome-voiced `reference` string
(`GOAL_REFERENCE`, formatted per case) instead of reusing
`expected_behavior` -- see test_q9.py/test_q11.py/test_q13.py. Not yet
re-verified against a real run (this fix itself is unexecuted, same
sandbox constraint as everything else in this file).

One disclosed limitation: this project's `ChatResult` (app/graph.py)
exposes the final answer text and the ordered tool-call list, but not
each individual tool CALL RESULT's raw content (what search_filings
etc. actually returned) -- so the `user_input` trace built here is
`[HumanMessage(question), AIMessage(tool_calls=...), AIMessage
(final_answer)]`, not a fully faithful turn-by-turn replay with real
ToolMessage content in between. This is enough for
`InferGoalOutcomePrompt` to infer a real `end_state` from (it reads the
final answer content, which is the actual thing being judged), but a
richer trace with real tool outputs might change the inferred
`user_goal`/`end_state` in edge cases. Judge LLM is plain `ChatOpenAI`
(matching `run_eval.py`'s and `compare_retrievers.py`'s existing RAGAS
judge LLMs), not routed through `llm_gateway.build_chat_llm` -- the
Portkey requirement is scoped to the application itself, not
eval-harness-internal judge calls (same scoping already used
elsewhere in this codebase).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from langchain_openai import ChatOpenAI
from ragas.dataset_schema import MultiTurnSample
from ragas.llms import LangchainLLMWrapper
from ragas.messages import AIMessage, HumanMessage, ToolCall
from ragas.metrics import AgentGoalAccuracyWithReference, ToolCallAccuracy

_metric = ToolCallAccuracy()
_goal_metric = AgentGoalAccuracyWithReference(
    llm=LangchainLLMWrapper(ChatOpenAI(model="gpt-4.1-mini", temperature=0))
)


def _to_ai_message(predicted_tool_calls: list[dict]) -> AIMessage:
    """This project's ChatResult.tool_calls (plain {"name","args"} dicts,
    extracted in app/graph.py's get_tool_calls() from the real LangGraph
    trace) collapsed into one synthetic Ragas AIMessage. Faithful to what
    ToolCallAccuracy actually reads: `_multi_turn_ascore` only pulls
    `.tool_calls` off AIMessage instances in `sample.user_input` -- it
    never inspects message content or turn structure, HumanMessage vs.
    ToolMessage boundaries, etc. -- so one AIMessage carrying the full,
    real, ordered tool-call sequence is functionally equivalent to a
    faithfully turn-by-turn-converted trace for this specific metric,
    without fabricating intermediate turns the metric wouldn't use anyway.
    """
    calls = [ToolCall(name=c["name"], args=c["args"]) for c in predicted_tool_calls]
    return AIMessage(content="", tool_calls=calls or None)


@dataclass
class ToolCallAccuracyResult:
    score: float
    best_reference: list[str]  # tool names, in the order that scored best
    all_scores_tried: list[tuple[list[str], float]]


def score_tool_call_accuracy(
    question: str,
    predicted_tool_calls: list[dict],
    acceptable_tool_sets: list[list[ToolCall]],
) -> ToolCallAccuracyResult:
    """Score real predicted tool calls against one or more acceptable
    "correct" tool-call sets, each representing a legitimate way to
    answer -- e.g. Q9 accepts either search_filings or
    search_filings_exact as the filings check. Every order permutation
    of every acceptable set is tried (see module docstring, limitation
    2); the best score wins.
    """
    ai_message = _to_ai_message(predicted_tool_calls)
    user_input = [HumanMessage(content=question), ai_message]

    all_scores: list[tuple[list[str], float]] = []
    best_score = -1.0
    best_ref: list[str] = []

    for acceptable_set in acceptable_tool_sets:
        for perm in itertools.permutations(acceptable_set):
            sample = MultiTurnSample(user_input=user_input, reference_tool_calls=list(perm))
            score = _metric.multi_turn_score(sample)
            names = [tc.name for tc in perm]
            all_scores.append((names, score))
            if score > best_score:
                best_score = score
                best_ref = names

    return ToolCallAccuracyResult(
        score=best_score, best_reference=best_ref, all_scores_tried=all_scores
    )


def score_goal_accuracy(
    question: str,
    predicted_tool_calls: list[dict],
    final_answer: str,
    reference: str,
) -> float:
    """Score the real RAGAS `AgentGoalAccuracyWithReference` metric.

    `reference` must be a SHORT, OUTCOME-voiced statement -- e.g. "The
    agent reported X's next earnings date and named every flagged
    sub-signal" -- not eval_dataset.json's `expected_behavior` field
    verbatim (that text is rubric/spec prose, a real confirmed source of
    a uniform 0.00 false-negative across every case tried -- see module
    docstring's "CONFIRMED BUG" section). Callers build their own
    per-case outcome string (see test_q9.py/test_q11.py/test_q13.py's
    GOAL_REFERENCE). Returns 0.0 or 1.0 (binary metric).
    """
    ai_tool_message = _to_ai_message(predicted_tool_calls)
    final_message = AIMessage(content=final_answer)
    user_input = [HumanMessage(content=question), ai_tool_message, final_message]
    sample = MultiTurnSample(user_input=user_input, reference=reference)
    return _goal_metric.multi_turn_score(sample)
