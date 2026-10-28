import type { Route } from '../types';
import { ROUTE_COLORS } from '../lib/colors';
import { formatUsd } from '../lib/format';

// Route chip and plain-language route glossary, shared by the Decision_Log and
// the Guide so a route looks and reads the same everywhere. The figures match
// sim/config.py (RESTART_PENALTY_MIN, MOVE_PENALTY_USD, MOVE_DELAY_SLOTS).

const RESTART_PENALTY_MIN = 5;
const MOVE_PENALTY_USD = 75;
const MOVE_DELAY_MIN = 30;

export const ROUTE_ORDER: readonly Route[] = ['GRID_NOW', 'STORAGE_NOW', 'DEFER', 'MOVE_REGION'];

/** One sentence per route, for an audience that has not read the spec. */
export const ROUTE_TEXT: Readonly<Record<Route, string>> = {
  GRID_NOW: 'Run now on grid power.',
  STORAGE_NOW: "Run now on the site's own battery and flywheel instead of the grid.",
  DEFER: `Pause and restart at a cheaper, cleaner hour (costs a ${RESTART_PENALTY_MIN}-minute restart).`,
  MOVE_REGION:
    `Run in BPAT (Pacific Northwest) or CISO (California) if they have idle GPUs ` +
    `(costs ${formatUsd(MOVE_PENALTY_USD)} and ${MOVE_DELAY_MIN} minutes).`,
};

export function RouteChip({ route }: { route: Route }) {
  const color = ROUTE_COLORS[route];
  return (
    <span
      className="inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-xs font-semibold"
      style={{ color, borderColor: color }}
    >
      <span aria-hidden="true" className="inline-block size-2 rounded-sm" style={{ backgroundColor: color }} />
      {route}
    </span>
  );
}

/** The four routes, each with its chip and sentence. */
export function RouteGlossary() {
  return (
    <ul className="flex flex-col gap-1.5">
      {ROUTE_ORDER.map((route) => (
        <li key={route} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-slate-300">
          <RouteChip route={route} />
          <span>{ROUTE_TEXT[route]}</span>
        </li>
      ))}
    </ul>
  );
}
