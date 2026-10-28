import { useEffect, useMemo, useRef, type ReactNode } from 'react';
import type { Decision, Scenario } from '../types';
import type { DrPhase, Mode } from '../state/appState';
import { hhmmDenver } from '../lib/time';
import { orderDecisions } from '../lib/orderDecisions';
import { RouteChip, RouteGlossary } from './RouteChip';

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
  const target = targetLabel(d.target);
  return (
    <li className="border-b border-slate-800 py-2 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
        <time dateTime={d.t} className="font-mono tabular-nums text-slate-300">
          {hhmmDenver(d.t)}
        </time>
        <span className="font-mono text-slate-100">job#{d.job}</span>
        <RouteChip route={d.route} />
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

  // Baseline keeps the required sentence first, then says what Headroom adds
  // (owner request, DECISIONS.md 2026-10-28 "Guide panel and in-page explanations").
  let body: ReactNode;
  if (mode === 'baseline') {
    body = (
      <div className="flex flex-col gap-2 text-sm text-slate-300">
        <p className="text-slate-100">{BASELINE_TEXT}</p>
        <p>
          It makes no choices. Switch to Headroom to see the {decisions.length} routing decisions for this scenario,
          each with its reason. Every training job, every 15 minutes, takes one of four routes:
        </p>
        <RouteGlossary />
      </div>
    );
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

  // Below 1280 px the scroll region is a fixed 18rem. At >= 1280 px it has a
  // zero flex basis and a 9rem minimum, so it fills whatever the side column
  // has left in the grid row without making the row taller; the row height is
  // set by the layout, never by the number of decisions (Requirements 21.4, 22.9).
  return (
    <section className="flex flex-col rounded-lg bg-slate-900 p-4 xl:grow">
      <div className="mb-2 flex flex-col gap-1">
        <h2 id="decision-log-heading" className="text-base font-semibold text-slate-100">
          Decision log
        </h2>
        {mode === 'headroom' && ordered.length > 0 && (
          <p className="text-sm text-slate-300">
            {drFired ? 'DR decisions first. ' : 'Newest first. '}
            Reasons cite demand percentile, carbon and peak added.
          </p>
        )}
      </div>
      {/* A focusable scroll region needs a role that may carry a name. */}
      <div
        ref={scrollRef}
        tabIndex={0}
        role="region"
        aria-label={drFired ? 'Decision log, demand-response decisions first' : 'Decision log, newest first'}
        className="h-72 overflow-x-hidden overflow-y-auto rounded pr-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300 xl:h-auto xl:min-h-36 xl:grow xl:basis-0"
      >
        {body}
      </div>
    </section>
  );
}

export default DecisionLog;
