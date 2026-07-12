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
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from langgraph.prebuilt.chat_agent_executor import AgentState as PrebuiltAgentState

from app.tools import TOOL_BELT, get_fundamentals_health_score

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
- get_market_data: live quote, insider transactions, and analyst recommendation trends.

The user's current Fundamentals Health Score has already been computed from objective data \
(XBRL revenue/margin, 8-K leadership disclosures, insider activity) and is given below, in a \
per-turn context block. Do not re-derive it -- use it as ground truth, and explicitly compare \
whatever you find via your tools against it. State plainly whether new information changes \
anything about this score; do not invent a more dramatic or more reassuring conclusion than the \
evidence supports.

Always cite your source (document name, or news URL + date) for any claim. If a tool returns no \
relevant results, say so explicitly rather than guessing."""


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
    """
    ticker = state.get("ticker", "UNKNOWN")
    health_score_text = state.get("health_score_text", "(not computed)")
    # Variable, per-turn content -- appended AFTER the stable block, never
    # interleaved into it, so the stable block's tokens stay a reusable
    # prefix (Session 12 Task 6; see STABLE_SYSTEM_PROMPT above).
    per_turn_context = f"\n\nCURRENT FUNDAMENTALS HEALTH SCORE ({ticker}):\n{health_score_text}"
    return [SystemMessage(content=STABLE_SYSTEM_PROMPT + per_turn_context)] + state["messages"]


def build_graph():
    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)
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


class ChatResult(NamedTuple):
    answer: str
    tools_used: list[str]


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
    if verbose:
        print_tool_trace(result["messages"])
    return ChatResult(
        answer=result["messages"][-1].content,
        tools_used=get_tools_used(result["messages"]),
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
