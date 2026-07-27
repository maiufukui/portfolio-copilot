import { AlertTriangle, Check, Minus } from "lucide-react";

import { cn } from "@/lib/utils";

// Three states, never color-only (PRD Appendix G): each state pairs a
// color, an icon, AND a text label, so it reads correctly for colorblind
// users and screen readers, not just sighted users scanning for color.
// Colors are the brand spec's muted/desaturated status palette (2026-07-26),
// not Tailwind's saturated emerald/amber/red -- referenced via the
// --status-* CSS custom properties defined in globals.css.
const STATUS_CONFIG = {
  intact: {
    label: "Intact",
    icon: Check,
    className: "bg-[var(--status-intact-bg)] text-[var(--status-intact-fg)]",
  },
  monitor: {
    label: "Monitor",
    icon: Minus,
    className: "bg-[var(--status-monitor-bg)] text-[var(--status-monitor-fg)]",
  },
  at_risk: {
    label: "At Risk",
    icon: AlertTriangle,
    className: "bg-[var(--status-at-risk-bg)] text-[var(--status-at-risk-fg)]",
  },
  insufficient_data: {
    label: "No data",
    icon: Minus,
    className: "bg-muted text-muted-foreground",
  },
} as const;

export function HealthPill({
  status,
  size = "default",
}: {
  status: keyof typeof STATUS_CONFIG;
  size?: "default" | "sm";
}) {
  const config = STATUS_CONFIG[status] ?? STATUS_CONFIG.insufficient_data;
  const Icon = config.icon;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium",
        size === "sm" ? "text-[0.7rem]" : "text-xs",
        config.className
      )}
    >
      <Icon className={size === "sm" ? "size-2.5" : "size-3"} />
      {config.label}
    </span>
  );
}
