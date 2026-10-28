// Pure data helpers for MainChart and CarbonBand (Requirements 8.9, 19.1–19.9).
// Kept free of React so they can be unit tested without rendering.

import type { GridPoint, Scenario } from '../types';
import type { Mode } from '../state/appState';
import { formatKw } from './format';
import { hhmmDenver } from './time';

/** Slots between labelled x-axis ticks: 24 × 15 min = 6 h (Requirement 19.1). */
export const TICK_EVERY_SLOTS = 24;

/** One chart row per slot. Slot i spans x ∈ [i, i + 1); its point sits at the centre. */
export interface ChartRow {
  x: number;
  pace_mw: number;
  baseline_kw: number;
  headroom_kw: number;
  grid_kw: number;
  battery_kw: number;
  flywheel_kw: number;
  moved_kw: number;
}

export function buildRows(s: Scenario): ChartRow[] {
  return s.grid.map((g, i) => {
    const h = s.headroom[i];
    const b = s.baseline[i];
    return {
      x: i + 0.5,
      pace_mw: g.pace_demand_mw,
      baseline_kw: b.grid_kw,
      headroom_kw: h.grid_kw,
      grid_kw: h.grid_kw,
      battery_kw: h.battery_kw,
      flywheel_kw: h.flywheel_kw,
      moved_kw: h.moved_kw,
    };
  });
}

/** Contiguous runs of stress slots as [start, end) slot indices (Requirement 19.6). */
export function stressRuns(grid: readonly GridPoint[]): Array<{ start: number; end: number }> {
  const runs: Array<{ start: number; end: number }> = [];
  let start = -1;
  grid.forEach((g, i) => {
    if (g.stress && start < 0) start = i;
    if (!g.stress && start >= 0) {
      runs.push({ start, end: i });
      start = -1;
    }
  });
  if (start >= 0) runs.push({ start, end: grid.length });
  return runs;
}

/** Slot duration in ms, taken from the data (15 min in the contract). */
function slotMs(grid: readonly GridPoint[]): number {
  if (grid.length < 2) return 15 * 60 * 1000;
  return Date.parse(grid[1].t) - Date.parse(grid[0].t);
}

/** Tick positions every 6 h, at slot boundaries, inside the window. */
export function xTicks(n: number): number[] {
  const ticks: number[] = [];
  for (let i = 0; i < n; i += TICK_EVERY_SLOTS) ticks.push(i);
  return ticks;
}

/** Denver HH:MM label for slot boundary x (start of slot x). */
export function tickLabel(grid: readonly GridPoint[], x: number): string {
  if (grid.length === 0) return '';
  return hhmmDenver(new Date(Date.parse(grid[0].t) + x * slotMs(grid)));
}

/** Round a range out to multiples of `step` and list the ticks. */
function niceRange(lo: number, hi: number, step: number): { domain: [number, number]; ticks: number[] } {
  const min = Math.floor(lo / step) * step;
  let max = Math.ceil(hi / step) * step;
  if (max <= min) max = min + step;
  const ticks: number[] = [];
  for (let v = min; v <= max + step / 2; v += step) ticks.push(v);
  return { domain: [min, max], ticks };
}

/** A tick step from {1, 2, 5} × 10^k giving at most ~8 intervals. */
function niceStep(span: number): number {
  const raw = Math.max(span, 1) / 8;
  const mag = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) if (m * mag >= raw) return m * mag;
  return 10 * mag;
}

/**
 * Left (kW) axis range. Covers both modes so the axis does not jump when the
 * presenter toggles Baseline ↔ Headroom; the bottom reaches below zero when
 * storage charges (stackOffset="sign" stacks charging below the axis).
 */
export function kwAxis(rows: readonly ChartRow[]): { domain: [number, number]; ticks: number[] } {
  let lo = 0;
  let hi = 0;
  for (const r of rows) {
    const neg = Math.min(0, r.grid_kw) + Math.min(0, r.battery_kw) + Math.min(0, r.flywheel_kw) + Math.min(0, r.moved_kw);
    const pos = Math.max(0, r.grid_kw) + Math.max(0, r.battery_kw) + Math.max(0, r.flywheel_kw) + Math.max(0, r.moved_kw);
    lo = Math.min(lo, neg);
    hi = Math.max(hi, pos, r.baseline_kw, r.headroom_kw);
  }
  return niceRange(lo, hi, niceStep(hi - lo));
}

/** Right (MW) axis range for PACE demand. */
export function mwAxis(rows: readonly ChartRow[]): { domain: [number, number]; ticks: number[] } {
  if (rows.length === 0) return { domain: [0, 1], ticks: [0, 1] };
  const vals = rows.map((r) => r.pace_mw);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  return niceRange(lo, hi, niceStep(hi - lo));
}

/** One cell per hour: slot range [start, end) and that hour's carbon intensity. */
export interface HourCell {
  start: number;
  end: number;
  carbon: number;
}

/** Group consecutive slots into UTC hours (Denver offsets are whole hours). */
export function hourCells(grid: readonly GridPoint[]): HourCell[] {
  const cells: HourCell[] = [];
  grid.forEach((g, i) => {
    const hour = Math.floor(Date.parse(g.t) / 3_600_000);
    const last = cells[cells.length - 1];
    if (last && Math.floor(Date.parse(grid[last.start].t) / 3_600_000) === hour) {
      last.end = i + 1;
    } else {
      cells.push({ start: i, end: i + 1, carbon: g.carbon_g_per_kwh });
    }
  });
  return cells;
}

/** Low → mid → high carbon colours (emerald-400, amber-300, red-400). */
export const CARBON_STOPS = ['#34d399', '#fcd34d', '#f87171'] as const;

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

function mix(a: string, b: string, t: number): string {
  const ca = hexToRgb(a);
  const cb = hexToRgb(b);
  const c = ca.map((v, i) => Math.round(v + (cb[i] - v) * t));
  return `#${c.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
}

/** Continuous colour for `value` on the scenario's [min, max] carbon scale (Requirement 19.7). */
export function carbonColor(value: number, min: number, max: number): string {
  const t = max > min ? Math.min(1, Math.max(0, (value - min) / (max - min))) : 0;
  return t <= 0.5 ? mix(CARBON_STOPS[0], CARBON_STOPS[1], t * 2) : mix(CARBON_STOPS[1], CARBON_STOPS[2], (t - 0.5) * 2);
}

export function carbonRange(grid: readonly GridPoint[]): { min: number; max: number } {
  if (grid.length === 0) return { min: 0, max: 0 };
  const vals = grid.map((g) => g.carbon_g_per_kwh);
  return { min: Math.min(...vals), max: Math.max(...vals) };
}

const SCENARIO_LABEL: Record<Scenario['meta']['scenario'], string> = {
  peak: 'Peak week',
  sun: 'Sunny day',
};

const mwFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

/** Text summary for the chart's role="img" aria-label. */
export function chartAriaLabel(s: Scenario, mode: Mode): string {
  const rows = buildRows(s);
  const maxOf = (f: (r: ChartRow) => number) => (rows.length ? Math.max(...rows.map(f)) : 0);
  const baselinePeak = maxOf((r) => r.baseline_kw);
  const headroomPeak = maxOf((r) => r.headroom_kw);
  const pacePeak = maxOf((r) => r.pace_mw);
  const runs = stressRuns(s.grid).length;
  const first = s.grid.length ? hhmmDenver(s.grid[0].t) : '';
  const parts = [
    `Site grid draw over ${s.grid.length} 15-minute slots from ${first} Denver time, ${SCENARIO_LABEL[s.meta.scenario]} scenario, ${mode === 'headroom' ? 'Headroom' : 'Baseline'} mode.`,
    mode === 'headroom'
      ? `Headroom grid draw peaks at ${formatKw(headroomPeak)} against a baseline peak of ${formatKw(baselinePeak)}, with grid, battery, flywheel and moved load stacked.`
      : `Baseline grid draw peaks at ${formatKw(baselinePeak)}.`,
    `PACE demand peaks at ${mwFormat.format(Math.round(pacePeak))} MW.`,
    `${runs} grid stress ${runs === 1 ? 'period is' : 'periods are'} shaded.`,
  ];
  return parts.join(' ');
}
