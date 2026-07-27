// Hand-rolled sparkline -- no charting library dependency. Plots real
// quarterly data (revenue QoQ growth, margin %) computed by
// app/tools.py's classify_revenue_trend/classify_margin_trend from actual
// XBRL filings.
//
// Rebuilt 2026-07-27 to fix a real sizing bug: the previous version was a
// single <svg viewBox="0 0 280 56" className="w-full"> with no CSS height,
// so its rendered height was locked to the viewBox's aspect ratio and grew
// with whatever width its container gave it. That was fine at the old
// narrow 4-column width, but once Fundamentals Health Score became a
// full-width stacked card, the container got wide and the chart (values,
// axis labels, everything -- all drawn as SVG text inside that same scaled
// coordinate system) blew up to ~5x its intended size.
//
// Fix: the chart's CSS height is fixed (unaffected by container width) and
// only the line/dots live in the scaled SVG coordinate space. Value labels
// and quarter labels are both plain HTML text, never subject to SVG
// viewBox scaling, so their font size is always exactly what the
// className says regardless of how wide the row is -- this is what makes
// it safe to show every point's value always-on (2026-07-27, Maiu) without
// reintroducing the original bug: the old broken version and this one
// both label every point, the difference is entirely about where that
// text lives (inside the scaled SVG then, plain HTML now).

interface Point {
  label: string;
  value: number;
}

// Percent-based viewBox (0-100 on both axes) + preserveAspectRatio="none"
// so the line stretches to fill the fixed-height box exactly, with no
// aspect-ratio-driven scaling of any kind.
const PAD_PCT = 15;

export function MiniLineChart({
  points,
  color = "var(--color-primary)",
}: {
  points: Point[];
  color?: string;
}) {
  if (points.length === 0) {
    return (
      <div className="flex h-10 items-center text-xs text-muted-foreground">Not enough data</div>
    );
  }

  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const coords = points.map((p, i) => {
    const xPct = points.length === 1 ? 50 : (i / (points.length - 1)) * 100;
    const yPct = PAD_PCT + (100 - PAD_PCT * 2) * (1 - (p.value - min) / range);
    return { xPct, yPct, ...p };
  });

  const path = coords
    .map((c, i) => `${i === 0 ? "M" : "L"}${c.xPct.toFixed(1)},${c.yPct.toFixed(1)}`)
    .join(" ");

  return (
    <div className="w-full">
      {/* Extra top padding (pt-3) reserves room for a value label floating
          above whichever dot happens to be the highest point, without
          needing to know that position ahead of time. The 0-100 SVG
          coordinate box itself is unchanged by this -- it's still anchored
          to the bottom h-8 of this wrapper. */}
      <div className="relative h-11 w-full pt-3">
        <div className="absolute inset-x-0 bottom-0 h-8">
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            className="absolute inset-0 h-full w-full"
            role="img"
            aria-label="Trend chart"
          >
            <path
              d={path}
              fill="none"
              stroke={color}
              strokeWidth={2}
              vectorEffect="non-scaling-stroke"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          </svg>
          {coords.map((c) => (
            <div
              key={c.label}
              className="absolute -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${c.xPct}%`, top: `${c.yPct}%` }}
            >
              <span className="absolute bottom-full left-1/2 mb-1 -translate-x-1/2 text-[9px] leading-none font-medium whitespace-nowrap text-foreground">
                {c.value.toFixed(1)}%
              </span>
              <span
                className="block size-1.5 rounded-full ring-2 ring-card"
                style={{ backgroundColor: color }}
              />
            </div>
          ))}
        </div>
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
        {points.map((p) => (
          <span key={p.label}>{p.label}</span>
        ))}
      </div>
    </div>
  );
}
