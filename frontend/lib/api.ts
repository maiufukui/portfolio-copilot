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
  signals: {
    revenue_growth?: HealthSignal & { yoy_growth_by_quarter?: QuarterPoint[] };
    margin?: HealthSignal & { margin_by_quarter?: QuarterPoint[] };
    leadership?: HealthSignal;
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

export async function fetchTickers(): Promise<string[]> {
  const data = await getJson<{ tickers: string[] }>("/tickers");
  return data.tickers;
}

export async function fetchDashboard(ticker: string): Promise<DashboardData> {
  return getJson<DashboardData>(`/dashboard/${ticker}`);
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
