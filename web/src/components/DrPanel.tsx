import { useId } from 'react';
import type { Scenario } from '../types';
import type { DrPhase, Mode } from '../state/appState';
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

  return (
    <section aria-labelledby="dr-panel-heading" className="flex flex-col gap-3 rounded-lg bg-slate-900 p-4">
      <h2 id="dr-panel-heading" className="text-base font-semibold text-slate-100">
        Demand response
      </h2>

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

      <ZoomPanel drEvent={scenario.dr_event} dr={dr} drRun={drRun} onDone={onDone} />
    </section>
  );
}

export default DrPanel;
