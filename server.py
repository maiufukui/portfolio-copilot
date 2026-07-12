"""
FastAPI backend -- Portfolio Copilot.

Thin HTTP layer around app/graph.py's build_graph()/ask(). Nothing about
the agent (tools, prompt, checkpointer) changes here -- this file's only
job is to let a browser reach the already-validated agent over the
internet instead of only a local terminal command.

Deliberately NOT a LangGraph Agent Server (no `langgraph dev`/`up`, no
Docker, no threads/runs/assistants protocol). The certification rubric
(Task 4: "deploy your prototype to a public endpoint using a tool like
Vercel, Render, or FastAPI Cloud") and Task 2's explicit requirement
("run it on my phone and laptop in a browser") don't ask for LangGraph's
own serving infra -- they ask for a public, working, browser-reachable
app. A plain FastAPI service on Render satisfies that directly, and the
PRD's Task 4 section 5 already ruled out LangGraph Platform on cost
before this file existed.

CORS is wide open (allow_origins=["*"]) on purpose, not an oversight:
unlike Session 9's frontend (which had to hide a LangSmith bearer API
key behind a server-side Next.js proxy, since anyone holding that key
could run up billed usage against the deployed agent), this backend
never hands the browser any credential. Every secret (OPENAI_API_KEY,
TAVILY_API_KEY, FINNHUB_API_KEY) stays server-side inside app/tools.py
and app/graph.py; the browser only ever calls this service's own
keyless /chat endpoint. There's nothing here for an open CORS policy to
leak.

Usage:
    uvicorn server:app --reload            # local dev, http://localhost:8000
    uvicorn server:app --host 0.0.0.0 --port $PORT   # Render start command
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.graph import ask, build_graph
from app.tools import TICKER_TO_COMPANY, get_dashboard_data

load_dotenv()

REQUIRED_ENV_VARS = ["OPENAI_API_KEY", "TAVILY_API_KEY", "FINNHUB_API_KEY"]


def _check_env() -> None:
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise SystemExit(
            f"Missing required env var(s): {', '.join(missing)}. "
            "Set them in .env (local) or the platform's environment settings (Render)."
        )


_check_env()

# Built once at import time, not per-request -- app/graph.py's build_graph()
# constructs the LLM client, tool belt, and checkpointer. Rebuilding it on
# every request would be wasteful and would also silently reset the
# checkpointer's conversation history on every single call, defeating the
# whole point of thread-scoped memory (Session 3).
graph = build_graph()

app = FastAPI(title="Portfolio Copilot API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # see module docstring -- safe here, no credential ever crosses this boundary
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    ticker: str
    question: str
    thread_id: str = "default"


class ChatResponse(BaseModel):
    answer: str
    ticker: str
    thread_id: str
    tools_used: list[str]


@app.get("/health")
def health() -> dict:
    """Render hits this to confirm the service is up before routing traffic to it."""
    return {"status": "ok"}


@app.get("/tickers")
def tickers() -> dict:
    """The tickers this MVP actually has filings/CIK mappings for --
    single source of truth (TICKER_TO_COMPANY in app/tools.py) so the
    frontend doesn't hardcode its own separate copy of this list."""
    return {"tickers": list(TICKER_TO_COMPANY.keys())}


@app.get("/dashboard/{ticker}")
def dashboard(ticker: str) -> dict:
    """Powers the dashboard cards: Fundamentals Health Score + sub-signals,
    live quote, next earnings date, and recent news for one ticker.
    Deliberately excludes cost basis/shares/gain-loss/% of portfolio --
    see get_dashboard_data()'s comment block in app/tools.py for why."""
    ticker = ticker.upper()
    if ticker not in TICKER_TO_COMPANY:
        raise HTTPException(status_code=400, detail=f"No data available for {ticker!r}.")
    return get_dashboard_data(ticker)


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    if not req.ticker.strip() or not req.question.strip():
        raise HTTPException(status_code=422, detail="ticker and question must both be non-empty.")

    try:
        result = ask(graph, req.ticker, req.question, thread_id=req.thread_id)
    except FileNotFoundError as e:
        # load_ticker_documents() raises this for an unmapped/unindexed ticker --
        # a real, expected user error (wrong ticker), not a server bug. 400, not 500.
        raise HTTPException(status_code=400, detail=str(e))

    return ChatResponse(
        answer=result.answer,
        ticker=req.ticker.upper(),
        thread_id=req.thread_id,
        tools_used=result.tools_used,
    )
