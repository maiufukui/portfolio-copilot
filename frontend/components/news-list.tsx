import { Newspaper } from "lucide-react";

import type { NewsItem } from "@/lib/api";

// Styled as the "Supporting Evidence" list (North redesign, 2026-07-26).
// Still real, backend-wired data -- Tavily-sourced per-ticker news, same
// fetchDashboard() call as before. Two honest limits, on purpose, not
// oversights:
//
// 1. The source label is parsed off the existing "<headline> - <Source>"
//    title format already returned by the backend -- it is not a real
//    typed taxonomy. There is no structured "SEC Filing" / "Transcript" /
//    "Analyst Note" classification anywhere in this data; every item uses
//    the same generic icon rather than fabricating type badges that don't
//    exist in the underlying data.
// 2. Relative time ("2 hours ago") is computed from the item's real
//    published date.
function splitTitleAndSource(title: string): { headline: string; source: string | null } {
  const idx = title.lastIndexOf(" - ");
  if (idx === -1) return { headline: title, source: null };
  return { headline: title.slice(0, idx), source: title.slice(idx + 3) };
}

function relativeTime(dateStr: string | null): string | null {
  if (!dateStr) return null;
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return dateStr;

  const diffMs = Date.now() - d.getTime();
  const diffMin = Math.round(diffMs / 60000);
  if (diffMin < 1) return "Just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay === 1) return "Yesterday";
  if (diffDay < 7) return `${diffDay} days ago`;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function NewsList({ news }: { news: NewsItem[] }) {
  if (news.length === 0) {
    // Honest empty state, not a fabricated placeholder -- see the
    // relevance-filtering comment in app/tools.py's get_dashboard_data():
    // an empty list here means nothing found actually mentioned the
    // company, not that the search failed silently.
    return (
      <p className="text-sm text-muted-foreground">
        No significant recent news found for this company.
      </p>
    );
  }

  return (
    <ul className="flex flex-col divide-y divide-border">
      {news.map((item) => {
        const { headline, source } = splitTitleAndSource(item.title);
        const when = relativeTime(item.date);
        return (
          <li key={item.url} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
            <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
              <Newspaper className="size-3.5" />
            </div>
            <div className="min-w-0 flex-1">
              <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm font-medium hover:underline"
              >
                {headline}
              </a>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {source ? `${source}` : "News"}
                {when ? ` · ${when}` : ""}
              </p>
              <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{item.excerpt}</p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
