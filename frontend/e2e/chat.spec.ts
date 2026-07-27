import { test, expect } from "@playwright/test";

// Smoke test: does a real demo question submitted through the actual UI
// reach the live agent and render a real answer -- not a mock. This is
// the flow most likely to get scrutinized (grilled), so it deliberately
// exercises the full stack: React input -> POST /chat -> LangGraph
// create_react_agent tool-calling loop (incl. AnswerGuard corrections,
// app/graph.py) -> rendered response. It does NOT assert on the answer's
// exact wording -- phrasing legitimately varies run to run (confirmed via
// check_demo_question_stability.py), and re-asserting that here would
// just be a second, weaker copy of that harness. What this checks instead
// is the thing the stability harness can't: that the UI wiring itself
// (submit, loading state, render) actually works end to end.
//
// Requires the backend running with a full .env (OPENAI_API_KEY,
// FINNHUB_API_KEY, TAVILY_API_KEY, DATABASE_URL, COHERE_API_KEY) -- this
// test makes a real paid OpenAI call (plus whatever tools the agent
// chooses), same cost/time profile as any other live run in this repo.
test("chat answers a real demo question end to end through the UI", async ({ page }) => {
  // Live agent call with a tool-calling loop, plus a possible AnswerGuard
  // correction (a second full graph.invoke()) -- these demo questions
  // have taken up to ~30-45s in prior live runs this session. 120s gives
  // real headroom without masking an actual hang.
  test.setTimeout(120_000);

  await page.goto("/");
  await expect(page.getByText("Loading dashboard...")).toBeHidden({ timeout: 30_000 });

  // ALAB is the default selected ticker (first in both FALLBACK_TICKERS
  // and the backend's tracked list), so the chat input's placeholder
  // should read "Ask about ALAB...".
  const input = page.getByPlaceholder(/Ask about/);
  await expect(input).toBeVisible();

  // Q2 from the 4 locked live-demo questions (PRD Demo Success Criteria /
  // check_demo_question_stability.py's DEMO_QUESTIONS) -- chosen because
  // it's the one with a confirmed guard (no_majority_customer) behind it,
  // so this test also incidentally exercises the guard-correction path,
  // not just the happy path.
  const question =
    "Does ALAB rely heavily on any single customer for revenue -- is any one customer a majority?";
  await input.fill(question);
  await input.press("Enter");

  // The human bubble should render immediately (no backend round trip
  // needed for this one).
  await expect(page.getByText(question)).toBeVisible();

  // Thinking indicator confirms the request actually went out.
  await expect(page.getByText("Checking filings, news, and market data...")).toBeVisible();

  // Wait for the real response. Two outcomes are both "the UI is broken"
  // failures worth catching here: the request errors out (server.py
  // down, or a genuine backend exception), or the request just hangs
  // past the timeout.
  await expect(
    page.getByText(/Something went wrong -- is the backend running\?/)
  ).toHaveCount(0);
  await expect(page.getByText("Checking filings, news, and market data...")).toBeHidden({
    timeout: 100_000,
  });

  // Two message bubbles now: the human question and the AI answer.
  // whitespace-pre-wrap is the message-bubble class in chat.tsx and,
  // confirmed by grep, isn't reused anywhere else in this frontend.
  const bubbles = page.locator(".whitespace-pre-wrap");
  await expect(bubbles).toHaveCount(2);

  const answerText = (await bubbles.nth(1).textContent())?.trim() ?? "";
  // Not asserting specific wording (see file header) -- just that a real,
  // substantive answer rendered, not an empty or truncated response.
  expect(answerText.length).toBeGreaterThan(40);

  // At least one tool badge should render above the answer (chat.tsx's
  // toolsUsed badges) -- confirms tools_used came back non-empty, i.e.
  // the agent actually did something rather than answering from nothing.
  await expect(page.getByText(/search_filings|Filings|filings/i).first()).toBeVisible();
});
