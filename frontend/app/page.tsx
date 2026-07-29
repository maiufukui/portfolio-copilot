"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { ChatPanel } from "@/components/chat-panel";
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
  const [chatCollapsed, setChatCollapsed] = useState(true);

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
          rather than living on its own page. Collapsed "Ask North" is an
          inline button next to Portfolio Value, passed into Dashboard via
          chatToggle (2026-07-29, Maiu: "keep it to the right, right next
          to the portfolio value"). Expanded, it becomes a right-hand
          column next to the dashboard (row layout above the lg
          breakpoint; below that it stacks, dashboard on top, chat below,
          bounded height, since a fixed 1/4-width column would be unusably
          cramped on a phone, and the rubric explicitly requires this to
          work on one). */}
      <div className="flex h-full min-h-0 flex-col lg:flex-row">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <Dashboard
            tickers={tickers}
            selectedTicker={selectedTicker}
            onSelectTicker={setSelectedTicker}
            chatToggle={
              chatCollapsed && (
                <ChatPanel
                  ticker={selectedTicker}
                  threadId={selectedTicker}
                  collapsed
                  onToggle={setChatCollapsed}
                />
              )
            }
          />
        </div>

        {!chatCollapsed && (
          <ChatPanel
            ticker={selectedTicker}
            threadId={selectedTicker}
            collapsed={false}
            onToggle={setChatCollapsed}
          />
        )}
      </div>
    </AppShell>
  );
}
