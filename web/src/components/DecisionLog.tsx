import { useEffect, useId, useMemo, useRef, type ReactNode } from 'react';
import type { Decision, Scenario } from '../types';
import type { DrPhase, Mode } from '../state/appState';
import { formatKw } from '../lib/format';
import { hhmmDenver, weekdayDenver } from '../lib/time';
import { decisionKey, orderDecisions } from '../lib/orderDecisions';
import { OptionBreakdown } from './OptionBreakdown';
import { RouteChip, RouteGlossary } from './RouteChip';

// Decision_Log panel (Requirements 20.6, 21.1–21.7). In Headroom each row is a
// button that opens how its routes were priced; one row is open at a time, and
// App marks its slot on the main chart (DECISIONS.md, "Explainable decisions").

interface Props {
  scenario: Scenario;
  mode: Mode;
  dr: DrPhase;
  /** decisionKey of the open row, or null. */
  openKey: string | null;
  onToggle: (key: string) => void;
}

const BASELINE_TEXT = 'Baseline: every job runs on arrival from the grid.';

/** Cheapest hour records no decisions; one paragraph says what happened instead. */
function naiveText({ naive, naive_metrics: nm }: Scenario): string {
  const peak = naive.reduce((best, r) => (r.grid_kw > best.grid_kw ? r : best), naive[0]);
  return (
    'No coordination: each job picked its own cheapest, cleanest start. ' +
    `${nm.peak_jobs_running} jobs were running at once at ${hhmmDenver(peak.t)} on ${weekdayDenver(peak.t)}, ` +
    `against ${nm.baseline_jobs_running} at that moment today. ` +
    `Held to today’s ${formatKw(nm.cap_kw)} connection instead, ${nm.capped_missed} of ${nm.capped_total} jobs ` +
    'would miss their deadline.'
  );
}
const EMPTY_TEXT = 'No decisions for this scenario.';

/** DEFER targets are slot times (shown in Denver HH:MM); MOVE_REGION targets are regions. */
function targetLabel(target: Decision['target']): string | null {
  if (target === null) return null;
  if (target === 'BPAT' || target === 'CISO') return target;
  return hhmmDenver(target);
}

function DecisionRow({ d, open, onToggle }: { d: Decision; open: boolean; onToggle: (key: string) => void }) {
  const target = targetLabel(d.target);
  const panelId = `${useId()}-options`;
  // A button may hold only phrasing content, so the rows are spans.
  return (
    <li className="border-b border-slate-800 py-1 last:border-b-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => onToggle(decisionKey(d))}
        className={`block w-full cursor-pointer rounded px-2 py-1 text-left hover:bg-slate-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300 ${
          open ? 'bg-slate-800' : ''
        }`}
      >
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span aria-hidden="true" className={`inline-block w-3 text-slate-300 ${open ? 'rotate-90' : ''}`}>
            ›
          </span>
          <time dateTime={d.t} className="font-mono tabular-nums text-slate-300">
            {hhmmDenver(d.t)}
          </time>
          <span className="font-mono text-slate-100">job#{d.job}</span>
          <RouteChip route={d.route} />
          {target !== null && <span className="font-mono text-slate-300">→ {target}</span>}
        </span>
        <span className="mt-1 block text-sm break-words whitespace-normal text-slate-300">{d.reason}</span>
      </button>
      {open && (
        <div id={panelId}>
          <OptionBreakdown decision={d} />
        </div>
      )}
    </li>
  );
}

export function DecisionLog({ scenario, mode, dr, openKey, onToggle }: Props) {
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
  } else if (mode === 'naive') {
    body = <p className="text-sm text-slate-300">{naiveText(scenario)}</p>;
  } else if (ordered.length === 0) {
    body = <p className="text-sm text-slate-300">{EMPTY_TEXT}</p>;
  } else {
    body = (
      <ol>
        {ordered.map((d) => {
          const key = decisionKey(d);
          return <DecisionRow key={key} d={d} open={key === openKey} onToggle={onToggle} />;
        })}
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
            {drFired ? 'Demand-response decisions first. ' : 'Newest first. '}
            Click a decision to see how its four options were priced.
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
