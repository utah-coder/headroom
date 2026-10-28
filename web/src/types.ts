// Scenario_File shape (Output_Contract, Requirement 16).
// Mirrors the key sets in sim/contract.py INTERFACE_KEYS; the mirror test
// (sim/tests/test_types_mirror.py) compares the `export interface` blocks
// below against them, so keep interface and field names in sync.

export type ScenarioName = 'peak' | 'sun';
export type Route = 'GRID_NOW' | 'STORAGE_NOW' | 'DEFER' | 'MOVE_REGION';
export type IsoUtc = string; // "YYYY-MM-DDTHH:MM:SSZ"

export interface Sources {
  grid: 'REAL: EIA-930 PACE';
  site: 'SIMULATED';
  jobs: 'SIMULATED';
  storage: 'SIMULATED';
  tariff: 'ILLUSTRATIVE';
}

export interface Meta {
  scenario: ScenarioName;
  window: [IsoUtc, IsoUtc];
  seed: number;
  built_at: IsoUtc;
  sources: Sources;
  params: Record<string, unknown>; // free-form in the contract
}

export interface GridPoint {
  t: IsoUtc;
  pace_demand_mw: number;
  pace_forecast_mw: number;
  carbon_g_per_kwh: number;
  price_per_kwh: number;
  stress: boolean;
}

export interface BaselinePoint {
  t: IsoUtc;
  grid_kw: number;
  inference_kw: number;
  training_kw: number;
}

export interface HeadroomPoint {
  t: IsoUtc;
  grid_kw: number;
  inference_kw: number;
  training_kw: number;
  battery_kw: number; // + discharge, − charge
  flywheel_kw: number; // + discharge, − charge
  soc_pct: number; // battery SOC, 0–100
  moved_kw: number;
}

export interface Decision {
  t: IsoUtc;
  job: string;
  route: Route;
  target: IsoUtc | 'BPAT' | 'CISO' | null; // DEFER: later slot; MOVE_REGION: region; else null
  reason: string;
  options: OptionCost[]; // the routes compared: GRID_NOW first, then each other offered route
}

// One route priced for a decision. The six *_usd parts add up to total_usd
// (to the cent, after rounding).
export interface OptionCost {
  route: Route;
  target: IsoUtc | 'BPAT' | 'CISO' | null; // same encoding as Decision.target
  offered: boolean; // false: GRID_NOW over the demand-response grid budget
  total_usd: number;
  energy_usd: number;
  carbon_usd: number;
  peak_usd: number;
  deadline_usd: number;
  storage_usd: number;
  fee_usd: number;
}

export interface ZoomPoint {
  s: number;
  grid_kw: number;
  flywheel_kw: number;
  battery_kw: number;
}

export interface DrEvent {
  start: IsoUtc;
  end: IsoUtc;
  requested_shed_kw: number;
  cap_kw: number;
  zoom: ZoomPoint[];
}

export interface Metrics {
  headroom_kw: number;
  usd_saved: number;
  co2_avoided_t: number;
  deadlines_met_pct: number;
  inference_sla_pct: number;
  dr_delivered_pct: number;
  dr_seconds_to_full: number;
  baseline_site_peak_kw: number; // highest grid draw over the window: the connection the site needs
  headroom_site_peak_kw: number;
}

// The cheapest-hour policy against baseline: each job picks its own cheapest,
// cleanest start, blind to the site's peak.
export interface NaiveMetrics {
  site_peak_kw: number;
  stress_peak_kw: number;
  usd_saved: number; // vs baseline; negative when it costs more
  co2_avoided_t: number;
  deadlines_met_pct: number;
  peak_hour_starts: number; // jobs starting in the clock hour of the new peak
  peak_jobs_running: number; // jobs running in the peak slot
  baseline_jobs_running: number; // jobs running in that slot under baseline
  capped_missed: number; // held to cap_kw: jobs that miss their deadline
  capped_total: number;
  cap_kw: number; // today's connection: the baseline window peak
}

export interface Scenario {
  meta: Meta;
  grid: GridPoint[];
  baseline: BaselinePoint[];
  headroom: HeadroomPoint[];
  naive: BaselinePoint[]; // cheapest hour: same row shape as baseline
  decisions: Decision[];
  dr_event: DrEvent;
  metrics: Metrics;
  naive_metrics: NaiveMetrics;
}
