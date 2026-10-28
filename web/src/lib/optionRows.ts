import type { Decision, OptionCost, Route } from '../types';
import { ROUTE_ORDER } from '../components/RouteChip';

// Pure layout for OptionBreakdown: one row per route, each priced option as a
// stacked bar of its cost parts. Kept free of React rendering so it can be unit tested.

export type CostPart = 'energy_usd' | 'carbon_usd' | 'peak_usd' | 'deadline_usd' | 'storage_usd' | 'fee_usd';

/** Bar order, left to right, with the legend label of each part. */
export const COST_PARTS: ReadonlyArray<{ key: CostPart; label: string }> = [
  { key: 'energy_usd', label: 'Energy' },
  { key: 'carbon_usd', label: 'Carbon' },
  { key: 'peak_usd', label: 'Peak charge' },
  { key: 'deadline_usd', label: 'Deadline risk' },
  { key: 'storage_usd', label: 'Storage' },
  { key: 'fee_usd', label: 'Fees' },
];

export interface Segment {
  key: CostPart;
  label: string;
  usd: number;
  /** Share of the full bar track, 0–100. */
  pct: number;
}

export interface OptionRow {
  route: Route;
  /** null when the route was not priced (filtered out before pricing). */
  option: OptionCost | null;
  chosen: boolean;
  offered: boolean;
  /** Non-zero parts only, in COST_PARTS order. */
  segments: Segment[];
}

/**
 * One row per route in route order. Bars share one scale, the largest priced
 * total, so a GRID_NOW price over the demand-response budget is drawn to the
 * same scale as the routes that were offered.
 */
export function optionRows(d: Decision): OptionRow[] {
  const byRoute = new Map(d.options.map((o) => [o.route, o]));
  const scale = Math.max(0, ...d.options.map((o) => o.total_usd));
  return ROUTE_ORDER.map((route) => {
    const option = byRoute.get(route) ?? null;
    const segments: Segment[] =
      option === null || scale <= 0
        ? []
        : COST_PARTS.filter(({ key }) => option[key] > 0).map(({ key, label }) => ({
            key,
            label,
            usd: option[key],
            pct: (option[key] / scale) * 100,
          }));
    return { route, option, chosen: route === d.route, offered: option?.offered ?? false, segments };
  });
}
