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
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
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


def build_graph():
    llm = build_chat_llm(model="gpt-4.1-mini", temperature=0)
    checkpointer = MemorySaver()  # thread-scoped short-term memory (Session 3)
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

    Added for the Q9/Q11/Q13 ToolCallAccuracy swap (Open Items, Task 5):
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
# removed from the Q13 fix: a question that needs a filings check but
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


# Q13 fix (Task 5 finding), full history in the PRD's Open Items --
# summarized here because it explains why this function exists in this
# shape. Four attempts tried to detect-and-correct an LLM-composed
# since-you-bought-it comparison after the fact: a prompt-only rule
# (didn't fix it), a keyword list checking the RESPONSE for banned
# phrases (evaded by paraphrase -- "have not gotten worse... remain
# intact or improved"), that same check gated by a keyword list on the
# QUESTION instead (same brittleness, one level removed), and finally an
# ungated LLM classifier that correctly detected the overclaim but whose
# "please revise" correction turn kept reproducing the same underlying
# problem in new words, and whose later append-only-a-disclaimer version
# left an explicit comparison claim sitting uncontradicted earlier in
# the same message.
#
# Every attempt shared the same flaw: they let the model freely compose
# an answer to a question that invites a since-purchase comparison, then
# tried to police what it said afterward. This function removes the
# STATUS COMPUTATION from the model entirely, following the same
# principle this codebase already uses for Q8 (`compute_trend_deltas`
# computes the real numbers in Python; the LLM only narrates them, never
# computes them) -- Task 7's Next Steps names this as the intended
# pattern for Q13 too. get_fundamentals_health_score() already returns
# the exact, deterministic status of every signal; this function renders
# that as a fixed string the model cannot alter or invent.
#
# 2026-07-27 update: this text is no longer displayed to the user
# directly (Maiu: "remove this template from every chat response"). It's
# now handed to _compose_grounded_narrative as status_facts -- ground
# truth the model paraphrases into its own opening prose sentence rather
# than a bulleted block shown verbatim. What stays structurally
# impossible either way: the model never COMPUTES the status itself,
# only narrates a value this function already determined. That's the
# actual Q13 fix; the display format was always a separate concern from
# it, just coupled by having lived in the same prepended block.
def _render_current_status_block(ticker: str, health_score: dict) -> str:
    order = {"intact": 0, "monitor": 1, "at_risk": 2, "insufficient_data": -1}
    overall = health_score.get("overall", "insufficient_data")
    lines = [
        f"**Current Fundamentals Health Score for {ticker} -- today's snapshot only, "
        "not a comparison to any prior date:**",
        f"- Overall: {overall.replace('_', ' ').upper()}",
    ]
    for name, sig in health_score.get("signals", {}).items():
        status = sig.get("status", "insufficient_data").replace("_", " ")
        label = name.replace("_", " ").title()
        detail = sig.get("reason")
        if not detail and name == "insider_activity" and "total_sell_value_30d" in sig:
            detail = (
                f"${sig['total_sell_value_30d']:,} sold across "
                f"{sig.get('distinct_sellers_30d', 0)} insider(s) in the last 30 days"
            )
        if not detail and name == "leadership" and sig.get("departures"):
            detail = f"{len(sig['departures'])} departure-related 8-K(s) in the last 90 days"
        lines.append(f"- {label}: {status}" + (f" -- {detail}" if detail else ""))
    return "\n".join(lines)


# Q13 fix, 6th attempt: a real re-run of the deterministic-block design
# above (already proven correct in isolation -- it cannot itself contain
# a comparison claim) still FAILed honest_framing. The status block was
# fine; the model's own free-text answer, right underneath it, opened
# with "Since you bought Astera Labs (ALAB), here is what the objective
# data... show" -- almost the exact phrase STABLE_SYSTEM_PROMPT names as
# banned, immediately after telling the model a status block already
# precedes its answer. Not a subtle miss -- a direct instruction
# violation.
#
# Best-supported explanation: the model is mirroring the QUESTION, not
# ignoring the rule. The question literally contains "...since I bought
# it..."; opening a response by echoing the question's own framing is a
# generic, strong conversational habit that a rule buried in a long
# system prompt doesn't reliably beat. Suppressing the model's response
# is fighting the wrong end of the problem -- the fix is to stop the
# model from ever seeing that literal phrasing at the point it composes
# the free-text narrative, not to add yet another instruction telling it
# not to react to something it can still see.
#
# This is the same retrieve-then-synthesize discipline this app's RAG
# design already runs on (Task 3 SS3: retrieval and synthesis are
# separate steps), applied one step further in: the supporting-detail
# narrative is now composed from the raw TOOL OUTPUTS this turn already
# gathered, in a standalone prompt that never includes the user's
# original question text at all -- there is nothing for it to mirror.
# Routing to this path uses a structured classifier on the QUESTION
# (TemporalComparisonQuestion, same shape as Q9's FilingsRelevance), not
# a keyword list -- scoped narrowly so Q7/Q9/Q11 and every other
# question shape keep getting the normal agent's own tailored answer,
# unchanged and unregressed.
class TemporalComparisonQuestion(BaseModel):
    """Classification of whether a question invites a since-purchase /
    change-over-time comparison this app has no historical data to
    support."""

    invites_temporal_comparison: bool = Field(
        description="True if the question asks whether something has changed, gotten worse/better, "
        "or otherwise implies a before/after comparison against a PRIOR reference point (e.g. 'since "
        "I bought it', 'has this changed', 'is this still a good hold'). False if the question only "
        "asks what's notable/current within a recency window (e.g. 'this week', 'lately') without "
        "comparing to a past state -- a time window is not the same as a before/after comparison. "
        "Also False for a reaction to a live price move asking for a forward decision (e.g. 'it just "
        "dropped X%, should I sell?') -- that asks what the current data supports doing NEXT, not "
        "whether anything has changed since a past reference point."
    )


# Real bug, found via a real test_q9.py run: this classifier fired True on
# Q9's "Summarize everything notable about {company} this week -- filings,
# media, and analyst activity" question, misrouting it into the Q13-only
# signal-facts narrative composer below (confirmed by matching the observed
# response's exact structure -- one paragraph per health-score signal,
# "(structured data)" citations -- to _SUPPORTING_DETAIL_PROMPT's literal
# output shape). Root cause: the model over-generalized "this week" (a
# recency WINDOW) into "temporal" -> "comparison", even though Q9 never
# asks whether anything changed. Fixed with explicit few-shot examples
# below, drawing the window-vs-comparison line directly instead of relying
# on the one-line instruction alone.
#
# Second real report, same mechanism, ALL tickers: the live "{company} just
# dropped {move_pct}% today, I'm nervous -- should I sell?" question (Q7)
# was also getting misrouted here -- "should I sell" reads as adjacent to
# "is this still a good hold" (already a True example below), so the
# classifier over-generalized again. Once routed here, the answer is
# structurally incapable of addressing "should I sell" at all -- this
# composer's prompt never sees the question text by design (see comment
# above TemporalComparisonQuestion), so a misroute doesn't just get the
# tone wrong, it produces an answer that ignores what was actually asked,
# every time, for every ticker. Added an explicit False example below for
# this question shape, and a debug print in
# _question_invites_temporal_comparison so the next real run shows the
# classifier's actual verdict directly in stdout, instead of having to
# infer it indirectly from the response's structure.
_TEMPORAL_QUESTION_PROMPT = """Classify whether this question about a stock holding asks for a \
comparison over time (since purchase, since a date, whether something has changed) rather than \
asking only about current status or what's notable within a recency window.

A question mentioning a TIME WINDOW ("this week", "recently", "lately") is NOT automatically a \
comparison question -- it's asking what's new/notable within that window, not whether anything has \
changed relative to a past state. Only classify True if the question explicitly or implicitly asks \
to compare against a PRIOR reference point (a purchase date, "has this changed", "gotten worse/\
better", "is this still worth holding").

Examples:
- "Summarize everything notable about Astera Labs this week -- filings, media, and analyst \
activity." -> False (asks what's notable within a window, not a comparison against the past)
- "Has anything about Astera Labs's underlying business gotten worse since I bought it -- revenue, \
margins, insider activity, or leadership?" -> True (explicit since-purchase comparison)
- "When does Marvell report next, and what should I watch for based on its current Fundamentals \
Health Score?" -> False (asks about current status and what to watch, not whether anything changed)
- "Is Nebius still a good hold given what's happened since Q1?" -> True (implies a before/after \
comparison)
- "Astera Labs just dropped 8% today, I'm nervous -- should I sell?" -> False (asks what the current \
data supports doing next, a forward decision -- not a claim that something has changed since a past \
reference point)
- "Marvell's gross margin has been bouncing around the last few quarters -- up, down, up again. \
What's driving that, and is the next quarter's guidance more of the same, or something different?" \
-> False (asks whether a metric's current/guided move is consistent with its own recent trailing \
pattern -- a data-driven trend question answerable directly from already-available figures, not a \
comparison against a personal reference point like a purchase date)"""
_temporal_question_llm = build_chat_llm(model="gpt-4.1-mini", temperature=0).with_structured_output(
    TemporalComparisonQuestion
)


def _question_invites_temporal_comparison(question: str) -> bool:
    # Diagnostic bypass, added 2026-07-27, temporary and off by default:
    # forces this classifier to always return False, skipping the LLM
    # call entirely, so the 4 demo questions can be retested through the
    # normal question-aware agent path with zero chance of a misroute
    # into _compose_grounded_narrative. This does NOT remove the
    # classifier or the composer-split fix -- eval_dataset.json's real
    # Q13 ("...since I bought it...") still needs both to pass
    # test_q13.py, and this flag must be unset (the default) for that.
    # Scoped to a env var, not a code deletion, specifically so it's
    # trivial to confirm this stays off outside of deliberate testing.
    if os.environ.get("DISABLE_TEMPORAL_CLASSIFIER"):
        print(f"[temporal-comparison classifier] BYPASSED (DISABLE_TEMPORAL_CLASSIFIER set) -> False")
        return False

    verdict = _temporal_question_llm.invoke(
        [("system", _TEMPORAL_QUESTION_PROMPT), ("human", question)]
    )
    # Debug print, deliberate and permanent for now: the previous fix for
    # Q9 (few-shot examples alone) could not be confirmed working or
    # broken from response shape alone -- this makes the classifier's
    # actual verdict visible directly in every real run's stdout.
    print(f"[temporal-comparison classifier] {question!r} -> {verdict.invites_temporal_comparison}")
    return verdict.invites_temporal_comparison


def _extract_tool_outputs(messages) -> str:
    """Raw tool results from this turn only -- what the fact-grounded
    narrative below is built from, instead of the model's own prior
    answer (which is exactly what kept mirroring the question)."""
    parts = [
        f"[{getattr(msg, 'name', 'unknown_tool')}]\n{msg.content}"
        for msg in messages
        if getattr(msg, "type", None) == "tool"
    ]
    return "\n\n".join(parts) if parts else "(no tool results this turn)"


def _render_signal_facts(health_score: dict) -> str:
    """Supporting numeric detail for every signal that has real computed
    data -- NOT the signal's status/verdict. Status is a separate input
    to the composer now (status_facts, from _render_current_status_block
    -- see _compose_grounded_narrative), stated in the composer's own
    opening sentence; this function stays scoped to supporting facts only
    so that ground-truth status text isn't duplicated or drifted between
    two different functions.

    Exists because a real test_q13.py run against ALAB caught a gap:
    two of the four signals (revenue_growth, margin) are computed
    directly from XBRL data inside get_fundamentals_health_score() and
    never go through an agent tool call, so _extract_tool_outputs()
    alone has nothing to say about them. The narrative composer wrote
    about insider activity and leadership (both tool-sourced that turn)
    but skipped margin entirely -- not a flaky judge result, a real gap
    in what data the composer had access to. This function closes it by
    handing over each signal's raw supporting numbers regardless of
    which source (tool call or structured XBRL fetch) produced them.
    """
    lines = []
    for name, sig in health_score.get("signals", {}).items():
        if sig.get("status") == "insufficient_data":
            continue
        facts = {k: v for k, v in sig.items() if k not in ("status", "reason")}
        if not facts:
            continue
        label = name.replace("_", " ").title()
        lines.append(f"[{label} -- structured data, not a tool result]\n{facts}")
    return "\n\n".join(lines) if lines else "(no additional structured signal data)"


# 2026-07-27: now receives the actual question (see USER QUESTION below),
# reversing this composer's original design, which deliberately never
# showed it the question text -- that isolation existed specifically to
# stop Q13-shaped mirroring ("since you bought it..."). Per Maiu,
# explicit: disregard that concern for this fix -- relevance filtering
# needs to know what the question is actually about, and there is no
# way to judge relevance without seeing it. The comparison-language ban
# below stays regardless -- that's a real honesty constraint (the app
# has no purchase-date data, so a since-purchase claim would be false
# regardless of Q13), not eval-scoring infrastructure.
_SUPPORTING_DETAIL_PROMPT = """You are writing a portfolio research answer about the user's \
current Fundamentals Health Score. GROUND TRUTH STATUS below is deterministic, computed from real \
data, not written by you.

USER QUESTION:
{question}

Mention ONLY the parts of GROUND TRUTH STATUS that are directly relevant to answering this \
question -- e.g. a question about insider selling should surface insider activity (and the \
overall verdict only if genuinely germane), not a full rundown of all four signals regardless of \
what was asked. If nothing in the status is relevant to this question, don't mention status at \
all. Exception: if the question directly asks about overall status or a specific signal, always \
answer that honestly from GROUND TRUTH STATUS -- never omit or deflect a direct status question. \
When you do state status, write it as your own plain-prose sentence, not a copy of the bullet \
formatting.

After that, add specific supporting detail ONLY for the signal(s) you determined are relevant \
above -- exact numbers, filing citations, dates -- whether that data came from a tool call (news/\
filings/market data) or from the structured signal data section (computed directly, e.g. XBRL \
revenue/margin figures). Do not add a paragraph for a signal that isn't relevant to the question \
just because it has data available.

Never phrase anything -- the status sentence or the supporting detail -- as a comparison over \
time. Do not use the words "since", "compared to", "change", "remains", or reference when the \
user purchased anything. There is no stored history of past scores; state the current status and \
its supporting facts as CURRENT facts only, never as an answer to a since-then question.

GROUND TRUTH STATUS:
{status_facts}

STRUCTURED SIGNAL DATA:
{signal_facts}

TOOL RESULTS:
{tool_outputs}"""
_narrative_llm = build_chat_llm(model="gpt-4.1-mini", temperature=0)


def _compose_grounded_narrative(question: str, tool_outputs: str, signal_facts: str, status_facts: str) -> str:
    prompt = _SUPPORTING_DETAIL_PROMPT.format(
        question=question,
        tool_outputs=tool_outputs,
        signal_facts=signal_facts,
        status_facts=status_facts,
    )
    return _narrative_llm.invoke([("human", prompt)]).content


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

    if _question_needs_filings_check(question) and not (_FILINGS_TOOLS & set(tools_used)):
        # Call the real tool ourselves -- don't just ask the model to
        # try again, since that's the exact thing the prompt-only fix
        # already failed to reliably produce.
        forced_filings_args = {
            "ticker": ticker,
            "query": "recent filings, 8-K disclosures, and material events",
        }
        filings_result = search_filings.invoke(forced_filings_args)
        correction = (
            "SYSTEM CHECK: your previous answer discussed filings without actually checking any. "
            "Here is the real result of a filings search you must incorporate:\n\n"
            f"{filings_result}\n\n"
            "Revise your prior answer to add or correct ONLY the filings section: if this shows "
            "relevant filings, cite them with source and date; if it shows nothing relevant, state "
            "that as a checked result, not an assumption. Keep every other section -- market data, "
            "news, insider activity, analyst recommendations -- EXACTLY as you already reported it "
            "in your prior answer, with the same level of detail and the same source attributions. "
            "Do not summarize, shorten, or drop specificity from any section you are not correcting."
        )
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
        if "search_filings" not in tools_used:
            tools_used.append("search_filings")  # forced call above, bypassed the graph's own tool node
            tool_calls.append({"name": "search_filings", "args": forced_filings_args})

    # Bulleted status block REMOVED from the displayed answer (2026-07-27,
    # Maiu: "remove this template from every chat response"). It is not
    # simply deleted, though -- _render_current_status_block's deterministic
    # text is still computed and still the single ground-truth source for
    # overall/per-signal status; it's now handed to the model as
    # status_facts (composer path) / health_score_text (normal path,
    # already wired above) to fold into its own prose instead of being
    # displayed verbatim as bullets. Both STABLE_SYSTEM_PROMPT and
    # _SUPPORTING_DETAIL_PROMPT were updated in the same change to
    # instruct the model to actually state that status now -- they
    # previously banned restating it specifically because a separate
    # block was guaranteed to show it. Removing the display without also
    # flipping those two instructions would have silently made the agent
    # stop ever stating the health-score verdict anywhere; see the
    # decision recorded here for why both moved together, not just the
    # display line.
    #
    # 6th Q13 attempt, PARTIALLY superseded 2026-07-27 -- for questions
    # shaped as a since-purchase / has-this-changed comparison, still
    # don't use the agent's own free-text answer as the narrative (it
    # has already seen the question and, on this question shape,
    # historically mirrored its framing -- see comment block above
    # TemporalComparisonQuestion). Still compose the narrative from this
    # turn's raw tool outputs PLUS the health score's own structured
    # signal data (_render_signal_facts -- added after a real test_q13.py
    # run caught the narrative skipping margin entirely, since margin/
    # revenue_growth are computed from XBRL directly and never appear in
    # a tool-call result). What changed: the composer prompt now DOES
    # receive the question text (see _SUPPORTING_DETAIL_PROMPT), because
    # relevance filtering needs it and Maiu explicitly said to disregard
    # the Q13 mirroring concern for this fix. The mirroring risk this
    # isolation existed to prevent is real and undefended again here --
    # disclosed, not silently dropped; test_q13.py (locked eval set)
    # would be the way to notice if it resurfaces. Every other question
    # shape is unaffected and keeps the normal agent answer.
    if _question_invites_temporal_comparison(question):
        narrative = _compose_grounded_narrative(
            question,
            _extract_tool_outputs(result["messages"]),
            _render_signal_facts(health_score),
            _render_current_status_block(ticker, health_score),
        )
    else:
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
