"use client";

import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { HealthPill } from "@/components/health-pill";
import { MiniLineChart } from "@/components/mini-line-chart";
import { NewsList } from "@/components/news-list";
import { TickerCard } from "@/components/ticker-card";
import { fetchDashboard, type DashboardData } from "@/lib/api";

const SUB_SIGNAL_LABELS: Record<string, string> = {
  revenue_growth: "Revenue growth",
  margin: "Margin",
  leadership: "Leadership",
  insider_activity: "Insider activity",
};

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadAll() {
      setLoading(true);
      setError(null);
      try {
        const results = await Promise.all(tickers.map((t) => fetchDashboard(t)));
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

  return (
    <div className="flex w-full flex-col gap-4 px-4 py-4">
      <h1 className="text-xl font-medium">Welcome back, Maiu</h1>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {tickers.map((t) =>
          data[t] ? (
            <TickerCard
              key={t}
              data={data[t]}
              isSelected={t === selectedTicker}
              onSelect={() => onSelectTicker(t)}
            />
          ) : null
        )}
      </div>

      {selected && (
        <>
          <Card className="w-full">
            <CardHeader>
              <CardTitle>Fundamentals Health Score -- {selected.ticker}</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_2fr]">
              <div className="flex flex-col gap-3">
                {Object.entries(SUB_SIGNAL_LABELS).map(([key, label]) => {
                  const signal = selected.health_score.signals[
                    key as keyof typeof selected.health_score.signals
                  ];
                  if (!signal) return null;
                  return (
                    <div key={key} className="flex items-center justify-between text-sm">
                      <span>{label}</span>
                      <HealthPill status={signal.status} size="sm" />
                    </div>
                  );
                })}
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <p className="mb-1 text-xs text-muted-foreground">Revenue YoY growth</p>
                  <MiniLineChart
                    points={
                      selected.health_score.signals.revenue_growth?.yoy_growth_by_quarter?.map((q) => ({
                        label: q.period.slice(2, 7),
                        value: q.yoy_pct ?? 0,
                      })) ?? []
                    }
                  />
                </div>
                <div>
                  <p className="mb-1 text-xs text-muted-foreground">Gross margin</p>
                  <MiniLineChart
                    points={
                      selected.health_score.signals.margin?.margin_by_quarter?.map((q) => ({
                        label: q.period.slice(2, 7),
                        value: q.margin_pct ?? 0,
                      })) ?? []
                    }
                    color="var(--color-chart-2)"
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="w-full">
            <CardHeader>
              <CardTitle>Recent news -- {selected.company}</CardTitle>
            </CardHeader>
            <CardContent>
              <NewsList news={selected.news} />
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
