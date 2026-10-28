import { useId } from 'react';
import type { Scenario } from '../types';
import type { DrPhase, Mode } from '../state/appState';
import { formatKw } from '../lib/format';
import { hhmmDenver } from '../lib/time';
import { ZoomPanel } from './ZoomPanel';

// DR panel (Requirements 20.1, 20.2, 20.9). The "Fire demand-response event"
// button shows in both modes. In Baseline it is aria-disabled rather than
// disabled, so it stays in the tab order (Requirement 18.8); a click or
// Enter/Space does nothing, and the helper text is linked by aria-describedby.
// Activations while playing are ignored by the reducer (Requirement 20.9).

const LABEL = 'Fire demand-response event';
const BASELINE_HELP = 'Switch to Headroom to dispatch storage';

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
  const disabled = mode === 'baseline';
  const { dr_event } = scenario;
  const drHours = Math.round((Date.parse(dr_event.end) - Date.parse(dr_event.start)) / 3_600_000);

  return (
    <section aria-labelledby="dr-panel-heading" className="flex flex-col gap-3 rounded-lg bg-slate-900 p-4">
      <div className="flex flex-col gap-1">
        <h2 id="dr-panel-heading" className="text-base font-semibold text-slate-100">
          Demand response
        </h2>
        <p className="text-sm text-slate-300">
          At {hhmmDenver(dr_event.start)} the utility asks the site to cut {formatKw(dr_event.requested_shed_kw)} for{' '}
          {drHours} h.{' '}
          {disabled
            ? 'Baseline never uses storage or shifts jobs, so it cannot answer.'
            : 'Headroom pauses or moves training, and the flywheel and battery cover the rest.'}
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
        {disabled && (
          <p id={helpId} className="text-sm text-slate-300">
            {BASELINE_HELP}
          </p>
        )}
      </div>

      <ZoomPanel
        drEvent={dr_event}
        dr={dr}
        drRun={drRun}
        onDone={onDone}
        idleText={
          disabled
            ? 'In Headroom mode, this shows the first 10 minutes of the event second by second.'
            : 'Fire the event to watch grid draw drop to the cap as the flywheel and battery take over.'
        }
      />
    </section>
  );
}

export default DrPanel;
