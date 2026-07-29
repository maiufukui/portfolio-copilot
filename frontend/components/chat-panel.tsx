"use client";

// Collapsible "Ask North" panel -- shared by app/page.tsx and
// app/portfolio/page.tsx.
//
// 2026-07-29, Maiu: three rounds of feedback on this so far --
//   1. Collapsed state shows "Ask North" with a conversation-bubble icon,
//      not a chevron/carrot -- a carrot alone wasn't a clear enough
//      affordance that this is chat you can open. Starts collapsed by
//      default on both pages -- dashboard/portfolio is the primary view,
//      chat is opt-in, not something that should eat 28% of the width
//      before the user has asked anything.
//   2. First fix made the collapsed state a full-height vertical strip on
//      the right (mirroring the sidebar's collapsed column) -- called out
//      as confusing.
//   3. Second fix made it a full-width bar at the very top of the page --
//      also wrong; Maiu wants it inline, to the right, sitting right next
//      to Portfolio Value (on the dashboard) / next to Add Holding (on
//      the portfolio page), not spanning the page or living in its own
//      row. So the collapsed state below is now a small inline button,
//      not a full-width bar -- callers place it directly in their own
//      header row next to whatever else lives there (see
//      app/page.tsx and app/portfolio/page.tsx).
//
// Expanded state is unchanged: a right-hand column next to the page's
// main content. Collapsed/expanded state is owned by the parent page
// (not this component) since where each state gets placed in the layout
// differs per page.

import { MessageCircle } from "lucide-react";

import { Chat } from "@/components/chat";

export function ChatPanel({
  ticker,
  threadId,
  collapsed,
  onToggle,
}: {
  ticker: string;
  threadId: string;
  collapsed: boolean;
  onToggle: (collapsed: boolean) => void;
}) {
  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => onToggle(false)}
        aria-label="Expand Ask North"
        className="flex shrink-0 items-center gap-2 rounded-xl border bg-card px-3 py-2 transition-colors hover:bg-accent"
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <MessageCircle className="size-3.5" />
        </span>
        <span className="text-sm font-medium whitespace-nowrap text-foreground">Ask North</span>
      </button>
    );
  }

  return (
    <div className="flex h-[45vh] min-h-[320px] flex-col border-t lg:h-auto lg:w-[28%] lg:min-w-[340px] lg:border-t-0 lg:border-l">
      <div className="flex items-center justify-between gap-2 border-b bg-background px-4 py-3">
        <div>
          <p className="font-heading text-base font-semibold">Ask North</p>
          <p className="text-xs text-muted-foreground">
            Grounded in filings, earnings, news, and market data.
          </p>
        </div>
        <button
          type="button"
          onClick={() => onToggle(true)}
          aria-label="Collapse Ask North"
          className="shrink-0 rounded-lg px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          Collapse
        </button>
      </div>
      {/* key={ticker} remounts Chat on ticker switch -- a clean, fresh
          conversation per holding, matching threadId={ticker} on the
          backend's checkpointer (Session 3: thread-scoped memory). */}
      <div className="min-h-0 flex-1">
        <Chat key={ticker} ticker={ticker} threadId={threadId} />
      </div>
    </div>
  );
}
