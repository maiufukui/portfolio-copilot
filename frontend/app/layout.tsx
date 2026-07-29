import type { Metadata } from "next";
import { Inter, Newsreader } from "next/font/google";
import "./globals.css";

// Inter -- a modern, geometric sans used across most current AI-product
// UIs (Linear, Vercel, and many others), swapped in for Geist. Named
// "--font-inter" and wired to Tailwind's --font-sans token in
// globals.css's @theme block -- the previous version of that block had
// a real bug (--font-sans: var(--font-sans), a circular reference that
// never resolved to any loaded font), which is why headings were
// silently falling back to the browser's default serif font.
const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

// Newsreader -- free Google Fonts substitute for the brand spec's
// "Editorial New" (a commercial font we don't have a license for). Swapped
// in 2026-07-27 for Fraunces, which read too quirky/idiosyncratic at
// display size for a "quiet, premium, trustworthy" brand (Fraunces' ink
// traps and wonky-by-default optical-size personality is a deliberate
// design choice for that font, just not this one). Newsreader is a
// transitional serif built specifically for long-form reading UI (closer
// in spirit to the FT/NYT register the brand doc names) -- calmer,
// higher x-height, no quirky detailing. Used only for hero numbers/
// headlines (portfolio value, prices, large metrics) via --font-heading
// in globals.css -- everything else stays on Inter.
const newsreader = Newsreader({
  variable: "--font-newsreader",
  subsets: ["latin"],
  weight: ["500", "600"],
});

export const metadata: Metadata = {
  // Fixed 2026-07-28 (UI-audit bug-list item "branding drift in the tab
  // title") -- was still "Portfolio Copilot," the pre-rename product name;
  // every other user-facing surface (dashboard.tsx's header copy, this
  // session's own work) already calls it North.
  title: "North",
  description: "Agentic RAG assistant grounding your holdings in filings, fundamentals, and live data",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${inter.variable} ${newsreader.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
