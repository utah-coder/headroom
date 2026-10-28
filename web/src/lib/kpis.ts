import type { Scenario } from '../types';
import type { DrPhase, Mode } from '../state/appState';
import { formatKw, formatKwChange, formatPct, formatTonnes, formatUsd } from './format';

// KPI selector for KpiRow (Requirements 18.5–18.7, 18.10, 20.5, 24.3).
// Pure: same inputs give the same output, no React, no side effects.
// Cheapest-hour mode reads naive_metrics; its stress-hour headroom and
// inference SLA come from the series (DECISIONS.md, "Cheapest hour").

export type KpiId =
  | 'site_peak_kw'
  | 'headroom_kw'
  | 'usd_saved'
  | 'co2_avoided_t'
  | 'inference_sla_pct'
  | 'deadlines_met_pct';

export interface KpiCardValue {
  id: KpiId;
  label: string;
  value: string; // formatted per Requirement 18.10
  change?: string; // vs baseline, e.g. "+19,627 kW vs baseline"
  note?: string; // small qualifier under the value
}

export interface DrStripValue {
  id: 'requested_shed_kw' | 'dr_delivered_pct' | 'dr_seconds_to_full';
  label: string;
  value: string;
}

export interface KpiSelection {
  cards: [KpiCardValue, KpiCardValue, KpiCardValue, KpiCardValue, KpiCardValue, KpiCardValue];
  dr: [DrStripValue, DrStripValue, DrStripValue] | null; // null unless Headroom and dr === 'done'
}

// Baseline_Policy values (Requirement 18.7): nothing freed, saved or avoided,
// and it serves all inference and runs every job from arrival.
const BASELINE = {
  headroom_kw: 0,
  usd_saved: 0,
  co2_avoided_t: 0,
  inference_sla_pct: 100,
  deadlines_met_pct: 100,
} as const;

const secondsFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

/** "20 s" (whole seconds). */
function formatSeconds(n: number): string {
  if (!Number.isFinite(n)) return '–';
  const r = Math.round(n);
  return `${r < 0 ? '-' : ''}${secondsFormat.format(Math.abs(r))} s`;
}

/** Highest grid draw over the stress slots. */
function stressPeakKw(scenario: Scenario, series: readonly { grid_kw: number }[]): number {
  return Math.max(...scenario.grid.flatMap((g, i) => (g.stress ? [series[i].grid_kw] : [])));
}

/** The cheapest-hour policy's KPIs, in the shape of the Headroom ones. */
function naiveValues(scenario: Scenario) {
  const nm = scenario.naive_metrics;
  const served = scenario.naive.filter((r, i) => r.inference_kw === scenario.baseline[i].inference_kw).length;
  return {
    headroom_kw: stressPeakKw(scenario, scenario.baseline) - nm.stress_peak_kw,
    usd_saved: nm.usd_saved,
    co2_avoided_t: nm.co2_avoided_t,
    inference_sla_pct: (100 * served) / scenario.naive.length,
    deadlines_met_pct: nm.deadlines_met_pct,
  };
}

export function selectKpis(scenario: Scenario, mode: Mode, dr: DrPhase): KpiSelection {
  const reference = mode === 'baseline';
  const m = mode === 'headroom' ? scenario.metrics : mode === 'naive' ? naiveValues(scenario) : BASELINE;
  const baselinePeak = scenario.metrics.baseline_site_peak_kw;
  const sitePeak =
    mode === 'headroom'
      ? scenario.metrics.headroom_site_peak_kw
      : mode === 'naive'
        ? scenario.naive_metrics.site_peak_kw
        : baselinePeak;

  // One short line under each value for an audience new to the demo. Baseline
  // is the reference the savings are measured against, so its zeros say so.
  const cards: KpiSelection['cards'] = [
    {
      id: 'site_peak_kw',
      label: 'Site peak',
      value: formatKw(sitePeak),
      change: reference ? undefined : `${formatKwChange(sitePeak - baselinePeak)} vs baseline`,
      note: 'grid connection the site needs',
    },
    {
      id: 'headroom_kw',
      label: 'Headroom returned (stress hours)',
      value: formatKw(m.headroom_kw),
      note: reference ? 'baseline is the reference point' : 'grid capacity freed at the grid’s busiest hours',
    },
    {
      id: 'usd_saved',
      label: '$ saved',
      value: formatUsd(m.usd_saved),
      note: reference ? 'baseline is the reference point' : 'energy + demand charges, 48 h',
    },
    {
      id: 'co2_avoided_t',
      label: 'CO₂ avoided',
      value: formatTonnes(m.co2_avoided_t),
      note: 'average intensity, not marginal', // Requirement 24.3
    },
    {
      id: 'inference_sla_pct',
      label: 'Inference SLA',
      value: formatPct(m.inference_sla_pct),
      note: 'customer-facing work served on time',
    },
    {
      id: 'deadlines_met_pct',
      label: 'Training deadlines met',
      value: formatPct(m.deadlines_met_pct),
      note: 'training jobs finished by deadline',
    },
  ];

  // DR values appear only in Headroom, after playback completes or the static chart shows (Requirement 20.5).
  const drStrip: KpiSelection['dr'] =
    mode === 'headroom' && dr === 'done'
      ? [
          {
            id: 'requested_shed_kw',
            label: 'Demand-response cut requested',
            value: formatKw(scenario.dr_event.requested_shed_kw),
          },
          { id: 'dr_delivered_pct', label: 'Cut delivered', value: formatPct(scenario.metrics.dr_delivered_pct) },
          {
            id: 'dr_seconds_to_full',
            label: 'Seconds to full response',
            value: formatSeconds(scenario.metrics.dr_seconds_to_full),
          },
        ]
      : null;

  return { cards, dr: drStrip };
}
