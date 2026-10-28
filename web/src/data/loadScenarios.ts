import { SCENARIOS } from '../config';
import type { Scenario, ScenarioName } from '../types';

// Loads the Scenario_Files once per page load (Requirements 17.1, 17.2, 24.2).
// Every request is same-origin under the Vite base URL; nothing else is fetched.
// Failures are reported per file, with no retries (Requirement 17.3).

export type LoadError = {
  file: 'peak.json' | 'sun.json';
  kind: 'load' | 'parse';
  detail: string;
};

// `scenarios` holds an entry for every name in SCENARIOS.
export type Loaded =
  | { ok: true; scenarios: Record<ScenarioName, Scenario> }
  | { ok: false; errors: LoadError[] };

const TOP_LEVEL_KEYS = [
  'meta',
  'grid',
  'baseline',
  'headroom',
  'naive',
  'decisions',
  'dr_event',
  'metrics',
  'naive_metrics',
] as const;

const GRID_LENGTH = 192;

type FileResult =
  | { ok: true; name: ScenarioName; scenario: Scenario }
  | { ok: false; error: LoadError };

// Module-level: one request per file, even when StrictMode runs effects twice.
let once: Promise<Loaded> | null = null;

export function loadScenarios(): Promise<Loaded> {
  if (once === null) {
    once = Promise.all(SCENARIOS.map(loadOne)).then(combine);
  }
  return once;
}

// Clears the cached promise so tests can exercise a fresh load.
export function resetLoadScenariosForTests(): void {
  once = null;
}

function combine(results: FileResult[]): Loaded {
  const errors: LoadError[] = [];
  const scenarios: Partial<Record<ScenarioName, Scenario>> = {};
  for (const r of results) {
    if (r.ok) scenarios[r.name] = r.scenario;
    else errors.push(r.error);
  }
  if (errors.length > 0) return { ok: false, errors };
  return { ok: true, scenarios: scenarios as Record<ScenarioName, Scenario> };
}

async function loadOne(name: ScenarioName): Promise<FileResult> {
  const file = `${name}.json` as const;
  const fail = (kind: LoadError['kind'], detail: string): FileResult => ({
    ok: false,
    error: { file, kind, detail },
  });

  let text: string;
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}scenarios/${file}`);
    if (!res.ok) return fail('load', `HTTP ${res.status}`);
    text = await res.text();
  } catch (err) {
    return fail('load', errorMessage(err));
  }

  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch (err) {
    return fail('parse', errorMessage(err));
  }

  const problem = shapeProblem(data);
  if (problem !== null) return fail('parse', problem);
  return { ok: true, name, scenario: data as Scenario };
}

// Light runtime check: the contract's top-level keys and 192 grid entries.
function shapeProblem(data: unknown): string | null {
  if (typeof data !== 'object' || data === null || Array.isArray(data)) {
    return 'top level is not an object';
  }
  const obj = data as Record<string, unknown>;
  const missing = TOP_LEVEL_KEYS.filter((k) => !(k in obj));
  if (missing.length > 0) return `missing keys: ${missing.join(', ')}`;
  if (!Array.isArray(obj.grid)) return 'grid is not an array';
  if (obj.grid.length !== GRID_LENGTH) {
    return `grid has ${obj.grid.length} entries, expected ${GRID_LENGTH}`;
  }
  return null;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
