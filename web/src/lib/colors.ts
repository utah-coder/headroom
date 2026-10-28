// Palette and WCAG contrast helper (Requirements 21.3, 22.7).
// Hex values are the sRGB Tailwind shades named in the design, so Recharts
// (which needs literal colours) and the contrast test use the same values.

export const SLATE = {
  950: '#020617', // page background
  900: '#0f172a', // panel background
  800: '#1e293b', // raised surfaces, borders
  700: '#334155', // grid lines, dividers (not used for text)
  400: '#94a3b8', // muted text
  300: '#cbd5e1', // secondary text
  100: '#f1f5f9', // primary text
} as const;

/** Route names, kept local so this module does not depend on types.ts. */
export type RouteName = 'GRID_NOW' | 'STORAGE_NOW' | 'DEFER' | 'MOVE_REGION';

/** One colour per route, the same in every scenario (Requirement 21.3). */
export const ROUTE_COLORS: Readonly<Record<RouteName, string>> = {
  GRID_NOW: '#7dd3fc', // sky-300
  STORAGE_NOW: '#6ee7b7', // emerald-300
  DEFER: '#fcd34d', // amber-300
  MOVE_REGION: '#f0abfc', // fuchsia-300
};

/** Cost parts of a priced route (OptionBreakdown bars). Storage and fees reuse their route's hue. */
export const COST_PART_COLORS = {
  energy_usd: '#60a5fa', // blue-400
  carbon_usd: '#a8a29e', // stone-400
  peak_usd: '#f87171', // red-400
  deadline_usd: '#fdba74', // orange-300
  storage_usd: '#34d399', // emerald-400
  fee_usd: '#e879f9', // fuchsia-400
} as const;

export const BACKGROUNDS = {
  page: SLATE[950],
  panel: SLATE[900],
} as const;

export const TEXT = {
  primary: SLATE[100],
  secondary: SLATE[300],
  muted: SLATE[400],
} as const;

/** Shades used only for control and alert surfaces and the text on them. */
export const CONTROL = {
  sky200: '#bae6fd', // DR button hover surface
  sky300: '#7dd3fc', // DR button surface
  red100: '#fee2e2', // error banner text
  red200: '#fecaca', // error banner detail text
  red950: '#450a0a', // error banner surface
} as const;

/** Every text/background pair the UI draws; each must reach 4.5:1. */
export const TEXT_PAIRS: ReadonlyArray<{ name: string; fg: string; bg: string }> = [
  ...Object.entries(TEXT).flatMap(([t, fg]) =>
    Object.entries(BACKGROUNDS).map(([b, bg]) => ({ name: `${t} on ${b}`, fg, bg })),
  ),
  ...Object.entries(ROUTE_COLORS).flatMap(([route, fg]) =>
    Object.entries(BACKGROUNDS).map(([b, bg]) => ({ name: `${route} on ${b}`, fg, bg })),
  ),
  // Controls (ToggleGroup, DrPanel) and the ErrorBanner.
  { name: 'selected toggle: slate-950 on slate-100', fg: SLATE[950], bg: SLATE[100] },
  { name: 'toggle hover: slate-100 on slate-800', fg: SLATE[100], bg: SLATE[800] },
  { name: 'DR button: slate-950 on sky-300', fg: SLATE[950], bg: CONTROL.sky300 },
  { name: 'DR button hover: slate-950 on sky-200', fg: SLATE[950], bg: CONTROL.sky200 },
  { name: 'DR button, Baseline: slate-400 on slate-800', fg: SLATE[400], bg: SLATE[800] },
  // Guide steps: the current step sits on slate-800; step numbers on slate-700.
  { name: 'guide step, current: slate-100 on slate-800', fg: SLATE[100], bg: SLATE[800] },
  { name: 'guide step, current: slate-300 on slate-800', fg: SLATE[300], bg: SLATE[800] },
  { name: 'guide step number: slate-100 on slate-700', fg: SLATE[100], bg: SLATE[700] },
  // Guide links are sky-300 (the GRID_NOW pair above) and sky-200 on hover.
  { name: 'guide link hover: sky-200 on slate-900', fg: CONTROL.sky200, bg: SLATE[900] },
  // Decision_Log rows on hover or when open, and the chosen option in the
  // breakdown, sit on slate-800: secondary text, the time, the job and the chips.
  { name: 'decision row, open: slate-100 on slate-800', fg: SLATE[100], bg: SLATE[800] },
  ...Object.entries(ROUTE_COLORS).map(([route, fg]) => ({
    name: `decision row, open: ${route} chip on slate-800`,
    fg,
    bg: SLATE[800],
  })),
  { name: 'error banner: red-100 on red-950', fg: CONTROL.red100, bg: CONTROL.red950 },
  { name: 'error detail: red-200 on red-950', fg: CONTROL.red200, bg: CONTROL.red950 },
];

/** Parse "#rgb" or "#rrggbb" into 0–255 channels. */
function parseHex(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) throw new Error(`Invalid hex colour: ${hex}`);
  const h =
    m[1].length === 3
      ? m[1]
          .split('')
          .map((c) => c + c)
          .join('')
      : m[1];
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

/** WCAG 2.x relative luminance of a hex colour (0 = black, 1 = white). */
export function relativeLuminance(hex: string): number {
  const [r, g, b] = parseHex(hex).map((c) => {
    const s = c / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG contrast ratio between two hex colours, from 1 to 21. */
export function contrastRatio(fg: string, bg: string): number {
  const a = relativeLuminance(fg);
  const b = relativeLuminance(bg);
  const [hi, lo] = a >= b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}
