# Personal Portfolio Copilot — Demo Day Presentation Plan

**Status: brainstorm/planning only. No deck file has been built yet.** This document
captures everything discussed so far so we can pick the thread back up without
re-deriving it. Nothing here is final until we actually build the HTML deck.

---

## 1. Deliverable

- **Format:** a single-file, self-contained **HTML deck** — not a PowerPoint file.
  Modeled mechanically on the reference (`Stylists.ai`, AI Makerspace AIE9 Demo Day):
  full-viewport slides, scroll-snap + keyboard/wheel/touch navigation, a progress
  bar, nav dots, and a speaker-notes popup window (press `S`) synced via
  `BroadcastChannel`.
- **Length target:** ~10 minutes, ~11 slides (matches the reference's 11 slides /
  10:20 runtime pacing, roughly 50–90 sec/slide).
- **Theme:** dark, not light. Adapted from the reference's "dark botanical" palette
  (which was warm rose/gold/floral, fit for a styling product) into a **finance
  palette**: dark navy/charcoal base with emerald + gold accents. We explicitly
  rejected switching to a second reference's light theme (white bg, mint-green
  accent tags) for the overall deck — that reference's *value* is a structural
  pattern (see below), not its color scheme.

## 2. Reference material actually pulled (not assumed)

- **Stylists.ai / AIE9 Demo Day** (`youtube.com/watch?v=TaL9C8kIKZM`) — same
  certification program's demo day format. Pulled the real slide source from
  its GitHub repo (`fox8991/stylists-ai-backend-codex/presentation/Stylists_AI_Demo_Day_v5.html`),
  not just the video. Slide shape: Title → Problem → Product Promise → Audience
  (3 cards) → How We Deliver It (flow) → Live Demo (3 prompt cards) → The Loop/Moat
  → Agent Architecture → System Architecture → What's Next → Closing (struck-through
  contrast lines into one final statement).
- **Second reference (user-provided screenshot):** a light-themed "How trust gets
  delivered" slide — a numbered top-level flow (User → Web app → Finance assistant
  → Answer) with tagged sub-cards underneath (SOURCE / TOOL / TOOL / MEMORY) showing
  what feeds the middle step. We're borrowing this **structural pattern** (numbered
  flow + tagged cards) for our own How-It-Works and Validation slides, rendered in
  our dark palette, not its light one.

## 3. Confirmed decisions log

| Decision | Answer |
|---|---|
| Output format | HTML deck (not .pptx) |
| Visual theme | Dark navy/charcoal/emerald/gold (finance-adapted, not the reference's warm rose/gold, not the light mint-green alternative) |
| Demo slide content | Prompt cards only — no app screenshots/recordings (user will switch to the live app when presenting) |
| Validation/Proof slide framing | **Hardening moves, not raw eval scores.** Explicitly reframed away from "context recall 0.875→1.00" style stat callouts toward "here's what we caught and fixed" stories |
| Problem statement copy | User-supplied, verbatim (see §5) — supersedes the PRD-paraphrased version drafted earlier |

## 4. Persona (3 cards)

The PRD describes one working-professional persona; split into three behavior modes,
each tied to a specific locked eval question so the persona slide isn't generic:

1. **The Distracted Holder** — full-time job, 10–30 individual stock positions, no
   time to read a 10-Q or earnings transcript across all of them.
2. **The Reactive Seller** — a price drop or headline triggers a sell impulse before
   fundamentals are checked. (Embodies eval Q7: "Company X just dropped 8% today,
   I'm nervous, should I sell?")
3. **The Thesis-less Rebuyer** — bought for a reason months ago, can't reconstruct
   or locate the original thesis now. (Embodies eval Q9/Q13.)

## 5. Problem statement (final copy, user-supplied)

> The hardest investing decision isn't buying. It's knowing when to hold or sell.
> The real challenge is knowing whether your original investment thesis still holds.
> For most retail investors, monitoring every earnings report, SEC filing, and
> market event is not always possible. As a result, hold and sell decisions are
> often driven by headlines and price movements instead of changes in the
> underlying business.

Supporting stat available if useful: 66% of investors regret an impulsive or
emotional investing decision (MagnifyMoney, cited in PRD §1.1).

## 6. Product promise

"Checks your actual holdings against objective fundamentals — before a price move
or a headline gets to make the decision for you." Grounding pills: **Filings &
Transcripts · Live News · Market & Insider Data · Fundamentals Health Score.**

## 7. Full slide outline (11 slides)

1. **Title** — "Personal Portfolio Copilot" · tagline: grounds hold/buy/sell
   instinct in real filings and objective fundamentals · context line: "AI
   Engineering Certification v1.0 · Capstone Demo Day"
2. **Problem** — copy from §5 + reactive-workflow bullets (search each ticker
   manually, recall reasoning from memory, emotional hold/sell) + 66% stat
3. **Persona** — 3 cards from §4
4. **Product Promise** — grounding pills from §6
5. **How It Works** — numbered flow (User question → Classify & Plan → 4 tools →
   Synthesis vs. Fundamentals Health Score → Cited answer), tagged sub-cards below
   showing SOURCE (Qdrant filings/transcripts) / TOOL (Tavily live news) / TOOL
   (Finnhub market + insider data) / SIGNAL (Fundamentals Health Score) — borrowing
   the "How trust gets delivered" structural pattern, rendered dark
6. **Live Demo** — 3 prompt cards, ascending difficulty, pulled from the actual
   locked eval set:
   - Q7: "Company X just dropped 8% today, I'm nervous, should I sell?"
   - Q5: "Has there been any recent capacity/demand or customer-concentration
     problems in Company X's filings?"
   - Q9: "Summarize everything notable about Company X this week."
7. **Fundamentals Health Score** — the differentiator: 4 signals (revenue growth,
   margin, insider activity, leadership stability), worst-of (not averaged) rollup
8. **Architecture** — layered stack: Next.js chat UI on Render → FastAPI + LangGraph
   agent (GPT-4.1 mini via Portkey) → Qdrant RAG / keyword search / Tavily /
   Finnhub+EDGAR → LangSmith tracing. Honest note: in-memory only, no DB yet
   (matches PRD's own precise-status-reporting standard)
9. **Validation / Hardening** — see §8, reframed around hardening moves
10. **What's Next** — Keep vs. Change, from PRD Task 7 (see §9)
11. **Closing** — struck-through contrast lines ("Not a stock picker." "Not a
    robo-advisor." "Not another headline feed.") → single closing statement

## 8. Validation/Hardening slide — current draft (reframed, not final)

Four cards, same tagged-card grammar as the How-It-Works slide, each a
problem-caught-and-fixed story rather than a bare metric:

- **RETRIEVAL** — Structure-aware retrieval. Swapped flat chunking for parent-child
  retrieval so a match never comes back stranded without its full context — fixed
  an inconsistency traced to pure chunk-boundary luck.
- **DEDUP** — Cleaned inputs before judging. On exhaustive-recall questions,
  deduplicated near-duplicate hits before synthesis — killed repeated timeouts and
  stopped drift on redundant context.
- **DETERMINISTIC** — Numbers computed, not composed. Verdicts and deltas are
  computed in Python and only narrated by the model, never composed by it — removes
  the model's tendency to mirror a user's own wording into a false claim.
- **GUARDRAIL** — Self-correcting tool coverage. If a question needs a filings
  check and none happened, the app runs it and corrects the answer before it's
  sent — no more confidently claiming "nothing found" when nothing was checked.

Optional 5th card — a "caught live" proof point rather than a fix: a real test case
described an 8% price drop that didn't match the live quote (actual was +1.6%), and
the agent flagged the false premise instead of validating it.

**Not yet decided:** whether to add any of the new hardening ideas below as
*additional* "already built" cards, or keep them as roadmap-only "what we'd harden
next" items. This depends on whether any get actually built before the deck is
finalized — see §9 open question.

## 9. New hardening ideas surfaced from the /v1-0 course material (not yet in the PRD)

Pulled directly from the numbered session folders, not assumed:

- **Layered guardrails with repair, not just reject** (Session 12: Production Agent
  Patterns) — cheap deterministic checks (injection, PII) run first, then a
  model-based topical guard only on what survives, then an *output rail that
  repairs* a drafted reply rather than just blocking it. Richer than the PRD's
  current flat 3-piece guardrail plan.
- **Semantic caching — deliberately rejected, not just absent** (Session 12) — the
  course's own example: a semantic cache serving a cached answer for "treat" when
  the query said "poison," a one-word difference with a catastrophic consequence.
  Direct analog to "buy" vs. "sell" here. Worth framing as a considered-and-rejected
  decision rather than a silent gap.
- **Supervisor + specialist split with a deterministic citation audit**
  (Session 4: Multi-Agent Systems) — route to focused specialists (filings, market
  data, news) each with a clean context window, plus a code-level audit that
  verifies every cited filing/URL actually exists rather than trusting the model's
  citation.
- **Adversarial/synthetic test generation** (Session 5: Synthetic Data Generation
  for RAG Evals) — the 12 eval questions are all hand-authored happy-path cases;
  Ragas knowledge-graph-based synthetic generation + adversarial cases (prompt
  injection, ambiguous tickers, contradictory news-vs-filing) would widen coverage
  beyond the locked set.
- **Load/concurrency testing of the actual deployed endpoint** (Session 10: LLM
  Servers, `endpoint_slammer` pattern) — the PRD asserts Render free-tier cold start
  is "the only tradeoff" but this has never actually been load-tested under
  concurrent requests.
- **Graph-enhanced retrieval for cross-document narrative drift** (Session 3: Agent
  Memory + Graph-Enhanced RAG) — a small entity/topic graph linking recurring
  themes across transcripts previews the mechanism for eval Q3 (narrative drift
  across quarters), currently just blocked/unbuilt in the PRD.

## 10. PRD's own Task 7 "Next Steps" (Keep vs. Change), for reference

**Keep (no change needed):** the 4-tool agent architecture; the Fundamentals Health
Score's worst-of (not averaged) rollup; deterministic math computed in Python,
narrated by the LLM; provider-side prompt caching + bounded LRU/TTL tool caches.

**Change, in the PRD's own priority order:**
1. Add the guardrail layer — PRD calls this "the single highest-value remaining gap"
2. Resolve the retrieval source-preference workaround (hardcoded transcript-vs-filing
   preference rule)
3. Script the transcript ingestion pipeline (currently a one-time manual fix)
4. Widen eval coverage past 10/12 built questions (Q3 blocked on data, Q12 blocked
   on a threshold filter)
5. Wire parent-child retrieval into the live agent (currently comparison-only)
6. Make the custom PASS/FAIL judge criteria scalable

## 11. Open question, pending further discussion

Whether the Validation/Hardening slide should claim only what's built today (§8's
4 cards) with a couple of §9's ideas listed as roadmap-only — or whether we
actually build one or more of §9's ideas before finalizing the deck, letting them
become "done" cards instead of "next" cards.

**Next up:** more discussion on demo enhancements before we build anything.
