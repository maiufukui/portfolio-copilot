// Hand-rolled SVG line chart -- no charting library dependency. Plots
// real quarterly data (revenue YoY growth, margin %) computed by
// app/tools.py's classify_revenue_trend/classify_margin_trend from
// actual XBRL filings -- not a mocked portfolio-value chart, since no
// real portfolio-value time series exists anywhere in this codebase
// (see get_dashboard_data()'s comment in app/tools.py).

interface Point {
  label: string;
  value: number;
}

const WIDTH = 280;
const HEIGHT = 100;
const PADDING = 24;

export function MiniLineChart({
  points,
  unit = "%",
  color = "var(--color-primary)",
}: {
  points: Point[];
  unit?: string;
  color?: string;
}) {
  if (points.length === 0) {
    return (
      <div className="flex h-[100px] items-center justify-center text-xs text-muted-foreground">
        Not enough data
      </div>
    );
  }

  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;

  const innerWidth = WIDTH - PADDING * 2;
  const innerHeight = HEIGHT - PADDING * 2;

  const coords = points.map((p, i) => {
    const x = PADDING + (points.length === 1 ? innerWidth / 2 : (i / (points.length - 1)) * innerWidth);
    const y = PADDING + innerHeight - ((p.value - min) / range) * innerHeight;
    return { x, y, ...p };
  });

  const path = coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="w-full" role="img" aria-label="Trend chart">
      <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      {coords.map((c) => (
        <g key={c.label}>
          <circle cx={c.x} cy={c.y} r={2.5} fill={color} />
          <text x={c.x} y={HEIGHT - 4} textAnchor="middle" className="fill-muted-foreground" fontSize={8}>
            {c.label}
          </text>
          <text x={c.x} y={c.y - 6} textAnchor="middle" className="fill-foreground" fontSize={8}>
            {c.value.toFixed(1)}
            {unit}
          </text>
        </g>
      ))}
    </svg>
  );
}
