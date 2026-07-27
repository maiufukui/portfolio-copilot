"use client";

// Portfolio holdings page -- North redesign (2026-07-26 demo hardening
// plan, item 5). Wired to the real /holdings backend (app/db.py's
// holdings table + server.py's routes, 2026-07-27) -- this page used to
// be front-end-only (edits/deletes only touched local React state,
// seeded from lib/mock-holdings.ts, nothing survived a reload). That was
// a disclosed, scoped shortcut at the time; this pass replaces it with
// the real thing per Maiu's explicit go-ahead, not a silent stand-in.
//
// Only Ticker / Shares / Cost Basis / Purchase Date are real user
// inputs (add/edit/delete, all persisted). Current Price is live (same
// fetchDashboard() quote call the Dashboard uses). Market Value and
// Gain/Loss are computed from those two, not stored -- same
// "deterministic math, not persisted" pattern the PRD's Key Technical
// Next Steps section names for these fields.

import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";

import { AppShell } from "@/components/app-shell";
import { Chat } from "@/components/chat";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  createHolding,
  deleteHolding as apiDeleteHolding,
  fetchDashboard,
  fetchHoldings,
  fetchTickers,
  updateHolding,
  type DashboardData,
  type HoldingRecord,
} from "@/lib/api";

function formatMoney(n: number): string {
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

function formatDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

interface Draft {
  shares: string;
  costBasis: string;
  purchaseDate: string;
}

interface AddDraft extends Draft {
  ticker: string;
}

function emptyAddDraft(defaultTicker: string): AddDraft {
  return { ticker: defaultTicker, shares: "", costBasis: "", purchaseDate: "" };
}

export default function PortfolioPage() {
  const [holdings, setHoldings] = useState<HoldingRecord[]>([]);
  const [availableTickers, setAvailableTickers] = useState<string[]>([]);
  const [quotes, setQuotes] = useState<Record<string, DashboardData>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editingTicker, setEditingTicker] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);

  const [adding, setAdding] = useState(false);
  const [addDraft, setAddDraft] = useState<AddDraft | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadAll() {
      setLoading(true);
      setError(null);
      try {
        const [holdingRows, tickerList] = await Promise.all([fetchHoldings(), fetchTickers()]);
        if (cancelled) return;
        setHoldings(holdingRows);
        setAvailableTickers(tickerList);

        const uniqueTickers = Array.from(new Set(holdingRows.map((h) => h.ticker)));
        const results = await Promise.all(uniqueTickers.map((t) => fetchDashboard(t).catch(() => null)));
        if (cancelled) return;
        const byTicker: Record<string, DashboardData> = {};
        results.forEach((d) => {
          if (d) byTicker[d.ticker] = d;
        });
        setQuotes(byTicker);
      } catch {
        if (!cancelled) setError("Couldn't load holdings -- is the backend running?");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadAll();
    return () => {
      cancelled = true;
    };
  }, []);

  const heldTickers = new Set(holdings.map((h) => h.ticker));
  const tickersAvailableToAdd = availableTickers.filter((t) => !heldTickers.has(t));

  function startEdit(h: HoldingRecord) {
    setEditingTicker(h.ticker);
    setDraft({
      shares: String(h.shares),
      costBasis: String(h.cost_basis_avg),
      purchaseDate: h.purchase_date,
    });
  }

  function cancelEdit() {
    setEditingTicker(null);
    setDraft(null);
  }

  async function saveEdit(ticker: string) {
    if (!draft) return;
    const shares = Number(draft.shares);
    const costBasis = Number(draft.costBasis);
    if (!Number.isFinite(shares) || shares <= 0) return;
    if (!Number.isFinite(costBasis) || costBasis <= 0) return;
    if (!draft.purchaseDate) return;

    const updated: HoldingRecord = {
      ticker,
      shares,
      cost_basis_avg: costBasis,
      purchase_date: draft.purchaseDate,
    };

    setBusy(true);
    try {
      await updateHolding(updated);
      setHoldings((prev) => prev.map((h) => (h.ticker === ticker ? updated : h)));
      setEditingTicker(null);
      setDraft(null);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Couldn't save changes.");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(h: HoldingRecord) {
    if (typeof window !== "undefined" && !window.confirm(`Remove ${h.ticker} from your holdings?`)) {
      return;
    }
    setBusy(true);
    try {
      await apiDeleteHolding(h.ticker);
      setHoldings((prev) => prev.filter((x) => x.ticker !== h.ticker));
      if (editingTicker === h.ticker) cancelEdit();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Couldn't delete this holding.");
    } finally {
      setBusy(false);
    }
  }

  function startAdd() {
    if (tickersAvailableToAdd.length === 0) return;
    setAdding(true);
    setAddDraft(emptyAddDraft(tickersAvailableToAdd[0]));
  }

  function cancelAdd() {
    setAdding(false);
    setAddDraft(null);
  }

  async function saveAdd() {
    if (!addDraft) return;
    const shares = Number(addDraft.shares);
    const costBasis = Number(addDraft.costBasis);
    if (!addDraft.ticker) return;
    if (!Number.isFinite(shares) || shares <= 0) return;
    if (!Number.isFinite(costBasis) || costBasis <= 0) return;
    if (!addDraft.purchaseDate) return;

    const created: HoldingRecord = {
      ticker: addDraft.ticker,
      shares,
      cost_basis_avg: costBasis,
      purchase_date: addDraft.purchaseDate,
    };

    setBusy(true);
    try {
      await createHolding(created);
      setHoldings((prev) => [...prev, created].sort((a, b) => a.ticker.localeCompare(b.ticker)));
      setAdding(false);
      setAddDraft(null);
      fetchDashboard(created.ticker)
        .then((d) => setQuotes((prev) => ({ ...prev, [d.ticker]: d })))
        .catch(() => {
          // Non-fatal -- row still shows via holdings state, just without
          // a live price/company name until the next full reload.
        });
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "Couldn't add this holding.");
    } finally {
      setBusy(false);
    }
  }

  const chatTicker = holdings[0]?.ticker ?? "ALAB";

  return (
    <AppShell>
      <div className="flex h-full min-h-0 flex-col lg:flex-row">
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <h1 className="font-heading text-2xl font-semibold">Portfolio</h1>
              <p className="mt-1 text-sm text-muted-foreground">Manage your holdings</p>
            </div>
            <Button
              title={
                tickersAvailableToAdd.length === 0
                  ? "All supported tickers are already in your portfolio"
                  : undefined
              }
              disabled={adding || tickersAvailableToAdd.length === 0}
              onClick={startAdd}
            >
              <Plus className="size-4" />
              Add Holding
            </Button>
          </div>

          {loading && <p className="py-10 text-center text-sm text-muted-foreground">Loading holdings...</p>}
          {error && <p className="py-10 text-center text-sm text-destructive">{error}</p>}

          {!loading && !error && (
            <div className="overflow-hidden rounded-xl bg-card ring-1 ring-foreground/10">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-border text-xs text-muted-foreground uppercase">
                    <th className="px-4 py-3 font-medium">Ticker</th>
                    <th className="px-4 py-3 font-medium">Shares</th>
                    <th className="px-4 py-3 font-medium">Cost Basis</th>
                    <th className="px-4 py-3 font-medium">Purchase Date</th>
                    <th className="px-4 py-3 font-medium">Current Price</th>
                    <th className="px-4 py-3 font-medium">Market Value</th>
                    <th className="px-4 py-3 font-medium">Gain / Loss</th>
                    <th className="px-4 py-3 font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {adding && addDraft && (
                    <tr>
                      <td className="px-4 py-3 align-top">
                        <select
                          value={addDraft.ticker}
                          onChange={(e) =>
                            setAddDraft((d) => (d ? { ...d, ticker: e.target.value } : d))
                          }
                          className="h-8 rounded-md border border-input bg-transparent px-2 text-sm"
                        >
                          {tickersAvailableToAdd.map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-4 py-3 align-top">
                        <Input
                          type="number"
                          min="0"
                          step="any"
                          value={addDraft.shares}
                          onChange={(e) =>
                            setAddDraft((d) => (d ? { ...d, shares: e.target.value } : d))
                          }
                          className="h-8 w-20"
                        />
                      </td>
                      <td className="px-4 py-3 align-top">
                        <Input
                          type="number"
                          min="0"
                          step="any"
                          value={addDraft.costBasis}
                          onChange={(e) =>
                            setAddDraft((d) => (d ? { ...d, costBasis: e.target.value } : d))
                          }
                          className="h-8 w-24"
                        />
                      </td>
                      <td className="px-4 py-3 align-top">
                        <Input
                          type="date"
                          value={addDraft.purchaseDate}
                          onChange={(e) =>
                            setAddDraft((d) => (d ? { ...d, purchaseDate: e.target.value } : d))
                          }
                          className="h-8 w-36"
                        />
                      </td>
                      <td className="px-4 py-3 align-top text-muted-foreground">—</td>
                      <td className="px-4 py-3 align-top text-muted-foreground">—</td>
                      <td className="px-4 py-3 align-top text-muted-foreground">—</td>
                      <td className="px-4 py-3 align-top">
                        <div className="flex gap-2">
                          <Button size="sm" disabled={busy} onClick={saveAdd}>
                            Save
                          </Button>
                          <Button size="sm" variant="outline" disabled={busy} onClick={cancelAdd}>
                            Cancel
                          </Button>
                        </div>
                      </td>
                    </tr>
                  )}

                  {holdings.map((h) => {
                    const isEditing = editingTicker === h.ticker;
                    const company = quotes[h.ticker]?.company ?? h.ticker;
                    const price = quotes[h.ticker]?.quote?.price ?? null;
                    const marketValue = price != null ? h.shares * price : null;
                    const costTotal = h.shares * h.cost_basis_avg;
                    const gainLoss = marketValue != null ? marketValue - costTotal : null;
                    const gainLossPct =
                      price != null && h.cost_basis_avg > 0
                        ? ((price - h.cost_basis_avg) / h.cost_basis_avg) * 100
                        : null;
                    const gainUp = (gainLoss ?? 0) >= 0;

                    return (
                      <tr key={h.ticker}>
                        <td className="px-4 py-3 align-top">
                          <p className="font-medium">{h.ticker}</p>
                          <p className="text-xs text-muted-foreground">{company}</p>
                        </td>

                        <td className="px-4 py-3 align-top">
                          {isEditing ? (
                            <Input
                              type="number"
                              min="0"
                              step="any"
                              value={draft?.shares ?? ""}
                              onChange={(e) => setDraft((d) => (d ? { ...d, shares: e.target.value } : d))}
                              className="h-8 w-20"
                            />
                          ) : (
                            h.shares
                          )}
                        </td>

                        <td className="px-4 py-3 align-top">
                          {isEditing ? (
                            <Input
                              type="number"
                              min="0"
                              step="any"
                              value={draft?.costBasis ?? ""}
                              onChange={(e) => setDraft((d) => (d ? { ...d, costBasis: e.target.value } : d))}
                              className="h-8 w-24"
                            />
                          ) : (
                            formatMoney(h.cost_basis_avg)
                          )}
                        </td>

                        <td className="px-4 py-3 align-top">
                          {isEditing ? (
                            <Input
                              type="date"
                              value={draft?.purchaseDate ?? ""}
                              onChange={(e) =>
                                setDraft((d) => (d ? { ...d, purchaseDate: e.target.value } : d))
                              }
                              className="h-8 w-36"
                            />
                          ) : (
                            formatDate(h.purchase_date)
                          )}
                        </td>

                        <td className="px-4 py-3 align-top">{price != null ? formatMoney(price) : "—"}</td>

                        <td className="px-4 py-3 align-top">
                          {marketValue != null ? formatMoney(marketValue) : "—"}
                        </td>

                        <td className="px-4 py-3 align-top">
                          {gainLoss != null && gainLossPct != null ? (
                            <span
                              className={
                                gainUp
                                  ? "text-[var(--status-intact-fg)]"
                                  : "text-[var(--status-at-risk-fg)]"
                              }
                            >
                              {gainUp ? "+" : ""}
                              {formatMoney(gainLoss)} ({gainUp ? "+" : ""}
                              {gainLossPct.toFixed(2)}%)
                            </span>
                          ) : (
                            "—"
                          )}
                        </td>

                        <td className="px-4 py-3 align-top">
                          {isEditing ? (
                            <div className="flex gap-2">
                              <Button size="sm" disabled={busy} onClick={() => saveEdit(h.ticker)}>
                                Save
                              </Button>
                              <Button size="sm" variant="outline" disabled={busy} onClick={cancelEdit}>
                                Cancel
                              </Button>
                            </div>
                          ) : (
                            <div className="flex items-center gap-3">
                              <button
                                type="button"
                                onClick={() => startEdit(h)}
                                disabled={busy || adding}
                                className="text-sm font-medium text-primary hover:underline disabled:opacity-50"
                              >
                                Edit
                              </button>
                              <button
                                type="button"
                                onClick={() => handleDelete(h)}
                                disabled={busy}
                                className="flex items-center gap-1 text-sm font-medium text-[var(--status-at-risk-fg)] hover:underline disabled:opacity-50"
                              >
                                <Trash2 className="size-3.5" />
                                Delete
                              </button>
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {!loading && !error && (
            <p className="mt-3 text-center text-xs text-muted-foreground">
              Showing {holdings.length} of {holdings.length} holdings
            </p>
          )}
        </div>

        <div className="flex h-[45vh] min-h-[320px] flex-col border-t lg:h-auto lg:w-[28%] lg:min-w-[340px] lg:border-t-0 lg:border-l">
          <div className="border-b bg-background px-4 py-3">
            <p className="font-heading text-base font-semibold">Ask North</p>
            <p className="text-xs text-muted-foreground">
              Grounded in filings, earnings, news, and market data.
            </p>
          </div>
          <div className="min-h-0 flex-1">
            <Chat key={chatTicker} ticker={chatTicker} threadId={chatTicker} />
          </div>
        </div>
      </div>
    </AppShell>
  );
}
