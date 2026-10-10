type Point = { time: number | string | null; equity: number; drawdown: number };

export function EquityCurve({ points }: { points: Point[] }) {
  if (points.length < 2) {
    return <p className="text-sm text-terminal-muted">No equity curve.</p>;
  }
  const values = points.map((point) => point.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 640;
  const height = 160;
  const coords = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width;
      const y = height - 8 - ((value - min) / span) * (height - 16);
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <svg
      data-testid="grid-equity"
      viewBox={`0 0 ${width} ${height}`}
      className="h-40 w-full text-terminal-accent"
      role="img"
      aria-label="Equity curve"
    >
      <polyline fill="none" stroke="currentColor" strokeWidth="2" points={coords} />
    </svg>
  );
}
