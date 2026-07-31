// Talks directly to our own FastAPI backend (server.py) via plain fetch --
// deliberately NOT @langchain/react's useStream, which expects a real
// LangGraph Agent Server's threads/runs/assistants protocol. Our backend
// returns one JSON response per request, not a stream, so a plain fetch
// wrapper is all this needs. See server.py's module docstring and the
// PRD's UI infra row for why.

export interface ChatApiResponse {
  answer: string;
  ticker: string;
  thread_id: string;
  tools_used: string[];
}

export interface QuarterPoint {
  period: string;
  yoy_pct?: number;
  qoq_pct?: number;
  margin_pct?: number;
  compared_to?: string;
}

export interface HealthSignal {
  status: "intact" | "monitor" | "at_risk" | "insufficient_data";
  [key: string]: unknown;
}

export interface HealthScore {
  ticker: string;
  overall: HealthSignal["status"];
  // Most recent status from before today, or null on day one before any
  // history has accumulated (app/db.py's health_score_history, wired
  // 2026-07-29 for the portfolio summary feature). Not currently
  // rendered per-signal anywhere in the UI -- the portfolio summary
  // endpoint does its own status-change comparison server-side using
  // this same field.
  overall_yesterday: HealthSignal["status"] | null;
  // ISO timestamp of that snapshot -- may be more than one calendar day
  // old, since the write only happens when the health score is freshly
  // computed (2026-07-29, Maiu caught the "since yesterday" copy was
  // wrong for exactly this reason). Not currently rendered in the UI.
  overall_as_of: string | null;
  signals: {
    revenue_growth?: HealthSignal & {
      yoy_growth_by_quarter?: QuarterPoint[];
      // Separate, wider (~2yr) QoQ series -- independent of whatever
      // window the status calc actually reads (backend, 2026-07-27). No
      // longer read by the chart (see yoy_growth_chart below) but kept on
      // the wire since the status pill itself is still QoQ-streak-based
      // and this remains useful debugging context.
      qoq_growth_chart?: QuarterPoint[];
      // Wider (~2yr) YoY series -- what the Revenue Growth chart actually
      // renders as of 2026-07-28 (Maiu: reverted from QoQ to YoY). Status
      // pill above is UNCHANGED and still QoQ-streak-based -- see
      // fetch_xbrl_financials.py's classify_revenue_trend docstring.
      yoy_growth_chart?: QuarterPoint[];
    };
    margin?: HealthSignal & { margin_by_quarter?: QuarterPoint[] };
    leadership?: HealthSignal & {
      // Only present when NO 8-K Item 5.02 was found at all (app/tools.py:
      // signals["leadership"] = {"status": "intact", "reason": "..."}).
      reason?: string;
      // Present instead of `reason` whenever a real 8-K Item 5.02 WAS
      // found -- one entry per matched filing (app/tools.py:
      // signals["leadership"] = {"status": overall, "departures": results}).
      // A UI that only reads `reason` and falls back to "no data" when
      // it's missing would incorrectly call this "no data" instead of
      // describing the real departure(s) -- found in a 2026-07-27 audit,
      // fixed in dashboard.tsx.
      departures?: {
        status: HealthSignal["status"];
        is_ceo_or_cfo?: boolean;
        successor_named?: boolean;
        filed?: string;
        reason?: string;
      }[];
    };
    insider_activity?: HealthSignal & {
      total_sell_value_30d?: number;
      distinct_sellers_30d?: number;
    };
  };
}

export interface Quote {
  price: number;
  change_pct: number;
  prev_close: number;
  day_low: number;
  day_high: number;
}

export interface NewsItem {
  title: string;
  url: string;
  date: string | null;
  excerpt: string;
}

export interface DashboardData {
  ticker: string;
  company: string;
  health_score: HealthScore;
  quote: Quote | null;
  next_earnings_date: string | null;
  news: NewsItem[];
}

// Real backend contract for what frontend/lib/mock-holdings.ts had been
// faking client-side -- see app/db.py's holdings table (2026-07-27) and
// server.py's /holdings routes. snake_case on the wire (matches the
// FastAPI/Pydantic response), same convention already used above for
// DashboardData/HealthScore rather than translating to camelCase in
// this file.
export interface HoldingRecord {
  ticker: string;
  shares: number;
  cost_basis_avg: number;
  purchase_date: string; // YYYY-MM-DD
}

export class ChatApiError extends Error {}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // response body wasn't JSON -- fall back to the generic message above
    }
    throw new ChatApiError(detail);
  }
  return res.json();
}

// null is a real, expected response (no holdings yet, no history yet on
// day one, or the underlying LLM call failed) -- not an error. Callers
// render their own honest fallback text rather than treating null as a
// fetch failure. See server.py's /portfolio-summary and
// app/tools.py's generate_portfolio_summary docstring.
export async function fetchPortfolioSummary(): Promise<string | null> {
  const data = await getJson<{ summary: string | null }>("/portfolio-summary");
  return data.summary;
}

export async function fetchTickers(): Promise<string[]> {
  const data = await getJson<{ tickers: string[] }>("/tickers");
  return data.tickers;
}

export async function fetchDashboard(ticker: string): Promise<DashboardData> {
  return getJson<DashboardData>(`/dashboard/${ticker}`);
}

// Shared by the three holdings mutations below -- POST/PUT/DELETE all
// need the same error-unwrapping getJson already does for GETs, plus
// handling for DELETE's 204-no-body response. Kept separate from
// getJson/sendChatMessage above rather than refactoring either --
// both already work and aren't part of this change.
async function mutateJson<T>(path: string, method: "POST" | "PUT" | "DELETE", body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const errBody = await res.json();
      if (errBody?.detail) detail = errBody.detail;
    } catch {
      // response body wasn't JSON -- fall back to the generic message above
    }
    throw new ChatApiError(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export async function fetchHoldings(): Promise<HoldingRecord[]> {
  const data = await getJson<{ holdings: HoldingRecord[] }>("/holdings");
  return data.holdings;
}

export async function createHolding(holding: HoldingRecord): Promise<void> {
  await mutateJson<{ status: string; ticker: string }>("/holdings", "POST", holding);
}

// PUT replaces the full row (server.py's update_holding calls the same
// upsert_holding as create) -- pass the complete HoldingRecord, not a
// partial patch.
export async function updateHolding(holding: HoldingRecord): Promise<void> {
  await mutateJson<{ status: string; ticker: string }>(`/holdings/${holding.ticker}`, "PUT", holding);
}

export async function deleteHolding(ticker: string): Promise<void> {
  await mutateJson<void>(`/holdings/${ticker}`, "DELETE");
}

export async function sendChatMessage(params: {
  ticker: string;
  question: string;
  threadId: string;
}): Promise<ChatApiResponse> {
  const res = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ticker: params.ticker,
      question: params.question,
      thread_id: params.threadId,
    }),
  });

  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // response body wasn't JSON -- fall back to the generic message above
    }
    throw new ChatApiError(detail);
  }

  return res.json();
}
