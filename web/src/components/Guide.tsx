import { useId, useState } from 'react';
import type { Scenario, ScenarioName } from '../types';
import type { DrPhase, Mode } from '../state/appState';
import { formatKw } from '../lib/format';
import { RouteGlossary } from './RouteChip';

// Guide panel under the header: what the project is, the three demo steps,
// and a "How it works" section. The steps are buttons that drive the same
// state as the Mode toggle and the DR button, and the current step follows
// that state, so the panel stays in sync however the presenter gets there.
// Figures come from the Scenario where it carries them; the tariff and
// carbon price match sim/config.py (DEMAND_CHARGE_PER_KW, CARBON_SHADOW_USD_PER_T).

export type GuideStep = 1 | 2 | 3;

const DEMAND_CHARGE_PER_KW = 18;
const CARBON_SHADOW_USD_PER_T = 50;

const INTRO =
  'A simulated 10 MW AI data center in Utah decides, every 15 minutes, where each training ' +
  "job's electricity comes from, so the grid gets capacity back when it is busiest.";

const SCENARIO_TEXT: Record<ScenarioName, string> = {
  peak: "Peak week: the 48 hours around PacifiCorp East's highest demand hour of summer 2026.",
  sun: "Sunny day: the 48 hours with PacifiCorp East's highest solar share of summer 2026.",
};

interface Props {
  scenario: Scenario;
  mode: Mode;
  dr: DrPhase;
  onStep: (step: GuideStep) => void;
  /** Hides the panel; the Header then offers "Show guide". */
  onHide: () => void;
}

export function currentStep(mode: Mode, dr: DrPhase): GuideStep {
  if (mode === 'baseline') return 1;
  return dr === 'idle' ? 2 : 3;
}

export function Guide({ scenario, mode, dr, onStep, onHide }: Props) {
  const [open, setOpen] = useState(false);
  const detailsId = `${useId()}-how`;
  const current = currentStep(mode, dr);

  const { dr_event, metrics } = scenario;
  const drHours = Math.round((Date.parse(dr_event.end) - Date.parse(dr_event.start)) / 3_600_000);

  const steps: { n: GuideStep; title: string; text: string }[] = [
    {
      n: 1,
      title: 'Baseline: today',
      text: 'Every training job draws from the grid the moment it arrives, even at the grid’s peak.',
    },
    {
      n: 2,
      title: 'Turn on Headroom',
      text: 'Jobs take their cheapest route once energy, carbon and peak demand are priced in. Inference never moves.',
    },
    {
      n: 3,
      title: 'Fire a demand-response event',
      text:
        `The utility asks the site to shed ${formatKw(dr_event.requested_shed_kw)} for ${drHours} h. ` +
        `The flywheel answers at once, the battery within ${metrics.dr_seconds_to_full} s.`,
    },
  ];

  const linkClass =
    'rounded text-sm font-semibold text-sky-300 underline underline-offset-2 hover:text-sky-200 ' +
    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300';

  return (
    <section
      aria-labelledby="guide-heading"
      className="flex flex-col gap-3 rounded-lg border border-slate-800 bg-slate-900 p-3"
    >
      <div className="grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,5fr)]">
        <div className="flex flex-col items-start gap-1">
          <h2 id="guide-heading" className="sr-only">
            About this demo
          </h2>
          <p className="text-sm text-slate-100">{INTRO}</p>
          <p className="text-sm text-slate-300">{SCENARIO_TEXT[scenario.meta.scenario]}</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <button
              type="button"
              aria-expanded={open}
              aria-controls={detailsId}
              onClick={() => setOpen((o) => !o)}
              className={linkClass}
            >
              {open ? 'Hide how it works' : 'How it works'}
            </button>
            <button type="button" onClick={onHide} className={linkClass}>
              Hide guide
            </button>
          </div>
        </div>

        <ol aria-label="Demo steps" className="grid gap-2 sm:grid-cols-3">
          {steps.map(({ n, title, text }) => {
            const isCurrent = n === current;
            return (
              <li key={n} className="min-w-0">
                <button
                  type="button"
                  aria-current={isCurrent ? 'step' : undefined}
                  onClick={() => onStep(n)}
                  className={
                    'flex h-full w-full flex-col gap-1 rounded-md border px-3 py-2 text-left transition-colors ' +
                    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300 ' +
                    (isCurrent ? 'border-sky-300 bg-slate-800' : 'border-slate-700 hover:bg-slate-800')
                  }
                >
                  <span className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                    <span
                      aria-hidden="true"
                      className={
                        'inline-flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-bold ' +
                        (isCurrent ? 'bg-sky-300 text-slate-950' : 'bg-slate-700 text-slate-100')
                      }
                    >
                      {n}
                    </span>
                    {title}
                  </span>
                  <span className="text-sm text-slate-300">{text}</span>
                </button>
              </li>
            );
          })}
        </ol>
      </div>

      {open && (
        <div
          id={detailsId}
          className="grid gap-x-6 gap-y-4 border-t border-slate-800 pt-3 text-sm text-slate-300 md:grid-cols-2 xl:grid-cols-4"
        >
          <div className="flex flex-col gap-2">
            <h3 className="font-semibold text-slate-100">The four routes</h3>
            <RouteGlossary />
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="font-semibold text-slate-100">How a route is chosen</h3>
            <p>
              Every 15 minutes, each waiting training job takes the route with the lowest total cost: the energy
              price, plus ${DEMAND_CHARGE_PER_KW} for every kW it would add to the site’s monthly peak, plus carbon
              at ${CARBON_SHADOW_USD_PER_T} a tonne, plus a penalty that grows as its deadline nears.
            </p>
            <p>
              The scheduler plans 24 hours ahead from the grid’s own day-ahead forecast. It never sees what actually
              happens next. Inference, the customer-facing work, is never paused or moved.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="font-semibold text-slate-100">What the numbers mean</h3>
            <p>
              <span className="font-semibold text-slate-100">Headroom returned:</span> how much lower the site’s
              highest grid draw is than baseline during the grid’s stress hours, the busiest 5% of the 48 hours by
              PacifiCorp East demand.
            </p>
            <p>
              <span className="font-semibold text-slate-100">$ saved:</span> energy plus demand charges over the 48
              hours, after the fees for moving jobs.{' '}
              <span className="font-semibold text-slate-100">CO₂ avoided:</span> using each hour’s average grid
              carbon intensity, not the marginal plant’s.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="font-semibold text-slate-100">What is real</h3>
            <p>
              Real hourly EIA-930 data: PacifiCorp East demand and its day-ahead forecast, and the fuel mix behind
              carbon intensity in PacifiCorp East, the Pacific Northwest (BPAT) and California (CISO).
            </p>
            <p>
              The data center, its jobs, the battery and flywheel, and the tariff are simulated and illustrative.
              Every number is computed ahead of time; this page makes no network calls after it loads.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}

export default Guide;
