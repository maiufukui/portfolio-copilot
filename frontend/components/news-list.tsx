import { ExternalLink } from "lucide-react";

import type { NewsItem } from "@/lib/api";

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
    <ul className="flex flex-col gap-3">
      {news.map((item) => (
        <li key={item.url} className="text-sm">
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-start gap-1 font-medium hover:underline"
          >
            {item.title}
            <ExternalLink className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
          </a>
          {item.date && (
            <p className="text-xs text-muted-foreground">{item.date}</p>
          )}
          <p className="mt-1 text-xs text-muted-foreground">{item.excerpt}</p>
        </li>
      ))}
    </ul>
  );
}
