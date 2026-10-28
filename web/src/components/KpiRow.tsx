import type { Scenario } from '../types';
import type { DrPhase, Mode } from '../state/appState';
import { selectKpis } from '../lib/kpis';

// KPI row (Requirements 18.5–18.7, 18.10, 20.5, 24.3). Five labelled cards;
// values are 28 px below 768 px, 36 px from 768 px and 48 px from 1280 px.
// Text uses the contrast-checked slate palette (lib/colors.ts) on slate-900.

export interface KpiRowProps {
  scenario: Scenario;
  mode: Mode;
  dr: DrPhase;
}

export function KpiRow({ scenario, mode, dr }: KpiRowProps) {
  const { cards, dr: drStrip } = selectKpis(scenario, mode, dr);

  return (
    <section aria-label="Key metrics" className="flex flex-col gap-2">
      <dl className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-5 xl:gap-3">
        {cards.map((card) => (
          <div
            key={card.id}
            className="flex min-w-0 flex-col gap-1 rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 xl:px-4"
          >
            <dt className="text-sm text-slate-300">{card.label}</dt>
            <dd className="text-[28px] font-semibold leading-tight tabular-nums break-words text-slate-100 md:text-4xl xl:text-5xl xl:leading-none">
              {card.value}
            </dd>
            {card.note && <dd className="text-xs text-slate-300">{card.note}</dd>}
          </div>
        ))}
      </dl>

      {/* Always mounted so screen readers announce the DR values when they appear. */}
      <div aria-live="polite">
        {drStrip && (
          <dl
            aria-label="Demand-response result"
            className="flex flex-wrap gap-x-6 gap-y-1 rounded-lg border border-slate-800 bg-slate-900 px-3 py-2 xl:px-4"
          >
            {drStrip.map((item) => (
              <div key={item.id} className="flex items-baseline gap-2">
                <dt className="text-sm text-slate-300">{item.label}</dt>
                <dd className="text-lg font-semibold tabular-nums text-slate-100">{item.value}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </section>
  );
}
