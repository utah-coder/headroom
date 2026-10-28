import { useId, useState } from 'react';
import type { Metrics, Scenario, ScenarioName } from '../types';
import type { DrPhase, Mode } from '../state/appState';
import { formatKw } from '../lib/format';
import { RouteGlossary } from './RouteChip';

// Guide panel under the header: what the project is, the three demo steps,
// a plain-language explanation (data, engine, scorecard) with what is real and
// why it matters, and a collapsible "Technical details" section. The steps are buttons that drive the same
// state as the Mode toggle and the DR button, and the current step follows
// that state, so the panel stays in sync however the presenter gets there.
// Figures come from the Scenario where it carries them; the tariff and
// carbon price match sim/config.py (DEMAND_CHARGE_PER_KW, CARBON_SHADOW_USD_PER_T).

export type GuideStep = 1 | 2 | 3;

const DEMAND_CHARGE_PER_KW = 18;
const CARBON_SHADOW_USD_PER_T = 50;

const INTRO =
  "Headroom is a decision engine for a data center's electricity. It replays real conditions on Utah's " +
  'power grid from summer 2026. Every 15 minutes it decides where each AI training job should get its ' +
  "power: the grid, the site's own batteries, a later hour, or a data center in another state. The goal " +
  "is lower cost, cleaner power and less strain on the grid when it's busiest.";

/** The plain-language explanation: the data, the engine, and how the result is scored. */
function plainSteps(metrics: Metrics): { label: string; title: string; text: string }[] {
  const nothingMissed = metrics.inference_sla_pct === 100 && metrics.deadlines_met_pct === 100;
  return [
    {
      label: 'Input',
      title: 'Real grid data',
      text:
        'We pulled four months of hourly data, June to September 2026, from the U.S. Energy Information ' +
        'Administration for PacifiCorp East, the grid that serves most of Utah. It shows how much power people ' +
        'used, how much the utility expected them to use the next day, and which kinds of plants were running: ' +
        "coal, gas, solar, wind and hydro. That mix tells us how much carbon each hour's electricity carried.",
    },
    {
      label: 'Engine',
      title: 'A decision engine',
      text:
        'Every 15 minutes, the engine looks at each training job waiting to run and puts a price on each of its ' +
        "four options. The price counts the cost of the electricity, the extra charge if the job pushes the site's " +
        'peak higher, the carbon it would emit, and the risk of missing its deadline. The job takes the cheapest ' +
        "option. Like a real operator, the engine knows only the utility's forecast for the next day, not what " +
        'will actually happen.',
    },
    {
      label: 'Scorecard',
      title: 'A scorecard',
      text:
        'We then replay what really happened on the grid and compare two versions of the same data center. One ' +
        'runs every job the moment it arrives, which is how most sites work today. The other lets Headroom ' +
        'decide. The difference is the result: grid capacity freed in the busiest hours, dollars saved and ' +
        'carbon avoided.' +
        (nothingMissed ? ' Customer-facing work never slows and no deadline is missed.' : ''),
    },
  ];
}

const WHAT_IS_REAL =
  "The grid data is real: demand, the utility's forecast and the power-plant mix. The data center, its jobs, " +
  "its batteries and its electricity rates are simulated. There's no real data center behind this, so we " +
  'built a realistic one and labeled every made-up number.';

const WHY_IT_MATTERS =
  'Data centers want more power than the grid can easily deliver, but the grid is only stretched for a few ' +
  'hours at a time. A data center that moves its flexible work out of those hours puts less strain on the ' +
  'grid, pays less and runs on cleaner power. Headroom shows how those decisions get made, one job at a ' +
  "time. Today it replays a past summer so you can watch its hardest days. The same engine could run on EIA's " +
  'live feed.';

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
  const detailsId = `${useId()}-tech`;
  const current = currentStep(mode, dr);

  const { dr_event, metrics } = scenario;
  const plain = plainSteps(metrics);
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
        `The utility asks the site to cut ${formatKw(dr_event.requested_shed_kw)} for ${drHours} h to ease the grid. ` +
        'The flywheel responds at once.',
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
              {open ? 'Hide technical details' : 'Technical details'}
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

      <div className="flex flex-col gap-2 border-t border-slate-800 pt-3">
        <h3 className="text-sm font-semibold text-slate-100">How Headroom works, in plain terms</h3>
        <ol aria-label="How Headroom works" className="grid gap-2 md:grid-cols-3">
          {plain.map(({ label, title, text }) => (
            <li
              key={label}
              className="flex min-w-0 flex-col gap-1 rounded-md border border-slate-800 bg-slate-950 px-3 py-2"
            >
              <span className="text-xs font-semibold uppercase tracking-wide text-sky-300">{label}</span>
              <span className="text-sm font-semibold text-slate-100">{title}</span>
              <p className="text-sm text-slate-300">{text}</p>
            </li>
          ))}
        </ol>
        <div className="grid gap-x-6 gap-y-2 text-sm text-slate-300 md:grid-cols-2">
          <p>
            <span className="font-semibold text-slate-100">What’s real, what’s simulated:</span> {WHAT_IS_REAL}
          </p>
          <p>
            <span className="font-semibold text-slate-100">Why it matters:</span> {WHY_IT_MATTERS}
          </p>
        </div>
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
            <p>
              <span className="font-semibold text-slate-100">Demand response:</span> a utility’s request that large
              customers cut their power use for a few hours when the grid is strained. After the event, the KPI row
              shows the cut requested, the share delivered, and the seconds until the battery reached full output.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="font-semibold text-slate-100">Data sources</h3>
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
