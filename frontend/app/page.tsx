"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Chat } from "@/components/chat";
import { Dashboard } from "@/components/dashboard";
import { fetchTickers } from "@/lib/api";

// Fallback only, used if the /tickers fetch fails (e.g. backend not up
// yet) -- source of truth is the backend's TICKER_TO_COMPANY dict
// (app/tools.py), fetched at runtime so this file doesn't hold its own
// separate copy of that list.
const FALLBACK_TICKERS = ["ALAB", "AAPL", "MRVL", "NBIS", "PANW", "DELL"];

export default function Page() {
  const [tickers, setTickers] = useState<string[]>(FALLBACK_TICKERS);
  const [selectedTicker, setSelectedTicker] = useState(FALLBACK_TICKERS[0]);

  useEffect(() => {
    fetchTickers()
      .then((t) => {
        if (t.length > 0) {
          setTickers(t);
          setSelectedTicker(t[0]);
        }
      })
      .catch(() => {
        // keep the fallback list -- Dashboard/Chat will surface their own
        // "backend not running" errors once they try to actually fetch data
      });
  }, []);

  return (
    <AppShell>
      {/* Combined dashboard + chat, not separate modes (PRD Appendix G) --
          the dashboard is the primary view, chat is docked alongside it
          rather than living on its own page. Row layout (chat as a right
          column) only above the lg breakpoint -- below that it stacks
          (dashboard on top, chat below, bounded height), since a fixed
          1/4-width column would be unusably cramped on a phone, and the
          rubric explicitly requires this to work on one. */}
      <div className="flex h-full min-h-0 flex-col lg:flex-row">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <Dashboard
            tickers={tickers}
            selectedTicker={selectedTicker}
            onSelectTicker={setSelectedTicker}
          />
        </div>

        <div className="flex h-[45vh] min-h-[320px] flex-col border-t lg:h-auto lg:w-[24%] lg:min-w-[300px] lg:border-t-0 lg:border-l">
          <div className="border-b bg-background px-4 py-3">
            <p className="font-heading text-base font-semibold">Ask North</p>
            <p className="text-xs text-muted-foreground">
              Grounded in filings, earnings, news, and market data.
            </p>
          </div>
          {/* key={selectedTicker} remounts Chat on ticker switch -- a clean,
              fresh conversation per holding, matching threadId={ticker} on
              the backend's checkpointer (Session 3: thread-scoped memory). */}
          <div className="min-h-0 flex-1">
            <Chat key={selectedTicker} ticker={selectedTicker} threadId={selectedTicker} />
          </div>
        </div>
      </div>
    </AppShell>
  );
}
