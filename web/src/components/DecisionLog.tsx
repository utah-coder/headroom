import { useEffect, useMemo, useRef, type ReactNode } from 'react';
import type { Decision, Scenario } from '../types';
import type { DrPhase, Mode } from '../state/appState';
import { ROUTE_COLORS } from '../lib/colors';
import { hhmmDenver } from '../lib/time';
import { orderDecisions } from '../lib/orderDecisions';

// Decision_Log panel (Requirements 20.6, 21.1–21.7).

interface Props {
  scenario: Scenario;
  mode: Mode;
  dr: DrPhase;
}

const BASELINE_TEXT = 'Baseline: every job runs on arrival from the grid.';
const EMPTY_TEXT = 'No decisions for this scenario.';

/** DEFER targets are slot times (shown in Denver HH:MM); MOVE_REGION targets are regions. */
function targetLabel(target: Decision['target']): string | null {
  if (target === null) return null;
  if (target === 'BPAT' || target === 'CISO') return target;
  return hhmmDenver(target);
}

function DecisionRow({ d }: { d: Decision }) {
  const color = ROUTE_COLORS[d.route];
  const target = targetLabel(d.target);
  return (
    <li className="border-b border-slate-800 py-2 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <time dateTime={d.t} className="font-mono tabular-nums text-slate-300">
          {hhmmDenver(d.t)}
        </time>
        <span className="font-mono text-slate-100">job#{d.job}</span>
        <span
          className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-xs font-semibold"
          style={{ color, borderColor: color }}
        >
          <span aria-hidden="true" className="inline-block size-2 rounded-sm" style={{ backgroundColor: color }} />
          {d.route}
        </span>
        {target !== null && <span className="font-mono text-slate-300">→ {target}</span>}
      </div>
      <p className="mt-1 text-sm break-words whitespace-normal text-slate-300">{d.reason}</p>
    </li>
  );
}

export function DecisionLog({ scenario, mode, dr }: Props) {
  const drFired = mode === 'headroom' && dr === 'done';
  const { decisions, dr_event } = scenario;
  const ordered = useMemo(
    () => orderDecisions(decisions, drFired ? { start: dr_event.start, end: dr_event.end } : null),
    [decisions, drFired, dr_event.start, dr_event.end],
  );

  // Newest entry (or the DR_Event block) at the top on scenario change and
  // when DR-first ordering kicks in (Requirements 20.6, 21.6).
  const scrollRef = useRef<HTMLDivElement>(null);
  const scenarioName = scenario.meta.scenario;
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [scenarioName, mode, drFired]);

  let body: ReactNode;
  if (mode === 'baseline') {
    body = <p className="text-sm text-slate-300">{BASELINE_TEXT}</p>;
  } else if (ordered.length === 0) {
    body = <p className="text-sm text-slate-300">{EMPTY_TEXT}</p>;
  } else {
    body = (
      <ol>
        {ordered.map((d, i) => (
          <DecisionRow key={`${d.t}|${d.job}|${i}`} d={d} />
        ))}
      </ol>
    );
  }

  return (
    <section className="rounded-lg bg-slate-900 p-4" aria-labelledby="decision-log-heading">
      <h2 id="decision-log-heading" className="mb-2 text-base font-semibold text-slate-100">
        Decision log
      </h2>
      <div
        ref={scrollRef}
        tabIndex={0}
        aria-label={drFired ? 'Decision log, demand-response decisions first' : 'Decision log, newest first'}
        className="h-72 overflow-x-hidden overflow-y-auto rounded focus-visible:outline-2 focus-visible:outline-sky-300 pr-2"
      >
        {body}
      </div>
    </section>
  );
}

export default DecisionLog;
