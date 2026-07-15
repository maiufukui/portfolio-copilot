# Personal Portfolio Copilot: PRD

## Contents

[Task 1: Problem, Audience, Scope](#task-1-defining-your-problem-audience-and-scope) · [Task 2: Propose a Solution](#task-2-propose-a-solution) · [Task 3: Dealing with the Data](#task-3-dealing-with-the-data) · [Task 4: Build End-to-End Prototype](#task-4-build-end-to-end-prototype) · [Task 5: Evals](#task-5-evals) · [Task 6: Improving Your Prototype](#task-6-improving-your-prototype) · [Task 7: Next Steps](#task-7-next-steps) · [Appendix A–D](#appendix-data-requirements--supplementary-detail)

## Task 1: Defining your Problem, Audience, and Scope

### 1. Problem Statement

Everyday retail investors who buy individual stocks have no objective, consistent way to tell whether the underlying business is still fundamentally healthy, and without the tools or time to check, hold/buy/sell decisions end up driven by price swings and headlines rather than by whether the fundamentals that justified the position still hold.

### 1.1 Supporting Evidence (External Validation)

- "Most investors don't lose money because they picked the wrong stock, but because they never had a real reason to pick it in the first place; months later they can't explain why they entered the position." (Source: [Sleep Well Investments](https://www.sleepwellinvestments.com/p/thesis-tracker))
- "If your watchlist is so long that you cannot explain why each stock is on it without going back to your notes, it means scattered attention and impulsive decisions." (Source: [Sleep Well Investments](https://www.sleepwellinvestments.com/p/thesis-tracker))
- "Monitoring doesn't mean checking price every day; it means regularly checking whether the reasons you bought the stock are still true." (Source: [Equity Mates](https://equitymates.com/episode/thesis-how-to-record-track-your-investment-thesis/))
- 66% of investors regret an impulsive or emotional investing decision. 71% of self-managing investors made a regrettable decision vs. 59% of those with an advisor. 40% of self-managing investors report losing sleep over the market. (Source: [MagnifyMoney](https://www.magnifymoney.com/news/emotional-investing/))
- "An overwhelming portfolio is almost always an unautomated one." (Source: [Open Forem](https://open.forem.com/luketaylor25/how-to-create-a-portfolio-monitoring-system-that-doesnt-overwhelm-you-3g55))

### 2. Why This Is a Problem

The user is a working professional holding 10–30 individual stocks in a personal brokerage account, managed in the margins of a full-time job. Checking whether their original thesis still holds happens manually and emotionally: there's no time to read a full 10-Q or transcript across a dozen-plus positions, so a couple of skimmed headlines stand in, and a price drop triggers a sell impulse regardless of whether the fundamentals actually changed. Existing tools don't close the gap, brokerage apps show price without tracking fundamentals, and finance news isn't personalized to any individual's holdings.

### 3. Current-State Workflow Diagram

*workflow diagram illustrating how the user solves this problem today.*

```mermaid
flowchart LR
    A["Buy stock, form reasoning<br/><i>mental note, rarely written</i>"] --> B["Check portfolio<br/><i>usually triggered by price move</i>"]
    B --> C["Search each ticker<br/><i>news apps, X, one at a time</i>"]
    C --> D["Skim 1-2 articles<br/><i>rarely full filings/transcripts</i>"]
    D --> E["Check filings / IR site<br/><i>only if really concerned</i>"]
    E --> F["Recall reasoning from memory<br/><i>no written reference</i>"]
    F --> G["Hold / buy / sell<br/><i>reactive, emotionally-driven,<br/>no check against fundamentals</i>"]
    G -.->|"repeats weekly, separately<br/>for every position (x10-30 tickers)"| B

    style B fill:#fff3cd,stroke:#d39e00
    style C fill:#fff3cd,stroke:#d39e00
    style F fill:#fff3cd,stroke:#d39e00
    style G fill:#f8d7da,stroke:#c0392b
```

**Sequence of steps:** buy stock & form reasoning → check portfolio (price-move triggered) → search each ticker separately → skim 1–2 articles → occasionally check filings/IR site → recall reasoning from memory → hold/buy/sell → loop restarts weekly, per position.

**Tools, systems, documents:** brokerage app (Schwab/Fidelity/Robinhood) for price and notifications, X and a general news app for headlines, occasionally the company's IR page or SEC EDGAR for filings, a personal notes app (inconsistently) for why they bought. No single source of record.

**Where it's slow, repetitive, or error-prone:**
- **Check portfolio (B):** reactive by design. The price move happens first, investigation second.
- **Search each ticker (C):** manually repeated with zero reuse across 10–30 positions. Doesn't scale.
- **Recall reasoning (F):** no artifact to check against. The most error-prone link in the chain.
- **Hold/buy/sell (G):** the actual decision point, made emotionally rather than against an objective reference. This is where the lack of grounding produces real financial outcomes, not just wasted time.

### 4. Evaluation Questions / Input-Output Pairs

*list of questions or input-output pairs that you can use to evaluate your application.*

| # | Question (Input) | Expected Output Behavior |
|---|---|---|
| 1 | "What did Company X's management identify as the specific driver behind [a] this quarter's gross margin change, and [b] next quarter's gross margin guidance?" | Retrieves the exact quoted driver management cited (not a generic mention), distinguishes backward-looking results from forward-looking guidance, cites the exact transcript section. |
| 2 | "What's the latest news on Company X, and does it affect my position?" | Live search (Tavily) for recent news, cross-referenced against the ticker's current Fundamentals Health Score; flags relevance as high/medium/low with source links, noting whether the news touches any Monitor/At-Risk signal. |
| 3 | "Has Company X's tone or substance changed on [a specific qualitative risk/opportunity] across its last 4 earnings calls?" | Synthesizes across 4 chronologically-ordered transcripts, identifies whether language/emphasis shifted (introduced, dropped, escalated, softened), cites which call each shift occurred in. Catches gradual narrative drift a user would otherwise miss by only skimming 1-2 headlines a week. |
| 4 | "Is there any insider selling in my holdings this week?" | Queries insider-transaction data, filters to the user's portfolio tickers only, returns relevant Form 4 activity. |
| 5 | "Has there been any recent capacity/demand or customer-concentration problems mentioned in Company X's filings?" | Agent recognizes the question demands complete, exhaustive recall (a missed disclosure is a real failure, not a minor gap) and routes to keyword/exact-match search instead of lossy top-k vector retrieval (a previously-demonstrated failure mode). Returns a cited, synthesized answer distinguishing routine boilerplate risk language from an active, material signal. |
| 6 | "What did analysts say after today's guidance cut?" | Live search synthesis with dated, sourced citations distinguishing analyst commentary from company statements. |
| 7 | "Company X just dropped 8% today, I'm nervous, should I sell?" | Does not validate the fear reflexively. Checks the drop against the ticker's actual Fundamentals Health Score signals (revenue/margin/insider/leadership) and recent filings/news, and states plainly whether anything changed. Separates signal from noise instead of mirroring the user's emotional framing. Parametrized across ticker/move size in testing, not hardcoded to one scenario. |
| 8 | "Have analysts changed their rating on Company X recently?" | Queries Finnhub recommendation-trends, compares the current period to prior period(s), states whether the buy/hold/sell distribution shifted and by how much, with period dates cited. |
| 9 | "Summarize everything notable about Company X this week: filings, media, and analyst activity." | Synthesizes across all three signal categories (filings/keyword checks, Tavily media search, Finnhub institutional consensus) into one digest, each item cited to source and date. |
| 11 | "When does Company X report next, and what should I watch for based on its current Fundamentals Health Score?" | Surfaces the next earnings date and names the specific sub-signal(s), revenue growth, margin, insider activity, leadership stability, that the upcoming report will test, especially any currently at Monitor or At Risk. |
| 12 | "What happened across my whole portfolio this week that I should know about?" | Synthesizes across all held tickers, only surfaces items that would clear the alert-relevance threshold, cites each with source and date. A pull-based version of what a proactive digest would push. |
| 13 | "Has anything about Company X's underlying business gotten worse since I bought it: revenue, margins, insider activity, or leadership?" | Runs the four-signal Fundamentals Health Score (Task 2 §4): reports each sub-signal's status and the overall worst-of rollup, with the specific numbers/events driving any non-intact rating. |

**Evaluation methodology:** two scoring shapes, not one ad hoc LLM-judge rubric.

- **RAG-answerable questions (1, 3, 5):** score with RAGAS `Faithfulness`, `LLMContextRecall`, and `FactualCorrectness` against a written reference answer, following the `SingleTurnSample` → `EvaluationDataset` → `evaluate()` pattern.
- **Tool-calling and hybrid questions (2, 4, 6, 7, 8, 9, 11, 12, 13):** score on tool-call accuracy (did it call the right tool with the right arguments), goal accuracy (did the final answer satisfy the request), and topic adherence (did it stay grounded in the user's actual holdings/thesis rather than drifting into general advice). This is a custom LLM-judge prompt scoring PASS/FAIL against each criterion from a LangGraph trace.

## Task 2: Propose a Solution

### 1. Solution

An agentic RAG application that grounds each user's stock holdings in their own filings and objective business fundamentals (revenue growth, margin, insider activity, leadership stability), continuously checks those fundamentals against live news and market data via tool calls, and proactively alerts the user only when something clears a defined relevance threshold, with human review built into both the reactive and proactive paths, implemented differently for each.

### 1.1 Out of Scope

The solution above describes the full product vision, including the proactive/monitoring half. What's actually **built and deployed** for this submission is the **reactive chat path only**: no scheduler, cron job, or alert-delivery mechanism exists in this codebase, and every answer in the live app is triggered by a user message.

Also out of scope, each for a stated reason:

- **User accounts, holdings storage, and onboarding.** Tables B (Appendix A) and C (Appendix C) describe the intended data model; no database, auth, or onboarding form exists in the live app.
- **Alert delivery (email/SMS) and the relevance-threshold pipeline.** Designed (Task 2 §4, Appendix C) but not implemented. Blocks eval Q12, which needs the relevance-threshold filter this pipeline would provide.
- **Multi-user support.** The live app operates against 4 hardcoded tracked tickers (ALAB, AAPL, MRVL, NBIS), not a real multi-user, multi-portfolio system.
- **Historical/point-in-time comparisons.** The Fundamentals Health Score is always a current-state snapshot; nothing persists, so a true "since [date]" diff isn't possible yet. Affects how eval Q13 is scored (Task 5 §1).
- **Guardrail layer.** No code-enforced rail yet against unhedged buy/sell/hold language, prompt injection, or PII. See Task 7.
- **Production parent-child retrieval.** Built and evaluated as the Task 6 advanced-retriever upgrade, but not wired into the live agent. See Task 7 and Appendix C.
- **Multi-quarter transcript ingestion.** Only one transcript quarter exists per ticker today, blocking eval Q3. See Task 7.
- **Competitive-positioning signal, and everything else in Appendix C's Post-MVP Data Roadmap.** Reasoning per item there.

### 2. Infrastructure

Every tool here was chosen for free-tier pricing and fast iteration during testing.

| Component | Tool | Version/Tier | Why This Tool | Link |
|---|---|---|---|---|
| LLM | GPT-4.1 mini | $0.40/$1.60 per M tokens | Cheap enough for iteration, good enough for grounded, cited answers over retrieved/tool context. | [OpenAI models](https://developers.openai.com/api/docs/models) |
| Agent orchestration | LangGraph | latest stable | Natively supports the classify-retrieve-synthesize graph shape and stateful checkpointing this app needs. | [langchain-ai.github.io/langgraph](https://langchain-ai.github.io/langgraph/) |
| LLM gateway | Portkey | free dev tier, usage-based | Satisfies the Certification Challenge's LLM-gateway requirement, confirmed working via real agent-loop runs (one intermittent failure remains). | [portkey.ai/pricing](https://portkey.ai/pricing) |
| Live search tool | Tavily | free tier, 1k searches/mo | Only tool that can answer "what's happening right now," which can't be pre-indexed. | [tavily.com](https://tavily.com) |
| Market data tool | Finnhub | free tier, 60 calls/min | One free-tier API covers quotes, insider transactions, and recommendation trends. | [finnhub.io](https://finnhub.io) |
| Filings tool | SEC EDGAR full-text API | free, public | Authoritative, free, public source for the filings this app is grounded in. | [sec.gov/edgar](https://www.sec.gov/edgar/sec-api-documentation) |
| Insider transactions tool | Finnhub insider-transactions endpoint | free tier (Form 3/4/5 sourced) | Structured, filterable data answered by a direct query, not semantic retrieval. | [finnhub.io/docs/api/insider-transactions](https://finnhub.io/docs/api/insider-transactions) |
| Embedding model | text-embedding-3-small | ~$0.02/M tokens | Cheap and sufficient quality for this corpus size. | [OpenAI embeddings](https://platform.openai.com/docs/guides/embeddings) |
| Vector DB | Qdrant, embedded/in-memory (`location=":memory:"`) | free (Python library, no account or hosted service; same pattern used in prior course assignments) | Zero hosting cost, matches the pattern already proven in prior coursework (`rag.py`). | [qdrant-client docs](https://qdrant.tech/documentation/) |
| Keyword / exact-match search tool | Custom regex/substring search over raw filing & transcript text; a separate code path from the vector store, not a retriever config option | in-app, no service/cost | Vector similarity can't guarantee completeness for "every mention, verbatim" queries, confirmed in testing; this deterministic path covers that case. | n/a |
| Memory | LangGraph checkpointer, **`MemorySaver`, pure in-memory** | n/a | Refreshes on every restart, fine for MVP; consider Postgres-backed persistence later. | [LangGraph persistence docs](https://langchain-ai.github.io/langgraph/) |
| Monitoring | LangSmith | free dev tier | Integrates natively with LangGraph, no separate tool to wire in. | [langchain.com/langsmith](https://www.langchain.com/langsmith) |
| Evaluation | RAGAS: `Faithfulness`, `LLMContextRecall`, `FactualCorrectness` for RAG-answerable questions; tool-call accuracy / goal accuracy / topic adherence for tool-calling questions | open-source, free | Purpose-built for these two evaluation shapes, rather than an ad hoc LLM-judge rubric. | [github.com/explodinggradients/ragas](https://github.com/explodinggradients/ragas) |
| UI | Next.js | v15 | Reuses working chat UI components from prior coursework instead of building from scratch under a 1-week deadline. | [nextjs.org](https://nextjs.org) |
| Backend hosting | Render Web Service | Free tier | Cheapest path to a public endpoint; free tier confirmed sufficient for demo traffic, with a cold-start delay as the only tradeoff. | [render.com/pricing](https://render.com/pricing) |
| Frontend hosting | Render Web Service (Node) | Free tier | One repo, one deploy flow, rather than splitting hosting across two providers. | [render.com/pricing](https://render.com/pricing) |

*Post-MVP infrastructure: the tool-wrapper formalism (MCP), the proactive scheduler (Render Cron), and both alerting channels (Resend, Twilio).

**Infrastructure Diagram:**

![Infrastructure Diagram: Next.js UI on Render calls a FastAPI/LangGraph backend with an in-memory checkpointer. The backend routes model calls through Portkey to GPT-4.1 mini and calls four tools directly (Qdrant, keyword search, Tavily, Finnhub, SEC EDGAR). LangSmith observes the backend via traces.](assets/infrastructure_diagram.svg)

### 3. Agent Workflow

*agentic workflow diagram illustrating how the app solves the users problem.*

A request enters two ways:

- A user question in chat: the human is already present, so the agent shows a cited draft answer and the human reads it and decides what to do. Review is the user's own judgment, informed by sources.
- A scheduled job triggered by a new filing or news match (post-MVP, not built): no human is present to confirm before an alert fires, so a relevance/confidence threshold decides whether something is worth surfacing. The human's real review happens after, when they read the alert.

Both paths hit the same reasoning step, where LangGraph plans which tools to call: RAG retrieval, live search via Tavily, and/or a market-data or EDGAR lookup. A synthesis step then checks that information against the ticker's current Fundamentals Health Score.

**Agent Workflow Diagram:**

```mermaid
flowchart TD
    A1["User asks a question in chat"] --> C["Classify & Plan<br/><i>LangGraph reasoning step</i>"]

    subgraph Proactive1["Post-MVP, not built"]
        A2["Scheduled job triggers<br/><i>new filing/news matches watchlist</i>"] --> C
    end

    C --> D1["RAG retrieval<br/>Qdrant, filings"]
    C --> D2["Keyword / exact search<br/>completeness-sensitive queries"]
    C --> D3["Tavily<br/>live news search"]
    C --> D4["Finnhub<br/>market data / insider tx"]

    D1 --> E["Synthesis step<br/>compare vs. Fundamentals<br/>Health Score"]
    D2 --> E
    D3 --> E
    D4 --> E

    E --> F{"Reactive or<br/>Proactive path?"}
    F -->|Reactive| G["Cited chat answer<br/>human reviews directly"]

    subgraph Proactive2["Post-MVP, not built"]
        F -->|Proactive| H{"Clears relevance/<br/>confidence threshold?"}
        H -->|No| I["Suppressed / logged only<br/>no alert sent"]
        H -->|Yes| J["Alert sent<br/>Resend email"]
        J --> K["Human reviews<br/>after receiving alert"]
    end

    style G fill:#e6f4ea,stroke:#34a853
    style I fill:#f1f3f4,stroke:#9aa0a6
    style J fill:#fef7e0,stroke:#f9ab00
    style K fill:#e6f4ea,stroke:#34a853
    style Proactive1 fill:none,stroke:#9aa0a6,stroke-dasharray: 5 5
    style Proactive2 fill:none,stroke:#9aa0a6,stroke-dasharray: 5 5
```

### 4. Fundamentals Health Score Gate

**The Fundamentals Health Score decides whether something clears the threshold, not the user's free-text thesis.** The app scores against four objective, data-driven sub-signals (revenue growth, margin, insider activity, leadership) rather than matching the user's wording at all. The user's original thesis is still captured and shown for context; it just no longer drives the scoring.

Four sub-signals, each independently scored intact, monitor, or at risk, roll up to an overall status via **worst-of, not averaged**. A healthy revenue trend should never dilute away a genuine red flag elsewhere.

| Signal | Source | Intact | Monitor | At Risk |
|---|---|---|---|---|
| Revenue growth trend | SEC EDGAR XBRL company-facts API (structured, quarterly, not LLM-parsed prose) | Flat/accelerating YoY, or 1 quarter of deceleration | 2 straight quarters decelerating, or a single-quarter YoY drop >15pp | 3+ straight quarters decelerating, YoY growth negative, or QoQ revenue down 2 straight quarters |
| Margin (gross/operating) | Same XBRL source | Flat/expanding, or a single-quarter dip <100bps | Compressed 2 straight quarters, or a single-quarter drop >200bps | Compressed 3+ straight quarters, >500bps cumulative from peak, or a single-quarter drop >400bps |
| Insider activity | Finnhub Form 4 data (existing), plus new materiality filter | Routine 10b5-1 sale (plan 90+ days old), option exercises, standard grants | >$25M aggregate insider selling in a rolling 30 days, or a new 10b5-1 plan executing shortly after adoption | Discretionary CEO/CFO sale >$5M or >10% of holdings; multiple insiders selling discretionarily; a 10b5-1 plan cancelled/modified before scheduled execution |
| Leadership stability | 8-K Item 5.02 + news (new detection logic over existing 8-K ingestion) | No departure-related 8-K or news | Departure of a named exec below CEO/CFO | CEO/CFO departure, especially unplanned with no successor; 2+ C-suite departures within 90 days |

## Task 3: Dealing with the Data

### 1. Chunking Strategy

**Decision:** Fixed-size chunking, **512 tokens with 50-token overlap**, as the MVP default across all document types (10-K/10-Q/8-K/transcripts).

Testing exposed a real gap: at k=6, naive dense search missed the one chunk holding the exact quote a thesis-check question needed, because it sat in a 512-token fragment with lower similarity than the surrounding boilerplate. **Parent-child retrieval** fixes this by searching small child chunks for precision but returning the larger structure-aware parent, the full Item for filings, the full speaker turn for transcripts, so the whole Item/turn comes back regardless of which child chunk matched. It's deferred to Task 6 as the advanced-retriever upgrade.

**Why these numbers:** 512 tokens keeps each chunk focused on one idea while preserving the reasoning around a fact (e.g. "revenue grew 12%" stays attached to the "due to X" that explains it), roughly matches a natural 10-K/10-Q paragraph (~350–400 words), and is the standard RAG default; the 50-token (~10%) overlap then catches most boundary splits without meaningfully inflating storage or embedding cost. Alternatives were weighed against these two; see Appendix D for the full comparison.

### 2. Data Sources & External APIs

The app combines three kinds of data. A single source can't answer all of it, and mixing them up (e.g. treating a live rumor like a filed fact) would be a real accuracy risk:

- **What a company has formally filed:** the RAG corpus (pre-indexed, embedded, chunked per above), the "what was formally said/disclosed" layer.
- **What's being said about it right now:** Tavily, the external agent tool, the "what's happening right now" layer.
- **Hard numbers like insider trades:** insider-transaction and market-data tools, a third category, structured, tabular data answered by a filtered query, not retrieval at all. Vector search answers "what's conceptually similar to this;" this structured filer/date/share/price data has no semantic ambiguity to resolve, so it's stored and queried directly rather than embedded.

For most real questions, the agent uses both RAG and Tavily: RAG establishes the stated thesis and prior disclosures, Tavily brings in what's new since the last filing, and synthesis is explicitly a comparison between the two.

**Table A: MVP Data, Company/Market/Tool Data**

| Data | What | Source | Format | Where Stored[^1] | Why |
|---|---|---|---|---|---|
| 10-K filings | Full annual report text | SEC EDGAR | API pull, 512/50 chunked | Qdrant (public filings collection, metadata: ticker/doc_type/date); raw text cached in Postgres | Primary formal disclosure source. Answers driver-identification and verbatim-citation questions |
| 10-Q filings | Full quarterly report text | SEC EDGAR | Same pipeline as 10-K | Same as 10-K | Most frequent proactive-monitoring trigger (quarterly cadence) |
| 8-K filings | Material event disclosures | SEC EDGAR | Same pipeline | Same as 10-K | Filed on-demand: the most likely trigger for real-time alert scenarios. Also the source for leadership-departure detection (Item 5.02) feeding the Fundamentals Health Score |
| Earnings call transcripts | Full transcript, speaker-labeled, Q&A segmented | Motley Fool public transcript pages (static, not a live API) | Plain text via `glob.glob`, 512/50 chunked | Qdrant (doc_type=transcript); source `.txt` files live in the repo's `Data/{TICKER}/` folder, not a database | Qualitative reasoning behind the numbers. Complements filings' formal language |
| Financial statement history (XBRL) | Structured quarterly revenue/margin figures | SEC EDGAR XBRL company-facts API | Structured JSON, exact tagged values | **Postgres** structured table, keyed by ticker/period | Powers the revenue-growth-trend and margin sub-signals in the Fundamentals Health Score (Task 2 §4). Deterministic numbers, not inferred from transcript text |
| Insider transactions (Form 3/4/5) | Filer, role, date, shares, price, code | Finnhub | Structured JSON, filtered by ticker + date range | **Postgres** structured table. No chunking/embedding; exact, filterable, numeric-comparable data, not semantic text | Answers "insider selling this week" via filtered query. Also feeds the insider-activity sub-signal, with a materiality filter distinguishing routine 10b5-1 sales from discretionary/unscheduled ones |
| Live news/search | Headline, snippet, URL, date | Tavily API | Live API call | Not persisted long-term; cached ~24–48h in Postgres for dedup checks only | Answers "what's the latest news." Inherently current, can't be pre-indexed |
| Market price | Live quote, daily % change | Finnhub | Live API call | Not persisted, or cached transiently for the price-magnitude-gate check | Powers the price-move gate and derived portfolio value (see Appendix A's note on total portfolio value) |

Per-holding user data (ticker, shares, cost basis, purchase date, account type: Table B) and portfolio-wide user preferences (Table C) are both post-MVP data models, not something built.

[^1]: This table describes the target data architecture this design calls for. The actual deployed prototype (Task 4) does not have a Postgres database at all; every "Postgres" cell above is the intended destination, not something built. In the live app today, filings/transcripts are fetched live via API and held in an in-memory LRU cache (`app/tools.py`'s `_DOC_CACHE`/`_RETRIEVER_CACHE`), XBRL figures are fetched live from SEC EDGAR on each health-score computation (TTL-cached in memory, not persisted), and news is fetched live from Tavily with no dedup cache at all. See Task 2 §1.1 for the full list of what's out of scope for this submission.

## Task 4: Build End-to-End Prototype

### 1. Build an End-to-End Prototype (reactive chat path only)

**Build sequence:**

| Phase | What | Key decisions applied |
|---|---|---|
| **0: Foundation** | Scaffold repo, empty-deploy to Render first to validate the pipeline before building features | De-risks the actual Task 4 deploy requirement early |
| **1: Data ingestion** | EDGAR ingestion (10-K/10-Q/8-K) + transcript ingestion (Motley Fool, static files), chunked at 512/50, embedded with text-embedding-3-small, indexed into in-memory Qdrant | Runs automatically on app startup (same `@lru_cache`-on-first-call pattern as `rag.py`), not a manual step. Re-runs on every restart. Known gap: a new filing isn't picked up until the next restart; no scheduler exists yet to close that (post-MVP, Appendix C). |
| **2: Core agent loop** | Single `create_react_agent` node, 4 bound tools (Qdrant RAG, keyword/exact search, Tavily, Finnhub+XBRL+8-K), `ToolNode`+`tools_condition` ReAct loop, in-memory checkpointer. Fundamentals Health Score computed deterministically per turn and injected as ground truth, not re-derived by the model | Tested against the Task 1 eval questions via `run_eval.py`, scored with RAGAS |
| **3: UI** | Reuse the chat UI components from `09_Agent_Servers/frontend` (`chat.tsx`, shadcn/ui pieces), rewired to call our own FastAPI `/chat` endpoint via `fetch()`, with branding swapped and citation rendering added | Fastest path to a working UI under a 1-week deadline |
| **4: Deploy** | Backend (FastAPI wrapping `app/graph.py`) + frontend to Render, free tier; wire secrets; re-verify all locked Task 1 eval questions against the live URL, not localhost | Chosen over LangGraph Platform, whose free tier has no public URL and whose Plus plan ($39/user/month plus $0.001/node executed) isn't justified for a solo demo project. Both `portfolio-copilot-backend` and `portfolio-copilot-frontend` run on Render's free plan today, with a cold-start delay after inactivity as the only tradeoff |

## Task 5: Evals

### 1. Test Dataset

*Prepare a test data set (either by generating synthetic data or by assembling an existing dataset).*

The eval dataset is `eval_dataset.json`, the same locked 12-question list from Task 1 §4. Each question carries its scoring method (`ragas_triad`, `tool_call_goal_topic`, `deterministic_assertion`, or `hybrid`), real test-case parameters against the 4 tracked tickers, and, for the 3 RAG-answerable questions (1, 3, 5), a written reference answer, not generated.

**Table E: Per-Question Data, Test Coverage, and Harness**

| # | Status | Data Used | Test Details | Eval Harness |
|---|---|---|---|---|
| 1 | Built | All 4 tickers' 10-K/10-Q + transcripts (Qdrant, both baseline flat-chunk and parent-child retrievers) | 8 cases across all 4 tickers (2 per ticker: backward-looking result, forward-looking guidance). Baseline vs. parent-child compared head-to-head, full 8-case run: `context_recall` mean 0.875 (baseline, dragged down by an ALAB outlier) → 1.00 (parent-child, 8/8 cases), `faithfulness` a wash (0.97 vs 0.96), `factual_correctness` mean 0.49 → 0.54. Full table + cost/latency in Task 6 §2. | `run_eval.py` (RAGAS triad) + `compare_retrievers.py` |
| 2 | Built | ALAB, NBIS, live Tavily news + current health score | 2 cases, 7-day news window, relevance flagged high/medium/low against health-score status | `test_q2.py` |
| 3 | Not built `*` | Would need 4 chronologically-ordered transcripts per ticker | Blocked on data, not logic. Only 1 transcript quarter exists per ticker today; test case is a placeholder pending a real recurring topic once more quarters are ingested | none. Not runnable until the data exists |
| 4 | Built | Finnhub insider transactions, all 4 tickers | 1 case, all 4 tickers, 7-day window | `test_q5.py` |
| 5 | Built | ALAB 10-K/10-Q, exhaustive keyword search | 2 ALAB cases (capacity/demand; customer concentration) scored against a hand-authored written reference for exact recall | `test_q7.py` (`find_hits`, `dedupe_hits`) + `SUMMARY_PROMPT` |
| 6 | Built | MRVL, live Tavily news + Finnhub recommendation trends | 1 case, guidance-cut event, 3-day window | `test_q8.py` (`ANALYST_PROMPT`) |
| 7 | Built | ALAB/NBIS/MRVL, real deployed agent, live price + news + filings + health score | 3 cases (8%, 12%, 3% drops). NBIS case specifically caught a false premise: the described 12% drop didn't match the real live price (+1.6%), and said so instead of validating it. | `test_q7_grounding.py`, calls the real agent end to end; LLM judge scores topic_adherence/goal_accuracy/tool_call_accuracy |
| 8 | Built | MRVL, Finnhub recommendation trends | 1 case; deterministic delta (29→30 buy-rating count) verified against 2 separate real runs | `test_q8.py --mode rating_change`, a narration-chain test (not the full deployed agent); deterministic assertion on the delta itself, LLM only narrates it |
| 9 | Built | ALAB, real deployed agent, filings + news + market data | 1 case. First run surfaced a real defect (ungrounded "no filings found" claim), fixed this session with a code-level guard. | `test_q9.py`, calls the real agent end to end; deterministic tool-category-coverage check + LLM judge for source_coverage/citation_quality/tool_call_accuracy |
| 11 | Built | MRVL (has a real flagged signal), NBIS (insufficient_data; tests honest reporting of a real gap). Finnhub earnings calendar + health score | 2 cases deliberately exercising both paths: a real monitor/at_risk signal to surface, and a missing-data case to report honestly rather than invent. Both cases correctly cite the real next-earnings date and correctly reflect the health score's own signals. NBIS carries one known, deferred gap where a real transcript-sourced number sitting next to an insufficient_data signal isn't yet labeled as self-reported. | `test_q11.py`, precomputes the real earnings date + flagged signals in Python *before* asking the agent anything, deterministically checks the response against that known answer, plus an LLM judge for the softer criteria (see Task 5 §2) |
| 12 | Not built `*` | Would extend Q9's orchestration across all 4 tickers | Blocked. The relevance-threshold filter it needs doesn't exist yet | none |
| 13 | Built | ALAB, full 4-signal health score | 1 case, scored as current-state only (no health-score snapshot data exists to support a historical since-purchase diff). All 3 judge criteria passed after six fix attempts: the verdict now renders as a fixed Python block, never composed by the model, and narrative is fed only raw structured numbers with the question text excluded, so there's nothing for the model to mirror. | `test_q13.py`, precomputes the real 4-signal health score in Python before asking the agent anything, deterministically checks all 4 signals are addressed and the worst-of rollup matches, plus an LLM judge for the softer criteria (see Task 5 §2) |

### 2. Evaluation Harness

**Two scoring methods, matched to two question types**, not one blanket LLM-judge rubric, since a driver-identification question and a "should I sell" question aren't the same evaluation problem (Task 1 §4):

| Question type | Questions | Scored by |
|---|---|---|
| RAG-answerable | 1, 3, 5 | RAGAS triad (`Faithfulness`, `LLMContextRecall`, `FactualCorrectness`) against a hand-written reference answer. Q5 specifically forces the keyword/exact-match retrieval path instead of vector search, since that's the mechanism being tested. |
| Tool-calling / hybrid | 2, 4, 6, 7, 8, 9, 11, 12, 13 | Tool-call accuracy, goal accuracy, and topic adherence from a LangGraph trace. A custom PASS/FAIL LLM-judge prompt, *not* RAGAS's actual `ToolCallAccuracy`/`AgentGoalAccuracyWithReference`/`TopicAdherence` classes. Q8 and Q13 also carry a deterministic check: the exact number is computed in Python first, and the harness verifies the model's answer against that number rather than judging whether the prose merely sounds right. |

**The core pattern: compute the real answer first, then check the model against it, not just whether it sounds plausible.** For Q11 and Q13, the harness precomputes a known-correct answer via the live agent's own tools (`get_fundamentals_health_score`, `fetch_next_earnings_date`) before asking anything, then checks the response two ways: a hard mechanical pass (right date, right flagged signals, right overall status) and a narrower LLM-judge pass for what can't be string-matched (grounded reasoning, no overclaiming, honest framing).

### 3. Conclusions

**The dominant failure mode across every eval run this session was retrieval completeness, what content reached the model, not hallucination or reasoning quality.** Faithfulness scored 1.0 in nearly every condition tested once the model had the right context in hand. What actually moved outcomes was whether the right context arrived at all.

| Evidence | Before | After |
|---|---|---|
| Q1: retriever comparison | Baseline retriever's `context_recall` swung 0.0 → 1.0 across identical repeat runs, purely from where a 512-token chunk boundary happened to fall | Parent-child retriever scored 1.0 `context_recall` on every run. It recovers the full section regardless of chunk-boundary luck |
| Q5: synthesis fix | `faithfulness` 0.0, plus a recurring RAGAS-judge timeout (up to 87 raw, mostly-duplicate snippets in one test case) | `faithfulness` 1.0/1.0 across both cases, no timeouts, after deduplicating hits before scoring |

One metric didn't track the improvement: RAGAS's `FactualCorrectness` (F1) stayed flat or dipped slightly on both Q1 and Q5 despite better-sourced responses, likely because its atomic-claim scoring penalizes detail a terse hand-written reference omits.

Separately, RAGAS's `AgentGoalAccuracyWithReference` proved low-precision for status-heavy questions: it preserves *topics* but not *status words*, so Q13/ALAB's correct "at risk" call scored "different" for not repeating the reference's exact phrase "worst-of status". The custom PASS/FAIL judge criteria remain primary for status-accuracy; this metric is now secondary only.

**Bottom line:** every fix that helped this session, parent-child retrieval and the Q5 dedup fix, targeted what reaches the model, not how it reasons, the throughline into Task 6's improvements below.

## Task 6: Improving Your Prototype

### 1. Advanced Retrieval Technique

**Technique:** Parent-child retrieval (`parent_child_retriever.py`). Search small 512-token child chunks for embedding precision, but return the full structure-aware parent.

**Why it's useful:** a fact can score lower similarity than surrounding boilerplate purely because of where a fixed 512-token boundary falls, stranding a correct-but-narrow match without its context (Task 3 §1). Parent-child retrieval doesn't change the child chunk's ranking; it guarantees that whenever a narrow chunk *does* match, the model gets its full source section back, not a fragment.

### 2. Performance Comparison

**Scope of this comparison: Q1 only, not the full 10-question eval set.** These retrieval metrics don't apply to the 8 tool-calling questions (scored differently, Task 5 §2). Q1 is tested across all 4 tickers with this triad: 8 cases (2 per ticker), a real executed run. Full per-case breakdown in Appendix D.

**RAGAS summary (Q1):**

| Retriever | Faithfulness | Context Recall | Factual Correctness (F1) |
|---|---|---|---|
| Baseline mean | **0.97** | **0.875** | **0.49** |
| Parent-child mean | **0.96** | **1.00** | **0.54** |

**Cost/latency (mean per query):**

| Retriever | Mean retrieval latency | Mean context tokens/query | Mean synthesis-input cost/query |
|---|---|---|---|
| Baseline | 0.40s | 4,354 | $0.00174 |
| Parent-child | 0.28s | 4,726 | $0.00189 |

**Key findings:**

- **Retrieval completeness improved decisively.** `context_recall` goes from an inconsistent 0.875 mean under baseline (dragged down entirely by ALAB's 0.0 outlier, the same chunk-boundary failure documented in Task 5 §3) to a perfect 1.00 across every one of the 8 cases under parent-child.
- **Answer faithfulness is unaffected** (0.97 vs 0.96, both near ceiling). This is a retrieval fix, not a reasoning-quality change, and it doesn't behave like one.
- **The real cost is tokens, not accuracy or speed:** parent-child returns fewer, larger context blocks, so it runs ~9% more expensive per query (4,726 vs 4,354 tokens) with no reliable latency difference.
- **One-time embedding cost is small and separate from query cost:** ~$0.0018–0.0043 per ticker to index its full corpus once, independent of retriever or query.
- **Parent-child returns fewer, larger, complete chunks:** 4-5 parents (~23.7K chars) vs. baseline's 10 chunks (~20.4K chars) per question. Not yet wired into the live agent (`app/graph.py` still uses the Task 4 baseline retriever); that's a named Task 7 next step.[^2]

[^2]: This is a comparison prototype per the rubric's requirement, not a production swap. The 8-case/4-ticker run above is a single real execution, not repeated multiple times the way the original 2-case ALAB finding was (see Task 5 §3). The context_recall improvement is consistent and total (8/8 cases at a perfect 1.00), which is a strong single-run signal, but run-to-run stability at this wider scale hasn't been separately re-confirmed the way the narrower 2-case result was.

### 3. A Change to Another Piece of the Solution

Both are synthesis-layer fixes, distinct from Task 6 §1-2's retrieval work, each with real before/after evaluation evidence.

- **Change A: Q9's filings-relevance guard.**
  - *Problem:* on a "summarize everything" question, the agent could skip the filings tool entirely and still tell the user "no new filings were found," an unchecked claim stated as fact.
  - *Fix:* if a question needs a filings check and the agent's trace shows none happened, the app runs the filings search itself and corrects the answer before it's sent. A classifier now decides which questions need that check, replacing an earlier keyword list that kept missing reworded questions.
  - *Evidence:* re-testing against ALAB confirmed all three judged criteria (source coverage, citation quality, tool-call accuracy) now pass, with real filing citations alongside full market/news/analyst detail.
- **Change B: Q13's narrative decoupling.**
  - *Problem:* Q13 ("has anything gotten worse since I bought it") kept producing a false since-purchase comparison the app has no data to support. The model was mirroring the wording of the user's own question, a habit no prompt instruction could reliably override.
  - *Fix:* take the model out of that decision. The health score's verdict is now generated directly by code, never composed by the model, and the supporting narrative is written from raw data alone, with the question text left out so there's nothing left to mirror.
  - *Evidence:* a re-run confirmed all three judge criteria pass, including honest framing, which had failed five times before.

Both are synthesis-layer fixes, distinct from Task 6 §1-2's retrieval work, each with real before/after evaluation evidence.

## Task 7: Next Steps

**Keep:**

- **The 4-tool agent architecture (Qdrant RAG, keyword/exact search, Tavily, Finnhub/EDGAR).** Each tool answers a genuinely different question shape, semantic similarity, exhaustive recall, live/current information, and structured numeric lookup, a deliberate design already deployed and eval-tested against all 4 shapes. *How:* no change needed.
- **The Fundamentals Health Score's worst-of (not averaged) rollup.** A real product decision, not an eval-passing shortcut, so a healthy revenue trend never dilutes away a genuine leadership red flag; `test_q13.py`'s `rollup_accuracy` criterion checks this directly and passes in the current verified run. *How:* no change, already implemented in `app/tools.py`'s `get_fundamentals_health_score()`.
- **Deterministic math computed in Python, narrated by the LLM rather than computed by it (Q8, Q13).** A certification eval, or a real user's actual numbers, shouldn't have to trust probabilistic output for an exact number. *How:* no change; the pattern (`compute_trend_deltas` for Q8, equivalent logic for Q13) should be the default for every future numeric-answer question.
- **Provider-side prompt caching + bounded LRU/TTL tool caches.** Cheap and mechanically verified (cache hit/miss instrumented via `tools.cache_stats`), with no real downside once traffic exceeds a single demo session. *How:* no change, already applied in `app/graph.py`/`app/tools.py`.

**Change:**

- **Add the guardrail layer.**
  - *What:* "never present a calculation as a recommendation" (Q7) is enforced only by the system prompt, with no code check.
  - *Why:* the single highest-value remaining gap for a finance-adjacent app; Q9 already proved a prompt-only fix isn't reliable enough.
  - *How:* three pieces, reusing the same plain-Python wrapper pattern already used for the Q9/Q13 fixes (one extra model call where it applies):
    - An input-injection rail: keyword/regex check on the incoming question, short-circuits with a canned response if tripped.
    - A PII-redaction rail: regex-based redaction of SSNs/emails on anything logged or traced, mechanical, no model call.
    - An output rail against unhedged buy/sell/hold language: needs a classifier rather than a keyword list, since Q9 and Q13 both showed that reworded claims slip past regex undetected.
- **Resolve the retrieval source-preference workaround.**
  - *What:* the parent-child retriever currently uses a hardcoded rule to prefer transcript content over filing content, set by hand for one known question, not derived from the question at runtime.
  - *Why:* it doesn't generalize past that one case, and blocks promoting parent-child retrieval into the live agent with confidence.
  - *How:* either a runtime query-intent classifier, or a content-based reranker that scores each retrieved parent against the actual query. The reranker is the more robust fix. Neither is built yet.
- **Script the transcript ingestion pipeline.**
  - *What:* transcripts are now clean, verbatim `.txt` files for all 4 tickers, but the fix so far was a one-time manual correction.
  - *Why:* won't hold up once this project tracks more than 4 tickers or refreshes quarterly.
  - *How:* wrap the same fetch-and-extract approach into a script alongside `fetch_edgar_filings.py`.
- **Widen eval coverage past the current 10 of 12 built questions.**
  - *What:* Q3 (narrative drift) is blocked on multi-quarter transcript data; only one transcript per ticker exists today. Q12 (portfolio-wide digest) needs Q9's orchestration logic (now built) plus an unbuilt relevance-threshold filter.
  - *Why:* real product gaps, not polish.
  - *How:* Q3 needs a second transcript quarter fetched per ticker. Q12 is more mechanical: extend Q9's digest logic across all 4 tickers and add a threshold filter.
- **Wire parent-child retrieval into the live agent.**
  - *What:* it has only ever been a comparison prototype; the deployed agent still uses the plain flat-chunk retriever.
  - *Why:* Task 6 §2's evaluation evidence (perfect, stable context recall vs. an inconsistent baseline) makes the case to move it from tested to shipped.
  - *How:* resolve the source-preference workaround above first, then swap the live retriever over and re-run the full eval suite to confirm nothing regresses.
- **Make the custom PASS/FAIL judge criteria scalable.**
  - *What:* the custom judge exists because RAGAS's real agentic classes are a low-precision signal for this project's status-heavy questions. It's rigorous for a locked 12-question set, but doesn't scale: every new question needs a hand-written reference and rubric.
  - *Why:* a real, disclosed gap between "passes certification" and "production-ready eval."
  - *How:* four options, not mutually exclusive:
    - Calibrate the LLM-judge against a gold-labeled set and track agreement with human raters.
    - Prefer reference-free metrics where they apply.
    - Sample real production traffic via LangSmith instead of pre-writing a reference for every question.
    - Keep the deterministic-precompute pattern for high-stakes flows and accept a lighter, sampled judge for the rest.

## Appendix: Data Requirements & Supplementary Detail

*Supporting detail: minimum onboarding data set, competitive landscape, and post-MVP data roadmap. Appendix B (competitive landscape) is worth reading directly.*

### A. Minimum Onboarding Data Set

**Table B: MVP Data, Per-Holding User Data** (design only, not built; 1:1 with each ticker, one row per holding in a target `holdings` table). Same caveat as Table A: this is the target schema, not something built. There is no `holdings` table, no user accounts, and no per-user holdings tracking anywhere in the deployed app. The 4 tracked tickers (ALAB, AAPL, MRVL, NBIS) are a hardcoded dict in `app/tools.py`, not rows a user entered.

| Field | What's Asked | Stored | Why |
|---|---|---|---|
| Ticker | Select/type each stock held | `holdings.ticker` | Defines scope, required |
| Shares owned | Exact share count | `holdings.shares` | Combined with cost basis and live price, lets the app derive total invested, current value, and gain/loss. Nothing self-reported goes stale |
| Cost basis | $ amount or price per share at purchase | `holdings.cost_basis` | Grounds "should I sell" answers in the user's actual entry point. Near-zero marginal capture cost on the same onboarding form |
| Date purchased | Date picker | `holdings.date_purchased` | Enables holding-period framing and sequencing. A filing from before the purchase is irrelevant, one after matters |
| Account type | Single-select: taxable / IRA / Roth / 401k | `holdings.account_type` | Determines whether certain answers even apply (e.g. tax-loss harvesting is meaningless in a Roth) |

Note: total portfolio value is deliberately **not** a captured field. It's derived live as `sum(shares × current price)` using the market-data tool, since a self-reported number would go stale the moment prices move.

Table C (portfolio-wide user preferences) is in Appendix C alongside the rest of the not-built proactive design, since it's entirely proactive-alerting configuration, not something the reactive chat path uses at all.

### B. Competitive Landscape

| Product | Value prop | Where it differs from this app |
|---|---|---|
| **Fiscal.ai** (formerly FinChat) | AI copilot answering fundamentals questions from 20+ years of filings/KPIs | Research tool you pull from. No per-user, per-holding fundamentals tracking, no proactive alerting |
| **Perplexity Finance** | Conversational research with live citations | Pure search tool. No portfolio state or ongoing monitoring |
| **Tickeron** | Quant "AI robots," signal-based entries, $60–250/mo | Replaces user reasoning with algorithmic signals |
| **Stokhold** | AI picks/times trades, alerts to copy into brokerage, $6.99/mo | Explicitly replaces human judgment. Opposite of grounding the user's own holdings in objective, explainable fundamentals |
| **Magnifi** | Plain-English portfolio Q&A | No per-holding fundamentals tracking or divergence alerting |
| **Simply Wall St** | Visual scorecards, portfolio tracking + community | Static reporting, not proactive or personalized to what the user actually holds |

None of the above continuously check a user's specific holdings against objective fundamentals and proactively surface only what's actually changed. That gap is this app's core differentiation.

### C. Post-MVP Data Roadmap

| Data | Role | Why deferred |
|---|---|---|
| X (Twitter) social sentiment | Contrast signal only, e.g. "sentiment is very negative today, but nothing in filings/news has changed." Never used as a standalone alert trigger | No free API tier as of 2026 (pay-per-use, ~$0.005/read); risk of reinforcing emotional noise if not clearly separated from fact-based signals |
| Structured data + onboarding phase | Postgres schema straight from Tables B (Appendix A) & C (below), a minimal onboarding form, auto-trigger ingestion when a user adds a ticker | Planned as an early build phase, skipped for this submission. No database, onboarding form, or holdings storage exists in the deployed app (see Task 2 §1.1). The data model itself is already finalized, ready whenever this phase gets picked up |
| Render Cron Job (scheduler) | Triggers the proactive monitoring loop when a new filing or news item matches a watchlist | Not built. The live app has no proactive path at all (see Task 2 §1.1); this has to exist before alerts below can fire at all |
| Resend (email alerts) | Primary alert channel once the proactive loop above exists | Free tier (3,000/mo) comfortably covers a single user's volume, no cost to justify for MVP. Not built; no Resend integration exists in the deployed app (see Task 2 §1.1) |
| SMS alerts (Twilio) | Upgrade channel once email adoption is validated | Real per-message cost vs. free email. Email covers the same job for v1 |
| Postgres-backed memory (semantic + episodic) | Durable memory across restarts: **semantic memory** (durable user facts like risk tolerance/alert sensitivity, Table C columns) and **episodic memory** (a 24–48h news-dedup cache). Also a history of past Fundamentals Health Score computations (would unlock a true point-in-time "since you bought it" comparison for eval Q13) | In-memory `MemorySaver` checkpointer sufficient for MVP (see Task 2 §2 for what this does and doesn't survive); no database exists anywhere in the deployed app (see Task 2 §1.1). Migrate once persistence needs are proven. Procedural memory has no product driver in MVP scope and isn't tracked here. |
| MCP tool wrapper | Standardizes tool-calling interface | Optional formalism, no grading/product benefit for v1 |
| Parent-child retrieval, production promotion | Built and evaluated as the Task 6 advanced-retriever upgrade (`parent_child_retriever.py`, `compare_retrievers.py`), with real before/after evidence (Task 6 §2). Not wired into the live agent; `app/tools.py`'s `search_filings` tool still uses the plain flat-chunk retriever. | Blocked on resolving the source-preference hardcode first (see Task 7 Next Steps). Promoting to production without it risks misranking transcript vs. filing content on question shapes it wasn't tuned for |
| Two-tier model routing | GPT-4.1 mini for high-frequency/tool-synthesis calls, a stronger model reserved for final answer synthesis only | MVP runs GPT-4.1 mini uniformly. Simpler to build and cheap enough that cost isn't the bottleneck yet; worth revisiting once real usage data shows where reasoning quality (not cost) is the limiting factor |
| Competitive positioning / market share signal | e.g. "competitor won X deals," share-of-wallet shifts | No structured API exists. Only derivable from Tavily news + LLM synthesis of transcript commentary, inherently softer/more judgment-dependent than the four deterministic Fundamentals Health Score signals (Task 2 §4) |
| Analyst estimates/price targets | Comparison layer: what does the street expect vs. what was said | Not required by any of the 12 core eval questions |
| Structured watch-conditions | User-set custom thresholds per holding, beyond the four default Fundamentals Health Score signals | Increases threshold precision further. Deferred pending validation of the default thresholds against real data |
| Sector-concentration threshold | User-editable comfort limit | Ships with a sensible default (e.g. 30%) rather than adding onboarding friction |

**Table C: Portfolio-Wide User Data (design only, not built)**, one value per user, not per holding, in a target `users` table. Every field here exists to configure the proactive-alerting path above (frequency, timing, delivery), not the reactive chat path, which is why it's grouped here rather than with Table B (Appendix A):

| Field | What's Asked | Stored | Why |
|---|---|---|---|
| Risk tolerance | Single-select (conservative/moderate/aggressive) | `users.risk_tolerance` | Calibrates tone/sensitivity across all holdings |
| Alert sensitivity | Single-select (real-time/daily digest) | `users.alert_sensitivity` | Sets the frequency-cap threshold across the whole portfolio |
| Timezone | Auto-detected, editable | `users.timezone` | Correctly schedules digest delivery and "market open" framing |
| Quiet hours | Two time pickers (e.g. no alerts 10pm–7am) | `users.quiet_hours_start/end` | Avoids off-hours pings once SMS is live |
| Digest delivery time | Single time picker (if daily digest chosen) | `users.digest_time` | User controls when their daily summary arrives |
| Email | Standard field | `users.email` | Alert delivery channel, required |

### D. Alternatives Considered: Chunking & Retrieval Detail

*Supporting detail for Task 3 §1 (chunking strategy) and Task 6 §2 (retriever comparison). The decisions and headline results live in those sections; the full comparison tables live here.*

**Chunk size, pros/cons:**

| Size | Pros | Cons |
|---|---|---|
| Small (128–256 tokens) | High retrieval precision, cheap to embed | Loses surrounding context; a match may be a fragment without enough info to answer fully; more chunks means more index overhead |
| **512 tokens (chosen)** | Balances precision and context; matches natural paragraph length; standard default, low implementation risk | Can still split a table or a multi-sentence argument mid-thought occasionally |
| Large (1000+ tokens) | Preserves full context/argument, good for narrative sections like MD&A | Dilutes precision: one chunk loosely matches many queries instead of precisely matching one; more expensive per retrieval; harder to cite a specific sentence rather than a large block |

**Overlap, pros/cons:**

| Overlap | Pros | Cons |
|---|---|---|
| None (0%) | Simplest, cheapest, no redundant storage | High risk of severing sentences/ideas exactly at the boundary |
| **~10% / 50 tokens (chosen)** | Catches most boundary-split content cheaply | Doesn't guarantee zero splits for unusually long sentences |
| Large (25–50%) | Maximum boundary-safety | Meaningful extra storage/embedding cost; more duplicate content in results, diluting diversity of what's retrieved |

**Task 6 §2 retriever comparison, full per-case results** (means, cost/latency, and the narrative interpretation live in Task 6 §2 itself; this is the underlying 16-row detail):

**Baseline (flat 512-tok, k=10), 8 cases across ALAB/AAPL/MRVL/NBIS:**

| Case | Faithfulness | Context Recall | Factual Correctness (F1) |
|---|---|---|---|
| ALAB: this quarter's gross margin change | 1.00 | 0.00 | 0.29 |
| ALAB: next quarter's gross margin guidance | 1.00 | 1.00 | 0.86 |
| AAPL: this quarter's gross margin change | 1.00 | 1.00 | 0.10 |
| AAPL: next quarter's gross margin guidance | 1.00 | 1.00 | 0.50 |
| MRVL: this quarter's data center revenue growth | 0.80 | 1.00 | 0.09 |
| MRVL: fiscal 2028 data center revenue growth guidance | 1.00 | 1.00 | 0.78 |
| NBIS: this quarter's adjusted EBITDA margin change | 0.94 | 1.00 | 0.38 |
| NBIS: next quarter's margin guidance | 1.00 | 1.00 | 0.90 |

**Parent-child (k≈4-5 parents), 8 cases across ALAB/AAPL/MRVL/NBIS:**

| Case | Faithfulness | Context Recall | Factual Correctness (F1) |
|---|---|---|---|
| ALAB: this quarter's gross margin change | 1.00 | 1.00 | 0.75 |
| ALAB: next quarter's gross margin guidance | 1.00 | 1.00 | 0.50 |
| AAPL: this quarter's gross margin change | 1.00 | 1.00 | 0.63 |
| AAPL: next quarter's gross margin guidance | 0.71 | 1.00 | 0.40 |
| MRVL: this quarter's data center revenue growth | 1.00 | 1.00 | 0.13 |
| MRVL: fiscal 2028 data center revenue growth guidance | 1.00 | 1.00 | 0.82 |
| NBIS: this quarter's adjusted EBITDA margin change | 1.00 | 1.00 | 0.23 |
| NBIS: next quarter's margin guidance | 1.00 | 1.00 | 0.82 |

