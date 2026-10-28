import type { Scenario } from '../types';
import type { DrPhase, Mode } from '../state/appState';
import { formatKw, formatPct, formatTonnes, formatUsd } from './format';

// KPI selector for KpiRow (Requirements 18.5–18.7, 18.10, 20.5, 24.3).
// Pure: same inputs give the same output, no React, no side effects.

export type KpiId = 'headroom_kw' | 'usd_saved' | 'co2_avoided_t' | 'inference_sla_pct' | 'deadlines_met_pct';

export interface KpiCardValue {
  id: KpiId;
  label: string;
  value: string; // formatted per Requirement 18.10
  note?: string; // small qualifier under the value
}

export interface DrStripValue {
  id: 'requested_shed_kw' | 'dr_delivered_pct' | 'dr_seconds_to_full';
  label: string;
  value: string;
}

export interface KpiSelection {
  cards: [KpiCardValue, KpiCardValue, KpiCardValue, KpiCardValue, KpiCardValue];
  dr: [DrStripValue, DrStripValue, DrStripValue] | null; // null unless dr === 'done'
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

export function selectKpis(scenario: Scenario, mode: Mode, dr: DrPhase): KpiSelection {
  const m = mode === 'headroom' ? scenario.metrics : BASELINE;

  const cards: KpiSelection['cards'] = [
    { id: 'headroom_kw', label: 'Headroom returned (stress hours)', value: formatKw(m.headroom_kw) },
    { id: 'usd_saved', label: '$ saved', value: formatUsd(m.usd_saved) },
    {
      id: 'co2_avoided_t',
      label: 'CO₂ avoided',
      value: formatTonnes(m.co2_avoided_t),
      note: 'average intensity, not marginal', // Requirement 24.3
    },
    { id: 'inference_sla_pct', label: 'Inference SLA', value: formatPct(m.inference_sla_pct) },
    { id: 'deadlines_met_pct', label: 'Training deadlines met', value: formatPct(m.deadlines_met_pct) },
  ];

  // DR values appear only after playback completes or the static chart shows (Requirement 20.5).
  const drStrip: KpiSelection['dr'] =
    dr === 'done'
      ? [
          {
            id: 'requested_shed_kw',
            label: 'DR requested shed',
            value: formatKw(scenario.dr_event.requested_shed_kw),
          },
          { id: 'dr_delivered_pct', label: 'DR delivered', value: formatPct(scenario.metrics.dr_delivered_pct) },
          {
            id: 'dr_seconds_to_full',
            label: 'Seconds to full response',
            value: formatSeconds(scenario.metrics.dr_seconds_to_full),
          },
        ]
      : null;

  return { cards, dr: drStrip };
}
