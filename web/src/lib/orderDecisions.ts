import type { Decision, IsoUtc } from '../types';

// Decision_Log ordering (Requirements 20.6, 21.1; design Property 39).

/** The DR_Event window; both ends are inclusive (Requirement 20.6). */
export interface DrWindow {
  start: IsoUtc;
  end: IsoUtc;
}

/**
 * Return the decisions newest first by `t`, keeping file order among equal
 * `t`. When `drWindow` is given (DR has fired), decisions with
 * `start ≤ t ≤ end` come first, ordered the same way, followed by the rest.
 * The input array is not modified.
 */
export function orderDecisions(
  decisions: readonly Decision[],
  drWindow: DrWindow | null,
): Decision[] {
  const lo = drWindow ? Date.parse(drWindow.start) : NaN;
  const hi = drWindow ? Date.parse(drWindow.end) : NaN;
  const keyed = decisions.map((d, i) => {
    const ms = Date.parse(d.t);
    return { d, i, ms, inDr: drWindow !== null && ms >= lo && ms <= hi };
  });
  keyed.sort(
    (a, b) => Number(b.inDr) - Number(a.inDr) || b.ms - a.ms || a.i - b.i,
  );
  return keyed.map((k) => k.d);
}

/** Stable id of a decision: a job gets at most one decision per slot. */
export function decisionKey(d: Decision): string {
  return `${d.t}|${d.job}`;
}
