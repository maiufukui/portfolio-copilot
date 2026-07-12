# Personal Portfolio Copilot

## What this is

Capstone project for **The AI Engineering Certification v1.0**. An agentic RAG + tool-calling
app that answers portfolio-monitoring questions (SEC filings, earnings transcripts, live
market/fundamentals data) for 4 tracked tickers: **ALAB, AAPL, MRVL, NBIS**.

**Deadline:** Tuesday, July 16, 2026, 7pm ET.

## Where things live

- **This repo** (`Portfolio Tracker Assistant/`): all code.
- **PRD**: `Personal Portfolio Copilot-prd.md` (this repo's root) — Task 1–4 (problem, solution,
  data, prototype), Task 5–7 (evals, improvements, next steps), Appendix, and Open Items. Moved
  here from `/Users/maiufukui/v1-0/` so the written deliverable lives inside the graded repo, per
  the rubric's "Public GitHub Repo" requirement. Open Items is the living log of known gaps/bugs
  — read it directly, don't trust a stale summary.
- **Grading rubric**: `/Users/maiufukui/v1-0/rubric.md` — 100 pts across 8 sections. Note the
  "Improving Your Prototype" section has **three** separate line items (advanced retriever 6pt,
  before/after table 2pt, change to *some other* piece of the solution 6pt) — easy to miss the
  third one since it's not about retrieval at all.
- **Course material this project draws from**: `/Users/maiufukui/v1-0/`, numbered folders
  `01_...` through `12_...`. Every technique used here should trace back to a specific session's
  notebook — cite the session when adding new patterns, don't invent unrelated ones.

## Architecture (one paragraph)

FastAPI backend (`server.py` wrapping `app/graph.py`) + Next.js frontend, both deployed on
Render free tier. Agent is a LangGraph `create_react_agent` over 4 tools (Qdrant vector RAG,
keyword/exact-match search, Tavily live search, Finnhub/EDGAR fundamentals). Baseline RAG
(`test_q1.py`) is flat 512-token chunks; `parent_child_retriever.py` is the Task 6
advanced-retriever upgrade (Item-based parents for filings, turn-based for transcripts) —
it's a comparison prototype (`compare_retrievers.py`), not wired into the live app. Eval harness
is `eval_dataset.json` (13 locked questions) + `run_eval.py`, scored with RAGAS.

## Working agreements for this project

- **Verify before asserting.** Read the real file / run the real command before stating a
  technical claim. If unverified, say so explicitly and label it a hypothesis.
- **No file edits without an explicit go-ahead.** "Why," "check," "walk through," "what do you
  think" are requests to discuss, not to build. Only act on a direct instruction.
- **Don't fold a build decision into a multiple-choice question and treat the answer as
  authorization to start building.** Present the choice, then wait for an explicit "go."
- **Re-verify after every fix** by re-running and checking real output — a mechanically-correct
  change isn't the same as a fix that actually worked.
- **Report status precisely**: state what's committed vs. not, tested vs. not, in the PRD vs.
  only in chat.
