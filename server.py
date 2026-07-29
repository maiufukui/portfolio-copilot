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

from app import db
from app.graph import ask, build_graph
from app.tools import TICKER_TO_COMPANY, get_dashboard_data

load_dotenv()

# DATABASE_URL added alongside the pre-existing three: price history
# (app/tools.py's get_market_data) now depends on Postgres being
# reachable, same hard-requirement status as the LLM/search/market-data
# keys below -- a missing DATABASE_URL should fail loudly at startup,
# not silently degrade every price-history answer in production.
#
# COHERE_API_KEY added 2026-07-28 (Maiu, explicit call, overriding an
# earlier soft-warning draft of this fix): search_filings's rerank step
# (parent_child_retriever.py's _rerank) silently falls back to a local
# BM25 rerank if Cohere is unavailable -- real retrieved passages either
# way (BM25 doesn't fabricate content, it just ranks the same candidate
# set differently), but a worse-selected top-k directly undermines this
# product's core promise of grounding answers in the actual filing.
# Quality is the top priority here, so this key gets the same
# hard-requirement treatment as OPENAI_API_KEY rather than a silent,
# self-healing degradation nobody would notice without watching logs.
REQUIRED_ENV_VARS = ["OPENAI_API_KEY", "TAVILY_API_KEY", "FINNHUB_API_KEY", "DATABASE_URL", "COHERE_API_KEY"]


def _check_env() -> None:
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        raise SystemExit(
            f"Missing required env var(s): {', '.join(missing)}. "
            "Set them in .env (local) or the platform's environment settings (Render)."
        )


_check_env()

# Create price_snapshots/health_score_history/user_memory/news_dedup if
# they don't already exist -- safe to run on every boot (checkfirst=True
# internally), so this is the "run once on startup" init step rather
# than a separate manual migration step someone has to remember to run
# against a fresh deploy.
db.init_db()

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


class HoldingRequest(BaseModel):
    ticker: str
    shares: float
    cost_basis_avg: float
    purchase_date: str  # YYYY-MM-DD


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


@app.get("/holdings")
def list_holdings() -> dict:
    """Real backend for what frontend/lib/mock-holdings.ts had been
    faking -- see app/db.py's holdings table comment (2026-07-27).
    Both the Dashboard and Portfolio pages call this instead of each
    holding its own copy of MOCK_HOLDINGS, so an edit on one page is
    visible on the other -- the mock file's own docstring named this
    as a known gap ("no shared store... nothing here persists past a
    page reload anyway"), fixed by this being a real shared backend.
    """
    return {"holdings": db.list_holdings()}


@app.post("/holdings", status_code=201)
def create_holding(req: HoldingRequest) -> dict:
    """Create or replace the one row for req.ticker -- one row per
    ticker by design (see app/db.py), so POST and PUT both just call
    upsert_holding(). Ticker validated against TICKER_TO_COMPANY, same
    check /dashboard/{ticker} already does, since the RAG/health-score
    pipeline only has ingested data for these 6 tickers -- accepting an
    unmapped ticker here would create a holding the rest of the app can
    never show a health score or chat answer for.
    """
    ticker = req.ticker.upper()
    if ticker not in TICKER_TO_COMPANY:
        raise HTTPException(status_code=400, detail=f"No data available for {ticker!r}.")
    db.upsert_holding(ticker, req.shares, req.cost_basis_avg, req.purchase_date)
    return {"status": "created", "ticker": ticker}


@app.put("/holdings/{ticker}")
def update_holding(ticker: str, req: HoldingRequest) -> dict:
    """Same upsert as POST -- kept as a separate verb/route so the
    frontend's edit action reads as an update, not a second create,
    even though the underlying write is identical."""
    ticker = ticker.upper()
    if ticker not in TICKER_TO_COMPANY:
        raise HTTPException(status_code=400, detail=f"No data available for {ticker!r}.")
    db.upsert_holding(ticker, req.shares, req.cost_basis_avg, req.purchase_date)
    return {"status": "updated", "ticker": ticker}


@app.delete("/holdings/{ticker}", status_code=204, response_model=None)
def delete_holding(ticker: str) -> None:
    deleted = db.delete_holding(ticker.upper())
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No holding found for {ticker.upper()!r}.")


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
