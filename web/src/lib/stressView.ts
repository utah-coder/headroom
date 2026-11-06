import type { BandPoint, StressFamily, StressRange } from '../stressTypes';
import { formatUsd } from './format';

// Pure helpers for the stress panel. Every figure comes from stress.json; nothing is computed
// here except positions on a scale, counts of points, and text.

/** Saved-money scale for the range bars: every row's p10 to p90, always including zero. */
export function savedDomain(families: readonly StressFamily[]): [number, number] {
  const values = families.flatMap((f) => [f.usd_saved.p10, f.usd_saved.p90]);
  return [Math.min(0, ...values), Math.max(0, ...values)];
}

/** Where a value sits on the scale, as a percentage of the track's width. */
export function positionPct(value: number, [low, high]: readonly [number, number]): number {
  if (high <= low) return 0;
  return ((value - low) / (high - low)) * 100;
}

/** "$1,669 to $33,060", or the one value when a family is a single run. */
export function rangeText(range: StressRange, samples: number): string {
  if (samples === 1) return formatUsd(range.p50);
  return `${formatUsd(range.p10)} to ${formatUsd(range.p90)}`;
}

/** Hours whose actual value fell inside the band, of the hours that had one. */
export function bandHits(points: readonly BandPoint[]): { inside: number; total: number } {
  const banded = points.filter((p) => p.low !== null && p.high !== null);
  const inside = banded.filter((p) => (p.low as number) <= p.actual && p.actual <= (p.high as number));
  return { inside: inside.length, total: banded.length };
}

/** Recharts reads a range area from a [low, high] pair. */
export function bandChartData(points: readonly BandPoint[]) {
  return points.map((p) => ({
    t: p.t,
    actual: p.actual,
    forecast: p.forecast,
    band: p.low === null || p.high === null ? null : ([p.low, p.high] as [number, number]),
  }));
}

/** "1 of 100 runs" for a tested family, "yes" or "no" for a single run. */
export function runsText(count: number, samples: number): string {
  if (samples === 1) return count > 0 ? 'Yes' : 'No';
  return `${count} of ${samples}`;
}

/** Share of the money saved that came from a lower peak, rounded. Null when saved isn't positive. */
export function demandChargeSharePct(family: StressFamily): number | null {
  const saved = family.usd_saved.p50;
  return saved > 0 ? Math.round((family.demand_charge_saved_usd.p50 / saved) * 100) : null;
}
