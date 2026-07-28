# Portfolio Copilot — Demo Hardening Plan

## The 4 Live Demo Questions (locked)

Ticker: ALAB, unless noted. Source of truth: `check_demo_question_stability.py`'s `DEMO_QUESTIONS`
dict — keep this block in sync with that file if either changes, there's no single shared source
today. Confirmed live and passing end to end (UI + real backend) as of 2026-07-27, including
`frontend/e2e/chat.spec.ts` and 3x repeated-run stability checks per question.

1. **ALAB dropped 12% this week. I'm getting nervous—should I sell?**
2. **Does ALAB rely heavily on any single customer for revenue -- is any one customer a majority?**
3. **Revenue growth has slowed for several quarters straight -- does the latest quarter suggest
   that's stabilizing, or is a bigger slowdown coming?**
4. **Is there anything in ALAB's latest earnings I should be worried about moving forward,
   especially around margin or guidance?**

---

## Open Next Steps

### Semantic / episodic memory

Not built. `MemorySaver()` (`app/graph.py:240`) is short-term, in-memory, thread-scoped only, wiped
on restart. `user_memory` (schema in `app/db.py`) exists but nothing reads or writes it. Two
separable pieces of work, different sizes:

**Persistent conversation memory (small, mechanical).** Swap `MemorySaver()` for LangGraph's
`PostgresSaver` (`langgraph-checkpoint-postgres` package), pointed at the existing `DATABASE_URL`.
Creates its own checkpoint tables, separate from `app/db.py`'s schema. `/chat` is a plain sync `def`
in `server.py`, and the graph is built once at import time, so this is the straightforward sync
variant, not async. Verify after: a conversation survives a restart, and the AnswerGuard's second
`graph.invoke()` on the same thread still behaves correctly with a real DB round trip instead of an
in-memory dict lookup (adds latency per turn, worth measuring). Roughly half a day of real work
including testing.

**Semantic + episodic memory (bigger, real product decisions, not just infra).**

Semantic memory examples for North, stable facts, not tied to one conversation:
- Investment thesis per holding: "bought ALAB for the AI datacenter interconnect thesis, holding
  long term." Changes what a health-score flag should mean to this specific user, a margin dip
  matters more to a short-term trader than someone who stated a 3-year thesis.
- Stated risk tolerance and answer-style preferences: "don't hedge, give me a direct answer" or
  "always show me the bear case first." The most immediately useful one, and cheap to build since
  it's a small, bounded set of facts per user, not something that grows over time.
- Known context not captured elsewhere: "this is a core position, not a trade" or "I already know
  about the customer concentration risk, don't re-explain it every time."

Episodic memory examples for North, records of specific past exchanges:
- "Last week I told you ALAB's top end customer is over 70% of revenue" so the agent doesn't
  re-fetch and re-state the same fact identically, and can reference it ("as I mentioned").
- Already-surfaced news, literally what the `news_dedup` table's schema was built for: don't
  present the same headline as "notable" twice within a 24 to 48 hour window.
- A log of past flags: "on 2026-07-20 I flagged an insider-selling cluster for ALAB," useful for
  detecting an actual pattern over time rather than treating every insider sale as isolated.

A third kind, named directly since `app/graph.py`'s own comment names all three: procedural
memory, about response format rather than facts. Example: "when I ask about margin, always show
QoQ and YoY together," learned from how the user actually reacted to past answers.

What actually needs deciding before any code gets written: what counts as worth remembering and
how it gets captured (explicit "remember that" command vs. an LLM inferring it, which is a new LLM
call, new cost, new failure surface of extracting the wrong thing or missing something); what gets
loaded back in on a new turn (semantic facts are cheap, a small bounded set; episodic memory needs
real relevance retrieval, recent N events or real semantic search over past episodes, or context
grows unbounded). LangGraph has a purpose-built abstraction for this, the Store API
(`BaseStore`/`PostgresStore`), separate from the checkpointer, meant for cross-thread long-term
memory — using that instead of hand-rolling reads/writes against the flat `user_memory(key, value,
memory_type, updated_at)` schema is likely the more durable choice, worth deciding explicitly.
Single-user, no-auth scope removes the per-user partitioning problem, usually half the real
complexity here. Even so: realistically multiple days of real design, build, and test time, mostly
because the hard part is deciding what should be remembered and how transparently it's used, not
the database wiring.

**News dedup specifically (the easy piece of episodic memory).** No LLM, no "what's worth
remembering" judgment call, just a deterministic hash-and-check: hash each news item's URL, check
`news_dedup` for `(ticker, url_hash)` within the 24-48h window before treating it as new, insert if
not seen. Touches two separate live Tavily call sites, not one: `app/tools.py`'s `get_dashboard_data`
(runs on every dashboard load) and the chat agent's `search_live_news` tool (runs only when the
agent chooses it) — worth deciding scope precisely (does chat's proactive tool call need dedup at
all, since it's user-initiated per question, not surfaced unprompted) rather than blanket-applying
to both. A few hours of real work, not days — the easiest item on this list.

---

**Status: planning only. Nothing below is built. Per CLAUDE.md, no work starts on any item until
Maiu reviews and explicitly approves it, item by item.**

This captures the 6 approved-for-planning items, each broken into: what it's for and who it helps,
the real technical steps, and — per the CTO standard — the caveats and systems-level angles not yet
considered. Verified against the actual repo (not assumed) wherever a claim depends on current
code: `app/tools.py`, `app/graph.py`, `server.py`, `fetch_edgar_filings.py`, `fetch_xbrl_financials.py`,
`frontend/app/page.tsx`, and `Data/` were all read directly before writing this.

---

## 1. Persistence layer infrastructure [MUST WORK FOR DEMO]

### Infra decision (finalized): Render paid, not a local file or a new vendor

Earlier in this planning process, local SQLite was floated as the persistence choice. That's wrong
for this deployment target: **Render's free web services have an ephemeral filesystem** — any local
file (a SQLite database included) is wiped on every redeploy *and* every idle spin-down, and free
services spin down after just 15 minutes of inactivity. A local file would get wiped constantly,
including mid-demo if there's a gap between questions.

Neon (an external managed Postgres host) was considered next, but rejected as an unnecessary new
vendor once cost was put on the table as an option. **Decision: upgrade to Render's paid tier and use
Render's own paid Postgres** — same account already in use for hosting this app, zero new company,
zero new signup:

- Backend: Render Starter, **$7/mo** — always-on, no cold starts (replaces the free tier's 15-minute
  spin-down entirely, which was also the likely real cause of "first question after idle pays full
  latency," separate from the persistence question).
- Database: Render Postgres Basic, **~$6–7/mo** — same account, no 30-day free-tier expiration to
  worry about.
- Frontend: **moving to Vercel** (see note below) — free Hobby tier, not a Render service, so no
  added Render cost here.

**Total: ~$13–14/mo**, one paid vendor already in use, versus LangGraph Platform's $35/mo base plus
per-node and per-minute-idle charges (already evaluated and rejected in the PRD; current 2026
pricing confirms that reasoning still holds) or a platform migration to Railway/Fly.io, which was
considered and rejected too — not because they're worse, but because re-platforming this close to a
deadline trades a real migration risk for a marginal few-dollars-a-month saving, which isn't worth
it right now.

**Frontend hosting change, also decided:** move the Next.js frontend to **Vercel** (free Hobby tier).
Vercel is built by the same company as Next.js and is the most natively optimized host for it
(edge caching, image optimization, zero-config GitHub deploys) — a genuine upgrade at zero added
cost, not a new dependency in the same sense as the backend/database decision above. One thing to
know, not hide: Vercel's free Hobby tier is scoped to "personal, non-commercial" use — a
certification capstone project fits that, but it's worth knowing where the line is.

**What requires Maiu's direct action, not something I can do:** upgrading Render's plan and paying
for it, and creating/connecting the Vercel project, both involve entering payment details and/or
account creation — both are things Claude does not do on someone's behalf. These need to happen on
the Render and Vercel dashboards directly before any of the code below can actually be deployed
against them.

### The 3 demo-critical use cases

| Use case | What it unblocks | Why it matters to the end user |
|---|---|---|
| Daily price snapshots | "Dropped X% last week, should I sell?" grounding (this week's real bug) | A common, natural question gets an honestly-grounded answer instead of a non-answer that just recites fundamentals |
| Fundamentals Health Score history | A real since-you-bought-it comparison for eval Q13 | This is the literal core product promise — "has anything gotten worse since I bought it" — and today the app cannot actually answer that; it can only state a current snapshot and disclaim the comparison |
| Semantic + episodic memory | Durable user preferences, 24–48h news-dedup cache | A user shouldn't have to re-state "I prefer concise answers" every session, and shouldn't see the same headline surfaced as "notable" twice |

### Other use cases this same layer could serve — not yet considered, flagged as roadmap unless pulled into demo scope

- **Persisted filings/transcript embeddings.** Right now `_DOC_CACHE`/`_RETRIEVER_CACHE` in
  `app/tools.py` are in-memory only — every Render cold start re-fetches from EDGAR/Motley Fool and
  re-embeds from scratch. This is very likely the *actual* mechanism behind "first question after
  idle pays full latency," which was flagged as a risk days ago but never root-caused to a specific
  cache. This may deserve to be pulled into demo scope given how directly it affects live
  responsiveness — flagging for a decision, not assuming it.
- **LangGraph checkpointer backed by Postgres** (`PostgresSaver` instead of the current
  `MemorySaver()`) — so a mid-demo Render restart doesn't silently wipe the conversation thread.
- **Eval-run history table.** Track RAGAS scores per run over time instead of eyeballing one-off JSON
  output — catches a regression from a code change instead of missing it.
- **Tool-call audit log**, independent of LangSmith (which `app/graph.py`'s own comments already
  note isn't reviewable from this session/environment). Gives a local, always-available source of
  real numbers for the deck's Validation slide instead of hand-copied figures.
- **Insider-transaction history beyond the current 30-day rolling window** — lets the app detect an
  actual *pattern* in an executive's 10b5-1 filings over time, directly deepening the health score's
  own documented caveat that it can't yet distinguish routine plan sales from discretionary ones.
- **API budget tracking per provider** — self-throttle before a hard 429 from Finnhub/Tavily live on
  stage, instead of discovering the limit the hard way.
- **Portfolio-level aggregation** (total value, sector concentration) — both already named post-MVP
  in the PRD, and this is literally the layer they'd be computed from once item 5 (holdings) exists.

### Status: code built and unit-tested; live DB/network verification not yet done from here

Render Starter (backend) and Render Postgres Basic-256mb are both live under the existing Render
account. `DATABASE_URL` is provisioned (confirmed present in local `.env`). All code below is
written and committed-ready. What's actually been verified, precisely:

**Built:**
- `app/db.py` — schema (all 4 tables), URL normalization, `init_db()`, `save_price_snapshot`
  (upsert), `get_price_history` (read-limit, newest-first). No delete/prune function anywhere in it.
- `backfill_price_history.py` — one-time yfinance seed script, ~1 year per ticker, driven by a
  local `TICKERS` list mirroring `fetch_edgar_filings.py`'s (deliberately not importing
  `app.tools`, to avoid dragging the full LangChain/Qdrant import chain into a one-time script).
- `app/tools.py` — dead FMP `fetch_price_history` removed entirely (not kept alongside); replaced
  with a version reading `app/db.py`. `get_market_data` now upserts today's Finnhub quote into
  `price_snapshots` after every real call — the permanent self-snapshot mechanism.
- `server.py` — `DATABASE_URL` added to the required-env-var startup check (fails loudly, not
  silently, if missing) and `db.init_db()` runs at import time, so tables exist on a fresh deploy
  without a separate manual migration step.
- `.env.example`, `requirements.txt`, `requirements-server.txt` updated to match (yfinance kept out
  of the server requirements — same "lean prod image" reasoning already applied to `ragas`).
- `test_db.py` — full suite, described below.

**Tested, for real, from this session:** URL-scheme normalization (all 4 cases), table schema, the
exact SQL `save_price_snapshot`/`get_price_history` generate (compiled against the real Postgres
dialect), the backfill script's DataFrame-parsing logic against a DataFrame shaped like yfinance's
real output (including a dropped-NaN-row case), and that `app.tools` imports cleanly end-to-end with
the new code wired in (`fetch_price_history('ALAB')` against an unreachable DB degrades to `None`
exactly as designed, doesn't crash). 13 tests, all passing.

**Live-verified — run for real by Maiu, locally, against the real Render Postgres (2026-07-25):**
this dev sandbox's outbound network is allowlisted (confirmed to block both the Render Postgres host
and Yahoo Finance), so the integration tests and backfill could only be written and unit-tested from
here, not run live. They have since actually been run, for real, locally:

- `python backfill_price_history.py` — real run: `ALAB: 251 rows written`, plus AAPL/MRVL/NBIS in
  the same run.
- `pytest test_db.py -v` — **18 passed**, including all 5 `TestLiveDatabase` integration tests
  against the real DB (they'd skipped from the sandbox; they ran and passed locally).
- `get_market_data.invoke({'ticker': 'ALAB'})` — real output, the actual bug this was all built to
  fix, confirmed fixed:
  ```
  Price change over time (verify any claimed % move against this -- do not take the user's stated
  number at face value):
    ~1 week (2026-07-20 -> 2026-07-25): -5.67% ($309.09 -> $291.58)
    ~1 month (2026-06-25 -> 2026-07-25): -26.74% ($398.00 -> $291.58)
  ```

One thing hit and fixed along the way: `pytest` was missing from `requirements.txt` (only installed
directly in the sandbox's own test env, not committed) — added now.

**Deployed and confirmed live (2026-07-25):** commit `c37481d` pushed to `github.com:maiufukui/
portfolio-copilot.git`, redeployed on Render, and the actual live chat now answers a real price-drop
question correctly — real week/month numbers, a direct should-I-sell answer grounded in them, correct
sourcing. This also shipped a second, previously-undeployed fix along with it: `app/graph.py`'s
temporal-comparison classifier (the Q13 guard) had been sitting committed-nowhere on disk since an
earlier session, so the live app had never actually had that fix either — the original broken-chat
symptom was two undeployed fixes stacked together, not a new bug introduced this session. Root-cause
chain, for the record: uncommitted local changes -> stale deploy -> misdiagnosed as a live classifier
bug -> falsified by Maiu testing the "today" phrasing directly against the deployed app -> confirmed
via local `--verbose` run that the code was already correct -> traced to `git status` showing 8
modified + 6 untracked files never committed -> fixed by committing and redeploying, not by patching
code that didn't need it.

`render.yaml` also fixed: added `DATABASE_URL` (was missing entirely), removed the now-dead
`FMP_API_KEY` line. Not touched: `plan: free` in both service blocks still doesn't match the actual
provisioned plan (Render Starter, paid) — left alone deliberately, since editing a Blueprint's `plan`
field is a billing-consequential change, not a docs fix, and wasn't asked for.

### Assumptions

- `DATABASE_URL` works in both places it needs to (Render service env var, local `.env`) — confirmed.
- The `yfinance` backfill (below) is a one-time seed script, run once, never called again by the
  live app — a failure there is contained and one-time, not a live-app outage.
- Backfilled history only needs to be good enough to reach the 5- and 21-trading-day-back indices
  `compute_price_change_over` already uses — not a complete or perfectly clean dataset.

### Schema — one migration, four tables, only one wired up this pass

- `price_snapshots(ticker, date, close, captured_at)` — built **and wired** this pass.
- `health_score_history(ticker, computed_at, overall, signals_json)` — schema created now, wiring is
  Q13's real since-purchase-comparison work, a separate item, not this pass.
- `user_memory(key, value, memory_type, updated_at)` and `news_dedup(url_hash, ticker, first_seen_at)`
  — schema created now, wiring is the guardrails/memory item, not this pass.

Creating all four tables in one migration now (rather than three separate schema changes later) is
the call being made here — cheap either way, but worth naming as a deliberate choice, not an
assumption.

### Key technical steps

1. `app/db.py` — SQLAlchemy Core (not the full ORM, matching this project's existing bias toward
   fewer moving parts — same reasoning `llm_gateway.py` used to hand-roll two headers instead of
   adding a package), `psycopg` driver, URL-scheme normalization (`postgres://` → `postgresql://`,
   a real and common failure point if skipped), `init_db()` creating all four tables.
2. **One-time backfill script** (`backfill_price_history.py`, not part of the running app):
   `yfinance`, pull 1 year of daily closes per ticker (cheap and one-time either way, so pulling a
   full year instead of just 90 days avoids a second backfill if a longer-lookback feature gets
   added later) for the 4 tickers actually live today (`TICKER_TO_COMPANY`'s current keys) — not 6.
   Correction from the earlier draft of this plan: item 6 (PANW/DELL) hasn't landed yet, so there
   is no 6th/5th ticker to backfill yet. The script's own ticker list mirrors
   `fetch_edgar_filings.py`'s (a plain local list, not an import of `app.tools`, to avoid dragging
   the full LangChain/Qdrant chain into a one-time script) — update both together when PANW/DELL
   land. Never called again after this runs.
3. **Self-snapshot wiring**, the permanent mechanism: inside `get_market_data`, after the existing
   `fetch_quote()` call, upsert today's price into `price_snapshots` — zero new API calls, reuses
   data already fetched.
4. Replace the dead FMP-based `fetch_price_history` in `app/tools.py` (FMP is confirmed gated for
   real tickers — 402 on ALAB) with `db.get_price_history(ticker, limit=90)`. **90 here is a read
   limit — the most recent 90 rows returned per query — not a retention limit.** Nothing in
   `price_snapshots` is ever deleted. Retention is decided (see below): every row written by step 3
   stays, permanently.
5. `compute_price_change_over` does not change — already written, already tested against a
   simulated case, this only changes where its input comes from.
6. `DATABASE_URL` gets the same secret handling as every other key in `.env`: never logged verbatim,
   never included in a tool-call trace shown to the user.

### Verification, before anything is committed

1. Run the backfill script against all 4 tickers — confirm real rows land in `price_snapshots` for
   each, and spot-check a couple of close values against a known source, not just "the script exited
   without an error."
2. Run `get_market_data` for ALAB locally — confirm it now returns real week/month numbers (not the
   "not enough history" message, since the backfill just seeded a year of data).
3. Deploy, then ask the actual "ALAB dropped X% last week" question against the **live** app —
   confirm it answers the question instead of dodging it, the original bug this all started from.

### Retention — decided

`price_snapshots` retains everything indefinitely: the full 1-year backfill (step 2) and every daily
self-snapshot going forward (step 3's permanent write path). No pruning, no expiry, no delete path —
this isn't a separate policy to design later, it's a direct consequence of steps 2/3 as scoped: there
is no code anywhere in this plan that removes a row. Confirmed done once step 3 ships.

### Explicitly deferred, not built in this pass

Q13's real health-score-history comparison and the memory write/read wiring (guardrails/memory
item).

---

## 2. Guardrail layer — the richer, layered version [MUST WORK FOR DEMO]

### What "richer layered version" means, explained directly

The PRD's own Task 7 plan for this is three flat, independent pieces (input-injection rail,
PII-redaction rail, output rail). The richer version — pulled from the course's actual Session 12
material, not invented — is layered and ordered, with each rail returning a real decision instead of
a boolean:

1. **Layer 1 — deterministic input rails, run first.** Cheap, fast, non-probabilistic, no model
   call. In order: a PII rail (redact SSNs/account numbers before anything is logged or reaches a
   model), a prompt-injection rail (regex/keyword match on known jailbreak phrasing), and — new for
   this domain, not in the cat-health original — a **crisis/urgency rail**: detects language
   suggesting real financial distress ("about to lose my house," "everything I have is in this
   stock and I'm panicking") and routes to a distinct, compassionate response rather than being
   lumped in with a routine "should I sell?" question.
2. **Layer 2 — model-based topical guard, run second.** A small structured-output classifier (same
   Pydantic-verdict pattern this codebase already uses for `FilingsRelevance` and
   `TemporalComparisonQuestion` in `app/graph.py` — consistent with existing style, not a new
   paradigm) checking whether the question is actually in-scope, or is asking the product to do
   something it shouldn't (e.g. "just tell me buy or sell, no hedging"). Runs second specifically
   because it costs a real model call — only spend that on input that already passed the free
   checks.
3. **Layer 3 — output rail, checks the drafted answer.** The key difference from the PRD's flatter
   plan: this **repairs** the answer (rewrites the offending sentence to hedge properly) rather than
   just blocking the whole response. A full refusal on a legitimate "should I sell?" question is a
   worse outcome for the user than a properly-hedged, still-useful answer.

Each rail returns `escalate` / `block` / `rewrite` / `pass`, not a boolean — matching why the
course material itself gives this reasoning: a crisis needs a different downstream action than a
mundane off-topic drift, and collapsing both into one generic "reject" either over-blocks legitimate
questions or under-responds to a genuinely serious one.

### Key technical steps

1. New `app/guardrails.py` module — isolates this concern the same way `llm_gateway.py` isolates
   LLM-call wiring.
2. Layer 1, cheapest first: PII regex → injection regex → crisis/urgency keyword rail.
3. Layer 2: structured-output topical classifier, reusing the existing Pydantic-verdict pattern.
4. Layer 3: an output repair pass. **Systems-level note, not a new mechanism:** `app/graph.py`'s
   `ask()` already has a real detect-and-correct pattern (the Q9 filings-correction re-invoke). This
   guardrail's repair pass should generalize that existing pattern into one reusable utility, rather
   than becoming a fourth bespoke detect-and-fix mechanism sitting alongside Q9's and Q13's. Building
   three near-identical ad hoc patches instead of one shared one is exactly the kind of technical
   debt this project's own standard now rules out.
5. Wire all three layers into `ask()`, ordered cheap-to-expensive.
6. Log every rail decision to the persistence layer's audit table from Section 1 — so guardrail
   behavior is auditable after the fact, not just trusted to have worked.

### Caveats / not yet considered

- **False-positive risk on the repair rail**: an aggressive rewrite could soften a genuinely
  important "at_risk" signal into mush along with the buy/sell language. This needs its own eval
  cases, not just a "guardrail exists" checkbox — a guardrail that damages a correct, important
  answer is its own kind of failure.
- **The crisis-escalation response needs to be written carefully and tested with real care** — this
  is a genuine user-wellbeing surface, not a compliance checkbox. Get the tone wrong and it reads as
  either punitive or dismissive of someone in real distress.
- **Latency/cost**: three sequential rail stages plus the main agent loop is up to 3 additional model
  calls per turn. Worth actually measuring the added latency before the demo, since responsiveness
  matters live in a way it doesn't in an eval batch run.

---

## 3. Automated transcript ingestion pipeline [MUST WORK FOR DEMO]

### Status: built, unit-tested, and verified against a real saved page — not yet run live

`fetch_transcripts.py`, `ingest_ticker.py`, and `test_fetch_transcripts.py` are written. What's
actually verified, precisely:

- **Real site-structure investigation, not assumed:** fool.com is a Next.js app. The actual article
  body isn't literal `<h2>`/`<p>` tags in the raw HTML — it's server-rendered as HTML strings
  embedded inside JSON-escaped React Server Component ("flight") payloads
  (`self.__next_f.push([1,"..."])` calls). Confirmed directly against this repo's own saved ALAB
  page, not guessed.
- **A real bug found and fixed during verification, not just written and trusted:** the first working
  version leaked unrelated page-chrome content (promo widgets, disclosure metadata, serialized as raw
  React-element JSON rather than HTML) into the saved output, past the real transcript's end. Fixed by
  detecting that pattern and stopping extraction there; the QA gate now also checks for it directly as
  defense-in-depth, not just relying on the extraction-side fix holding.
- **18 tests, all passing** (`test_fetch_transcripts.py`) — unit tests for speaker-turn detection,
  continuation-paragraph joining, unattributed-intro handling, and the page-chrome contamination bug
  specifically (a real regression test, not a synthetic nice-to-have), plus a genuine integration test
  against the real saved ALAB page confirming the full pipeline extracts real participants, passes the
  QA gate, and ends with the real disclosure footer, not garbage.
- Re-extracting ALAB's known transcript produces 50,602 chars vs. the hand-built reference's 50,751 —
  same structure, same content, minor known gap: 2 substitute analysts (labeled generically as
  "Analyst" on the page rather than by name) aren't individually named the way the original manual
  build hand-corrected them to be. Disclosed, not hidden — doesn't affect QA-gate passage or content
  completeness.

**Live-verified — run for real by Maiu, locally (2026-07-25):** both PANW and DELL ran end to end,
for real. Filings: 21 real 10-K/10-Q/8-K documents saved for PANW, 16 for DELL, straight from SEC.
Transcripts: real URLs found via live Tavily search (neither guessed nor pre-known to this script —
`fool.com/earnings/call-transcripts/2026/06/02/panw-q3-2026-earnings-transcript` and
`.../2026/02/26/dell-dell-q4-2026-earnings-call-transcript`), fetched, parsed, and QA-gate-passed:
39,179 chars for PANW, 61,700 for DELL, both with real participant lists, real TAKEAWAYS/SUMMARY
content, and correctly structured speaker turns — confirmed directly by reading the saved files, not
just trusting the "OK" print line. `pytest test_fetch_transcripts.py -v` still 18/18 passing
afterward. CIKs resolved correctly for both (PANW `0001327567`, DELL `0001571996`).

This closes the one thing that couldn't be verified from the sandbox — the live network half (Tavily
search + fool.com fetch) now has real evidence, not just untested code.

**Scope boundary, stated directly:** `ingest_ticker.py` handles filings + transcript (the two real
fetch-and-save steps) and resolves each ticker's real CIK from SEC's own data, printing the exact
lines to paste into `fetch_xbrl_financials.py`'s `TICKER_TO_CIK` and `app/tools.py`'s
`TICKER_TO_COMPANY`. It does not auto-edit either dict — both are small, static, hand-maintained, and
imported directly into the live agent's per-query path (`get_fundamentals_health_score`, called on
every chat turn); auto-mutating either under time pressure without a full test pass against that live
path would trade a two-line manual edit for real risk to a tested, load-bearing piece of the app. Not
done, deliberately.

**`fetch_edgar_filings.py` also changed** while building this: refactored to expose
`ingest_filings_for_ticker(ticker, cik_map)` so `ingest_ticker.py` doesn't duplicate that logic, and
`TICKERS` now includes PANW/DELL (item 6, step 1 — done as a side effect of this work, not separately).

### Verified: there is currently no automation at all

Checked directly — there is no `fetch_transcript*.py` anywhere in this repo. `Data/ALAB/` contains
the raw saved Motley Fool `.html` page (a browser save, not a fetch script's output) alongside the
cleaned `transcript_Q1_2026.txt`. This confirms the PRD's own account: the existing 4 tickers'
transcripts came from a one-time manual process, not a script. There is nothing to "wrap" — this is
a build from scratch, and it needs to work for the 2 new tickers in item 6, which makes it a real
test rather than a hypothetical one.

### Key technical steps

1. New `fetch_transcripts.py`, alongside the existing `fetch_edgar_filings.py` (which already
   establishes the pattern: a `TICKERS` list at the top, one function per pipeline stage, save raw
   output into `Data/<TICKER>/`).
2. Locate the correct Motley Fool transcript URL per ticker/quarter — Motley Fool doesn't have a
   predictable URL pattern per ticker, so this needs a search step (Tavily, already integrated, is a
   reasonable fit) rather than a guessed URL format.
3. Fetch and parse: extract speaker-labeled, Q&A-segmented text from the real page structure — not
   naive text extraction, since the PRD specifically describes the source format as structured.
4. **A verification/QA gate, not optional.** The PRD already documented that the one existing manual
   transcript needed hand-correction once. Trusting a new scraper on 2 new tickers with zero
   automated check would risk silently ingesting garbled text. Check for expected structural markers
   (speaker names, an "Operator:" line, a Q&A section header) after extraction; fail loudly and flag
   for manual review rather than silently embedding something broken.
5. Wire into a single "ingest new ticker" entrypoint that also triggers EDGAR filings + XBRL fetch —
   currently these are 3 separate manual steps; onboarding a ticker should be one function call, not
   three remembered steps.
6. Run this pipeline against Palo Alto Networks (PANW) and Dell (DELL) this week — the real
   end-to-end validation of the automation, and simultaneously the actual task item 6 needs done.

### Caveats / not yet considered

- Motley Fool's page layout may not be identical across tickers or years — a parser tuned against 4
  known-good examples may not generalize on the first try. Needs graceful, loud failure, not a
  silent bad parse that looks like it worked.
- Worth a quick look at Motley Fool's terms of use before this becomes a real recurring automated
  pipeline rather than a one-time manual pull — the PRD already treats this source as "static, not a
  live API," which implies some awareness it isn't a sanctioned integration.
- **End-user angle, the real reason the QA gate matters**: a garbled transcript ingested silently
  produces a confidently-wrong answer that still cites "the transcript" as its source — worse than no
  transcript at all, given this entire product's premise is trustworthy citations. The automation
  itself isn't what protects the user here; the QA gate is.

---

## 4. Resolve retrieval source-preference workaround → wire parent-child retrieval [MUST WORK FOR DEMO]

Real dependency, not just sequential list order: the PRD states wiring parent-child retrieval into
the live agent is "blocked on resolving the source-preference workaround first." These are one
combined piece of work with a hard ordering, not two independent items.

### Key technical steps

1. Build the reranker — the PRD's own stated preferred fix, over a query-intent classifier: given
   retrieved parent-child candidates from both transcript and filing sources, score each against the
   actual query semantically instead of the current hardcoded "prefer transcript" rule (which the PRD
   says was "set by hand for one known question").
2. Replace the hardcoded preference in `parent_child_retriever.py` with the reranker.
3. Swap `app/tools.py`'s `search_filings` tool from the flat baseline retriever
   (`test_q1.build_retriever`) to the parent-child retriever.
4. Re-run `run_eval.py` against **all 12 eval questions**, not just Q1 — parent-child retrieval was
   only ever evaluated on Q1's 8 cases (Task 6); it has never been checked against how the other 11
   questions actually behave once it's live.
5. Re-run against the 2 new tickers (PANW, DELL) once item 3's ingestion completes for them — the
   original source-preference bug was hand-tuned against the original 4 tickers specifically; a new
   ticker is exactly where a hardcoded rule is most likely to break in a new way.

### Caveats / not yet considered

- The reranker adds latency and cost (an extra scoring pass per retrieval), on top of parent-child's
  already-measured ~9% higher token cost per query (Task 6). Cumulative cost should be measured
  before/after, not just accuracy.
- This changes what the 3 RAG-answerable eval questions actually return — worth re-checking that the
  hand-written reference answers still hold up reasonably, not just that the RAGAS scores stay high.

**Real data-quality bug found and fixed along the way (2026-07-25):** step 1's Cohere smoke test
surfaced a garbled "Item 1" parent for ALAB (`10-Q_2026-05-06.htm`) -- raw Inline XBRL fact IDs
instead of prose. Root cause: every SEC filing since ~2019 embeds machine-readable tag data inside
`<ix:header>`/`<ix:hidden>` elements and `display:none`-styled elements, none meant to be read by a
human; `BSHTMLLoader`'s default text extraction doesn't respect CSS visibility and was pulling all
of it into the "visible" text. Fixed in `test_q1.py`'s `load_ticker_documents` (the one shared
loader used by the live agent's `search_filings`/`search_filings_exact`, `run_eval.py`, and
`parent_child_retriever.py`) by stripping these elements before text extraction. Verified across all
44 filings, all 6 tickers, not just the one file that surfaced it: this wasn't ALAB-specific --
every ticker was affected, corpus shrank ~10.5% overall (1.4M of 13.7M characters removed). Spot-
checked that removed content is genuinely redundant (iXBRL's standard "shadow tagging" -- the same
numeric facts also appear in the normally-rendered, still-present visible text) by confirming
specific removed figures/phrases are still findable in the post-strip text -- not data loss.

**Real regression found and fixed in the reranker itself (2026-07-25):** first live test of the new
Cohere rerank against the actual case it needs to solve (ALAB, "this quarter's gross margin
change") failed -- the reranker alone ranked a bare numeric table (Item 2, no narrative) #1 and,
worse, a 10-K's ANNUAL MD&A section #2, which cited a different period's change in the OPPOSITE
direction (a decline, "(70) bps," vs. the quarter's actual improvement) -- a confidently wrong
answer, not just a weak one. Root cause: the reranker has no concept that "this quarter" means the
10-Q, not the trailing fiscal year: that distinction only exists in metadata/dates, not in raw
semantic similarity. Diagnosed by testing query reformulation before touching any retrieval code:
anchoring the query to the exact period ("...for the fiscal quarter ended March 31, 2026...")
eliminated the wrong-period match entirely and moved the correct transcript excerpt from #4 to
#1/#2. Further tested whether a non-date-specific phrasing achieved the same fix (it did, equally
well) before committing to it -- avoids requiring the agent to know each company's actual fiscal
calendar (varies by ticker), which an exact-date instruction would have silently depended on.
Fixed via `search_filings`'s tool docstring (`app/tools.py`) instructing the agent to phrase
period-scoped queries as "...for the most recently reported quarter, not the full fiscal year"
rather than adding retrieval-side date logic -- deliberately a prompt-based fix (Option A), not the
deterministic per-query-type routing considered (Option B), since the added complexity of B isn't
justified except for genuinely multi-period question shapes (Q3's "across last 4 earnings calls,"
not yet built) -- see chat discussion for the full reasoning. **Not yet verified against the live
agent** -- confirmed only via direct calls to `parent_child_retriever.build_parent_child_retriever`,
which `search_filings` doesn't use yet (still on the flat baseline retriever, pending step 3 below).
Also not yet deployed -- this changes a live tool's docstring in `app/tools.py`, so it needs a
redeploy before it affects the running app, same as every other `app/tools.py`/`app/graph.py` change
this session.

---

## 5. Simple onboarding form — 4 fields, no account type [DO THIS IF WE HAVE TIME]

Explicitly lower priority than the other 5 — flagging clearly so nothing else depends on this
existing. Fields: ticker, shares owned, cost basis, date purchased.

### Key technical steps

1. New table `holdings(id, ticker, shares, cost_basis, date_purchased, created_at)` in the same
   Neon Postgres instance from Section 1 — one persistence layer, not a separate bolt-on store.
2. A minimal form in the Next.js frontend (reuse the existing shadcn `Input`/`Button` components
   already in `frontend/components/ui/`), posting to a new `/holdings` endpoint in `server.py`.
3. No real authentication. Given the PRD's own scope is a single-user demo app, a single implicit
   "demo user" is sufficient — but this must be stated explicitly as a demo-scope shortcut, not
   silently presented as production-ready multi-user auth.
4. Once real shares/cost-basis data exists, derive total portfolio value live as
   `shares × current price` (the PRD is explicit this should never be self-reported/stored) — reuse
   the existing `fetch_quote()` call, don't build a second price path.
5. Wire "add a holding" to also trigger item 3's "ingest new ticker" entrypoint if the ticker isn't
   already tracked — this is literally the trigger point the PRD's own Appendix C roadmap describes
   ("auto-trigger ingestion when a user adds a ticker").

### Caveats / not yet considered

- Because this is time-permitting, confirm now: items 1–4 and 6 must not come to depend on this
  existing. If it gets cut, the deck and any live demo script must not claim "enter your holdings"
  as a working feature — precise disclosure of built vs. not, same standard the PRD already holds
  itself to in Task 2 §1.1.

---

## 6. Analyst estimates/price targets + add Palo Alto Networks (PANW) and Dell (DELL) [MUST WORK FOR DEMO]

Two things bundled together here — sequencing the ticker addition first, since items 3 and 4 both
already need to be tested against real new tickers anyway.

### Key technical steps — new tickers

1. Add `PANW` and `DELL` to `fetch_edgar_filings.py`'s `TICKERS` list.
2. Add their real CIK numbers to `fetch_xbrl_financials.py`'s `TICKER_TO_CIK` dict — verified from
   SEC's own `company_tickers.json`, not guessed. **Systems-level note**: filings fetch already looks
   up CIK dynamically from that same SEC source, while XBRL fetch uses a separately hand-maintained
   dict — two sources of truth for the same mapping is small existing debt, worth collapsing into one
   dynamic lookup while touching this code anyway, not just adding two more hardcoded entries to it.
3. Add both to `app/tools.py`'s `TICKER_TO_COMPANY`. **Verified: this is the only change needed for
   the frontend** — `server.py`'s `/tickers` endpoint reads directly from this dict, and
   `frontend/app/page.tsx` fetches its ticker list from that endpoint at runtime (its hardcoded list
   is explicitly only a fallback for when the backend is unreachable). No frontend code change
   required.
4. Run the (now-automated, per item 3) full ingestion pipeline for both tickers: EDGAR filings,
   transcripts, XBRL.
5. Run the full eval suite against both new tickers wherever the eval questions are designed to be
   parametrized across tickers (Q1, Q7 grounding, etc. already are, per the PRD) — this is a real
   test of whether that parametrization actually generalizes to tickers it's never seen, which is a
   meaningfully stronger claim for the deck than "works on the 4 tickers it was built against."

**STATUS (2026-07-25): COMPLETE, live-verified for all 6 tickers.** Real bug found and fixed along
the way: `fetch_xbrl_financials.py`'s `fetch_revenue()` was accepting the first revenue tag with ANY
data instead of the first tag with usable quarterly data — PANW's `Revenues` tag had only
annual/legacy entries, so revenue_growth and margin both silently showed `insufficient_data`. Fixed
to require `quarterly_series(entries)` be non-empty before committing to a tag (falls through to
`RevenueFromContractWithCustomerExcludingAssessedTax` otherwise). Re-verified live against PANW:
revenue_growth now `intact` (35 quarters found), margin now `at_risk` (67.55% latest, down from a
74.21% peak, 666bps cumulative compression). Note for demo narration: this GAAP margin figure is
lower than the 75.8% PANW cited on its earnings call, because the call figure is non-GAAP — expected
gap for this company, not a bug, but avoid citing both numbers back-to-back without noting which is
which if asked about PANW's margin in the same demo session.

### Key technical steps — analyst estimates/price targets

1. Identify a real free-tier source — Finnhub has a `/stock/price-target` endpoint; FMP has an
   equivalent. **Given this week's lesson, test both against all 6 tickers live before trusting
   either** — do not repeat building against docs-page assumptions.
2. Wire into `get_market_data` following the same pattern already established for recommendation
   trends (`fetch_recommendation_trends`/`format_recommendation_trends` in `test_q8.py`) — reuse
   existing formatting conventions rather than inventing a new response shape.

**STATUS (2026-07-25): NOT COMPLETED — blocked, both free-tier sources gated.** Tested live via
`check_price_targets.py` against all 6 tickers:
- Finnhub `/stock/price-target`: 403 "no access" on all 6/6 tickers (including AAPL) — not
  available on the current plan at all, not ticker-specific.
- FMP `/stable/price-target-summary`: 402 gated on 5/6 tickers (ALAB, MRVL, NBIS, PANW, DELL); PASS
  only on AAPL, per the error body a per-symbol subscription gate, not general access — not usable
  for the actual portfolio.
- Analyst *rating* (buy/hold/sell consensus, Finnhub `/stock/recommendation`) is unaffected and
  already working (Q6/Q8) — this only blocks the dollar price-target figure specifically.
- **DECISION (2026-07-25, Maiu): hold, not paying for an upgrade right now.** Not an open question
  pending a cost check anymore — deliberately deferred. Step 2 (wiring into `get_market_data`) will
  not be started. Ticker-onboarding (this item's other half, below) is unaffected and still must be
  fully completed.

### Caveats / not yet considered

- **PANW and DELL are both large, long-public, heavily-covered companies** — likely to have *better*
  data coverage across every vendor (Motley Fool, Finnhub, FMP) than ALAB or NBIS. That means
  testing cleanly against them is not a full stress-test of the pipeline's robustness for smaller or
  newer tickers — Task 6's own findings already showed ALAB as the harder edge case (a 0.0
  context-recall outlier). Worth stating this precisely in the deck/validation story so "works great
  on PANW" isn't mistaken for "works great on any ticker."

---

## 7. Hybrid dense + BM25 retrieval (RRF) for `search_filings` [DO THIS IF WE HAVE TIME, after item 5]

Scoped narrowly, on purpose — see the chat discussion this came out of. This is NOT about fusing
`search_filings` and `search_filings_exact` together; those serve different guarantees (best-guess
relevance vs. guaranteed-complete recall) and RRF-blending them would reintroduce the lossy top-k
behavior `search_filings_exact` exists specifically to avoid. This is about making `search_filings`
itself — currently pure dense/vector retrieval — a real hybrid retriever: dense + BM25 fused via
Reciprocal Rank Fusion, the actual Session 07 pattern
(`07_Advanced_Retrievers/01_Cat_Health_Advanced_Retrieval.ipynb`'s hand-rolled `reciprocal_rank_fusion()`
feeding `hybrid_children_retrieve()`), same session that also supplies item 4's Cohere reranker
precedent.

### Key technical steps
1. Add a BM25 retriever alongside the existing dense child-retriever in `test_q1.build_retriever` /
   `parent_child_retriever.py`, over the same indexed child chunks.
2. Port Session 07's `reciprocal_rank_fusion()` (or an equivalent) to fuse the two ranked lists before
   parent-lookup/truncation.
3. Re-run against the RAG-answerable eval questions (Q1 at minimum) to confirm this is a real
   improvement, not just a different result — same "evaluation harness as hard evidence" standard
   this project holds every other retrieval change to, not a checkbox.

### Caveats / not yet considered
- This only touches `search_filings`. `search_filings_exact` and item 4's reranker (a separate stage,
  applied after retrieval) are both unaffected and stay as-is.
- Sequenced after item 5 (onboarding form) per Maiu's call — not blocking any MUST-WORK-FOR-DEMO item.

---

## 8. Improve Q1 eval correctness — added 2026-07-25, priority TBD (confirm with Maiu)

Real run of `run_eval.py --question 1` against all 12 cases (2026-07-25, after fixing the
per-ticker retriever-caching bug that was causing a 429): `faithfulness` averaged 0.9884 (strong —
the model isn't hallucinating beyond its retrieved context), but `factual_correctness(mode=f1)`
averaged only 0.4550, with several rows at or near 0.0 (two "this quarter's gross margin change"
cases scored 0.29 and 0.00; a PANW "fiscal Q4 2026 revenue and margin outlook" case scored 1.0 on
context_recall but 0.00 on factual_correctness). High faithfulness + low factual_correctness is a
specific, diagnosable pattern: the model is being faithful to what it retrieved, but what it
retrieved and/or how it's being compared against the written `reference` isn't matching on the
actual facts. Not yet root-caused — candidates worth checking before assuming which one it is:
retrieval missing the specific figure entirely (k=10 miss), the written `reference` answers stating
facts in a different form/granularity than what's retrievable (a scoring-mismatch problem, not a
retrieval problem — same failure shape Q5 hit earlier per its own deferred_reason notes), or
`context_recall`'s per-row 0.0 cases (2 of 12 rows) pointing at genuine retrieval misses.

### Key technical steps
1. Re-run `run_eval.py --question 1 --verbose` and read the actual retrieved_contexts vs. reference
   for the worst-scoring rows (the two 0.0 factual_correctness cases) before changing anything —
   diagnose first, don't guess at a fix.
2. Based on what's actually wrong, fix is either: a retrieval issue (k, chunking, embedding) — likely
   overlaps with item 4's reranker work — or a reference/scoring-format issue specific to these
   cases, independent of retrieval quality.

### Caveats / not yet considered
- Priority tag not yet set — Maiu flagged this needs fixing ("correctness should be better") but
  hasn't said whether it's MUST-WORK-FOR-DEMO or lower. Confirm before this competes for time against
  items 4/5/7.
