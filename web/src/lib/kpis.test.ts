import { describe, expect, it } from 'vitest';
import type { Metrics, Scenario } from '../types';
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
};

// selectKpis reads only metrics and dr_event.requested_shed_kw.
function scenario(metrics: Metrics = METRICS): Scenario {
  return {
    metrics,
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
    expect(cards[0].note).toBe('baseline is the reference point');
    expect(cards[1].note).toBe('baseline is the reference point');
  });
});

describe('selectKpis in Headroom', () => {
  it('shows the scenario metrics, formatted', () => {
    expect(values(selectKpis(scenario(), 'headroom', 'idle').cards)).toEqual({
      headroom_kw: '5,730 kW',
      usd_saved: '$29,334',
      co2_avoided_t: '19.9 t',
      inference_sla_pct: '100%',
      deadlines_met_pct: '97%',
    });
  });

  it('keeps a leading minus on a negative saving', () => {
    const { cards } = selectKpis(scenario({ ...METRICS, usd_saved: -1234.4 }), 'headroom', 'idle');
    expect(cards[1].value).toBe('-$1,234');
  });
});

describe('selectKpis cards', () => {
  it.each([
    ['baseline', 'idle'],
    ['headroom', 'idle'],
    ['headroom', 'done'],
  ] as const)('are the five cards in order, each labelled, in %s / %s', (mode, dr) => {
    const { cards } = selectKpis(scenario(), mode, dr);
    expect(cards.map((c) => c.id)).toEqual([
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
    expect(cards[2].note).toBe(CO2_NOTE); // Requirement 24.3, in every state
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
