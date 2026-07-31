"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, Sparkles } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HealthPill } from "@/components/health-pill";
import { MiniLineChart } from "@/components/mini-line-chart";
import { NewsList } from "@/components/news-list";
import { TickerCard } from "@/components/ticker-card";
import {
  fetchDashboard,
  fetchHoldings,
  fetchPortfolioSummary,
  type DashboardData,
  type HealthSignal,
  type HoldingRecord,
} from "@/lib/api";

// Real local time, not a hard-coded "Good afternoon" (2026-07-29, Maiu
// caught this -- the greeting never actually changed regardless of when
// the page was opened). Computed directly in the render body (see the
// <h1> below) rather than via a useEffect + setState -- that pattern
// trips eslint's react-hooks/set-state-in-effect rule (setState with no
// actual subscription/sync work happening is a real anti-pattern, not a
// false positive here), and it isn't needed anyway: the <h1> below
// carries suppressHydrationWarning, which is the standard React/Next.js
// way to say "this text legitimately differs between the server's
// render and the browser's local clock, that's expected, don't warn."
function greetingForHour(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

function formatMoney(n: number): string {
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

function capitalize(s: string): string {
  return s.length === 0 ? s : s.charAt(0).toUpperCase() + s.slice(1);
}

// Shared by PortfolioValue and the "Your Holdings" header (2026-07-29) --
// one formatting rule instead of two copies that could drift apart.
function formatEtTime(d: Date): string {
  return d.toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "2-digit",
  });
}

// "Mar '25" -- plain calendar month + year of the period's end date
// (2026-07-27, Maiu: switched from a "Q#'YY" quarter label, which is
// still a real value judgment worth naming -- this is the calendar month
// the quarter ENDED in, not an attempt to reconstruct each company's own
// fiscal-quarter numbering, which would need each ticker's fiscal-year-
// start date (not available here) to label correctly.
function formatMonthYearLabel(period: string): string {
  const d = new Date(`${period}T00:00:00`);
  if (Number.isNaN(d.getTime())) return period;
  return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" }).replace(" ", " '");
}

// Fixed 2026-07-27 (UI audit): the old version only ever read `reason`,
// which app/tools.py only sets when NO 8-K Item 5.02 was found at all --
// the moment a real departure filing exists, the backend sends
// `departures` instead, and the old code silently fell through to "No
// data available," misrepresenting a ticker that actually has real
// departure data as having none.
function describeLeadership(
  signal:
    | {
        reason?: string;
        departures?: {
          status: string;
          is_ceo_or_cfo?: boolean;
          successor_named?: boolean;
          filed?: string;
          reason?: string;
        }[];
      }
    | undefined
): string {
  if (typeof signal?.reason === "string") return capitalize(signal.reason);

  const departures = signal?.departures;
  if (!departures || departures.length === 0) return "No data available.";

  const real = departures.filter((d) => d.status !== "intact");
  if (real.length === 0) {
    return "8-K Item 5.02 filing(s) found in last 90 days; none indicated an actual departure.";
  }

  const ceoOrCfo = real.some((d) => d.is_ceo_or_cfo);
  const successorNamed = real.some((d) => d.successor_named);
  const latestFiled = real
    .map((d) => d.filed)
    .filter((f): f is string => Boolean(f))
    .sort()
    .at(-1);

  return (
    `${real.length} departure${real.length === 1 ? "" : "s"} reported in last 90 days` +
    (ceoOrCfo ? " (CEO/CFO level)" : "") +
    (successorNamed ? ", successor named" : "") +
    (latestFiled ? ` -- filed ${latestFiled}.` : ".")
  );
}

// Real prices (from the same quote data already fetched for the ticker
// cards) times real, persisted share counts (GET /holdings, app/db.py's
// holdings table -- 2026-07-27, replacing the old lib/mock-holdings.ts).
// Still not a real portfolio-value time series (that's a separate,
// larger item), but the inputs are both real now. No card border on
// purpose (Maiu, 2026-07-27) -- flush text block, not a bordered panel,
// per the reference design.
function PortfolioValue({
  data,
  holdings,
  lastFetchedAt,
}: {
  data: Record<string, DashboardData>;
  holdings: HoldingRecord[];
  // Real fetch time, not render time (2026-07-29, Maiu: fixed a bug found
  // while diagnosing the MRVL/ALAB "wrong %" report -- this label existed
  // before but was computed with `new Date()` at render time, so it
  // silently showed "now" on every re-render regardless of how old the
  // underlying data actually was. A tab left open for hours with a dead
  // backend would show a perfectly current-looking "As of" time right
  // next to hours-stale prices -- exactly the kind of silent staleness
  // that caused the original confusion.
  lastFetchedAt: Date | null;
}) {
  let value = 0;
  let prevValue = 0;
  let hasAnyQuote = false;

  for (const holding of holdings) {
    const quote = data[holding.ticker]?.quote;
    if (!quote) continue;
    hasAnyQuote = true;
    value += holding.shares * quote.price;
    prevValue += holding.shares * quote.prev_close;
  }

  const dollarChange = value - prevValue;
  const pctChange = prevValue > 0 ? (dollarChange / prevValue) * 100 : 0;
  const changeUp = dollarChange >= 0;

  const asOf = lastFetchedAt ? formatEtTime(lastFetchedAt) : null;

  return (
    <div className="w-full sm:w-72">
      <div className="mb-1">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Portfolio Value
        </p>
      </div>
      {hasAnyQuote ? (
        <>
          <p className="font-heading text-3xl font-semibold">{formatMoney(value)}</p>
          <p
            className={
              changeUp
                ? "text-sm font-medium text-[var(--status-intact-fg)]"
                : "text-sm font-medium text-[var(--status-at-risk-fg)]"
            }
          >
            {changeUp ? "+" : "-"}
            {formatMoney(Math.abs(dollarChange))} ({changeUp ? "+" : "-"}
            {Math.abs(pctChange).toFixed(2)}%) vs. previous close
          </p>
          {asOf && <p className="text-xs text-muted-foreground">Prices as of {asOf} ET</p>}
        </>
      ) : (
        <p className="text-sm text-muted-foreground">Loading...</p>
      )}
    </div>
  );
}

// Three explicit grid columns (dimension / status / trend-detail), not a
// flex block sharing one narrow width between label and pill -- widened
// 2026-07-27 (Maiu: "looks squished," fair -- label+pill were previously
// crammed into a single 160px flex block together, and the 3rd column had
// far more room than it needed while the first two were tight). Fixed
// pixel widths for columns 1/2 keep every row's label and pill aligned
// vertically down the list; column 3 still gets the large majority of the
// row via 1fr.
function SignalRow({
  label,
  status,
  right,
}: {
  label: string;
  status: HealthSignal["status"];
  right: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[180px_112px_1fr] items-center gap-6 border-b border-border py-3 first:pt-0 last:border-b-0 last:pb-0">
      <span className="text-sm font-medium">{label}</span>
      <HealthPill status={status} size="sm" />
      <div className="min-w-0">{right}</div>
    </div>
  );
}

// Bold current-quarter figure + compact sparkline, the standard
// current-value-plus-trend pairing (Apple Stocks, Robinhood, etc.) --
// replaces the old approach of floating a value label above every point,
// which got noisy/oversized once the chart's own bug (see
// mini-line-chart.tsx's header comment) was fixed and the chart shrank
// back down to its intended size.
function TrendCell({
  points,
  color,
}: {
  points: { label: string; value: number }[];
  color?: string;
}) {
  const latest = points.at(-1);
  return (
    <div className="flex items-center gap-3">
      <span className="w-14 shrink-0 font-heading text-base font-semibold">
        {latest ? `${latest.value.toFixed(1)}%` : "—"}
      </span>
      <div className="min-w-0 flex-1">
        <MiniLineChart points={points} color={color} />
      </div>
    </div>
  );
}

export function Dashboard({
  tickers,
  selectedTicker,
  onSelectTicker,
  chatToggle,
}: {
  tickers: string[];
  selectedTicker: string;
  onSelectTicker: (ticker: string) => void;
  // Collapsed "Ask North" button, rendered inline next to Portfolio Value
  // (2026-07-29, Maiu: "keep it to the right, right next to the portfolio
  // value" -- not a full-width bar, not a side column, an inline button
  // in this specific header row). Optional/null when chat is expanded --
  // the page itself renders the expanded panel as a separate side column.
  chatToggle?: React.ReactNode;
}) {
  const [data, setData] = useState<Record<string, DashboardData>>({});
  const [holdings, setHoldings] = useState<HoldingRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Real fetch time, set only when at least one ticker actually loaded --
  // see PortfolioValue's comment on why this replaced a render-time clock.
  const [lastFetchedAt, setLastFetchedAt] = useState<Date | null>(null);
  // AI-generated "vs. yesterday" summary (2026-07-29) -- null is a real,
  // expected state (no holdings, no history yet, or the LLM call
  // failed), not a loading placeholder. The header renders its own
  // honest fallback text when this is null; see fetchPortfolioSummary's
  // comment in lib/api.ts.
  const [summary, setSummary] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadAll() {
      setLoading(true);
      setError(null);
      try {
        const [settledResults] = await Promise.all([
          // allSettled, not all (fixed 2026-07-28, UI-audit bug-list item
          // "Dashboard Promise.all no per-ticker catch"): with Promise.all,
          // ONE ticker's fetchDashboard rejecting (a transient backend
          // hiccup, one Finnhub call timing out, etc.) rejected the whole
          // array, which threw into the catch block below and put the
          // ENTIRE dashboard into the generic error state -- hiding the
          // other tickers that fetched successfully. allSettled lets each
          // ticker fail independently; only the tickers that resolve get
          // rendered (the `if (!data[t]) return null` guard in the grid
          // below, and the `selected &&` guard on the detail panel, both
          // already handled a ticker being absent from `data` gracefully
          // -- this fix only had to change how `data` gets populated, not
          // how it's rendered).
          Promise.allSettled(tickers.map((t) => fetchDashboard(t))),
          // Real holdings, same call the Portfolio page makes -- both
          // pages now share one backend source of truth instead of each
          // holding its own local copy of mock-holdings.ts. Fetched
          // separately (not blocking on tickers/quotes) so one endpoint
          // being briefly unavailable doesn't take down the other.
          fetchHoldings()
            .then((h) => {
              if (!cancelled) setHoldings(h);
            })
            .catch(() => {
              // Non-fatal: dashboard still renders health scores/quotes
              // without a Portfolio Value figure if /holdings is down.
              if (!cancelled) setHoldings([]);
            }),
          // Non-fatal, same reasoning as fetchHoldings above -- a slow or
          // failed summary must never block or break the rest of the
          // dashboard. fetchPortfolioSummary itself already resolves to
          // null (not a rejection) for the expected "not enough data yet"
          // cases; this .catch only covers a genuine network/HTTP failure.
          fetchPortfolioSummary()
            .then((s) => {
              if (!cancelled) setSummary(s);
            })
            .catch(() => {
              if (!cancelled) setSummary(null);
            }),
        ]);
        if (cancelled) return;
        const byTicker: Record<string, DashboardData> = {};
        const failedTickers: string[] = [];
        settledResults.forEach((result, i) => {
          if (result.status === "fulfilled") {
            byTicker[result.value.ticker] = result.value;
          } else {
            failedTickers.push(tickers[i]);
          }
        });
        if (failedTickers.length > 0) {
          // eslint-disable-next-line no-console -- deliberate: surfaces a
          // per-ticker failure in the browser console without blocking the
          // tickers that did load, same "degrade gracefully, don't hide
          // the failure" reasoning as the rest of this fix.
          console.error(`Dashboard: failed to load data for ${failedTickers.join(", ")}`);
        }
        setData(byTicker);
        if (Object.keys(byTicker).length > 0) setLastFetchedAt(new Date());
        // Only fall back to the full-page error when EVERY ticker failed
        // (a real "backend is down" case) -- not when some tickers loaded
        // fine and one didn't, which is now a partial-data case handled by
        // simply omitting that ticker's card, not by hiding everything
        // that DID load.
        if (tickers.length > 0 && Object.keys(byTicker).length === 0) {
          setError("Couldn't load dashboard data -- is the backend running?");
        }
      } catch {
        if (!cancelled) setError("Couldn't load dashboard data -- is the backend running?");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadAll();
    return () => {
      cancelled = true;
    };
  }, [tickers]);

  const holdingByTicker = Object.fromEntries(holdings.map((h) => [h.ticker, h]));

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading dashboard...
      </div>
    );
  }

  if (error) {
    return <p className="px-4 py-6 text-sm text-destructive">{error}</p>;
  }

  const selected = data[selectedTicker];

  // Real rollup, not a fabricated count -- how many tracked tickers are
  // currently monitor/at_risk on their overall health score.
  const flaggedCount = tickers.filter(
    (t) => data[t] && data[t].health_score.overall !== "intact"
  ).length;

  const revenueSignal = selected?.health_score.signals.revenue_growth;
  const marginSignal = selected?.health_score.signals.margin;
  const leadershipSignal = selected?.health_score.signals.leadership;
  const insiderSignal = selected?.health_score.signals.insider_activity;

  return (
    <div className="flex w-full flex-col gap-6 px-6 py-6">
      {/* Grid header, banner nested INSIDE column 1 with the greeting
          (2026-07-29, Maiu, corrected after 3 wrong attempts at this as
          a separate full-width row below everything): same row as
          Portfolio Value, not below the whole header -- confined to the
          greeting's own column, so it structurally cannot reach into
          Portfolio Value's column no matter what. Stacked tight under
          the h1 (gap-2) so it reads as one block with the greeting,
          which is what makes the two columns end up visually balanced
          in height instead of the banner floating in dead space below a
          much taller Portfolio Value block. */}
      <div className="grid grid-cols-[1fr_auto_auto] items-start gap-x-4">
        <div className="flex flex-col gap-2">
          <h1 className="font-heading text-2xl font-semibold" suppressHydrationWarning>
            {greetingForHour(new Date().getHours())}, Maiu
          </h1>
          {/* Colors are hand-tuned --insight-bg/--insight-border
              (globals.css), not a Tailwind opacity modifier on --primary
              -- that read as flat gray, since North Navy is too dark for
              a low-opacity blend to keep any visible blue in it. Sparkle
              icon only appears for a real AI-generated summary -- the
              fallback sentence below is honest, deterministic copy, not
              AI output, so it doesn't get the same "AI insight" visual
              claim. */}
          <div className="flex w-[92%] items-start gap-2.5 rounded-xl border border-[var(--insight-border)] bg-[var(--insight-bg)] px-4 py-3">
            {summary && <Sparkles className="mt-0.5 size-4 shrink-0 text-primary" />}
            <p className="text-sm text-foreground/90">
              {summary ??
                // Honest fallback, not the old "North analyzed... and
                // found N changes" copy (2026-07-29, Maiu: that line
                // implied an AI-generated comparison to yesterday that
                // didn't actually exist -- see chat history). This is a
                // plain, real, present-tense count, shown when there
                // isn't yet enough data for the real summary (no
                // holdings, first day with no health-score history, or
                // the LLM call failed).
                `${flaggedCount} of ${tickers.length} tracked tickers ${
                  flaggedCount === 1 ? "is" : "are"
                } currently flagged for review.`}
            </p>
          </div>
        </div>
        <PortfolioValue data={data} holdings={holdings} lastFetchedAt={lastFetchedAt} />
        {chatToggle}
      </div>

      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-muted-foreground">
            Your Holdings
            {lastFetchedAt && (
              <span className="ml-2 font-normal text-muted-foreground/70">
                · prices as of {formatEtTime(lastFetchedAt)} ET
              </span>
            )}
          </h2>
          <Link href="/portfolio" className="text-sm text-primary hover:underline">
            View all holdings →
          </Link>
        </div>
        {/* 3 per row (2 rows for 6 tickers) rather than a single cramped
            row of 6, per Maiu (2026-07-27). */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {tickers.map((t) => {
            if (!data[t]) return null;
            const holding = holdingByTicker[t];
            const value =
              holding && data[t].quote ? holding.shares * data[t].quote!.price : undefined;
            return (
              <TickerCard
                key={t}
                data={data[t]}
                isSelected={t === selectedTicker}
                onSelect={() => onSelectTicker(t)}
                value={value}
              />
            );
          })}
        </div>
      </div>

      {selected && (
        // Stacked, not side-by-side (Maiu, 2026-07-27) -- Supporting
        // Evidence sits below Fundamentals Health Score, both full-width
        // so they're equal width rather than one card being wider than
        // the other.
        <div className="flex flex-col gap-4">
          <Card className="w-full">
            <CardHeader>
              <CardTitle>Fundamentals Health Score -- {selected.ticker}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col">
              <SignalRow
                label="Revenue Growth (YoY)"
                status={revenueSignal?.status ?? "insufficient_data"}
                right={
                  <TrendCell
                    points={
                      // yoy_growth_chart, not qoq_growth_chart (reverted
                      // 2026-07-28, Maiu) -- chart now shows YoY growth for
                      // each quarter. Note this is now a DELIBERATE mismatch
                      // with the status pill to its left, which is still
                      // QoQ-streak-based (classify_revenue_trend) -- flagged
                      // explicitly rather than silently changed, since this
                      // is the same kind of chart/status split that was a
                      // real bug the last time (2026-07-27), just the
                      // opposite direction and intentional this time. ~2
                      // years of quarters, independent of the status calc's
                      // own window -- see fetch_xbrl_financials.py.
                      revenueSignal?.yoy_growth_chart?.map((q) => ({
                        label: formatMonthYearLabel(q.period),
                        value: q.yoy_pct ?? 0,
                      })) ?? []
                    }
                  />
                }
              />
              <SignalRow
                label="Margin"
                status={marginSignal?.status ?? "insufficient_data"}
                right={
                  <TrendCell
                    points={
                      marginSignal?.margin_by_quarter?.map((q) => ({
                        label: formatMonthYearLabel(q.period),
                        value: q.margin_pct ?? 0,
                      })) ?? []
                    }
                    color="var(--color-chart-2)"
                  />
                }
              />
              <SignalRow
                label="Leadership Change"
                status={leadershipSignal?.status ?? "insufficient_data"}
                right={
                  <p className="text-xs text-muted-foreground">{describeLeadership(leadershipSignal)}</p>
                }
              />
              <SignalRow
                label="Insider Activity"
                status={insiderSignal?.status ?? "insufficient_data"}
                right={
                  <p className="text-xs text-muted-foreground">
                    {insiderSignal?.distinct_sellers_30d != null &&
                    insiderSignal?.total_sell_value_30d != null
                      ? `${insiderSignal.distinct_sellers_30d} insider${
                          insiderSignal.distinct_sellers_30d === 1 ? "" : "s"
                        } sold; total value ${formatMoney(
                          insiderSignal.total_sell_value_30d
                        )} in last 30 days.`
                      : "No data available."}
                  </p>
                }
              />
            </CardContent>
          </Card>

          <Card className="w-full">
            <CardHeader>
              <CardTitle>Supporting Evidence -- {selected.company}</CardTitle>
            </CardHeader>
            <CardContent>
              <NewsList news={selected.news} />
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
