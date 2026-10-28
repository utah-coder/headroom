import { describe, expect, it } from 'vitest';
import type { Decision, OptionCost } from '../types';
import { formatUsdCents } from './format';
import { optionRows } from './optionRows';

// OptionBreakdown layout (DECISIONS.md, "Explainable decisions").

function opt(route: OptionCost['route'], parts: Partial<OptionCost>, offered = true): OptionCost {
  const p = {
    energy_usd: 0,
    carbon_usd: 0,
    peak_usd: 0,
    deadline_usd: 0,
    storage_usd: 0,
    fee_usd: 0,
    ...parts,
  };
  const total = p.energy_usd + p.carbon_usd + p.peak_usd + p.deadline_usd + p.storage_usd + p.fee_usd;
  return { route, target: null, offered, total_usd: total, ...p };
}

const DECISION: Decision = {
  t: '2026-07-20T18:00:00Z',
  job: '4001',
  route: 'DEFER',
  target: '2026-07-20T20:00:00Z',
  reason: 'r',
  options: [
    opt('GRID_NOW', { energy_usd: 60, carbon_usd: 20, peak_usd: 120 }),
    { ...opt('DEFER', { energy_usd: 40, carbon_usd: 10, deadline_usd: 50 }), target: '2026-07-20T20:00:00Z' },
  ],
};

describe('optionRows', () => {
  it('lists all four routes in route order', () => {
    expect(optionRows(DECISION).map((r) => r.route)).toEqual(['GRID_NOW', 'STORAGE_NOW', 'DEFER', 'MOVE_REGION']);
  });

  it('marks the chosen route and the routes that were not priced', () => {
    const rows = optionRows(DECISION);
    expect(rows.map((r) => r.chosen)).toEqual([false, false, true, false]);
    expect(rows.map((r) => r.offered)).toEqual([true, false, true, false]);
    expect(rows[1].option).toBeNull();
    expect(rows[1].segments).toEqual([]);
  });

  it('scales every bar to the largest total and keeps only non-zero parts', () => {
    const [grid, , defer] = optionRows(DECISION);
    expect(grid.segments.map((s) => [s.key, s.pct])).toEqual([
      ['energy_usd', 30],
      ['carbon_usd', 10],
      ['peak_usd', 60],
    ]);
    expect(defer.segments.map((s) => s.label)).toEqual(['Energy', 'Carbon', 'Deadline risk']);
    expect(defer.segments.reduce((a, s) => a + s.pct, 0)).toBeCloseTo(50, 9);
  });

  it('draws a GRID_NOW price that was over the budget on the same scale', () => {
    const d: Decision = {
      ...DECISION,
      route: 'MOVE_REGION',
      target: 'BPAT',
      options: [
        opt('GRID_NOW', { energy_usd: 300 }, false),
        { ...opt('MOVE_REGION', { energy_usd: 75, fee_usd: 75 }), target: 'BPAT' },
      ],
    };
    const [grid, , , move] = optionRows(d);
    expect(grid.offered).toBe(false);
    expect(grid.segments[0].pct).toBe(100);
    expect(move.segments.map((s) => s.pct)).toEqual([25, 25]);
  });
});

describe('formatUsdCents', () => {
  it.each([
    [955.79, '$955.79'],
    [0, '$0.00'],
    [0.5, '$0.50'],
    [1234.5, '$1,234.50'],
    [-0.004, '$0.00'],
    [-12.3, '-$12.30'],
    [NaN, '–'],
  ])('%s → %s', (n, s) => {
    expect(formatUsdCents(n)).toBe(s);
  });
});
