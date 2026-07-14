# Loom Demo Script — Personal Portfolio Copilot

Target: 9–10 minutes total. Timings are guides, not strict — pause naturally
during live tool calls (Tavily/Finnhub calls take a few real seconds; don't
narrate over dead air, just let it load and keep talking about what's coming).
Read this as a script the first time, then loosen it up on the actual take —
sounding scripted is worse than a few "um"s.

Before recording: have the live URL open in one tab, sign in / load your
holdings if the demo requires it, and have 3–4 questions ready to paste into
chat rather than typing live (typing live eats time and risks typos).

---

## 1. Cold open — the problem (0:00–1:00)

*(Talking to camera or over a blank screen, not the app yet.)*

"Hey — I'm going to walk you through Personal Portfolio Copilot, a project I
built for the AI Engineering Certification.

Here's the problem it solves. As a retail investor, if you hold 10 to 30
individual stocks, you have no consistent way to check whether the
fundamentals that justified each position still hold. Reading a filing or a
transcript for every position, every week, doesn't scale — so hold-or-sell
decisions end up driven by price swings and headlines instead of evidence. A
price drop triggers a sell impulse whether or not anything actually
changed — loss aversion and recency bias doing the thinking instead of the
facts.

This app closes that gap. It's an agentic RAG assistant that grounds each
holding in its own SEC filings and four objective fundamentals signals —
revenue growth, margin, insider activity, leadership stability — and answers
your questions by checking live news and market data against that ground
truth, not free-text impressions."

---

## 2. Architecture, fast (1:00–2:00)

*(Screen: the Infrastructure Diagram from Task 2 §2 of the PRD — the one
showing UI → Backend/LangGraph → LLM Layer → Data & Tools → Monitoring — or
just narrate over the app's landing page.)*

"Quickly on how it's built, then I'll jump into the live demo.

It's an agentic RAG app — a LangGraph agent that can call four tools: a
vector-search tool over each company's SEC filings and earnings transcripts,
a keyword/exact-match tool for questions where missing a disclosure would be
a real failure, a live-search tool for what's happening right now, and a
market-data tool for insider transactions and analyst consensus.

Why four tools instead of one do-everything retriever? Because these are
genuinely different question types. 'What did management say about margins'
is a similarity question — vector search handles that fine. 'Has this company
ever disclosed customer concentration risk' is an exhaustive-recall question —
vector search can quietly miss things, so that one gets a keyword path
instead. 'What's the latest news' can't be pre-indexed at all. And
insider-trading data is just a structured lookup, not something you'd embed.

It's deployed for real — FastAPI backend, Next.js frontend, both live on
Render, tracking four real tickers: Astera Labs, Apple, Marvell, and Nebius."

---

## 3. Live demo — grounded question (2:00–4:00)

*(Screen: the live app now. Type or paste a Q1-style question.)*

"Let's see it work. I'll ask something that requires it to find a specific
number management actually cited, not just describe margins in general terms:

**'What did Astera Labs' management identify as the driver behind this
quarter's gross margin change?'**

*(Send it, let it run.)*

Here's what to notice: it's not returning a generic margin number — it's
pulling the exact quote from the earnings call, separating that from what's
in the 10-Q, and citing exactly where it came from. [Read the actual figure
and citation off the response once it loads.] That citation is the whole
point — every claim it makes should be traceable back to a real source, not
a paraphrase."

*(Optional second question in this block — Q7-style, emotional grounding:)*

"Now let's try the case this app really exists for — someone panicking about
a price move:

**'[Ticker] just dropped 8% today, I'm nervous — should I sell?'**

*(Send it, let it run.)*

Notice it doesn't just validate the fear, and it doesn't just reassure
blindly either — it checks the drop against the company's real fundamentals
signals and news before saying anything. [Read the response, point out how it
separates 'here's what actually changed' from 'here's how you feel.']"

---

## 4. Live demo — completeness / exact-match (4:00–5:30)

*(Screen: still in chat.)*

"One more, because this is the reason there are two different retrieval paths
in this app, not one. Some questions need every mention of something, not
just the most similar-sounding chunk. If I ask:

**'Has [Company] disclosed any customer concentration risk recently?'**

*(Send it, let it run.)*

A missed disclosure here would be a real failure, not a minor gap. So this
routes to keyword/exact-match search instead of vector similarity — in
testing, plain vector search actually missed a real match once, because it
scored lower similarity than surrounding boilerplate text. This path checks
everywhere the term appears, then tells you whether that's routine boilerplate
language or an actual new signal."

---

## 5. Evals and what I learned (5:30–7:30)

*(Screen: can go back to slides/terminal output, or just talk over the app.)*

"Now, briefly, the evaluation side — this is where the real work was.

I built a 12-question locked eval set, hand-curated against the actual
behavior this app needs, not synthetically generated — most of these
questions need a live tool call, not just a document corpus, so a generator
couldn't have produced them anyway. I score it two ways: RAGAS's faithfulness,
context-recall, and factual-correctness triad for the retrieval-based
questions, and tool-call accuracy, goal accuracy, and topic adherence for
everything that's tool-calling. 10 of the 12 are built and passing today; the
other 2 are blocked on data I haven't ingested yet, not on unsolved design
problems.

The single biggest finding across everything I tested: this pipeline's
failure mode is almost entirely about what gets retrieved, not the model
hallucinating. Faithfulness scored close to perfect basically every time the
right context reached the model. What actually broke things was retrieval
completeness.

So for the advanced-retrieval piece, I built a parent-child retriever:
instead of returning an isolated 512-token chunk, it recovers the full filing
section or full speaker turn the match came from. Head-to-head, the baseline
retriever's recall was genuinely unstable — on the exact same question, it
swung between missing a fact entirely and finding it, purely depending on
where a chunk boundary happened to fall. The parent-child retriever scored a
perfect, stable recall on every run, because that boundary-luck problem just
doesn't exist for it.

Then, on the synthesis side of the pipeline — a different failure surface
entirely — I made two more fixes. First: the agent could skip the filings
tool completely on a 'summarize everything' question and still tell the user
'no new filings were found,' which is a claim about something it never
actually checked. I added a code-level guard that forces a correction turn
whenever the trace shows a needed filings check got skipped. Second: one
question kept producing a false 'has this gotten worse since I bought it'
comparison — data this app doesn't actually have. The real cause wasn't a
misunderstood rule; the model was just mirroring the wording of the user's
own question. I fixed it by taking the model out of that decision: the
fundamentals verdict is now rendered directly from code, never composed by
the LLM, and the narrative underneath is built only from this turn's real
numbers — the question text isn't even in its context anymore, so there's
nothing left to mirror. Both fixes are backed by real before-and-after eval
runs, not just a fix that looked plausible on paper."

---

## 6. Wrap-up — what's next (7:30–8:30)

*(Screen: back to the app, or a simple slide.)*

"Last thing — what I'd keep, and what I'd change, if I kept building this
past the certification.

Keep: the four-tool architecture, the deterministic math sitting under the
LLM's narration for anything involving real numbers — I don't want a model
doing arithmetic on someone's actual gain or loss — and the caching work,
which is already in place and cheap.

Change: the biggest gap right now is that there's no guardrail layer yet.
The rule that this app should never phrase a calculation as a recommendation
is currently enforced by the system prompt asking nicely, not by code that
doesn't have to trust the model. That's the first thing I'd build. After
that: there's a hardcoded workaround in the retrieval comparison that only
ranks transcript content over filing content for one known question — that
needs to become a general rule, not a one-off; the transcript-ingestion
pipeline needs to be scripted properly so a new ticker's transcript pulls in
as reliably as its filings already do; and I'd widen eval coverage past the
10 of 12 questions built today — the remaining 2 are blocked on ingesting
more transcript quarters and building a relevance-threshold filter, not open
design questions."

---

## 7. Close (8:30–9:00)

"That's Personal Portfolio Copilot — an agent that grounds portfolio
decisions in real filings and fundamentals instead of headlines and price
swings, with an eval harness that actually found and fixed real bugs, rather
than just producing a passing score. Thanks for watching."

---

## Notes for you before recording

- Swap in real tickers/numbers once you've picked which live answers to show
  — don't read this script's bracketed placeholders on camera.
- If a live tool call is slow (Tavily/Finnhub occasionally take 5–10s), keep
  talking through what you're about to show rather than sitting in silence.
- Section 3–4 (the actual live demo) is the part a grader will weight most —
  if you're tight on time, trim section 2 (architecture) rather than the
  live demo.
- This draft runs ~1,400 words of narration (checked directly, not guessed) —
  at a natural pace that's roughly 9.5 minutes before live tool-call pauses,
  so it still fits the 9–10 minute target but leaves less slack than before.
  If you land over 10 minutes on a real take, trim section 2 first per the
  note above.
