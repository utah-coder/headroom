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
}

export interface Scenario {
  meta: Meta;
  grid: GridPoint[];
  baseline: BaselinePoint[];
  headroom: HeadroomPoint[];
  decisions: Decision[];
  dr_event: DrEvent;
  metrics: Metrics;
}
