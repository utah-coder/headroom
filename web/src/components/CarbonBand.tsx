import type { GridPoint } from '../types';
import { CARBON_STOPS, carbonColor, carbonRange, hourCells } from '../lib/chartData';

// Hourly carbon intensity strip under the main chart (Requirements 8.9, 19.7, 24.3).
// It spans the same x-domain as the chart: slot 0 at the left edge of the plot
// area, slot n at the right. `insetLeft`/`insetRight` are the chart's margin plus
// axis width on each side, so the strip lines up with the plot area.

interface Props {
  grid: readonly GridPoint[];
  insetLeft: number;
  insetRight: number;
}

export function CarbonBand({ grid, insetLeft, insetRight }: Props) {
  const n = grid.length;
  const { min, max } = carbonRange(grid);
  const lo = Math.round(min);
  const hi = Math.round(max);
  const cells = hourCells(grid);

  return (
    <div>
      <div style={{ paddingLeft: insetLeft, paddingRight: insetRight }}>
        <svg
          className="block w-full"
          height={24}
          viewBox={`0 0 ${Math.max(n, 1)} 1`}
          preserveAspectRatio="none"
          role="img"
          aria-label={`Hourly carbon intensity, ${lo} to ${hi} gCO₂eq/kWh, average intensity, not marginal`}
        >
          {cells.map((c) => (
            <rect
              key={c.start}
              x={c.start}
              y={0}
              width={c.end - c.start}
              height={1}
              fill={carbonColor(c.carbon, min, max)}
              shapeRendering="crispEdges"
            />
          ))}
        </svg>
      </div>
      <p
        className="mt-1 flex flex-wrap items-center gap-x-2 text-xs text-slate-300"
        style={{ paddingLeft: insetLeft, paddingRight: insetRight }}
      >
        <span
          aria-hidden="true"
          className="inline-block h-3 w-10 rounded-sm"
          style={{ background: `linear-gradient(to right, ${CARBON_STOPS.join(', ')})` }}
        />
        <span>
          Carbon intensity per hour: {lo}–{hi} gCO₂eq/kWh · average intensity, not marginal
        </span>
      </p>
    </div>
  );
}
