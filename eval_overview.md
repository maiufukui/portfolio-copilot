# Eval Overview -- Personal Portfolio Copilot

Generated 2026-07-29. This is the single reference for how all 10 automated eval questions work:
what's asked, what tool/retrieval path answers it, what judges/metrics score it, and every prompt
change made to the pipeline this session with the before/after evidence for why it was made.

Scoring harness: `run_scorecard.py` (dispatches to each question's own `test_qN.py`). Full prompt
text lives in Appendix A. Session changelog with before/after numbers lives in Appendix B.

---

## Quick reference

| Q | Question template | Category | Scoring | Retrieval / tools |
|---|---|---|---|---|
| 1 | How does {company}'s {metric} guidance for next quarter compare to what they just reported? | rag | RAGAS triad | Hybrid vector (`search_filings`: parent-child + BM25 + Cohere rerank), k=6 |
| 2 | What's the latest news on {company}, and does it affect my position? | tool_calling | custom judge + RAGAS ToolCallAccuracy + AgentGoalAccuracy | live agent, `search_live_news` |
| 3 | Is there anything in {company}'s latest earnings I should be worried about, especially around margin or guidance? | tool_calling | custom judge only | live agent, any of `search_filings`/`search_filings_exact`/`get_market_data` |
| 4 | Is there any insider selling in my holdings this week? | tool_calling | custom judge + RAGAS ToolCallAccuracy + AgentGoalAccuracy | live agent, `get_market_data` |
| 5 | Has there been any recent {topic} mentioned in {company}'s filings? | rag | RAGAS triad | Deterministic regex exact-match (`find_hits`), no vector search |
| 6 | What did analysts say after today's {event}? | tool_calling | custom judge + RAGAS ToolCallAccuracy + AgentGoalAccuracy | live agent, `search_live_news` + `get_market_data` |
| 7 | {Company} just dropped {move_pct}% today, I'm nervous -- should I sell? | tool_calling | custom judge only | live agent, any data-checking tool |
| 8 | Have analysts changed their rating on {company} recently? | tool_calling | fully deterministic, no LLM judge | direct Finnhub call, no agent graph |
| 9 | Summarize everything notable about {company} this week -- filings, media, and analyst activity. | tool_calling | custom judge + RAGAS ToolCallAccuracy + AgentGoalAccuracy | live agent, filings + `search_live_news` + `get_market_data` |
| 10 | Revenue growth has slowed for several quarters straight for {company} -- does the latest quarter suggest that's stabilizing, or is a bigger slowdown coming? | tool_calling | custom judge only | live agent, any data-checking tool |
| 11 | (whole-portfolio digest) | -- | **not built / not tested** -- no product support for portfolio-wide queries | -- |

---

## Q1 -- RAG, RAGAS triad

**Question asked:** `How does {company}'s {metric} guidance for next quarter compare to what they just reported?`
Asked once per ticker with that ticker's own real headline metric (`metric` is per-ticker, not
hardcoded -- e.g. MRVL's is data center revenue growth, not margin).

**Retrieval:** hybrid vector search via `search_filings` (`app/tools.get_retriever` ->
`parent_child_retriever.py`'s dense embedding + BM25 + RRF fusion + Cohere rerank), k=6. Query text
explicitly asks for both the reported-quarter figure and the forward guidance figure, and asks the
retriever to prefer the exact verbatim management quote over a bullet-point summary (see Appendix
A, `Q1_DRIVER_PROMPT`/query text).

**Scored by:** RAGAS's real metric classes, judged by `gpt-4.1-mini`, against the hand-written
references in `eval_dataset.json`:
- **Faithfulness** -- does the response's claims trace back to the retrieved context.
- **LLMContextRecall** -- did retrieval surface everything the reference needs.
- **FactualCorrectness** (mode=f1) -- does the response's claims match the reference's claims.

**Test cases:** ALAB, AAPL, MRVL, NBIS, PANW, DELL (one per ticker). All 6 references
independently re-verified this session against the real local filing/transcript text -- see
Appendix B for the 2 errors found and fixed (DELL, PANW) and the 4 confirmed accurate as written
(ALAB, AAPL, MRVL, NBIS).

**Update 2026-07-29 (post reference-fix re-run):** the DELL reference fix resolved the
20.5-vs-17.8 conflict as intended, but the aggregate score barely moved (0.34 -> 0.32) because
two OTHER real, previously under-diagnosed problems are now the dominant drag -- see Appendix
B.9 (PANW retrieving an 18-month-old filing) and the per-case notes below. This was a genuinely
useful negative result: fixing the reference was necessary but not sufficient, and the flat
aggregate forced a real second look instead of being written off as noise.

---

## Q2 -- tool_calling, `NEWS_JUDGE_PROMPT` (`test_q2.py`)

**Question asked:** `What's the latest news on {company}, and does it affect my position?`

**Path:** live agent (`app.graph.ask`), expected tool: `search_live_news`.

**Custom judge criteria** (PASS/FAIL, strict):
1. `live_search_used` -- did it actually search live news, not just answer from the health score.
2. `relevance_assessment` -- does it assess relevance to the position, not just list headlines.
3. `citation_quality` -- is each news item attributed to a source and date.

**RAGAS metrics:**
- `ToolCallAccuracy` -- reference: `search_live_news` called. No fixed args (its query is
  free text the model composes itself), so the arg-accuracy component reads 0.00 by construction --
  a known, disclosed metric limitation, not a real failure (see `eval_tool_call_accuracy.py`).
- `AgentGoalAccuracyWithReference` -- reference: *"The AI assistant reported recent news for
  {company} and assessed whether it's relevant to the user's position."*

---

## Q3 -- tool_calling, `GROUNDING_JUDGE_PROMPT` (`test_q3.py`)

**Question asked:** `Is there anything in {company}'s latest earnings I should be worried about
moving forward, especially around margin or guidance?`

**Path:** live agent. Deterministic check (not LLM-judged): at least one of `search_filings` /
`search_filings_exact` / `get_market_data` must have been called -- a real red flag if zero tools
fired.

**Custom judge criteria:**
1. `topic_adherence` -- grounded in {company}'s actual latest-earnings content, not generic
   investing advice or mirroring the user's worried tone.
2. `goal_accuracy` -- explicitly states whether there IS or ISN'T a real concern, citing a specific
   number/quote/disclosure.
3. `tool_call_accuracy` -- actually checked real filings/transcript/health-score content, not
   general knowledge.

No RAGAS metric wired in -- judge-only.

---

## Q4 -- tool_calling, `INSIDER_JUDGE_PROMPT` (`test_q5.py`)

**Question asked:** `Is there any insider selling in my holdings this week?` -- asked once per
ticker, inside that ticker's own thread (the dataset's original portfolio-wide framing is expanded
into one case per ticker at load time; there's no portfolio-wide query surface in the real product).

**Path:** live agent, expected tool: `get_market_data`.

**Custom judge criteria:**
1. `real_data_checked` -- queried real insider-transaction data, not memory.
2. `accurate_reporting` -- names the transaction concretely (who, how many shares, when), or says
   plainly none occurred -- no hedging.
3. `scoped_correctly` -- stays scoped to the actual ticker, doesn't fabricate data for others.

**RAGAS metrics:**
- `ToolCallAccuracy` -- reference: `get_market_data(ticker=...)`.
- `AgentGoalAccuracyWithReference` -- reference: *"The AI assistant checked real
  insider-transaction data for {company} and reported whether any insider selling occurred in the
  past week."*

**Session fix (2026-07-29):** `accurate_reporting` failed the NBIS case in both full runs this
session -- the agent reported selling by a named insider but described the size only as "multiple
blocks" / "significant in volume" despite `get_market_data`'s insider-transaction lines already
containing an exact share count per transaction (`app/tools.py`, `abs(change)`). See Appendix B.

**Update 2026-07-29 (post-fix re-run):** still FAILs, identical vague wording. Traced to the real
cause: NBIS's case triggers the filings guard (see `app/graph.py`'s `ANSWER_GUARDS`), and the
guard's own correction-turn prompt explicitly instructs the model to keep every non-filings
section "EXACTLY as you already reported it... do not drop specificity" -- which locks in
whatever vague wording the first draft already had before the new anti-vagueness rule ever gets a
chance to apply. This is a real, structural conflict between two prompt layers, not a failed
fix -- see Appendix B.9 for the carve-out fix applied to both guard-correction prompts.

---

## Q5 -- RAG, RAGAS triad (exhaustive recall)

**Question asked:** `Has there been any recent {topic} mentioned in {company}'s filings?`
(capacity/demand, customer concentration -- both ALAB).

**Retrieval:** deterministic regex exact-match (`test_q7.find_hits`), **not** vector search -- this
question specifically tests whether the agent/harness routes to exhaustive keyword search instead
of lossy top-k similarity search. Hits are deduped (`dedupe_hits`) before synthesis; the raw
(pre-dedup) count is fed into the prompt separately so the stated total matches what a human would
count in the real filings, not the deduped set (see `SUMMARY_PROMPT`, Appendix A).

**Scored by:** the same three RAGAS metrics as Q1 (`Faithfulness`, `LLMContextRecall`,
`FactualCorrectness` f1), against hand-written references.

**Status: accepted as capped (2026-07-29, Maiu's explicit call).** Both test cases score low
`factual_correctness` (0.09 and 0.29 in the most recent run) for two different, now-understood
reasons:
- The capacity/demand case's reference previously had wrong raw counts (15/72 vs the real,
  independently-verified 16/75) -- **fixed**, see Appendix B.
- Independent of that fix, RAGAS's F1-based `FactualCorrectness` structurally penalizes this
  question's own correct design: the response is a long, multi-section, per-excerpt breakdown
  (the exhaustive-recall behavior the question is designed to force), while the reference is a
  short, dense paragraph. Decomposed into atomic claims, the verbose-but-correct response scores
  low precision against the terse reference even with zero factual errors (confirmed on the
  customer-concentration case, which matches the reference's own conclusion exactly and still only
  scored 0.29). This is a known characteristic of the metric applied to this question's format, not
  a pipeline bug -- **no further fix planned**, per Maiu's decision to accept the cap rather than
  redesign the reference/response format to chase a higher number.

---

## Q6 -- tool_calling, `REACTION_JUDGE_PROMPT` (`test_q8.py`)

**Question asked:** `What did analysts say after today's {event}?`

**Path:** live agent (NOT `test_q8.py`'s own standalone `--mode reaction` CLI, which is a separate,
unscored manual-review tool built directly on Tavily + Finnhub). Expected tools: `search_live_news`
+ `get_market_data`.

**Custom judge criteria:**
1. `company_vs_analyst_distinction` -- keeps company statements separate from analyst/institutional
   commentary, rather than blending the two.
2. `institutional_data_used` -- cites real buy/hold/sell consensus data, not just generic news
   chatter about analyst sentiment.
3. `citation_quality` -- each media/news claim attributed to a dated source.

**RAGAS metrics:**
- `ToolCallAccuracy` -- reference: `search_live_news` + `get_market_data(ticker=...)`.
- `AgentGoalAccuracyWithReference` -- reference: *"The AI assistant reported analyst and market
  reaction to {company}'s {event}, distinguishing company statements from institutional/analyst
  commentary."*

**Session finding:** weakest performer across both full runs -- `citation_quality` FAIL,
`goal_accuracy` 0.00, `tool_call_accuracy` 0.50, consistently. Root-caused and fixed this session --
see Appendix B ("multi-source citation drop").

**Update 2026-07-29 (post-fix re-run):** still FAILs `citation_quality` (sources listed, none
dated), and `institutional_data_used` regressed to FAIL (was PASS before). Not a confirmed failed
fix, though -- Q6 has only ONE test case (MRVL) and depends on live Tavily news search, so the
actual underlying articles differ run to run (this run's response was about a fresh ~7% drop and a
different news narrative than the prior run, not the same story re-scored). With n=1 and
daily-changing input data, this run's result can't cleanly separate "the fix didn't work" from
"this run drew different, weaker source material." Flagged as unresolved -- worth watching over
more runs, or adding a second test case, before concluding anything further.

---

## Q7 -- tool_calling, `GROUNDING_JUDGE_PROMPT` (`test_q7_grounding.py`)

**Question asked:** `{Company} just dropped {move_pct}% today, I'm nervous -- should I sell?`

**Path:** live agent. Deterministic check: at least one of `get_market_data` /
`search_live_news` / `search_filings` / `search_filings_exact` called.

**Custom judge criteria:**
1. `topic_adherence` -- grounded in the ticker's actual fundamentals/data, not generic advice or
   mirroring the emotional framing (validating the fear without evidence, or reassuring without
   evidence, either direction fails this).
2. `goal_accuracy` -- explicitly states whether anything about the fundamentals actually changed,
   citing a specific signal/number/news item.
3. `tool_call_accuracy` -- checked live data/news rather than static assumptions.

No RAGAS metric wired in -- judge-only. Clean across both full runs, no FAILs.

---

## Q8 -- tool_calling, fully deterministic (`test_q8.py`)

**Question asked:** `Have analysts changed their rating on {company} recently?`

**Path:** no agent graph at all -- direct Finnhub recommendation-trends call, diffed in plain
Python (`compute_trend_deltas`), narrated by an LLM from the computed numbers, then
`check_narration_matches_deltas` verifies the narration's stated dates and per-category deltas
literally appear in the text. Pass/fail is a string-match against real computed numbers, not an
LLM judgment call.

**Session fix (2026-07-28):** a real false negative was found and fixed -- see Appendix B. Now
confirmed `pass_rate=1.00`.

---

## Q9 -- tool_calling, `DIGEST_JUDGE_PROMPT` (`test_q9.py`)

**Question asked:** `Summarize everything notable about {company} this week -- filings, media, and
analyst activity.`

**Path:** live agent. Expected: at least one filings tool (`search_filings` or
`search_filings_exact`) + `search_live_news` + `get_market_data`.

**Custom judge criteria:**
1. `source_coverage` -- addresses all three named categories (filings, media, analyst), either
   with real findings or an explicit "nothing notable found" -- FAIL if a category is silently
   skipped.
2. `citation_quality` -- each concrete claim attributed to a specific source and date (filing name,
   news URL/date, or explicitly labeled institutional data).
3. `tool_call_accuracy` -- given the tools called, did it actually check something in each of the
   three categories.

**RAGAS metrics:**
- `ToolCallAccuracy` -- reference: a filings tool + `search_live_news` + `get_market_data`.
- `AgentGoalAccuracyWithReference` -- reference: *"The AI assistant produced a weekly digest for
  {company} covering filings, media coverage, and analyst activity."* Deliberately outcome-only --
  citation quality is left to the custom judge, since RAGAS's own `CompareOutcomePrompt` can't score
  a multi-part quality criterion bundled into the reference (confirmed via a real run: a
  citation-quality clause in the reference produced a uniform false 0.00 regardless of actual
  agent performance -- see `eval_tool_call_accuracy.py`'s module docstring).

**Session finding:** `citation_quality` FAIL in both full runs (media claims lacking specific
article sources/URLs). Same root cause as Q6 -- see Appendix B.

**Update 2026-07-29 (post-fix re-run): confirmed fixed.** ALAB's case flipped to PASS --
*"Each claim is attributed to specific sources with dates, including SEC filings, news article date
ranges, and market data as of a specific date."* This is the clean, direct confirmation that the
citation-preservation-under-synthesis fix works when nothing else (like the guard-correction
conflict found in Q4) interferes with it.

---

## Q10 -- tool_calling, `GROUNDING_JUDGE_PROMPT` (`test_q10.py`)

**Question asked:** `Revenue growth has slowed for several quarters straight for {company} -- does
the latest quarter suggest that's stabilizing, or is a bigger slowdown coming?`

Two locked cases, deliberately mixed premise-true/premise-false: ALAB (premise TRUE -- real
decelerating YoY growth) and AAPL (premise FALSE -- verified against real XBRL data pulled locally:
YoY growth was +15.65% then +16.60%, stable-to-improving, not decelerating).

**Path:** live agent. Deterministic check: at least one of `search_filings` /
`search_filings_exact` / `get_market_data` called.

**Custom judge criteria:**
1. `topic_adherence` -- checks {company}'s actual revenue-growth figures rather than accepting the
   question's premise at face value.
2. `goal_accuracy` -- states plainly whether the premise is accurate, correcting it with real
   figures if the growth is actually stable/improving.
3. `tool_call_accuracy` -- checked real financial data, not just reasoned from the question's own
   wording.

No RAGAS metric wired in -- judge-only. Clean across both full runs, no FAILs (confirms the agent
correctly pushes back on AAPL's false premise rather than reflexively agreeing).

---

## Q11 -- not built, not tested

Whole-portfolio digest question (old id-12). Explicitly out of scope: the real product has no
portfolio-wide query surface -- LangGraph is one thread per ticker, and every tool
(`get_market_data`, `search_filings`, etc.) takes a single ticker. Stays `status="not_built"` in
`eval_dataset.json`, skipped by `run_scorecard.py`'s own not-built branch. Not a gap to close --
a real product-design boundary.

---

# Appendix A -- full prompt text

## A.1 Live agent system prompt (`STABLE_SYSTEM_PROMPT`, `app/graph.py`)

Governs every live-agent question (Q2, Q3, Q4, Q6, Q7, Q9, Q10). Updated 2026-07-29 -- the two new
paragraphs (citation-preservation-under-synthesis, anti-vague-quantifier) are marked below; see
Appendix B for why.

```
You are Portfolio Copilot, an agentic research assistant that grounds a user's stock holdings in
objective business fundamentals and filings -- not a free-text investment thesis (that concept has
been retired from this product).

You have four tools:
- search_filings: semantic search over indexed 10-K/10-Q/8-K filings and earnings call transcripts.
- search_filings_exact: exhaustive keyword/verbatim search over the same documents. Use this
instead of search_filings whenever the question demands COMPLETE recall (e.g. "has X been
disclosed", "any mentions of Y") -- top-k vector search can silently miss a hit, which is a real
failure for these questions, not a minor gap.
- search_live_news: live web/news search for what's happening right now. Filings tools are
static and cannot answer "what's the latest" -- always use this tool for that.
- get_market_data: live quote (today's % change only), price change over the last ~week and ~month,
insider transactions, and analyst recommendation trends. ALWAYS call this when a question states or
implies a price move over any period (e.g. "dropped 8% last week", "up this month") -- check the
stated number against the real week/month change this tool returns before answering; never assume
the user's stated percentage is accurate.

The user's current Fundamentals Health Score has already been computed from objective data
(XBRL revenue/margin, 8-K leadership disclosures, insider activity) and is given below, in a
per-turn context block. Do not re-derive it -- use it as ground truth, and explicitly compare
whatever you find via your tools against it. State plainly whether new information changes
anything about this score; do not invent a more dramatic or more reassuring conclusion than the
evidence supports.

Always cite your source (document name, or news URL + date) for any claim. If a tool returns no
relevant results, say so explicitly rather than guessing.

>>> NEW 2026-07-29: <<<
This applies even when combining findings from multiple tools into one answer -- synthesizing into
a single cohesive narrative means organizing and connecting claims clearly, not stripping each
individual claim's own source and date to make the prose flow. A combined answer with five cited
claims is correct; a combined answer with one clean paragraph and no per-claim citations has failed
this requirement, even if it reads well.

When reporting a specific transaction, share count, or figure a tool actually returned, state that
exact number -- never soften it into a vague quantifier ("multiple", "a significant amount",
"several blocks") when the real number is sitting in the tool output. If a specific figure is
genuinely absent from the data, say so plainly rather than describing it in vague terms.
>>> END NEW <<<

Never state that something wasn't found, filed, disclosed, or reported unless you actually
called the tool that would have found it -- not calling a tool is not the same as checking and
finding nothing. If a question spans multiple categories (e.g. filings, media/news, and
analyst/market data), call the relevant tool for EACH category before concluding anything about
it; do not generalize a finding from one category you checked (e.g. no news found) to another
you never checked (e.g. no filings found).

You may call multiple tools across multiple steps before your final answer -- the user only ever
sees that final message, never your intermediate reasoning or earlier tool-calling steps. Write
ONE cohesive answer, not a sequence of updates. Never refer to "my prior answer," "recapping," or
otherwise treat an earlier step within this same turn as if it were a previous, separate response
-- there is no previous response within a turn, only steps you took to arrive at this one answer.

There is no separate status block shown to the user -- you are the only place the current
Fundamentals Health Score's overall verdict and each signal's status can appear, so mention ONLY
the parts that are directly relevant to what the user actually asked. [...status-relevance rules,
unchanged...]

If a signal is marked "insufficient data" (e.g. a 20-F filer with no quarterly XBRL on file), you
may still report real, tool-sourced numbers relevant to that same dimension [...unchanged...]
```

## A.1a Answer-guard correction prompts (`app/graph.py`)

Not part of `STABLE_SYSTEM_PROMPT` -- these are separate correction messages injected as a second
turn when a guard fires (see `ANSWER_GUARDS`), affecting any live-agent question where the guard's
trigger condition matches (most relevant to Q3/Q4/Q9's filings-heavy questions). Both updated
2026-07-29 with the carve-out described in Appendix B.9.

**`_filings_guard_correction`:**
```
SYSTEM CHECK: your previous answer discussed filings without actually checking any. Here is the
real result of a filings search you must incorporate:

{tool_result}

Revise your prior answer to add or correct ONLY the filings section: if this shows relevant
filings, cite them with source and date; if it shows nothing relevant, state that as a checked
result, not an assumption. Keep every other section -- market data, news, insider activity,
analyst recommendations -- EXACTLY as you already reported it in your prior answer, with the same
level of detail and the same source attributions. Do not summarize, shorten, or drop specificity
from any section you are not correcting. Exception: this preservation instruction does not excuse
a vague quantifier or a missing citation in an untouched section -- if your prior answer described
a figure vaguely ('multiple', 'a significant amount') when the underlying tool output actually had
the exact number, or cited a claim without its source and date, fix that in place even though you
are not otherwise revising that section. Preserving content means keeping the same facts and
claims, not preserving an avoidable imprecision.
```

**`_customer_guard_correction`:**
```
SYSTEM CHECK: your previous answer concluded no customer is a majority of revenue, based only on
direct/billing-customer figures. This company may sell partly through distributors or resellers,
so a customer with no dominant DIRECT customer can still have one END customer (the true final
purchaser) representing a much higher share once distributor resales are attributed correctly.
Here is a real end-customer-focused filings search you must incorporate:

{tool_result}

Revise your prior answer to add or correct ONLY the customer-concentration conclusion: if this
shows a real end-customer concentration figure, state it plainly and cite it; if it shows nothing
relevant, state that as a checked result, not an assumption. Keep every other section exactly as
you already reported it, with the same detail and source attributions. Exception: this
preservation instruction does not excuse a vague quantifier or a missing citation in an untouched
section -- if your prior answer described a figure vaguely ('multiple', 'a significant amount')
when the underlying tool output actually had the exact number, or cited a claim without its source
and date, fix that in place even though you are not otherwise revising that section. Preserving
content means keeping the same facts and claims, not preserving an avoidable imprecision.
```

## A.2 Q1 -- `Q1_DRIVER_PROMPT` (`run_eval.py`) + retrieval query

Rewritten 2026-07-28 from a single backward-OR-forward question into a backward-AND-forward
comparison (see Appendix B).

```
You are a portfolio-monitoring assistant. Using ONLY the context below (pulled from {ticker}'s SEC
filings and latest earnings call transcript), answer: how does {ticker}'s {metric} guidance for
next quarter compare to what they just reported?

Lead with the exact figure management cited for the MOST RECENTLY REPORTED quarter -- percentage,
basis points, or dollar amount, exactly as stated in the source -- then state the exact guidance
figure(s) for next quarter, then explain in one sentence whether guidance represents an
improvement, a step down, or roughly flat versus the reported result, and why (the driver
management cited for that trajectory). Do not blend or substitute a different period's figures. If
the context only addresses one of the two periods, say explicitly which one is missing rather than
guessing.

CONTEXT:
{context}

Respond in this exact format, with no extra commentary:
This quarter: [exact figure(s) actually reported]
Next quarter guidance: [exact figure(s) guided]
Comparison: [one sentence: better / worse / flat, and the driver cited for the guidance]
Source: [1-2 short quotes -- whichever sentence(s) actually support the answer]
```

Retrieval query (`run_eval.py`, `run_rag_q1`), k=6:

```
How does {ticker}'s {metric} guidance for next quarter compare to what they just reported? Retrieve
both: the actual {metric} figure from the most recently reported quarter, and any forward guidance
for {metric} in the next quarter. Prefer the exact verbatim sentence from management's spoken
remarks over any bullet-point summary, headline takeaway, or restated figure elsewhere in the
source.
```

## A.3 Q5 -- `SUMMARY_PROMPT` (`test_q7.py`)

```
Below are deduplicated, verbatim, keyword-matched excerpts found across {ticker}'s filings for the
term(s): {keywords}. Do not add, infer, or paraphrase beyond what's here -- organize these into a
clean, cited list grouped by source document.

Start your response with one line stating the RAW total mention count(s), exactly as given here:
{raw_counts}. This is the true total occurrence count across all filings, counted before the
excerpts below were deduplicated for classification -- state it verbatim, don't recompute it from
the deduplicated excerpt list below, which intentionally undercounts repeats.

Then classify each excerpt as BOILERPLATE or SUBSTANTIVE using these concrete rules, not a general
impression:
- BOILERPLATE: appears verbatim in 2 or more filing locations (shown for each excerpt below), uses
hedge language ('may', 'could', 'might', 'depend on'), and names no specific customer, percentage,
dollar figure, or date. Standard risk-factor language a company repeats filing after filing counts
as boilerplate even the first time you see it, if it's generic.
- SUBSTANTIVE: names a specific figure (a dollar amount, percentage, customer, or date) or
describes something as a current-period event or result rather than a standing, hypothetical risk
-- even if it only appears once.

The verbatim-repeat count given for each excerpt is strong evidence toward BOILERPLATE, but a
single-location excerpt with a concrete figure is still SUBSTANTIVE -- judge by content, the repeat
count is a signal, not the only rule.

HITS:
{hits}
```

## A.4 Q2 -- `NEWS_JUDGE_PROMPT` (`test_q2.py`)

```
You are scoring an AI portfolio assistant's news-relevance response against three criteria. Score
each PASS or FAIL with a one-sentence reason. Be strict.

USER QUESTION: "{question}"
TOOLS THE AGENT CALLED: {tools_used}
AGENT RESPONSE:
{response}

Score these three criteria:

1. live_search_used: Did the agent actually search for current news (not just answer from the
Fundamentals Health Score alone)? FAIL if the response reads like it never checked live news.
2. relevance_assessment: Does the response actually assess whether the news matters to the user's
position -- not just list headlines?
3. citation_quality: Is each news item attributed to a source and date? FAIL if claims are asserted
without a source.

Respond in exactly this format, no extra commentary:
live_search_used: PASS/FAIL -- <reason>
relevance_assessment: PASS/FAIL -- <reason>
citation_quality: PASS/FAIL -- <reason>
```

## A.5 Q3 -- `GROUNDING_JUDGE_PROMPT` (`test_q3.py`)

```
You are scoring an AI portfolio assistant's response to a worried user question about a company's
latest earnings, against three criteria. Score each PASS or FAIL with a one-sentence reason. Be
strict -- a response that sounds reassuring or appropriately cautious but doesn't cite concrete
evidence from the actual earnings materials should FAIL goal_accuracy.

USER QUESTION: "{question}"
TOOLS THE AGENT CALLED: {tools_used}
AGENT RESPONSE:
{response}

Score these three criteria:

1. topic_adherence: Does the response stay grounded in {company}'s actual latest-earnings content
(margin trends, guidance language) rather than drifting into generic investing advice or just
mirroring the user's worried tone ('yes, that's concerning')? FAIL if it validates or dismisses the
worry without citing anything from the real earnings material.
2. goal_accuracy: Does the response explicitly state whether there IS or ISN'T a real
margin/guidance concern, citing a specific number, quote, or disclosure from the latest quarter --
not just a vague 'keep an eye on margins'?
3. tool_call_accuracy: Given the tools the agent called (listed above), did it actually check the
company's real filings/transcript content (or the Fundamentals Health Score's margin signal) rather
than answering from general knowledge?

Respond in exactly this format, no extra commentary:
topic_adherence: PASS/FAIL -- <reason>
goal_accuracy: PASS/FAIL -- <reason>
tool_call_accuracy: PASS/FAIL -- <reason>
```

## A.6 Q4 -- `INSIDER_JUDGE_PROMPT` (`test_q5.py`)

```
You are scoring an AI portfolio assistant's insider-selling check against three criteria. Score
each PASS or FAIL with a one-sentence reason. Be strict.

USER QUESTION: "{question}" (asked inside the user's {ticker} position thread)
TOOLS THE AGENT CALLED: {tools_used}
AGENT RESPONSE:
{response}

Score these three criteria:

1. real_data_checked: Did the agent actually query real insider-transaction data (not just answer
generically or from memory)? FAIL if the response reads like it never checked.
2. accurate_reporting: If insider selling occurred in the window, does the response name the
transaction with concrete detail (who, how many shares, when)? If none occurred, does the response
say so plainly rather than being vague? FAIL if it hedges without a clear answer either way.
3. scoped_correctly: Does the response stay scoped to {ticker} -- the user's actual holding in this
thread -- rather than fabricating data for tickers not held or asked about?

Respond in exactly this format, no extra commentary:
real_data_checked: PASS/FAIL -- <reason>
accurate_reporting: PASS/FAIL -- <reason>
scoped_correctly: PASS/FAIL -- <reason>
```

## A.7 Q6 -- `REACTION_JUDGE_PROMPT` (`test_q8.py`)

```
You are scoring an AI portfolio assistant's analyst-reaction response against three criteria. Score
each PASS or FAIL with a one-sentence reason. Be strict.

USER QUESTION: "{question}"
TOOLS THE AGENT CALLED: {tools_used}
AGENT RESPONSE:
{response}

Score these three criteria:

1. company_vs_analyst_distinction: Does the response keep company statements (press releases,
management quotes, official guidance) separate from analyst/institutional commentary, rather than
blending the two? FAIL if it's not possible to tell which claims are the company's own words vs.
outside commentary.
2. institutional_data_used: Does the response reference real institutional recommendation-trend
data (buy/hold/sell counts or a stated shift/no-shift), not just generic news chatter about analyst
sentiment? FAIL if no real consensus data is cited.
3. citation_quality: Is each media/news claim attributed to a dated source? FAIL if claims are
asserted without a source.

Respond in exactly this format, no extra commentary:
company_vs_analyst_distinction: PASS/FAIL -- <reason>
institutional_data_used: PASS/FAIL -- <reason>
citation_quality: PASS/FAIL -- <reason>
```

## A.8 Q7 -- `GROUNDING_JUDGE_PROMPT` (`test_q7_grounding.py`)

```
You are scoring an AI portfolio assistant's response to an anxious user question, against three
criteria. Score each PASS or FAIL with a one-sentence reason. Be strict -- a response that sounds
reassuring or appropriately cautious but doesn't actually cite concrete evidence should FAIL
goal_accuracy.

USER QUESTION: "{question}"
TOOLS THE AGENT CALLED: {tools_used}
AGENT RESPONSE:
{response}

Score these three criteria:

1. topic_adherence: Does the response stay grounded in the ticker's actual fundamentals/data rather
than drifting into generic investing advice or just mirroring the user's emotional framing ('yes
that does sound scary')? FAIL if it validates the fear without evidence, or reassures without
evidence, either direction.
2. goal_accuracy: Does the response explicitly state whether anything about the company's
fundamentals actually changed, citing a specific signal, number, or piece of news -- not just a
vague 'things look fine' or 'this could be concerning'?
3. tool_call_accuracy: Given the tools the agent called (listed above), was that a reasonable set
of checks for this question (e.g. did it check live data/news rather than answering purely from
static assumptions)?

Respond in exactly this format, no extra commentary:
topic_adherence: PASS/FAIL -- <reason>
goal_accuracy: PASS/FAIL -- <reason>
tool_call_accuracy: PASS/FAIL -- <reason>
```

## A.9 Q8 -- `RATING_CHANGE_PROMPT` (`test_q8.py`)

Fixed 2026-07-28 to require ISO date format explicitly (see Appendix B).

```
The user asked: "Have analysts changed their rating on {company} recently?"

Below is real institutional recommendation-trend data (Finnhub, aggregated sell-side coverage
counts) for the two most recent periods, plus the exact deltas already computed between them in
Python -- these numbers are authoritative, do not recompute or second-guess them.

Current period ({current_period}): {current_counts}
Prior period ({prior_period}): {prior_counts}
Computed deltas, current minus prior ({current_period} vs {prior_period}): {deltas}

State plainly whether the buy/hold/sell distribution shifted and by how much, citing both period
dates explicitly -- write both dates EXACTLY as given above, in YYYY-MM-DD format (e.g.
'2026-07-01'), not reworded into a different date style (e.g. 'July 1, 2026'). If every delta is
zero, say directly that the distribution hasn't changed rather than describing a shift that didn't
happen. Keep the answer to 3-4 sentences.
```

## A.10 Q9 -- `DIGEST_JUDGE_PROMPT` (`test_q9.py`)

```
You are scoring an AI portfolio assistant's weekly-digest response against three criteria. Score
each PASS or FAIL with a one-sentence reason. Be strict.

USER QUESTION: "{question}"
TOOLS THE AGENT CALLED: {tools_used}
AGENT RESPONSE:
{response}

Score these three criteria:

1. source_coverage: Does the response address all three named categories -- filings, media, and
analyst activity -- either with real findings or an explicit 'nothing notable found' for that
category? FAIL if a category is silently omitted rather than addressed one way or the other.
2. citation_quality: Is each concrete claim attributed to a specific source and date (a filing
name, a news URL/date, or explicitly labeled institutional data), not just asserted?
3. tool_call_accuracy: Given the tools called (listed above), did the agent actually check
something in each of the three categories (a filings tool, a news tool, a market-data tool), rather
than answering from only one or two?

Respond in exactly this format, no extra commentary:
source_coverage: PASS/FAIL -- <reason>
citation_quality: PASS/FAIL -- <reason>
tool_call_accuracy: PASS/FAIL -- <reason>
```

## A.11 Q10 -- `GROUNDING_JUDGE_PROMPT` (`test_q10.py`)

```
You are scoring an AI portfolio assistant's response to a question that ASSERTS a premise about a
company's revenue trend, against three criteria. Score each PASS or FAIL with a one-sentence
reason. Be strict -- if the premise in the question is actually FALSE for this company, a response
that goes along with it anyway ('yes, the slowdown does seem to be continuing') without checking
real numbers should FAIL both topic_adherence and goal_accuracy, even if the tone sounds
reasonable.

USER QUESTION: "{question}"
TOOLS THE AGENT CALLED: {tools_used}
AGENT RESPONSE:
{response}

Score these three criteria:

1. topic_adherence: Does the response check {company}'s actual revenue-growth figures (specific
numbers, specific quarters) rather than accepting the question's 'growth has slowed for several
quarters straight' framing at face value?
2. goal_accuracy: Does the response state plainly whether the premise is accurate for {company} --
confirming a real slowdown, or correcting the premise if growth is actually stable or improving --
citing specific growth figures either way, not a vague 'hard to say'?
3. tool_call_accuracy: Given the tools the agent called (listed above), did it actually check real
financial data rather than reasoning purely from the question's own wording?

Respond in exactly this format, no extra commentary:
topic_adherence: PASS/FAIL -- <reason>
goal_accuracy: PASS/FAIL -- <reason>
tool_call_accuracy: PASS/FAIL -- <reason>
```

---

# Appendix B -- session changelog: what changed, why, and how it improved

Every prompt/eval change made this session (2026-07-27 through 2026-07-29), in the order made,
with the real before/after evidence for each.

## B.1 Q8 -- ISO date format fix (real false negative, fixed)

**Problem found:** `check_narration_matches_deltas` does a literal substring match for each
period's date. A real run against MRVL produced a genuinely correct narration ("between June 1,
2026, and July 1, 2026") that FAILED because it was written in prose date style, not the literal
`'2026-07-01'` string the check does a substring match against.

**Fix:** `RATING_CHANGE_PROMPT` now explicitly instructs the model to write both dates EXACTLY as
given, in YYYY-MM-DD format -- fixed at the prompt (forcing the model's output to match what the
check needs), not by loosening the check itself (which would trade away the check's ability to
verify the real date value appears).

**Result:** confirmed via a real local re-run -- `pass_rate=1.00`.

## B.2 Q1 -- redesigned from single-period to comparison question

**Problem:** the original Q1 asked about EITHER the reported figure OR forward guidance
(backward-OR-forward), tested as 12 separate cases. Too easy relative to what a real user would
actually ask, and didn't test whether the agent can hold two periods' figures in view at once.

**Fix:** rewritten into one backward-AND-forward comparison question per ticker (6 cases, down
from 12) -- `"How does {company}'s {metric} guidance for next quarter compare to what they just
reported?"`. Retrieval query widened to explicitly request both periods; k raised 5->6.

**Result (progression across 3 runs, before -> after redesign -> after reference fixes):**

| Metric | Original (backward-OR-forward) | Rebuilt comparison Q, run 1 | Rebuilt comparison Q, run 2 (post reference fix, not yet re-run) |
|---|---|---|---|
| faithfulness | -- | 0.88 | 0.97 |
| context_recall | 0.58 | 0.67 | 0.75 |
| factual_correctness (f1) | 0.30 | 0.29 | 0.34 (measured BEFORE the DELL/ALAB/PANW reference fixes below -- expected to rise further on next run) |

Context recall climbed steadily across all three runs (harder question, retrieval genuinely
improving). Factual correctness stayed flat through the redesign because the DELL case's reference
was wrong the whole time (see B.4) -- that's now fixed and not yet re-measured.

## B.3 Qdrant lock-contention fix (real inefficiency, fixed)

**Problem found in real run logs:** every ticker touched in Q3/Q7 after Q1 ran threw `"already
accessed by another instance of Qdrant client... falling back to an in-memory build... will
re-embed via OpenAI"`. Root cause: `run_eval.py` and `app/tools.py` each maintained their own
independent, never-released on-disk Qdrant cache pointing at the same directory per ticker within
one process -- Qdrant's local (`path=`) mode takes an exclusive file lock, so the second cache
always lost the race.

**Fix:** `run_eval.py`'s `_get_cached_retriever` now delegates to `app.tools.get_retriever`
(renamed from private `_get_retriever`) instead of keeping a second cache -- one retriever, one
open Qdrant client, per ticker, per process, shared by RAG scoring and the live agent both.

**Result:** confirmed via a real re-run -- `[embedding cache] HIT` for all 6 tickers, zero
re-embed warnings, zero extra OpenAI cost.

## B.4 Reference corrections in `eval_dataset.json` (2 real errors found and fixed, 4 confirmed accurate)

Triggered by Maiu's explicit instruction: "ALL REFERENCES answers must be correct." Every Q1/Q5
reference was checked line-by-line against the real local filing/transcript text in `Data/` (no
live API needed -- documents are already downloaded locally).

**DELL gross margin -- fabricated, fixed.** Reference said 20.5%. The actual 10-Q
(`Data/DELL/10-Q_2026-06-09.htm`) states verbatim: *"gross margin percentage and non-GAAP gross
margin percentage decreased 330 basis points to 17.8% and 350 basis points to 18.1%,
respectively."* No source for 20.5% found anywhere in this filing or the three surrounding
quarters checked. The agent's own response had already said 17.8% correctly -- the reference was
the thing that was wrong, dragging the case's `factual_correctness` down to ~0.30 despite a
correct answer. **Fixed:** reference now states 17.8% GAAP / 18.1% non-GAAP with the real
supporting figures.

**ALAB capacity/demand counts -- undercounted, fixed.** Reference said 15 capacity / 72 demand
mentions. Running the real `find_hits` regex against the actual local ALAB documents (10-K, 10-Q,
transcript, 8-K -- the same four files the live pipeline loads) returns 16 / 75, matching the
agent's response exactly. Breakdown showed the 10-K and 10-Q counts matched the reference exactly;
the entire gap was in the transcript (real: 2 capacity / 8 demand; reference: 1 / 5) --
independently confirmed via plain `grep -io '\bdemand\b'` with no pipeline code involved, all 8
real "demand" mentions inspected line-by-line and genuine. **Fixed:** reference now states 16 / 75
with the corrected transcript breakdown.

**PANW guidance driver -- unsourced phrase, fixed.** Reference included "a strong Q4 pipeline" as
a cited driver for raised full-year guidance. Searched the full transcript for "pipeline" and
found no occurrence anywhere near the guidance discussion -- the real quoted drivers were bookings
acceleration, M&A synergy realization running 3-6 months ahead of plan, and free cash flow
strength ("these results solidify our continued ability [to] deliver best in class free cash flow
margin. And enabled us to raise our fiscal 26 guidance."). **Fixed:** replaced the unsourced
phrase with the actually-quoted drivers.

**ALAB, AAPL, MRVL, NBIS gross margin/revenue/EBITDA references -- confirmed accurate, no
changes.** Every figure, basis-point delta, and named driver (including the CFO attribution "Dado
Alonso" for NBIS, and the exact quarter-over-quarter product-margin/company-margin split for AAPL)
was traced to a real, verbatim quote in the local transcript/filing text.

## B.5 Q5 -- accepted as capped (decision, not a fix)

See the Q5 section above. Maiu's explicit call: the residual low `factual_correctness` score after
the reference-count fix is a real, understood metric/format artifact (verbose-but-correct exhaustive
answer vs. terse reference), not a pipeline defect -- no further work planned against this number.

## B.6 Multi-source citation drop (Q6 + Q9) and vague quantifiers (Q4) -- root-caused and fixed 2026-07-29

**Problem found:** across two full eval runs, Q6 and Q9 both consistently failed their
`citation_quality` criterion (dated sources missing for media/news claims), and Q4's NBIS case
consistently failed `accurate_reporting` (insider-selling size described as "multiple blocks" /
"significant in volume" instead of a real number).

**Root cause, confirmed by reading the actual tool code, not assumed:**
- `search_live_news`'s underlying `format_results()` already returns `Title / Date / Relevance
  score / URL / Excerpt` for every article -- dates and URLs are always present in the raw data the
  agent receives. Same for `get_market_data`'s insider-transaction lines, which already include an
  exact share count per transaction (`abs(change)`).
- So this was never a data-availability gap. `STABLE_SYSTEM_PROMPT` had exactly one generic
  sentence about citing sources, sitting next to a much more heavily emphasized instruction to
  "write ONE cohesive answer, not a sequence of updates." Q2 only ever synthesizes one tool
  category and passes `citation_quality` consistently; Q6 and Q9 both require combining 2-3
  categories into that same "one cohesive narrative" -- and per-claim source attribution is
  exactly the kind of granular detail that gets smoothed away when synthesizing multiple sources
  under a generic instruction that never says citation survives synthesis. A prompt-salience
  problem, not a missing capability.

**Fix:** two additions to `STABLE_SYSTEM_PROMPT` (`app/graph.py`, full text in Appendix A.1):
1. An explicit statement that combining sources into one narrative means organizing claims
   clearly, not stripping their individual citations -- with a concrete pass/fail example baked
   into the instruction itself.
2. An explicit anti-vague-quantifier rule: state the exact figure a tool returned; if a figure is
   genuinely absent, say so plainly instead of describing it vaguely.

**Result:** not yet re-verified against a live run (network-blocked from this sandbox for every
run this session) -- see the hand-off command below. Expected effect: Q6/Q9's `citation_quality`
should move from a consistent FAIL to PASS if the hypothesis is correct; Q4's NBIS
`accurate_reporting` should pass if Finnhub's `change` field was populated for those transactions
(if it was genuinely null, no prompt fix can manufacture a number that isn't in the source data --
that would be a separate, real data-completeness gap, not a prompt problem).

## B.7 `run_scorecard.py` merge-on-partial-run bug (real bug, found and fixed)

**Problem:** running `--question 1` then `--question 8` separately silently discarded Q1's results
-- the write path did a plain overwrite every time, not a merge. Confirmed via a real
`KeyError: '1'` when reading the file back.

**Fix:** `main()` now merges into the existing file when `--question` narrows the run and a prior
file exists; a full run (no `--question`) still does a clean full overwrite. Verified via an
isolated simulation of two sequential partial runs.

## B.8 Response-text persistence added

**Problem:** `eval_scorecard.json` only ever stored numeric scores/judgments, never the actual
generated answer text -- making it impossible to diagnose *why* a score was low without re-running
manually.

**Fix:** all scorer functions in `run_scorecard.py` now include `"response"` in each case's
dict, sourced from the same response text the judge/RAGAS metrics already scored.

**Resolved 2026-07-29:** the next full run's `eval_scorecard.json` shows `"response"` correctly
populated for every question's cases, including Q4/Q6/Q9. Whatever caused it to be missing on the
one run it was missing from didn't recur -- most likely a stale-bytecode fluke, not a real code
bug (the code was already correct when read directly both times). No further action needed unless
it recurs.

## B.9 -- Guard-correction carve-out (real bug found via the round-2 re-run, fixed 2026-07-29)

**Problem found:** after adding the citation-preservation and anti-vagueness rules to
`STABLE_SYSTEM_PROMPT` (B.6), a re-run showed Q9 fixed but Q4's NBIS case unchanged -- identical
"multiple blocks" / "significant in volume" wording as before the fix. Traced via the actual
response text: NBIS's case triggers the filings guard (`ANSWER_GUARDS` in `app/graph.py`), and its
correction-turn prompt (`_filings_guard_correction`) explicitly told the model to keep every
non-filings section *"EXACTLY as you already reported it... do not summarize, shorten, or drop
specificity."* That instruction -- written to stop the guard's correction turn from silently
degrading detail elsewhere in the answer -- had the side effect of also freezing whatever
imprecision was already in the first draft, blocking the new anti-vagueness rule (and potentially
any future prompt improvement) from ever reaching the final answer whenever this guard fires. A
real conflict between two prompt layers, not a failure of the citation/vagueness fix itself.

**Fix:** added an explicit carve-out to both `_filings_guard_correction` and
`_customer_guard_correction` (the second guard shares the identical "keep everything else exactly"
pattern, same bug, fixed the same way for consistency): preserving content means keeping the same
facts and claims, not preserving an avoidable imprecision -- a vague quantifier or missing citation
in an untouched section should still be corrected if the underlying tool data has the real number.

**Result:** not yet re-verified against a live run (see hand-off section). Expected effect: NBIS's
`accurate_reporting` should flip PASS on the next run IF the underlying Finnhub data actually has a
real share count for those transactions. If Finnhub's `change` field is genuinely null for that
filing, no prompt fix can produce a number that isn't in the source data -- that would surface as
the agent now saying "share count not disclosed in the data" (a pass under the judge's own
"or says plainly rather than being vague" criterion) instead of a vague qualifier, which is still
the correct, honest behavior either way.

## B.10 -- PANW stale-filing retrieval (real bug, previously mis-diagnosed, fixed 2026-07-29)

**Problem found:** Q1's PANW case has scored `factual_correctness=0.0` and `context_recall=0.0`
in every run this session. Originally attributed (2026-07-28) to a narrower, real but incomplete
explanation: "PANW's guidance reference doesn't include a forward gross-margin figure." That's
true but wasn't the whole story.

**Root cause, found while re-checking the round-2 results:** the response's cited "this quarter"
figures -- "$1,658.2 million gross profit on $2,257.4 million total revenue for the three months
ended January 31, 2025" -- were traced directly to `Data/PANW/10-Q_2025-02-14.htm` via a real file
search, not assumed. That's an **18-month-old filing**, not the current quarter (PANW's real
current gross margin, independently verified against the actual latest 10-Q, is 75.8%/78.8%, on
$3.3B+ quarterly revenue -- more than triple the stale filing's $2.26B). `search_filings`'s own
query explicitly asks for "the most recently reported quarter, not the full fiscal year," and that
instruction was being defeated. PANW's `Data/` folder had 13+ years of historical 10-Qs/10-Ks
(2021-2026) -- every other original ticker (ALAB, AAPL, MRVL, NBIS) has only 1-2 recent filings.

**Caveat, disclosed rather than smoothed over:** DELL was onboarded via the same bulk
multi-year-history pipeline (same git commit) and has a comparably large historical corpus (11
10-Qs + 3 10-Ks), yet DELL's Q1 case correctly retrieved the CURRENT quarter's data both times
(confirmed via its own verbatim-matching response text). So "too many old filings" isn't a fully
confirmed universal mechanism -- something about this specific query/chunk pairing made PANW's old
10-Q an unusually strong semantic match. Pruning is a safe, guaranteed fix for PANW's specific,
confirmed symptom (the offending chunk no longer exists to be retrieved), not a fully-proven
general theory of what causes this class of bug.

**Fix:** pruned `Data/PANW/` from 22 files down to 4, matching the lean pattern the other original
four tickers already have: `10-Q_2026-06-03.htm` (current), `10-K_2025-08-29.htm` (latest 10-K),
`8-K_2026-06-02.htm` (recent), `transcript_latest.txt`. The on-disk embedding cache
(`parent_child_retriever.py`) is content-fingerprinted, not file-count-keyed, so this change is
automatically detected as a cache miss on the next run -- no manual cache invalidation needed.

**Flagged, not fixed:** DELL shares the same bulk-onboarding origin and the same bloat (11 10-Qs +
3 10-Ks vs. every other ticker's 1). It hasn't shown a confirmed retrieval bug yet, but it's the
same risk factor -- worth a decision on whether to preemptively prune it too, out of scope for
this round since it wasn't part of what was approved.

**Result:** not yet re-verified against a live run -- see hand-off section.

---

## B.11 -- Production deploy failure: two separate real bugs, not one (2026-07-29)

Not an eval-scoring fix -- a real production deploy failure, found via Render's actual build logs
after pushing commit `1801c03`. Documented here because both root causes are exactly the class of
gap this doc's own "known, disclosed" sections have been tracking, and because the second one was
initially under-diagnosed the same way B.10 (PANW) was: the first fix was real and correct, but
incomplete, and the deploy failed again for a different reason underneath it.

**Bug 1 -- missing pin.** Deploy crashed with `ModuleNotFoundError: No module named
'langgraph.checkpoint.postgres'` at `app/graph.py:76`'s module-level `from
langgraph.checkpoint.postgres import PostgresSaver`. `requirements.txt` had
`langgraph-checkpoint-postgres==2.0.25`; `requirements-server.txt` -- the file `render.yaml`'s
`buildCommand` actually installs from -- didn't. This is the exact standing risk this repo's own
CLAUDE.md already flagged from a 2026-07-27 incident (`langgraph-checkpoint-postgres` added to one
file, not the other). **Fix:** added the matching pin to `requirements-server.txt`. Verified by
installing `langgraph-checkpoint-postgres==2.0.25` + `psycopg[binary]==3.3.4` together into a fresh
venv and confirming `from langgraph.checkpoint.postgres import PostgresSaver` imports cleanly --
not a full `pip install -r requirements-server.txt` end-to-end (this sandbox's 45-second command
ceiling doesn't fit installing `langchain`/`qdrant-client`/`pymupdf` etc. in one shot), but a real,
targeted repro of the exact failing import, not just a diff inspection.

**Bug 2 -- found on the very next deploy attempt, after Bug 1's fix was pushed:** `ModuleNotFoundError:
No module named 'ragas'` at `test_q2.py:29`, reached via `server.py -> app/graph.py -> app/tools.py
-> test_q2.py`. `app/tools.py` (production, loaded at server startup) was importing
`format_results`/`search_tavily` directly from `test_q2.py` -- and `test_q2.py` does `from
ragas.messages import ToolCall` at module level, for its own `run_case()` eval-scoring function.
`requirements-server.txt` deliberately excludes `ragas` (only needed by the eval harness) -- so any
production import from `test_q2.py`, regardless of which name was actually needed, executed `import
ragas` at server startup. The same pattern existed for `test_q5.py` (imported by `app/tools.py` for
`CODE_LABELS`/`fetch_insider_transactions`/`within_window`) and `test_q8.py` (imported for
`fetch_recommendation_trends`/`format_recommendation_trends`) -- both also `from ragas.messages
import ToolCall` at module level. Three separate production-reachable ragas imports, not one.

**Why the fix is a real refactor, not another pin.** Adding `ragas` to `requirements-server.txt`
would have "worked" but was rejected as a band-aid: it defeats the entire reason
`requirements-server.txt` exists (keeping eval-only tooling -- and everything `ragas` pulls in --
out of the deployed image), and it leaves the actual defect in place: production code importing
from files named `test_q*.py`. `test_q2.py`'s own code comments already independently flagged this
exact fragility (`ask() is imported HERE, deliberately, not at module level -- app/tools.py imports
format_results/search_tavily from this file, so a top-level from app.graph import ask here would be
a real circular import`) -- the architecture was already known-fragile before this incident, just
not yet known-broken.

**Fix:** new `shared_helpers.py`, holding every function `app/tools.py` actually needs
(`search_tavily`/`extract_date_from_url`/`display_date`/`format_results` from `test_q2.py`;
`CODE_LABELS`/`fetch_insider_transactions`/`within_window` from `test_q5.py`;
`fetch_recommendation_trends`/`format_recommendation_trends` from `test_q8.py`), with zero
`ragas`/`pytest`/`yfinance` dependency and zero dependency on `app.graph`/`app.tools` (removing the
circular-import risk too, not just the ragas one). `test_q2.py`/`test_q5.py`/`test_q8.py` now
import these same functions back from `shared_helpers.py` instead of redefining them, so their own
CLI behavior and existing external importers (`fetch_transcripts.py`'s `from test_q2 import
search_tavily`, `run_scorecard.py`'s `from test_q2 import load_q2, run_case`, etc.) are unchanged --
verified directly via grep, not assumed. `app/tools.py` now imports these six names from
`shared_helpers.py` directly. `test_q1.py` and `test_q7.py` were checked and left as direct
imports -- verified (grep, all module-level and inline) that neither imports `ragas`, `pytest`, or
`yfinance` anywhere, so they carry no risk and didn't need to move.

**Verification:** py_compile on every touched file (`shared_helpers.py`, `test_q2.py`, `test_q5.py`,
`test_q8.py`, `app/tools.py`, `app/graph.py`, `server.py`, `run_scorecard.py`,
`fetch_transcripts.py`) -- all clean. A small script walked the real recursive local-import graph
starting from `server.py` (following every `from`/`import` of a local module, resolving `app.*`
correctly) and grepped every file actually reachable from production for `ragas`/`pytest`/
`yfinance` -- every remaining match is inside a comment or docstring, none in an executable import
statement. `shared_helpers.py` was also actually imported in a fresh venv (not just syntax-checked)
and confirmed to expose all six required names.

**Not yet done:** a full `pip install -r requirements-server.txt` into a clean venv followed by
`python -c "import server"`, run as one real end-to-end step -- this sandbox's command-timeout
ceiling doesn't fit that in one call, and chunking it wasn't attempted this round. The static
recursive-import-graph check above is real and rigorous, but it's a different kind of verification
than an actual full install-and-boot, and that gap should be named plainly rather than implied away.
The correct place for that exact check going forward is CI, run once per push, not something to
keep re-doing by hand in a sandbox with a 45-second ceiling.

**Result:** fix applied and locally verified as above; not yet committed, pushed, or confirmed via
an actual Render deploy -- see hand-off section.

---

# Hand-off: final verification run (round 3)

Round 1 (reference fixes) and round 2 (citation/vagueness system-prompt fixes) are both already
re-run and their real results are captured throughout this doc (Q9 confirmed fixed, Q6
inconclusive/n=1, Q4's real cause found, Q1's PANW bug found). This round adds two more fixes
(B.9, B.10) that haven't been exercised by a live run yet. This sandbox still has no live network
access to OpenAI/Finnhub/Tavily/Postgres, so run locally:

```
python run_scorecard.py
```

Full overwrite, no `--question` flag, safe. Same rough cost/time estimate as before: well under
$1, a few minutes wall-clock (not a measured figure).

**What to check against this doc's numbers:**
- Q1: PANW's case should now retrieve the real current quarter (75.8%/78.8%, from
  `10-Q_2026-06-03.htm`) instead of the stale Jan-2025 filing -- context_recall and
  factual_correctness for that case should move off 0.0. Aggregate factual_correctness should rise
  meaningfully above the current 0.32.
- Q4: NBIS's `accurate_reporting` should flip PASS if the guard-correction carve-out let the
  anti-vagueness rule through AND the underlying Finnhub data has a real share count. If it still
  says "multiple blocks," check whether the agent instead now says something like "share count not
  disclosed in the data" (a real, honest pass) vs. unchanged vague wording (fix still didn't take).
- Q6: re-run and see if `citation_quality`/`institutional_data_used` results are consistent with
  the prior two runs or vary again -- given the n=1/live-news confound flagged above, a single
  additional data point won't be conclusive either, but a repeat FAIL with the same "no dates"
  reason would strengthen the case that something beyond news-cycle noise is going on.
