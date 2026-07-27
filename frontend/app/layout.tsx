import type { Metadata } from "next";
import { Inter, Fraunces } from "next/font/google";
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

// Fraunces -- free Google Fonts substitute for the brand spec's "Editorial
// New" (a commercial font we don't have a license for). Used only for
// hero numbers/headlines (portfolio value, prices, large metrics) via
// --font-heading in globals.css -- everything else (nav, tables, body,
// chat, buttons) stays on Inter, matching the spec's split between the
// two typefaces.
const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  weight: ["500", "600"],
});

export const metadata: Metadata = {
  title: "Portfolio Copilot",
  description: "Agentic RAG assistant grounding your holdings in filings, fundamentals, and live data",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${inter.variable} ${fraunces.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
