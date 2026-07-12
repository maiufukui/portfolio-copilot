import { TrendingDown, TrendingUp } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { HealthPill } from "@/components/health-pill";
import { cn } from "@/lib/utils";
import type { DashboardData } from "@/lib/api";

export function TickerCard({
  data,
  isSelected,
  onSelect,
}: {
  data: DashboardData;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const { ticker, company, health_score, quote, next_earnings_date } = data;
  const changeUp = (quote?.change_pct ?? 0) >= 0;

  return (
    <button type="button" onClick={onSelect} className="text-left">
      <Card
        className={cn(
          "cursor-pointer transition-colors hover:ring-2 hover:ring-ring/40",
          isSelected && "ring-2 ring-primary"
        )}
      >
        <CardContent className="flex flex-col gap-2">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm font-semibold">{ticker}</p>
              <p className="text-xs text-muted-foreground">{company}</p>
            </div>
            <HealthPill status={health_score.overall} />
          </div>

          {quote && (
            <div className="flex items-baseline gap-1.5">
              <span className="text-lg font-medium">${quote.price?.toFixed(2)}</span>
              <span
                className={cn(
                  "flex items-center gap-0.5 text-xs font-medium",
                  changeUp ? "text-emerald-600" : "text-red-600"
                )}
              >
                {changeUp ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}
                {quote.change_pct?.toFixed(2)}%
              </span>
            </div>
          )}

          {next_earnings_date && (
            <p className="text-xs text-muted-foreground">
              Next earnings: {next_earnings_date}
            </p>
          )}
        </CardContent>
      </Card>
    </button>
  );
}
