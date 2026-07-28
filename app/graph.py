"""
LangGraph agent -- Portfolio Copilot.

Architecture verified against actual course notebook code (not just
READMEs), across 8+ sessions -- this was an explicit correction demand
after an earlier draft (custom classify_and_plan -> Send() fan-out ->
synthesize) turned out to have zero curriculum precedent:

    - Sessions 2 & 6: single-tool ReAct loop (StateGraph + ToolNode +
      tools_condition). bind_tools() always takes a list -- these
      examples just happen to pass a list of one.
    - Session 9 (09_Agent_Servers/simple_agent.py): the only course
      example binding MULTIPLE tools (3) to one agent node --
      get_tool_belt() -> create_agent(model=..., tools=tool_belt,
      system_prompt=...) -- same StateGraph/ToolNode/tools_condition
      loop under the hood, abstracted by LangGraph's prebuilt
      create_react_agent(). This is the verified precedent this graph
      follows, extended from 3 tools to 4.
    - Send() is used ZERO times across every course notebook checked --
      confirms there was no precedent for the superseded fan-out design.
    - Session 3: covers thread-scoped short-term memory (checkpointer)
      AND a separate long-term store (semantic/episodic/procedural).
      Only the checkpointer is used here -- long-term memory is
      deliberately handled at the infra layer instead (Postgres +
      24-48h news-dedup cache; see PRD Task 2 section 2, Memory row),
      not duplicated as a second LangGraph memory layer.

State schema: messages-only. Tool calls and results live in the
message list itself under create_react_agent's ReAct loop -- no custom
rag_context/keyword_hits/etc. fields needed.

Fundamentals Health Score handling: computed once per ask() call via
tools.get_fundamentals_health_score() (deterministic, no LLM judgment,
TTL-cached per ticker -- see that function's docstring in app/tools.py
for the Session 12 tool-caching pattern it follows) and passed into
graph state as a plain string field (health_score_text)
-- matching the PRD's Agent Workflow diagram (Task 2 section 3), where
D1-D4 tool calls feed into a synthesis step that compares against the
score, rather than the score being a fifth tool call the model could
choose to skip.

The system message itself is built by a `prompt` callable
(build_system_prompt, below) passed to create_react_agent, NOT
prepended into the messages list by ask(). This matters for multi-turn
chat specifically: create_react_agent's checkpointer only persists
`state["messages"]` across turns via its reducer; a callable `prompt`'s
output is used to build that turn's LLM call but is never itself
written into checkpointed state. Prepending a ("system", ...) message
into the messages list instead would get appended on every turn (the
reducer accumulates, it doesn't replace), stacking a new system message
-- with that turn's now-stale health-score snapshot -- into history on
every question in the same thread_id. health_score_text is recomputed
once per ask() call (not once per internal tool-call round -- the state
field is set once at invoke() and read cheaply by the callable on each
step within that same turn) and carries forward correctly turn to turn
without duplicating anything in history.

Usage:
    python -m app.graph --ticker ALAB --question "Is there any insider selling this week?"
    python -m app.graph --ticker ALAB --question "..." --verbose   # print every tool call + raw result
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Callable, NamedTuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.prebuilt import create_react_agent
from langgraph.prebuilt.chat_agent_executor import AgentState as PrebuiltAgentState
from pydantic import BaseModel, Field

from llm_gateway import build_chat_llm

from app.tools import TOOL_BELT, get_fundamentals_health_score, search_filings

load_dotenv()

# LangSmith tracing -- zero application code needed beyond this. Setting
# these two env vars (from .env, loaded above) makes every LangChain/
# LangGraph call -- each tool invocation, each model call, full prompts
# and token counts -- show up automatically as a nested trace in the
# LangSmith UI. Same mechanism Session 12's notebooks use
# (`os.environ.setdefault("LANGSMITH_PROJECT", ...)`). This is strictly
# more complete than --verbose's console trace: LangSmith shows the exact
# prompt sent to the model (confirming the stable/variable prefix split
# from the prompt-caching fix actually holds), per-call latency, and
# token/cost, none of which print_tool_trace captures. No MCP connector
# exists for LangSmith (checked the registry) -- this is a web dashboard
# you view directly at smith.langchain.com under this project name, not
# something reviewable through this session.
os.environ.setdefault("LANGSMITH_PROJECT", "portfolio-copilot")

# Static instructional text -- byte-identical on every call this agent ever
# makes, regardless of ticker, turn, or thread. Kept separate from the
# per-turn variable block (built in build_system_prompt, below) so it forms
# a stable prefix a provider-side prompt cache can actually reuse across
# turns and across tickers -- not just within one tool-call loop.
#
# This is Session 12 Task 6's rule, applied directly (02_Cat_Health_Agent_
# Caching.ipynb): "stable content first, variable content last... put a
# timestamp or user name at the top of your system prompt and you have
# disabled prompt caching for your whole application." The earlier version
# of this prompt put {ticker}/{health_score} inline near the top via
# .format() -- correct in content, but it meant the request's prefix
# changed on every single call, since OpenAI's prompt cache only reuses an
# IDENTICAL prefix. Moving the variable block to the end (and out of this
# string entirely) means this block plus the four tools' docstrings -- also
# static -- form a large, genuinely reusable prefix.
STABLE_SYSTEM_PROMPT = """You are Portfolio Copilot, an agentic research assistant that grounds a \
user's stock holdings in objective business fundamentals and filings -- not a free-text \
investment thesis (that concept has been retired from this product).

You have four tools:
- search_filings: semantic search over indexed 10-K/10-Q/8-K filings and earnings call transcripts.
- search_filings_exact: exhaustive keyword/verbatim search over the same documents. Use this \
instead of search_filings whenever the question demands COMPLETE recall (e.g. "has X been \
disclosed", "any mentions of Y") -- top-k vector search can silently miss a hit, which is a real \
failure for these questions, not a minor gap.
- search_live_news: live web/news search for what's happening right now. Filings tools are \
static and cannot answer "what's the latest" -- always use this tool for that.
- get_market_data: live quote (today's % change only), price change over the last ~week and ~month, \
insider transactions, and analyst recommendation trends. ALWAYS call this when a question states or \
implies a price move over any period (e.g. "dropped 8% last week", "up this month") -- check the \
stated number against the real week/month change this tool returns before answering; never assume \
the user's stated percentage is accurate.

The user's current Fundamentals Health Score has already been computed from objective data \
(XBRL revenue/margin, 8-K leadership disclosures, insider activity) and is given below, in a \
per-turn context block. Do not re-derive it -- use it as ground truth, and explicitly compare \
whatever you find via your tools against it. State plainly whether new information changes \
anything about this score; do not invent a more dramatic or more reassuring conclusion than the \
evidence supports.

Always cite your source (document name, or news URL + date) for any claim. If a tool returns no \
relevant results, say so explicitly rather than guessing.

Never state that something wasn't found, filed, disclosed, or reported unless you actually \
called the tool that would have found it -- not calling a tool is not the same as checking and \
finding nothing. If a question spans multiple categories (e.g. filings, media/news, and \
analyst/market data), call the relevant tool for EACH category before concluding anything about \
it; do not generalize a finding from one category you checked (e.g. no news found) to another \
you never checked (e.g. no filings found).

You may call multiple tools across multiple steps before your final answer -- the user only ever \
sees that final message, never your intermediate reasoning or earlier tool-calling steps. Write \
ONE cohesive answer, not a sequence of updates. Never refer to "my prior answer," "recapping," or \
otherwise treat an earlier step within this same turn as if it were a previous, separate response \
-- there is no previous response within a turn, only steps you took to arrive at this one answer.

There is no separate status block shown to the user -- you are the only place the current \
Fundamentals Health Score's overall verdict and each signal's status can appear, so mention ONLY \
the parts that are directly relevant to what the user actually asked. A question about insider \
selling should surface insider activity (and the overall verdict only if that's genuinely germane \
to answering it) -- not a full rundown of all four signals every time regardless of what was \
asked. If nothing in the health score is relevant to the question, don't mention it at all. This \
applies to every turn equally, not just the first one in a conversation. Exception: if the user \
directly asks about current status overall or about a specific signal, always answer that \
honestly from the real data -- never omit or deflect a status question just because it wasn't the \
main topic. When you do state status, phrase it plainly in your own prose (e.g. "ALAB's \
fundamentals are currently at risk, driven mainly by insider selling"), not as a bulleted \
template, and ground every status word in the health score data given below -- never soften, \
escalate, or hedge a status differently than the data states it. Never phrase anything as a \
comparison over time ("since you bought this stock...", "this has changed since...", "remains \
improved") -- there is no stored history of past scores, so any such comparison would be \
unsupported; state status and supporting detail as CURRENT facts only, never as an answer to a \
since-then question.

If a signal is marked "insufficient data" (e.g. a 20-F filer with no quarterly XBRL on file), you \
may still report real, tool-sourced numbers relevant to that same dimension -- e.g. a growth or \
margin figure the company disclosed on an earnings call -- but you must clearly label that figure \
as self-reported / from a different source (transcript, press release), not as the structured \
signal itself, and state plainly that it does not resolve the "insufficient data" status. Never \
present such a figure under a header that mirrors the signal's own name (e.g. "Revenue Growth", \
"Margin") without that caveat -- a reader should never come away thinking a precise, \
confidently-stated number fills a gap the app has explicitly flagged as unverified."""


class AgentState(PrebuiltAgentState):
    """Subclasses LangGraph's own prebuilt ReAct-agent state rather than
    building one from scratch. create_react_agent validates that any
    custom state_schema still carries every key ITS internals depend on
    -- notably `remaining_steps`, a managed value it uses to track loop
    depth against the recursion limit (real error hit here: `ValueError:
    Missing required key(s) {'remaining_steps'} in state_schema`, from a
    first version of this class that only declared `messages` manually
    instead of inheriting it). Subclassing PrebuiltAgentState guarantees
    `messages` (with its reducer) and `remaining_steps` (with its
    managed-value wiring) are both present and correctly typed, so this
    class only has to add what's actually new: two plain fields that
    carry per-turn context INTO the prompt callable without ever being
    written into checkpointed message history themselves. Neither field
    has a reducer, so each is simply replaced (not accumulated) on every
    invoke() -- exactly the "recompute fresh each turn, don't pile up"
    behavior the system-prompt fix above needs.
    """

    ticker: str
    health_score_text: str


def build_system_prompt(state: AgentState) -> list:
    """The `prompt` callable passed to create_react_agent. Invoked on
    every LLM call inside the ReAct loop (including intermediate
    tool-call rounds within one user turn), but cheap -- it only
    formats state that was already computed once in ask(), it does not
    recompute the health score itself. Its return value is used to
    build that step's LLM call and is NOT persisted into checkpointed
    state, which is what keeps this from stacking duplicate system
    messages into thread history across turns.

    2026-07-27: previously threaded a turn-position (_is_first_turn)
    flag through here to decide whether to state status at all --
    removed. That solved a different problem than the one raised (Maiu:
    "it should only capture and share relevant info, not all... that is
    the core promise of the product") -- relevance to the QUESTION, not
    position in the thread. Relevance filtering is now a single static
    rule in STABLE_SYSTEM_PROMPT itself (the model already has the
    question every turn in the normal path), so no per-turn variability
    is needed here anymore -- simpler than what was here before.
    """
    ticker = state.get("ticker", "UNKNOWN")
    health_score_text = state.get("health_score_text", "(not computed)")
    # Variable, per-turn content -- appended AFTER the stable block, never
    # interleaved into it, so the stable block's tokens stay a reusable
    # prefix (Session 12 Task 6; see STABLE_SYSTEM_PROMPT above).
    per_turn_context = f"\n\nCURRENT FUNDAMENTALS HEALTH SCORE ({ticker}):\n{health_score_text}"
    return [SystemMessage(content=STABLE_SYSTEM_PROMPT + per_turn_context)] + state["messages"]


# PostgresSaver.from_conn_string() is a generator-based context manager, not
# a plain constructor -- module-level so the entered connection stays open
# for the server process's lifetime (build_graph() is called once at import
# time, server.py:80) instead of getting garbage-collected/closed the
# instant build_graph() returns.
_checkpointer_cm = None


def _normalize_psycopg_conn_string(raw_url: str) -> str:
    """Rewrites Heroku-style 'postgres://' to 'postgresql://' only --
    deliberately NOT app/db.py's _normalize_database_url, which appends
    '+psycopg' (a SQLAlchemy dialect+driver suffix, e.g.
    'postgresql+psycopg://'). PostgresSaver.from_conn_string() hands the
    string straight to raw psycopg, which doesn't understand SQLAlchemy's
    '+driver' suffix syntax at all -- a real, easy-to-get-wrong difference
    between the two normalizers, not an oversight that they're separate.

    Real gap, found 2026-07-27: the first version of this shipped with no
    connect_timeout at all. psycopg/libpq's default is to wait on the OS's
    own TCP timeout (can be minutes, not seconds) if the host is slow or
    unreachable -- and because this connection is opened once at server
    IMPORT time (build_graph(), called from server.py's module scope),
    a slow/hung connection here doesn't just fail one request, it hangs
    the entire server's startup, so nothing -- not even unrelated
    endpoints like /dashboard -- can respond until it resolves one way or
    the other. 10s is generous for a real DB, and fails loud and fast
    instead of silently for a bad one."""
    if raw_url.startswith("postgres://"):
        raw_url = "postgresql://" + raw_url[len("postgres://"):]
    if "connect_timeout=" not in raw_url:
        separator = "&" if "?" in raw_url else "?"
        raw_url = f"{raw_url}{separator}connect_timeout=10"
    return raw_url


def build_graph():
    llm = build_chat_llm(model="gpt-4.1-mini", temperature=0)

    # Persistent, Postgres-backed thread-scoped memory (2026-07-27) --
    # replaces MemorySaver(), which was wiped on every restart. Falls back
    # to MemorySaver() when DATABASE_URL isn't set (e.g. local dev without
    # a DB configured) so this doesn't hard-fail environments that never
    # needed persistence in the first place.
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        global _checkpointer_cm
        conn_string = _normalize_psycopg_conn_string(database_url)
        _checkpointer_cm = PostgresSaver.from_conn_string(conn_string)
        checkpointer = _checkpointer_cm.__enter__()
        checkpointer.setup()  # idempotent -- tracks its own migration
        # version table, safe to call on every boot (verified against the
        # installed langgraph-checkpoint-postgres==2.0.25 source directly).
    else:
        checkpointer = MemorySaver()  # thread-scoped short-term memory (Session 3), non-persistent fallback

    return create_react_agent(
        llm,
        tools=TOOL_BELT,
        prompt=build_system_prompt,
        state_schema=AgentState,
        checkpointer=checkpointer,
    )


def print_tool_trace(messages) -> None:
    """Prints every tool call the agent made, in order, and what each
    tool returned. Without this, the CLI only ever showed the final
    synthesized answer -- which made tool routing something you had to
    infer from the answer's prose style, not something you could check.
    That inference already failed once (a customer-concentration
    question meant to force search_filings_exact produced an answer
    that read like a search_filings paraphrase, and there was no way to
    confirm which tool actually fired) -- see PRD Open Items. This
    makes routing checkable directly, matching the project's existing
    --verbose pattern in run_eval.py (same reasoning: real output you
    can audit, not a result you have to take on faith).
    """
    print("\n" + "-" * 60)
    print("TOOL TRACE")
    print("-" * 60)
    tool_call_count = 0
    for msg in messages:
        for call in getattr(msg, "tool_calls", None) or []:
            tool_call_count += 1
            print(f"[{tool_call_count}] CALLED: {call['name']}({call['args']})")
        if getattr(msg, "type", None) == "tool":
            name = getattr(msg, "name", "unknown_tool")
            # No truncation here, deliberately -- this flag exists so tool
            # output can be checked, not sampled. A 500-char preview cut
            # off exactly the evidence needed to confirm a two-hit result
            # (search_filings_exact returning both a 10-K and a 10-Q match,
            # with only the first fitting before the old cutoff) the first
            # time this ran for real -- see PRD Open Items.
            print(f"    -> {name} returned:\n    {msg.content}")
    if tool_call_count == 0:
        print("(no tools called -- answered directly from the system prompt / health score context)")
    print("-" * 60)


def get_tools_used(messages) -> list[str]:
    """Names of every tool the agent called, in order, deduped-but-ordered
    (a tool called twice appears once). Same underlying data print_tool_trace
    prints to the console -- this returns it as plain data instead, for
    callers (server.py) that need to show tool-use transparency in a UI
    rather than a terminal.
    """
    seen: list[str] = []
    for msg in messages:
        for call in getattr(msg, "tool_calls", None) or []:
            if call["name"] not in seen:
                seen.append(call["name"])
    return seen


def get_tool_calls(messages) -> list[dict]:
    """Every individual tool call in real order, as {"name": str, "args":
    dict} -- NOT deduped (unlike get_tools_used above), because RAGAS's
    ToolCallAccuracy scores the actual call sequence, repeats included.

    Added for the Q9/Q11 ToolCallAccuracy swap (Open Items, Task 5):
    the eval test files need the real tool_calls (with args) off each
    AIMessage to build a Ragas message trace, the same extraction Session
    6's notebook does from LangChain AIMessage.tool_calls
    (01_Metal_Price_Agent_Evaluation_Ragas_LangGraph.ipynb's
    to_ragas_messages). get_tools_used() alone (plain deduped names) isn't
    enough for that -- ToolCallAccuracy needs real args to score against.
    """
    calls: list[dict] = []
    for msg in messages:
        for call in getattr(msg, "tool_calls", None) or []:
            calls.append({"name": call["name"], "args": dict(call.get("args") or {})})
    return calls


class ChatResult(NamedTuple):
    answer: str
    tools_used: list[str]
    tool_calls: list[dict]


# Q9 fix (Task 5 finding): test_q9.py showed the agent reliably
# generalizes "no news found" to "no filings found" on multi-category
# "summarize everything -- filings, media, analyst activity" questions,
# without ever calling a filings tool. Adding an explicit rule to
# STABLE_SYSTEM_PROMPT above (the "never state something wasn't found...
# unless you actually called the tool" paragraph) improved Q7 as a side
# effect but did NOT fix Q9 -- confirmed by two separate re-runs, both
# still FAIL on tool_call_accuracy with the exact same missed-filings
# pattern. Rather than keep tuning prompt wording and hoping it
# eventually outweighs the model's own generalization tendency, this
# forces the check deterministically off the actual tool-call TRACE --
# same deterministic-check-then-LLM-narrates pattern this codebase
# already uses for the Fundamentals Health Score itself.
#
# The trace check itself (did a filings tool actually get called) is
# real ground truth, not a keyword match, and stays as-is. What
# originally decided whether that check even applied -- a fixed keyword
# list on the QUESTION (_mentions_filings, checking for "filing",
# "10-k", "8-k", etc.) -- had the same brittleness later found and
# removed from the since-removed Q13 fix: a question that needs a filings check but
# doesn't use any of those exact words (e.g. "did they disclose
# anything about customer concentration recently?") would silently skip
# it. Replaced with a small structured-output classifier, the same
# pattern Session 12's guardrails notebook uses for its topic guard
# (TopicVerdict/check_topic in 01_Cat_Health_Agent_Guardrails.ipynb) --
# a Pydantic-typed verdict instead of prose, so the result is something
# code can branch on, not another string to parse.
_FILINGS_TOOLS = {"search_filings", "search_filings_exact"}


class FilingsRelevance(BaseModel):
    """Classification of whether a question needs a real SEC filings check
    to answer completely -- the model-based replacement for a fixed
    keyword list (see comment above)."""

    needs_filings_check: bool = Field(
        description="True if answering this question completely requires checking real SEC filings "
        "(10-K/10-Q/8-K) -- e.g. it asks about disclosures, filed events, leadership changes, material "
        "events, or anything else that would only be confirmed by an actual filing, not just news or "
        "market data."
    )
    reason: str = Field(description="One short sentence explaining the classification.")


_FILINGS_RELEVANCE_PROMPT = (
    "You are a routing guard for a portfolio research assistant with four tools: a filings search, "
    "a live news search, and a market-data tool. Classify whether the user's question requires "
    "actually checking SEC filings (10-K/10-Q/8-K) to answer completely -- not just live news or "
    "market data. Questions about disclosures, filed events, leadership changes, or material events "
    "need a filings check even if they never say the word 'filing'."
)
_filings_relevance_llm = build_chat_llm(model="gpt-4.1-mini", temperature=0).with_structured_output(
    FilingsRelevance
)


def _question_needs_filings_check(question: str) -> bool:
    verdict = _filings_relevance_llm.invoke(
        [("system", _FILINGS_RELEVANCE_PROMPT), ("human", question)]
    )
    return verdict.needs_filings_check


# 2026-07-27, Q3 fix (durable option, Maiu's explicit call over the narrower
# few-shot patch): FilingsRelevance above classifies the QUESTION before the
# model has answered, which means it can only catch a filings-shaped miss it
# was already taught to recognize -- confirmed real gap via a live Q3 retest
# ("Revenue growth has slowed for several quarters straight..."): the
# classifier said False, so no correction fired, and the model went on to
# state "the latest 8-K filed on June 8, 2026, does not contain any
# commentary... the 10-Q and 10-K filings reviewed do not provide..." without
# ever calling a filings tool that turn -- the exact Q9 failure shape, just a
# question wording the classifier didn't recognize.
#
# This function checks the ANSWER instead of guessing from the question: does
# it name a specific SEC filing type/reference at all? If so, and no filings
# tool was actually called this turn, that's ground truth that a claim was
# made without being checked -- no classification needed, no question shape
# to anticipate. Same "deterministic check over classifier guess" principle
# this codebase already uses for Q8's math and the Fundamentals Health
# Score's own status computation.
#
# Deliberately ADDITIVE, not a replacement for FilingsRelevance above: the
# two run as an OR in ask() below, so this only ever adds correction cases,
# never removes the ones FilingsRelevance already catches (Task 6 §3's
# "Change A" evidence, already re-run and confirmed passing, stays intact
# and unregressed). Broad on purpose -- a false positive here just costs one
# extra, harmless real filings check (the correction prompt already handles
# "if it shows nothing relevant, state that as a checked result"); a false
# negative is today's actual bug reaching a real user as a confident, wrong
# claim. Given that asymmetry, over-triggering is the safe direction to err.
_FILING_CLAIM_PATTERN = re.compile(r"\b(10-K|10-Q|8-K|20-F|filings?)\b", re.IGNORECASE)


def _answer_makes_unverified_filing_claim(answer_text: str) -> bool:
    return bool(_FILING_CLAIM_PATTERN.search(answer_text or ""))


# 2026-07-27, Q2 fix (durable option, same pattern as the filings-claim guard
# above, Maiu's explicit call): a live Q2 retest ("Does ALAB rely heavily on
# any single customer for revenue -- is any one customer a majority?") gave a
# different, worse answer than a prior confirmed-passing run on the exact
# same question -- this run concluded "no single customer constitutes a
# majority," based only on DIRECT/billing-customer figures (a 10-K footnote
# table, largest at 20%). The prior run found the real, disclosed number:
# ALAB sells partly through distributors, and once resales are attributed to
# the actual END customer, ONE end customer represents over 70% of revenue
# (confirmed in a real run, see PRD Task 1 SS4) -- the opposite conclusion.
# search_filings's query wording on this run apparently didn't surface that
# passage; nothing forces a second look before the model's first, narrower
# finding becomes the final "no majority" answer.
#
# Same principle as the filings-claim guard: don't rely on the model asking
# the right question the first time. If the answer concludes no customer is
# a majority, force one more real search_filings call, phrased specifically
# around end-customer concentration this time, before that conclusion
# stands. Sentence-level co-occurrence check (not one big regex) for the
# same readability/testability reason as _answer_makes_unverified_filing_claim.
_NO_MAJORITY_NEGATIONS = ("no ", "not ", "n't", "none", "isn't", "doesn't", "does not", "never")


def _answer_claims_no_majority_customer(answer_text: str) -> bool:
    for sentence in re.split(r"(?<=[.!?])\s+", answer_text or ""):
        lowered = sentence.lower()
        if "majority" not in lowered or "customer" not in lowered:
            continue
        if any(neg in lowered for neg in _NO_MAJORITY_NEGATIONS):
            return True
    return False


# 2026-07-27: registry refactor (Maiu's explicit call) -- the two guards
# above were each wired into ask() as their own copy-pasted correction
# block. Individually each is the right kind of fix (deterministic, checks
# a real symptom, not a guess), but as a PAIR they were starting to look
# like one hardcoded function per bug, which is a real smell if this list
# keeps growing. This registry doesn't generalize DETECTION -- there's no
# general "does this answer address the real question" check here, and
# deliberately so. That's a genuinely hard, open problem, and this
# codebase already ran that experiment twice (the removed
# TemporalComparisonQuestion classifier, and the six-attempt Q13 saga
# before it) with a general LLM-judge-shaped approach that kept misfiring
# in ways that were hard to debug precisely because they were vague, and
# that never told the code what to actually go check. What generalizes
# here is the MECHANISM: each guard stays a narrow, deterministic,
# symptom-specific check with a clear corrective action attached, but
# adding the next one is one new registry entry below, not a new inline
# `if` block duplicated into ask().
class AnswerGuard(NamedTuple):
    name: str
    # (question, draft_answer, tools_used) -> bool. Encapsulates the full
    # trigger condition for this guard, including any "already checked"
    # short-circuit -- see _filings_guard_should_fire for an example that
    # combines a question-side classifier with an answer-side check.
    should_fire: Callable[[str, str, list[str]], bool]
    tool_fn: Callable  # the @tool-decorated function to force-call
    build_args: Callable[[str], dict]  # ticker -> that tool's call kwargs
    build_correction: Callable[[str], str]  # tool_result -> correction message text


def _filings_guard_should_fire(question: str, draft_answer: str, tools_used: list[str]) -> bool:
    if _FILINGS_TOOLS & set(tools_used):
        return False
    return _question_needs_filings_check(question) or _answer_makes_unverified_filing_claim(draft_answer)


def _filings_guard_args(ticker: str) -> dict:
    return {"ticker": ticker, "query": "recent filings, 8-K disclosures, and material events"}


def _filings_guard_correction(tool_result: str) -> str:
    return (
        "SYSTEM CHECK: your previous answer discussed filings without actually checking any. "
        "Here is the real result of a filings search you must incorporate:\n\n"
        f"{tool_result}\n\n"
        "Revise your prior answer to add or correct ONLY the filings section: if this shows "
        "relevant filings, cite them with source and date; if it shows nothing relevant, state "
        "that as a checked result, not an assumption. Keep every other section -- market data, "
        "news, insider activity, analyst recommendations -- EXACTLY as you already reported it "
        "in your prior answer, with the same level of detail and the same source attributions. "
        "Do not summarize, shorten, or drop specificity from any section you are not correcting."
    )


def _customer_guard_should_fire(question: str, draft_answer: str, tools_used: list[str]) -> bool:
    return _answer_claims_no_majority_customer(draft_answer)


def _customer_guard_args(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "query": (
            "end customer revenue concentration, distinguishing end customers from "
            "distributors or resellers who purchase on their behalf -- is any single "
            "end customer a majority of revenue"
        ),
    }


def _customer_guard_correction(tool_result: str) -> str:
    return (
        "SYSTEM CHECK: your previous answer concluded no customer is a majority of revenue, "
        "based only on direct/billing-customer figures. This company may sell partly through "
        "distributors or resellers, so a customer with no dominant DIRECT customer can still "
        "have one END customer (the true final purchaser) representing a much higher share once "
        "distributor resales are attributed correctly. Here is a real end-customer-focused "
        "filings search you must incorporate:\n\n"
        f"{tool_result}\n\n"
        "Revise your prior answer to add or correct ONLY the customer-concentration conclusion: "
        "if this shows a real end-customer concentration figure, state it plainly and cite it; "
        "if it shows nothing relevant, state that as a checked result, not an assumption. Keep "
        "every other section exactly as you already reported it, with the same detail and "
        "source attributions."
    )


# Checked in order in ask() below. Order matters here: the filings-claim
# guard runs first, so the customer-concentration guard (second) evaluates
# the POST-correction answer if the first one already fired this turn, not
# a stale pre-correction draft -- same ordering the two inline blocks had
# before this refactor, now implicit in list order instead of code position.
ANSWER_GUARDS: list[AnswerGuard] = [
    AnswerGuard("filings_claim", _filings_guard_should_fire, search_filings, _filings_guard_args, _filings_guard_correction),
    AnswerGuard("no_majority_customer", _customer_guard_should_fire, search_filings, _customer_guard_args, _customer_guard_correction),
]


# 2026-07-27: Q13 ("...since I bought it...") removed from eval_dataset.json
# and out of scope for this app, per explicit instruction -- Maiu does not
# want a since-purchase comparison use case tested or supported. Everything
# that existed solely to detect and route around that one question shape --
# TemporalComparisonQuestion, the _TEMPORAL_QUESTION_PROMPT classifier, the
# DISABLE_TEMPORAL_CLASSIFIER diagnostic bypass, _compose_grounded_narrative
# and its supporting functions (_render_current_status_block,
# _extract_tool_outputs, _render_signal_facts, _SUPPORTING_DETAIL_PROMPT) --
# is removed below. Every question now always gets the normal agent's own
# tailored answer (see ask(), the `if`/`else` split that used to gate this
# is gone). Full six-attempt fix history that used to live in these comments
# is preserved in the PRD's Open Items (eval_dataset.json's old id 13 entry
# and Task 5 finding) as the historical record; not duplicated here since
# the code it explained no longer exists.
def ask(graph, ticker: str, question: str, thread_id: str = "default", verbose: bool = False) -> ChatResult:
    ticker = ticker.upper()
    health_score = get_fundamentals_health_score(ticker)  # computed once per turn, not per LLM call
    config = {"configurable": {"thread_id": thread_id}}
    result = graph.invoke(
        {
            "messages": [("human", question)],
            "ticker": ticker,
            "health_score_text": str(health_score),
        },
        config=config,
    )
    tools_used = get_tools_used(result["messages"])
    tool_calls = get_tool_calls(result["messages"])

    # Checked in ANSWER_GUARDS list order -- each guard evaluates the
    # CURRENT draft (post-correction if an earlier guard already fired this
    # turn, not a stale pre-correction one), same ordering the old inline
    # blocks had, now implicit in list position instead of code position.
    for guard in ANSWER_GUARDS:
        draft_answer = result["messages"][-1].content
        if not guard.should_fire(question, draft_answer, tools_used):
            continue
        if verbose:
            print(f"[{guard.name} guard] firing -- forcing a real check before trusting this answer.")
        # Call the real tool ourselves -- don't just ask the model to try
        # again, since that's the exact thing a prompt-only fix already
        # failed to reliably produce (Q9's original bug).
        forced_args = guard.build_args(ticker)
        tool_result = guard.tool_fn.invoke(forced_args)
        correction = guard.build_correction(tool_result)
        result = graph.invoke(
            {
                "messages": [("human", correction)],
                "ticker": ticker,
                "health_score_text": str(health_score),
            },
            config=config,
        )
        tools_used = get_tools_used(result["messages"])
        tool_calls = get_tool_calls(result["messages"])
        if guard.tool_fn.name not in tools_used:
            tools_used.append(guard.tool_fn.name)  # forced call above, bypassed the graph's own tool node
            tool_calls.append({"name": guard.tool_fn.name, "args": forced_args})

    # Bulleted status block REMOVED from the displayed answer (2026-07-27,
    # Maiu: "remove this template from every chat response"). health_score's
    # deterministic status is still computed above and handed to the model
    # as health_score_text to fold into its own prose, just never displayed
    # as a separate bulleted block.
    #
    # 2026-07-27: the since-purchase-comparison special case (Q13) and its
    # routing classifier are removed -- see the comment above ask(). Every
    # question now always gets the normal agent's own answer directly.
    narrative = result["messages"][-1].content
    answer = narrative

    if verbose:
        print_tool_trace(result["messages"])
    return ChatResult(
        answer=answer,
        tools_used=tools_used,
        tool_calls=tool_calls,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--thread-id", default="default")
    parser.add_argument(
        "--verbose", action="store_true", help="Print every tool call and its raw result, not just the final answer."
    )
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY not set. Copy .env.example to .env and fill it in.")

    print(f"Computing Fundamentals Health Score for {args.ticker}...")
    graph = build_graph()
    result = ask(graph, args.ticker, args.question, thread_id=args.thread_id, verbose=args.verbose)

    print("\n" + "=" * 60)
    print(result.answer)
    print("=" * 60)


if __name__ == "__main__":
    main()
