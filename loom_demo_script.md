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

Here's the problem it solves. If you're an everyday investor holding, say,
15 or 20 individual stocks, you probably did real homework before buying each
one — you had a reason. Margin expansion, a new product cycle, whatever it
was. But once you own the stock, staying on top of *whether that reason still
holds* doesn't scale. You don't have time to read a 10-Q or listen to a full
earnings call for every position every quarter. So what actually happens is:
price drops, you get nervous, you skim a headline or two, and you make a
decision based on a vibe instead of the actual fundamentals.

This app is built to close that gap — it grounds every holding in the
company's actual filings and objective fundamentals, and answers questions
against that ground truth instead of letting a price move do the thinking for
you."

---

## 2. Architecture, fast (1:00–2:00)

*(Screen: architecture diagram from the PRD, or just narrate over the app's
landing page.)*

"Quickly on how it's built, then I'll get into the live demo.

It's an agentic RAG app — a LangGraph agent with four tools it can call: a
vector-search tool over each company's SEC filings and earnings transcripts,
a keyword/exact-match tool for questions where missing a disclosure would be
a real failure — not just a nicety, a separate deterministic path — a live
search tool for what's happening right now, and a market-data tool for
insider transactions and analyst consensus.

The reason there are four tools instead of one do-everything retriever is
that these are genuinely different question types. 'What did management say
about margins' is a semantic-similarity question. 'Has this company disclosed
customer concentration risk, ever' is an exhaustive-recall question — vector
search can miss things, so that one gets a keyword path instead. 'What's the
latest news' can't be pre-indexed at all. And insider trading data is just a
structured lookup, not something you'd embed.

It's deployed for real — FastAPI backend, Next.js frontend, both live on
Render, tracking four real tickers: Astera Labs, Apple, Marvell, and Nebius."

---

## 3. Live demo — grounded question (2:00–4:00)

*(Screen: the live app now. Type or paste a Q1-style question.)*

"Let's see it work. I'll ask something that requires it to actually find a
specific number management cited, not just describe margins in general —
something like:

**'What did Astera Labs' management identify as the driver behind this
quarter's gross margin change?'**

*(Send it, let it run.)*

What I want you to notice here: it's not just returning a generic margin
number — it's pulling the exact quote from the earnings call, distinguishing
it from what's in the 10-Q, and citing where it came from. [Read the actual
figure and citation off the response once it loads.] That citation is the
whole point — every claim it makes is supposed to be traceable back to a real
source, not a paraphrase."

*(Optional second question in this block — Q7-style, emotional grounding:)*

"Now let's try the case this app actually exists for — someone panicking
about a price move:

**'[Ticker] just dropped 8% today, I'm nervous — should I sell?'**

*(Send it, let it run.)*

Notice it doesn't just validate the fear or reassure blindly — it actually
checks the drop against the company's real fundamentals signals and news
before saying anything. [Read the response, point out that it separates
'here's what actually changed' from 'here's how you feel.']"

---

## 4. Live demo — completeness / exact-match (4:00–5:30)

*(Screen: still in chat.)*

"One more, because this one's the reason there are two different retrieval
paths in this app, not one. Some questions need *every* mention of something,
not just the most similar-sounding chunk. If I ask:

**'Has [Company] disclosed any customer concentration risk recently?'**

*(Send it, let it run.)*

...a missed disclosure here is a real failure, not a minor gap. So this
routes to a keyword/exact-match search instead of vector similarity — I found
in testing that plain vector search at k=6 actually missed a real match once,
because it scored lower similarity than surrounding boilerplate. This path
guarantees it checks everywhere the term appears, and then synthesizes
whether that's routine boilerplate language or an actual new signal."

---

## 5. Evals and what I learned (5:30–7:30)

*(Screen: can go back to slides/terminal output, or just talk over the app.)*

"Now the evaluation side, briefly, because this is where the real work was.

I built a 12-question locked eval set, hand-curated against the actual
product behavior this app needs — not synthetically generated, because most
of these questions need a live tool call, not just a document corpus, so a
corpus-driven generator couldn't produce them anyway. Scored two ways:
RAGAS's faithfulness/context-recall/factual-correctness triad for the
retrieval-based questions, and tool-call accuracy / goal accuracy for
everything that's tool-calling.

The single biggest finding, across everything I tested: this pipeline's
failure mode is almost entirely about *what gets retrieved*, not about the
model hallucinating. Faithfulness scored close to perfect basically every
time the right context made it into the model's hands. What actually broke
things was retrieval completeness.

Concretely — I built a parent-child retriever as an advanced-retrieval
upgrade: instead of returning an isolated 512-token chunk, it recovers the
full filing section or full speaker turn the match came from. On a head-to-
head comparison, the baseline retriever's recall was actually unstable — it
swung between missing a fact entirely and finding it, on the *same question*,
literally just depending on where a chunk boundary happened to fall. The
parent-child retriever scored a perfect, stable recall across every run,
because it doesn't have that boundary-luck problem.

Separately, I found and fixed a real bug in the synthesis layer — one of the
eval questions was sending up to 87 raw, mostly-duplicate snippets into the
model's context, which was actually causing the evaluation judge itself to
time out. Deduplicating that and giving the model a clear signal about what's
boilerplate versus what's new took faithfulness on that question from a hard
zero to a perfect score, twice in a row."

---

## 6. Wrap-up — what's next (7:30–8:30)

*(Screen: back to the app, or a simple slide.)*

"Last thing — what I'd keep and what I'd change if I kept building this past
the certification.

Keep: the four-tool architecture, the deterministic math under the LLM
narration for anything involving real numbers — I don't want a model doing
arithmetic on someone's actual gain or loss — and the caching work, which is
already in place and cheap.

Change: the biggest gap right now is that there's no guardrail layer yet —
the rule that this app should never phrase a calculation as a recommendation
is currently enforced by the system prompt asking nicely, not by code that
doesn't have to trust the model. That's the first thing I'd build next. After
that, wiring in two more data endpoints to unlock a few more eval questions,
and widening test coverage past the 9 of 12 questions that are fully built today."

---

## 7. Close (8:30–9:00)

"That's Personal Portfolio Copilot — an agent that grounds portfolio
decisions in real filings and fundamentals instead of headlines and price
swings, with an eval harness that actually found and fixed two real bugs
rather than just producing a passing score. Thanks for watching."

---

## Notes for you before recording

- Swap in real tickers/numbers once you've picked which live answers to show
  — don't read this script's bracketed placeholders on camera.
- If a live tool call is slow (Tavily/Finnhub occasionally take 5–10s), keep
  talking through what you're about to show rather than sitting in silence.
- Section 3–4 (the actual live demo) is the part a grader will weight most —
  if you're tight on time, trim section 2 (architecture) rather than the
  live demo.
- Total word count here is ~1,150 words of narration, which runs close to
  9 minutes at a natural pace once you add the live tool-call pauses — leaves
  you a little room under the 10-minute cap.
