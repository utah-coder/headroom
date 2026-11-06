// The shape of scenarios/stress.json, mirrored from STRESS_INTERFACE_KEYS in sim/stress.py.
// sim/tests/test_forecast_stress.py keeps the two in step. It's separate from types.ts
// because a Scenario_File is a different contract, and this file is optional for the page.

import type { ScenarioName } from './types';

export interface StressFile {
  meta: StressMeta;
  carbon_accuracy: CarbonAccuracyRow[];
  demand_accuracy: DemandAccuracyRow;
  scenarios: Record<ScenarioName, ScenarioStress>;
  replay: ReplaySummary;
}

export interface StressMeta {
  built_at: string;
  seed: number;
  samples: number;
  runtime_median: number;
  runtime_sigma: number;
  outage_hours_min: number;
  outage_hours_max: number;
  data_start: string;
  data_end: string;
}

export interface CarbonAccuracyRow {
  region: string;
  lead_hours: number;
  forecasts: number;
  mae_g_per_kwh: number;
  naive_mae_g_per_kwh: number;
  band_coverage_pct: number;
}

export interface DemandAccuracyRow {
  hours: number;
  mape_pct: number;
  bias_pct: number;
  band_coverage_pct: number;
}

export interface ScenarioStress {
  families: StressFamily[];
  carbon_band: BandPoint[];
  demand_band: BandPoint[];
}

export interface StressFamily {
  id: string;
  label: string;
  samples: number;
  usd_saved: StressRange;
  demand_charge_saved_usd: StressRange;
  bill_usd: StressRange;
  baseline_bill_usd: StressRange;
  co2_avoided_t: StressRange;
  headroom_kw: StressRange;
  site_peak_kw: StressRange;
  negative_runs: number;
  deadlines_missed_p50: number;
  deadlines_missed_max: number;
  baseline_deadlines_missed_max: number;
  dr_missed_runs: number;
  dr_shortfall_max_kw: number;
}

export interface StressRange {
  p10: number;
  p50: number;
  p90: number;
  min: number;
  max: number;
}

export interface BandPoint {
  t: string;
  actual: number;
  forecast: number;
  low: number | null;
  high: number | null;
}

export interface ReplaySummary {
  windows: ReplayWindow[];
  foresight_usd: StressRange;
  forecast_usd: StressRange;
  foresight_negative: number;
  forecast_negative: number;
  change_median_usd: number;
  change_worst_usd: number;
  change_best_usd: number;
  windows_lower: number;
  windows_higher: number;
  foresight_dr_missed: number;
  forecast_dr_missed: number;
  late_event_windows: number;
  forecast_dr_missed_late: number;
}

export interface ReplayWindow {
  start: string;
  foresight_usd: number;
  forecast_usd: number;
  forecast_co2_t: number;
  forecast_deadlines_missed: number;
  dr_hour: number;
  foresight_dr_shortfall_kw: number;
  forecast_dr_shortfall_kw: number;
}
