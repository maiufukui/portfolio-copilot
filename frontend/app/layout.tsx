import type { Metadata } from "next";
import { Inter } from "next/font/google";
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
    <html lang="en" className={`${inter.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
