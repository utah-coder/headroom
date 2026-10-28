import { describe, expect, it } from 'vitest';
import { contrastRatio, ROUTE_COLORS, TEXT_PAIRS } from './colors';

// Palette contrast (task 11.10, Requirement 22.7): every text/background pair
// the UI draws reaches WCAG AA, 4.5:1. TEXT_PAIRS is the list of those pairs;
// add a pair there when a component draws a new one.

const AA = 4.5;

describe('contrastRatio', () => {
  it('matches the WCAG reference values', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(contrastRatio('#ffffff', '#000000')).toBeCloseTo(21, 5);
    expect(contrastRatio('#777777', '#777777')).toBe(1);
    // #767676 on white is the usual "just passes AA" grey.
    expect(contrastRatio('#767676', '#ffffff')).toBeGreaterThanOrEqual(AA);
    expect(contrastRatio('#777777', '#ffffff')).toBeLessThan(AA);
  });

  it('accepts three-digit hex', () => {
    expect(contrastRatio('#fff', '#000')).toBeCloseTo(21, 5);
  });
});

describe('TEXT_PAIRS', () => {
  it('has unique names', () => {
    const names = TEXT_PAIRS.map((p) => p.name);
    expect(new Set(names).size).toBe(names.length);
  });

  it('covers every route colour on every background', () => {
    for (const route of Object.keys(ROUTE_COLORS)) {
      expect(TEXT_PAIRS.filter((p) => p.name.startsWith(`${route} on `))).toHaveLength(2);
    }
  });

  it.each(TEXT_PAIRS.map((p) => [p.name, p] as const))('%s reaches 4.5:1', (_name, pair) => {
    expect(contrastRatio(pair.fg, pair.bg)).toBeGreaterThanOrEqual(AA);
  });
});
