import { AlertTriangle, Check, Minus } from "lucide-react";

import { cn } from "@/lib/utils";

// Three states, never color-only (PRD Appendix G): each state pairs a
// color, an icon, AND a text label, so it reads correctly for colorblind
// users and screen readers, not just sighted users scanning for color.
const STATUS_CONFIG = {
  intact: {
    label: "Intact",
    icon: Check,
    className: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  },
  monitor: {
    label: "Monitor",
    icon: Minus,
    className: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  },
  at_risk: {
    label: "At Risk",
    icon: AlertTriangle,
    className: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
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
