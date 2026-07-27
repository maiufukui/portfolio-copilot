import { defineConfig } from "@playwright/test";

// Smoke test config for the 2 flows scoped 2026-07-27: dashboard render
// and chat (live backend, one demo question). Deliberately does NOT
// manage the FastAPI backend -- uvicorn needs a real .env (OPENAI_API_KEY,
// FINNHUB_API_KEY, FMP_API_KEY, DATABASE_URL, etc.) loaded from the repo
// root, and auto-launching it from here would mean guessing at venv paths
// and env loading that vary by machine. Same convention every other
// live script in this repo already follows: start the backend yourself
// first (`uvicorn server:app --reload`, from the repo root), then run
// these tests. Playwright only starts the Next.js dev server.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000, // per-test default; the chat spec overrides this itself
  // fullyParallel:false alone only serializes tests WITHIN one file -- it
  // does not stop separate spec files from running concurrently (confirmed
  // 2026-07-27: a real run still used 2 workers despite this setting).
  // workers:1 is what actually enforces "one test at a time," which is
  // what we want here -- both specs share one live backend + one real DB,
  // and running them concurrently risks cross-test interference for no
  // speed benefit at this scale (2 tests).
  fullyParallel: false,
  workers: 1,
  retries: 0, // a live-agent test that "passes on retry" is hiding real
  // flakiness, not fixing it -- see this repo's stability-harness
  // philosophy (check_demo_question_stability.py).
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: true, // if you already have `npm run dev` running, use it
    timeout: 60_000,
  },
});
