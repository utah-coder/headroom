import { describe, expect, it } from 'vitest';
import type { Metrics, NaiveMetrics, Scenario } from '../types';
import type { DrPhase } from '../state/appState';
import { selectKpis } from './kpis';

// KPI selector (task 12.8, Requirements 18.6, 18.7, 20.5, 24.3).

const METRICS: Metrics = {
  headroom_kw: 5729.5,
  usd_saved: 29333.5,
  co2_avoided_t: 19.904,
  deadlines_met_pct: 97.9,
  inference_sla_pct: 100,
  dr_delivered_pct: 100,
  dr_seconds_to_full: 20,
  baseline_site_peak_kw: 14769.8,
  headroom_site_peak_kw: 13299.0,
};

const NAIVE: NaiveMetrics = {
  site_peak_kw: 34396.5,
  stress_peak_kw: 4703.2,
  usd_saved: -351613.41,
  co2_avoided_t: 21.273,
  deadlines_met_pct: 100,
  peak_hour_starts: 2,
  peak_jobs_running: 52,
  baseline_jobs_running: 18,
  capped_missed: 29,
  capped_total: 119,
  cap_kw: 14769.8,
};

// Three slots; the last two are stress slots. Baseline's stress peak is 13,594.
const row = (grid_kw: number, inference_kw = 4000) => ({ t: '', grid_kw, inference_kw, training_kw: grid_kw - inference_kw });

// selectKpis reads metrics, naive_metrics, dr_event.requested_shed_kw, and for
// cheapest hour the grid stress flags and the baseline and naive series.
function scenario(metrics: Metrics = METRICS, naiveInference = 4000): Scenario {
  return {
    metrics,
    naive_metrics: NAIVE,
    grid: [{ stress: false }, { stress: true }, { stress: true }],
    baseline: [row(14769.8), row(13594.0), row(12000)],
    naive: [row(34396.5), row(4703.2), row(4000, naiveInference)],
    dr_event: { start: '', end: '', requested_shed_kw: 4000, cap_kw: 7864, zoom: [] },
  } as unknown as Scenario;
}

const PHASES: DrPhase[] = ['idle', 'playing', 'done'];
const CO2_NOTE = 'average intensity, not marginal';

const values = (cards: ReturnType<typeof selectKpis>['cards']) =>
  Object.fromEntries(cards.map((c) => [c.id, c.value]));

describe('selectKpis in Baseline', () => {
  it.each(PHASES)('shows zeros and 100%% with DR %s', (dr) => {
    expect(values(selectKpis(scenario(), 'baseline', dr).cards)).toEqual({
      site_peak_kw: '14,770 kW',
      headroom_kw: '0 kW',
      usd_saved: '$0',
      co2_avoided_t: '0.0 t',
      inference_sla_pct: '100%',
      deadlines_met_pct: '100%',
    });
  });

  it('ignores the scenario metrics', () => {
    const zero = { ...METRICS, headroom_kw: 0, usd_saved: 0, co2_avoided_t: 0 };
    expect(selectKpis(scenario(zero), 'baseline', 'idle')).toEqual(selectKpis(scenario(), 'baseline', 'idle'));
  });

  it('labels the zeros as the reference point', () => {
    const { cards } = selectKpis(scenario(), 'baseline', 'idle');
    expect(cards[1].note).toBe('baseline is the reference point');
    expect(cards[2].note).toBe('baseline is the reference point');
  });

  it('shows the site peak with no change line', () => {
    expect(selectKpis(scenario(), 'baseline', 'idle').cards[0].change).toBeUndefined();
  });
});

describe('selectKpis in Headroom', () => {
  it('shows the scenario metrics, formatted', () => {
    expect(values(selectKpis(scenario(), 'headroom', 'idle').cards)).toEqual({
      site_peak_kw: '13,299 kW',
      headroom_kw: '5,730 kW',
      usd_saved: '$29,334',
      co2_avoided_t: '19.9 t',
      inference_sla_pct: '100%',
      deadlines_met_pct: '97%',
    });
  });

  it('keeps a leading minus on a negative saving', () => {
    const { cards } = selectKpis(scenario({ ...METRICS, usd_saved: -1234.4 }), 'headroom', 'idle');
    expect(cards[2].value).toBe('-$1,234');
  });

  it('gives the site peak change vs baseline', () => {
    expect(selectKpis(scenario(), 'headroom', 'idle').cards[0].change).toBe('-1,471 kW vs baseline');
  });
});

describe('selectKpis in Cheapest hour', () => {
  it('shows naive_metrics, and stress-hour headroom from the series', () => {
    expect(values(selectKpis(scenario(), 'naive', 'idle').cards)).toEqual({
      site_peak_kw: '34,397 kW',
      headroom_kw: '8,891 kW', // 13,594 − 4,703.2: lower at the grid's stress hours, shown as is
      usd_saved: '-$351,613',
      co2_avoided_t: '21.3 t',
      inference_sla_pct: '100%',
      deadlines_met_pct: '100%',
    });
  });

  it('gives the site peak change vs baseline', () => {
    expect(selectKpis(scenario(), 'naive', 'idle').cards[0].change).toBe('+19,627 kW vs baseline');
  });

  it('measures inference from the series, not a constant', () => {
    const { cards } = selectKpis(scenario(METRICS, 3999), 'naive', 'idle');
    expect(cards.find((c) => c.id === 'inference_sla_pct')?.value).toBe('66%');
  });

  it('never shows the demand-response strip', () => {
    expect(selectKpis(scenario(), 'naive', 'done').dr).toBeNull();
  });
});

describe('selectKpis cards', () => {
  it.each([
    ['baseline', 'idle'],
    ['naive', 'idle'],
    ['headroom', 'idle'],
    ['headroom', 'done'],
  ] as const)('are the six cards in order, each labelled, in %s / %s', (mode, dr) => {
    const { cards } = selectKpis(scenario(), mode, dr);
    expect(cards.map((c) => c.id)).toEqual([
      'site_peak_kw',
      'headroom_kw',
      'usd_saved',
      'co2_avoided_t',
      'inference_sla_pct',
      'deadlines_met_pct',
    ]);
    for (const c of cards) {
      expect(c.label.length).toBeGreaterThan(0);
      expect(c.note?.length).toBeGreaterThan(0);
    }
    expect(cards[0].note).toBe('grid connection the site needs');
    expect(cards[3].note).toBe(CO2_NOTE); // Requirement 24.3, in every state
  });
});

describe('selectKpis DR strip', () => {
  it.each(['idle', 'playing'] as const)('is absent while DR is %s', (dr) => {
    expect(selectKpis(scenario(), 'headroom', dr).dr).toBeNull();
  });

  it('appears when DR is done', () => {
    expect(selectKpis(scenario(), 'headroom', 'done').dr).toEqual([
      { id: 'requested_shed_kw', label: 'Demand-response cut requested', value: '4,000 kW' },
      { id: 'dr_delivered_pct', label: 'Cut delivered', value: '100%' },
      { id: 'dr_seconds_to_full', label: 'Seconds to full response', value: '20 s' },
    ]);
  });
});
