"use client";

// Sidebar shell for the North-branded redesign (2026-07-26 demo hardening
// plan). Only Dashboard and Portfolio are real routes -- Discover and
// Settings are static/decorative per Maiu's explicit call: no backend, no
// route, not clickable. Don't wire these up just because they're visually
// present; that was a deliberate scope cut, not an oversight to
// "complete" later without being asked.

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Briefcase, Compass, LayoutDashboard, Menu, Settings } from "lucide-react";

import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
] as const;

// Discover stays in the main nav group (decorative). Settings and the user
// card are grouped at the bottom instead -- per Maiu (2026-07-27): the
// bottom user card already functions as the profile summary, so a
// separate "Profile" nav row was redundant and got removed; Settings sits
// directly above that card rather than up with Dashboard/Portfolio.
const DISCOVER_ITEM = { label: "Discover", icon: Compass } as const;
const SETTINGS_ITEM = { label: "Settings", icon: Settings } as const;

function DecorativeRow({
  icon: Icon,
  label,
  collapsed,
}: {
  icon: typeof Compass;
  label: string;
  collapsed: boolean;
}) {
  return (
    <div
      aria-disabled="true"
      title={collapsed ? label : undefined}
      className={cn(
        "flex cursor-not-allowed items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground/50",
        collapsed && "justify-center px-0"
      )}
    >
      <Icon className="size-4 shrink-0" />
      {!collapsed && label}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex h-dvh">
      <aside
        className={cn(
          "flex shrink-0 flex-col gap-6 border-r border-sidebar-border bg-sidebar py-5 transition-[width] duration-150",
          collapsed ? "w-16 px-2" : "w-56 px-4"
        )}
      >
        <div className={cn("flex items-center gap-2", collapsed ? "flex-col" : "justify-between")}>
          <Link href="/" className="flex items-center gap-2.5 overflow-hidden">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Compass className="size-4" />
            </div>
            {!collapsed && (
              <span className="font-heading text-lg font-semibold whitespace-nowrap text-foreground">
                North
              </span>
            )}
          </Link>

          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="flex size-7 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
          >
            <Menu className="size-4" />
          </button>
        </div>

        <nav className="flex flex-col gap-1">
          {NAV_ITEMS.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                title={collapsed ? item.label : undefined}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors",
                  collapsed && "justify-center px-0",
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"
                )}
              >
                <item.icon className="size-4 shrink-0" />
                {!collapsed && item.label}
              </Link>
            );
          })}

          {/* Decorative only -- not a link, not a button, no onClick.
              Explicitly out of scope, not a stub waiting to be finished. */}
          <DecorativeRow icon={DISCOVER_ITEM.icon} label={DISCOVER_ITEM.label} collapsed={collapsed} />
        </nav>

        <div className="mt-auto flex flex-col gap-1">
          <DecorativeRow icon={SETTINGS_ITEM.icon} label={SETTINGS_ITEM.label} collapsed={collapsed} />

          {/* Doubles as the profile summary -- no separate "Profile" nav
              row on purpose, see the comment above DISCOVER_ITEM/
              SETTINGS_ITEM. */}
          <div
            className={cn(
              "flex items-center gap-2.5 rounded-lg px-3 py-2",
              collapsed && "justify-center px-0"
            )}
          >
            <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-muted-foreground">
              MK
            </div>
            {!collapsed && (
              <div className="leading-tight whitespace-nowrap">
                <p className="text-sm font-medium text-foreground">Maiu K.</p>
                <p className="text-xs text-muted-foreground">Pro Plan</p>
              </div>
            )}
          </div>
        </div>
      </aside>

      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}
