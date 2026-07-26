"use client";

// Rough mockup only -- item 5 (demo doc), explicitly lower priority and
// "DO THIS IF WE HAVE TIME." Purpose right now is just to nail down the
// data shape (the 4 fields below, matching the demo doc's spec exactly)
// before the real UI gets built with the designer. Deliberately NOT wired
// to a real backend yet -- server.py has no /holdings endpoint yet (item
// 5, step 2), so submitting here only shows what would be sent, it
// doesn't persist anything. Replace this whole file once the designer's
// version + the real endpoint both exist; nothing else in the app
// (Dashboard, Chat) imports from or depends on this page.

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

interface HoldingFormData {
  ticker: string;
  shares: string;
  costBasis: string;
  datePurchased: string;
}

const EMPTY_FORM: HoldingFormData = {
  ticker: "",
  shares: "",
  costBasis: "",
  datePurchased: "",
};

export default function OnboardingPage() {
  const [form, setForm] = useState<HoldingFormData>(EMPTY_FORM);
  // Rough stand-in for a real POST /holdings response -- just echoes back
  // what was submitted, so the data shape is visible end to end without
  // needing the backend endpoint built yet.
  const [submitted, setSubmitted] = useState<HoldingFormData | null>(null);
  const [error, setError] = useState<string | null>(null);

  function update(field: keyof HoldingFormData, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();

    if (!form.ticker.trim()) {
      setError("Ticker is required.");
      return;
    }
    const sharesNum = Number(form.shares);
    if (!form.shares || Number.isNaN(sharesNum) || sharesNum <= 0) {
      setError("Shares owned must be a positive number.");
      return;
    }
    const costBasisNum = Number(form.costBasis);
    if (!form.costBasis || Number.isNaN(costBasisNum) || costBasisNum <= 0) {
      setError("Cost basis must be a positive number.");
      return;
    }
    if (!form.datePurchased) {
      setError("Date purchased is required.");
      return;
    }

    setError(null);
    // TODO (item 5, step 2): replace with a real
    // POST `${API_URL}/holdings` call once server.py has the endpoint.
    setSubmitted({ ...form, ticker: form.ticker.trim().toUpperCase() });
  }

  return (
    <main className="mx-auto max-w-md px-4 py-10">
      <Card>
        <CardHeader>
          <CardTitle>Add a holding</CardTitle>
          <CardDescription>
            Rough mockup -- 4 fields, no account type (demo doc item 5). Not
            yet connected to the backend; this just shows the data shape.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm">
              Ticker
              <Input
                placeholder="e.g. ALAB"
                value={form.ticker}
                onChange={(e) => update("ticker", e.target.value)}
              />
            </label>

            <label className="flex flex-col gap-1 text-sm">
              Shares owned
              <Input
                type="number"
                min="0"
                step="any"
                placeholder="e.g. 100"
                value={form.shares}
                onChange={(e) => update("shares", e.target.value)}
              />
            </label>

            <label className="flex flex-col gap-1 text-sm">
              Cost basis (per share, $)
              <Input
                type="number"
                min="0"
                step="any"
                placeholder="e.g. 62.50"
                value={form.costBasis}
                onChange={(e) => update("costBasis", e.target.value)}
              />
            </label>

            <label className="flex flex-col gap-1 text-sm">
              Date purchased
              <Input
                type="date"
                value={form.datePurchased}
                onChange={(e) => update("datePurchased", e.target.value)}
              />
            </label>

            {error && <p className="text-sm text-destructive">{error}</p>}

            <Button type="submit" className="mt-2">
              Add holding
            </Button>
          </form>

          {submitted && (
            <div className="mt-4 rounded-lg border bg-muted/50 p-3 text-sm">
              <p className="mb-1 font-medium">
                Would send to POST /holdings (not yet real):
              </p>
              <pre className="whitespace-pre-wrap text-xs text-muted-foreground">
                {JSON.stringify(
                  {
                    ticker: submitted.ticker,
                    shares: Number(submitted.shares),
                    cost_basis: Number(submitted.costBasis),
                    date_purchased: submitted.datePurchased,
                  },
                  null,
                  2
                )}
              </pre>
            </div>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
