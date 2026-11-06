import { describe, expect, it } from 'vitest';
import { isStressFile } from '../data/loadStress';
import type { BandPoint, StressFamily, StressRange } from '../stressTypes';
import { bandChartData, bandHits, demandChargeSharePct, positionPct, rangeText, runsText, savedDomain } from './stressView';

const range = (p10: number, p50: number, p90: number): StressRange => ({ p10, p50, p90, min: p10, max: p90 });

const family = (saved: StressRange, demandCharge = range(0, 0, 0)): StressFamily => ({
  id: 'x',
  label: 'x',
  samples: 100,
  usd_saved: saved,
  demand_charge_saved_usd: demandCharge,
  bill_usd: range(0, 0, 0),
  baseline_bill_usd: range(0, 0, 0),
  co2_avoided_t: range(0, 0, 0),
  headroom_kw: range(0, 0, 0),
  site_peak_kw: range(0, 0, 0),
  negative_runs: 0,
  deadlines_missed_p50: 0,
  deadlines_missed_max: 0,
  baseline_deadlines_missed_max: 0,
  dr_missed_runs: 0,
  dr_shortfall_max_kw: 0,
});

const point = (actual: number, low: number | null, high: number | null): BandPoint => ({
  t: '2026-07-01T00:00:00Z',
  actual,
  forecast: actual,
  low,
  high,
});

describe('savedDomain', () => {
  it('always includes zero, so a loss and a saving read on one scale', () => {
    expect(savedDomain([family(range(100, 200, 300))])).toEqual([0, 300]);
    expect(savedDomain([family(range(-50, 10, 30)), family(range(5, 6, 90))])).toEqual([-50, 90]);
  });
});

describe('positionPct', () => {
  it('places values on the scale', () => {
    expect(positionPct(0, [-100, 100])).toBe(50);
    expect(positionPct(100, [-100, 100])).toBe(100);
  });
  it('does not divide by zero on an empty scale', () => {
    expect(positionPct(0, [0, 0])).toBe(0);
  });
});

describe('rangeText', () => {
  it('shows one figure for a single run and a range otherwise', () => {
    expect(rangeText(range(5, 5, 5), 1)).toBe('$5');
    expect(rangeText(range(-805, 12035, 33259), 100)).toBe('-$805 to $33,259');
  });
});

describe('bandHits', () => {
  it('counts actuals inside the band and skips hours with no band', () => {
    expect(bandHits([point(5, 1, 9), point(10, 1, 9), point(5, null, null)])).toEqual({ inside: 1, total: 2 });
  });
  it('treats the band edges as inside', () => {
    expect(bandHits([point(1, 1, 9), point(9, 1, 9)]).inside).toBe(2);
  });
});

describe('bandChartData', () => {
  it('pairs the band for the range area, and leaves a gap where there is none', () => {
    const [a, b] = bandChartData([point(5, 1, 9), point(5, null, null)]);
    expect(a.band).toEqual([1, 9]);
    expect(b.band).toBeNull();
  });
});

describe('runsText', () => {
  it('says yes or no for a single run and a count otherwise', () => {
    expect(runsText(1, 1)).toBe('Yes');
    expect(runsText(0, 1)).toBe('No');
    expect(runsText(8, 100)).toBe('8 of 100');
  });
});

describe('demandChargeSharePct', () => {
  it('is the demand charge part of the money saved', () => {
    expect(demandChargeSharePct(family(range(0, 200, 0), range(0, 180, 0)))).toBe(90);
  });
  it('has no share when nothing was saved', () => {
    expect(demandChargeSharePct(family(range(0, -5, 0)))).toBeNull();
  });
});

describe('isStressFile', () => {
  it('accepts the top-level keys and rejects anything else, such as the HTML fallback', () => {
    expect(isStressFile({ meta: 1, carbon_accuracy: 1, demand_accuracy: 1, scenarios: 1, replay: 1 })).toBe(true);
    expect(isStressFile({ meta: 1 })).toBe(false);
    expect(isStressFile(null)).toBe(false);
  });
});
