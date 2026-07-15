# Deliverables Traceability

Maps every graded deliverable (per the [Certification Challenge rubric](https://docs.google.com/spreadsheets/d/1_sV7MuHj674BikQ4Fe1QYiGVUTaBybL3ND5IN2ILq3Q/edit?usp=sharing)) to its exact code location in this repo.

Some deliverables are narrative answers to a question (e.g. "describe your solution in one sentence") and have no code artifact of their own — those are marked **Narrative** and point to the PRD section that answers them instead of a file:line. Deliverables backed by real, running code point to the specific file, function, and line number. All line numbers verified directly against the current state of the files in this repo as of this commit.

Full narrative answers to every deliverable live in [`Personal Portfolio Copilot-prd.md`](./Personal%20Portfolio%20Copilot-prd.md); this file is the code-location index referenced from the submission form, not a replacement for that document.

---

## Task 1: Defining your Problem, Audience, and Scope

| Deliverable | Code Location |
|---|---|
| Problem statement (1 sentence) | **Narrative.** PRD Task 1 §1. No code artifact — this is a written answer. |
| Why this is a problem for your specific user | **Narrative.** PRD Task 1 §2. No code artifact. |
| Workflow diagram (current-state, how the user solves this today) | `Personal Portfolio Copilot-prd.md`, lines 49–63 (Mermaid `flowchart LR` source, Task 1 §3). |
| List of questions / input-output pairs to evaluate the app | `eval_dataset.json` — all 12 locked questions with `expected_behavior`, `scoring_method`, and `reuses` fields per question. Table version: PRD Task 1 §4. |

## Task 2: Propose a Solution

| Deliverable | Code Location |
|---|---|
| Solution in one sentence | **Narrative.** PRD Task 2 §1. No code artifact. |
| Infrastructure diagram + one sentence per tooling choice | `assets/infrastructure_diagram.svg` (diagram), referenced in `Personal Portfolio Copilot-prd.md` line 147. Backing code for each component: LLM gateway → `llm_gateway.py:93` (`build_chat_llm`, routes through Portkey); agent orchestration → `app/graph.py:208` (`build_graph`); tools → `app/tools.py:143,165,188,247` (`search_filings`, `search_filings_exact`, `search_live_news`, `get_market_data`); vector DB → `test_q1.py:71` (`build_retriever`, Qdrant embedded/in-memory); deploy → `render.yaml`. |
| Agent Workflow Diagram (end-to-end) | `Personal Portfolio Copilot-prd.md`, lines 162–196 (Mermaid `flowchart TD` source, Task 2 §3). Backing code: `app/graph.py:566` (`ask`, the actual request→response flow), `app/graph.py:208` (`build_graph`, wires the ReAct loop + checkpointer), `app/graph.py:210` (`MemorySaver()`, the required memory component). |

## Task 3: Dealing with the Data

| Deliverable | Code Location |
|---|---|
| Data sources & external APIs | `app/tools.py:143` (`search_filings`, Qdrant RAG over SEC filings/transcripts), `app/tools.py:166` (`search_filings_exact`, keyword search), `app/tools.py:189` (`search_live_news`, Tavily), `app/tools.py:248` (`get_market_data`, Finnhub). Ingestion: `fetch_edgar_filings.py`, `fetch_xbrl_financials.py`, `fetch_leadership_events.py`. |
| Chunking strategy + rationale | `test_q1.py:71–74` (`build_retriever`; `chunk_size=512, chunk_overlap=50, length_function=_tiktoken_len`). Reused by the live agent via `app/tools.py:130` (`_get_retriever`, calls `build_retriever` from `test_q1.py`, per the import note at `app/tools.py:14`). |

## Task 4: Build End-to-End Prototype

| Deliverable | Code Location |
|---|---|
| End-to-end prototype, deployed to a public endpoint | Backend: `server.py:119` (`chat`, the `/chat` endpoint wrapping `app/graph.py`), `server.py:107` (`dashboard`). Frontend: `frontend/components/chat.tsx`, `frontend/components/dashboard.tsx`, `frontend/lib/api.ts` (calls the backend over `fetch()`). Deploy config: `render.yaml` (both `portfolio-copilot-backend` and `portfolio-copilot-frontend` services). |

## Task 5: Evals

| Deliverable | Code Location |
|---|---|
| Test dataset | `eval_dataset.json` (12 locked questions, hand-curated against real filings/transcripts for the 4 tracked tickers). |
| Evaluation harness | `run_eval.py` (`load_dataset:75`, `run_rag_q1:108`, `run_rag_q5:131`, `score_rag_question:212`, `run_tool_question:305` — RAGAS triad + custom PASS/FAIL judge dispatch). Per-question harnesses: `test_q2.py`, `test_q5.py`, `test_q7.py`, `test_q7_grounding.py`, `test_q8.py`, `test_q9.py`, `test_q11.py`, `test_q13.py`. Consolidated scorecard: `run_scorecard.py:231` (`build_scorecard`) → `eval_scorecard.json`. |
| Conclusions on performance/effectiveness | **Narrative**, backed by real run output. PRD Task 5 §3. Underlying data: `eval_scorecard.json`, `compare_retrievers_output.txt`. |

## Task 6: Improving Your Prototype

| Deliverable | Code Location |
|---|---|
| Advanced retrieval technique + why it's useful | `parent_child_retriever.py:176` (`split_filing_into_items`), `parent_child_retriever.py:253` (`split_transcript_into_turns`), `parent_child_retriever.py:293` (`split_into_parents`), `parent_child_retriever.py:319` (`build_parent_child_retriever`). Rationale: PRD Task 6 §1. |
| Performance comparison table (before/after) | `compare_retrievers.py:87` (`run_case`), `compare_retrievers.py:115` (`score`) — produces `compare_retrievers_output.txt`. Table rendered in PRD Task 6 §2 and Appendix D. |
| Change to another piece of the solution, evidence via the eval harness | **Change A** (Q9 filings-relevance guard): `app/graph.py:326` (`FilingsRelevance` classifier), `app/graph.py:352` (`_question_needs_filings_check`). Evidence: `test_q9.py:132` (`run_case`), re-run confirms all 3 judge criteria pass. **Change B** (Q13 narrative decoupling): `app/graph.py:388` (`_render_current_status_block`, verdict rendered as a fixed Python block, never model-composed), `app/graph.py:510` (`_render_signal_facts`), `app/graph.py:561` (`_compose_grounded_narrative`). Evidence: `test_q13.py:132` (`run_case`), re-run confirms all 3 judge criteria pass including `honest_framing`. |

## Task 7: Next Steps

| Deliverable | Code Location |
|---|---|
| Keep/change reflection for Demo Day | **Narrative.** PRD Task 7. Code referenced within it: `test_q8.py:76` (`compute_trend_deltas`, deterministic math kept as-is), `app/tools.py:327` (`get_fundamentals_health_score`, worst-of rollup kept as-is), `app/tools.py:106–116` (`_DOC_CACHE`/`_RETRIEVER_CACHE`/`_bounded_cache_set`, bounded caching kept as-is), `llm_gateway.py:93` (`build_chat_llm`, Portkey-routed prompt caching kept as-is). |

## Final Submission

| Deliverable | Location |
|---|---|
| Loom demo video (≤10 min) | https://www.loom.com/share/7c94995f760d445db05bff79efc6561e |
| Written document addressing each deliverable | [`Personal Portfolio Copilot-prd.md`](./Personal%20Portfolio%20Copilot-prd.md), this repo. |
| All relevant code | This repo in full. |
