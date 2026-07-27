import { test, expect } from "@playwright/test";

// Smoke test: does the dashboard actually load real data from the live
// backend and render it, not just an empty shell. Requires the backend
// running (`uvicorn server:app --reload`, port 8000) with a working
// .env -- /dashboard/{ticker} pulls quotes (FINNHUB_API_KEY/FMP_API_KEY),
// health score (DATABASE_URL + parsed filings), and holdings.
test("dashboard loads and renders tracked tickers with real data", async ({ page }) => {
  await page.goto("/");

  // dashboard.tsx's loading state clears once every fetchDashboard(ticker)
  // call resolves.
  await expect(page.getByText("Loading dashboard...")).toBeHidden({ timeout: 30_000 });

  // If the backend isn't reachable, dashboard.tsx renders this exact
  // error text instead of the dashboard -- fail loudly here rather than
  // letting the assertions below fail confusingly one by one.
  await expect(
    page.getByText("Couldn't load dashboard data -- is the backend running?")
  ).toHaveCount(0);

  // Real h1 greeting from dashboard.tsx (not a placeholder/skeleton).
  await expect(page.getByRole("heading", { name: "Good afternoon, Maiu" })).toBeVisible();

  // At least one tracked ticker card rendered -- ticker-card.tsx renders
  // the symbol as plain text. ALAB is always first in both the backend's
  // TICKER_TO_COMPANY order and the frontend's FALLBACK_TICKERS, so this
  // isn't a guess about which ticker happens to be first.
  await expect(page.getByText("ALAB", { exact: true }).first()).toBeVisible();

  // Fundamentals Health Score card for the selected ticker -- CardTitle
  // renders as a plain div (components/ui/card.tsx), not a heading role,
  // so this matches by text rather than getByRole.
  await expect(page.getByText(/Fundamentals Health Score/)).toBeVisible();

  // Supporting Evidence card confirms the news/RAG side of the dashboard
  // also came back, not just the numeric health-score signals.
  await expect(page.getByText(/Supporting Evidence/)).toBeVisible();
});
