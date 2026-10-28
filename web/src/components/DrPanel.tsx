import { useId } from 'react';
import type { Scenario } from '../types';
import type { DrPhase, Mode } from '../state/appState';
import { formatKw } from '../lib/format';
import { hhmmDenver } from '../lib/time';
import { ZoomPanel } from './ZoomPanel';

// DR panel (Requirements 20.1, 20.2, 20.9). The "Fire demand-response event"
// button shows in every mode. Outside Headroom it is aria-disabled rather than
// disabled, so it stays in the tab order (Requirement 18.8); a click or
// Enter/Space does nothing, and the helper text is linked by aria-describedby.
// Activations while playing are ignored by the reducer (Requirement 20.9).

const LABEL = 'Fire demand-response event';
const HELP: Record<Exclude<Mode, 'headroom'>, string> = {
  baseline: 'Switch to Headroom to dispatch storage',
  naive: 'Cheapest hour has no storage and doesn’t respond to the utility.',
};
const IDLE_TEXT: Record<Mode, string> = {
  baseline:
    'Baseline never uses storage or shifts jobs, so it cannot respond. In Headroom mode, this shows the first 10 minutes of the event second by second.',
  naive:
    'Cheapest hour keeps every job at the start it picked, so it cannot respond. In Headroom mode, this shows the first 10 minutes of the event second by second.',
  headroom:
    'Fire the event: Headroom pauses or moves training, and grid draw drops to the cap as the flywheel and battery take over.',
};

interface Props {
  scenario: Scenario;
  mode: Mode;
  dr: DrPhase;
  drRun: number;
  /** Dispatches fireDr; the caller decides instant vs animated. */
  onFire: () => void;
  /** Dispatches drComplete when playback ends. Must be stable. */
  onDone: () => void;
}

export function DrPanel({ scenario, mode, dr, drRun, onFire, onDone }: Props) {
  const helpId = `${useId()}-dr-help`;
  const disabled = mode !== 'headroom';
  const { dr_event } = scenario;
  const drHours = Math.round((Date.parse(dr_event.end) - Date.parse(dr_event.start)) / 3_600_000);

  return (
    <section aria-labelledby="dr-panel-heading" className="flex flex-col gap-3 rounded-lg bg-slate-900 p-4">
      <div className="flex flex-col gap-1">
        <h2 id="dr-panel-heading" className="text-base font-semibold text-slate-100">
          Demand response
        </h2>
        {/* What demand response is, then this event. What each mode does about it is
            said by the helper text and the zoom's empty state below. */}
        <p className="text-sm text-slate-300">
          When the grid is strained, the utility asks large customers to use less power for a while. Here it asks this
          site to cut {formatKw(dr_event.requested_shed_kw)} for {drHours} h from {hhmmDenver(dr_event.start)}.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <button
          type="button"
          aria-disabled={disabled ? 'true' : undefined}
          aria-describedby={disabled ? helpId : undefined}
          onClick={() => {
            if (!disabled) onFire();
          }}
          className={
            'rounded-md px-4 py-2 text-sm font-semibold transition-colors ' +
            'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300 ' +
            (disabled
              ? 'cursor-not-allowed border border-slate-700 bg-slate-800 text-slate-400'
              : 'bg-sky-300 text-slate-950 hover:bg-sky-200')
          }
        >
          {LABEL}
        </button>
        {mode !== 'headroom' && (
          <p id={helpId} className="text-sm text-slate-300">
            {HELP[mode]}
          </p>
        )}
      </div>

      <ZoomPanel
        drEvent={dr_event}
        dr={dr}
        drRun={drRun}
        onDone={onDone}
        idleText={IDLE_TEXT[mode]}
      />
    </section>
  );
}

export default DrPanel;
