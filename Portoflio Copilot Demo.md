# Portfolio Copilot — Demo Hardening Plan

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

One thing still open, not yet verified: `server.py`'s new `DATABASE_URL` startup check and
`db.init_db()` call haven't been exercised against an actual Render deploy yet — only tested via
local `python -c` calls and the local pytest run above. Deploy and confirm the live Render service
still boots cleanly and a real chat question against it reflects this data before calling that part
done.

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

### Key technical steps — analyst estimates/price targets

1. Identify a real free-tier source — Finnhub has a `/stock/price-target` endpoint; FMP has an
   equivalent. **Given this week's lesson, test both against all 6 tickers live before trusting
   either** — do not repeat building against docs-page assumptions.
2. Wire into `get_market_data` following the same pattern already established for recommendation
   trends (`fetch_recommendation_trends`/`format_recommendation_trends` in `test_q8.py`) — reuse
   existing formatting conventions rather than inventing a new response shape.

### Caveats / not yet considered

- **PANW and DELL are both large, long-public, heavily-covered companies** — likely to have *better*
  data coverage across every vendor (Motley Fool, Finnhub, FMP) than ALAB or NBIS. That means
  testing cleanly against them is not a full stress-test of the pipeline's robustness for smaller or
  newer tickers — Task 6's own findings already showed ALAB as the harder edge case (a 0.0
  context-recall outlier). Worth stating this precisely in the deck/validation story so "works great
  on PANW" isn't mistaken for "works great on any ticker."
- If price-target data turns out to be gated the same way FMP's historical prices were, that's a
  self-contained failure — it should not block demo-readiness on items 1–4.
