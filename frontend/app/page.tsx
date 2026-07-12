"use client";

import { useEffect, useState } from "react";
import { LineChart } from "lucide-react";

import { Chat } from "@/components/chat";
import { Dashboard } from "@/components/dashboard";
import { fetchTickers } from "@/lib/api";

// Fallback only, used if the /tickers fetch fails (e.g. backend not up
// yet) -- source of truth is the backend's TICKER_TO_COMPANY dict
// (app/tools.py), fetched at runtime so this file doesn't hold its own
// separate copy of that list.
const FALLBACK_TICKERS = ["ALAB", "AAPL", "MRVL", "NBIS"];

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
    <main className="flex h-dvh flex-col">
      {/* No mx-auto/max-w here on purpose -- this needs to left-align with
          the dashboard content below it (Dashboard's own wrapper uses the
          same px-4, no max-w), not center itself independently at a fixed
          width while the cards below span the full column width. */}
      <header className="border-b bg-background">
        <div className="flex w-full items-center gap-3 px-4 py-3">
          <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <LineChart className="size-4" />
          </div>
          <div className="leading-tight">
            <p className="text-sm font-medium">Portfolio Copilot</p>
            <p className="text-xs text-muted-foreground">
              Grounded in filings + fundamentals, not a stored thesis
            </p>
          </div>
        </div>
      </header>

      {/* Combined dashboard + chat, not separate modes (PRD Appendix G) --
          the dashboard is the primary view, chat is docked alongside it
          rather than living on its own page. Row layout (chat as a right
          column) only above the lg breakpoint -- below that it stacks
          (dashboard on top, chat below, bounded height), since a fixed
          1/4-width column would be unusably cramped on a phone, and the
          rubric explicitly requires this to work on one. */}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <Dashboard
            tickers={tickers}
            selectedTicker={selectedTicker}
            onSelectTicker={setSelectedTicker}
          />
        </div>

        <div className="h-[45vh] min-h-[320px] border-t lg:h-auto lg:w-[32%] lg:min-w-[380px] lg:border-t-0 lg:border-l">
          {/* key={selectedTicker} remounts Chat on ticker switch -- a clean,
              fresh conversation per holding, matching threadId={ticker} on
              the backend's checkpointer (Session 3: thread-scoped memory). */}
          <Chat key={selectedTicker} ticker={selectedTicker} threadId={selectedTicker} />
        </div>
      </div>
    </main>
  );
}
