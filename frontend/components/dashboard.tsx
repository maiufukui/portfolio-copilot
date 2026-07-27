"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, MoreHorizontal } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HealthPill } from "@/components/health-pill";
import { MiniLineChart } from "@/components/mini-line-chart";
import { NewsList } from "@/components/news-list";
import { TickerCard } from "@/components/ticker-card";
import { fetchDashboard, fetchHoldings, type DashboardData, type HealthSignal, type HoldingRecord } from "@/lib/api";

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
}: {
  data: Record<string, DashboardData>;
  holdings: HoldingRecord[];
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

  const asOf = new Date().toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "numeric",
    minute: "2-digit",
  });

  return (
    <div className="w-full sm:w-72">
      <div className="mb-1 flex items-center justify-between gap-2">
        <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Portfolio Value
        </p>
        <button
          type="button"
          aria-label="Portfolio value options"
          className="text-muted-foreground hover:text-foreground"
        >
          <MoreHorizontal className="size-4" />
        </button>
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
            {Math.abs(pctChange).toFixed(2)}%) today
          </p>
          <p className="text-xs text-muted-foreground">As of {asOf} ET</p>
        </>
      ) : (
        <p className="text-sm text-muted-foreground">Loading...</p>
      )}
    </div>
  );
}

// One row per signal: label + status chip fixed on the left, trend chart
// or qualitative text filling the right side of the same row -- vertical
// stack rather than a 4-column grid, per Maiu (2026-07-27).
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
    <div className="flex items-center gap-4 border-b border-border py-3 first:pt-0 last:border-b-0 last:pb-0">
      <div className="flex w-40 shrink-0 items-center justify-between gap-2">
        <span className="text-sm font-medium">{label}</span>
        <HealthPill status={status} size="sm" />
      </div>
      <div className="min-w-0 flex-1">{right}</div>
    </div>
  );
}

export function Dashboard({
  tickers,
  selectedTicker,
  onSelectTicker,
}: {
  tickers: string[];
  selectedTicker: string;
  onSelectTicker: (ticker: string) => void;
}) {
  const [data, setData] = useState<Record<string, DashboardData>>({});
  const [holdings, setHoldings] = useState<HoldingRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadAll() {
      setLoading(true);
      setError(null);
      try {
        const [results] = await Promise.all([
          Promise.all(tickers.map((t) => fetchDashboard(t))),
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
        ]);
        if (cancelled) return;
        const byTicker: Record<string, DashboardData> = {};
        results.forEach((d) => {
          byTicker[d.ticker] = d;
        });
        setData(byTicker);
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
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold">Good afternoon, Maiu</h1>
          <p className="mt-1 max-w-md text-sm text-muted-foreground">
            North analyzed your portfolio and found {flaggedCount}{" "}
            {flaggedCount === 1 ? "change" : "changes"} that may need your attention.
          </p>
        </div>
        <PortfolioValue data={data} holdings={holdings} />
      </div>

      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium text-muted-foreground">Your Holdings</h2>
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
                label="Revenue (YoY)"
                status={revenueSignal?.status ?? "insufficient_data"}
                right={
                  <MiniLineChart
                    points={
                      revenueSignal?.yoy_growth_by_quarter?.map((q) => ({
                        label: q.period.slice(2, 7),
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
                  <MiniLineChart
                    points={
                      marginSignal?.margin_by_quarter?.map((q) => ({
                        label: q.period.slice(2, 7),
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
                  <p className="text-xs text-muted-foreground">
                    {typeof leadershipSignal?.reason === "string"
                      ? capitalize(leadershipSignal.reason)
                      : "No data available."}
                  </p>
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
