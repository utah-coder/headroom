import { useId, useMemo } from 'react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';
import type { IsoUtc, Scenario } from '../types';
import type { Mode } from '../state/appState';
import { ROUTE_COLORS, SLATE } from '../lib/colors';
import {
  buildRows,
  chartAriaLabel,
  kwAxis,
  mwAxis,
  stressRuns,
  tickLabel,
  xTicks,
} from '../lib/chartData';
import { CarbonBand } from './CarbonBand';

// Main chart (Requirements 19.1–19.10). Every series differs from every other
// by line style or fill pattern as well as colour (Requirement 19.8). The chart
// is drawn from the in-memory Scenario only, so toggles never fetch (19.10).

type PatternKind = 'diagonal' | 'cross' | 'dots' | 'vertical';

/** Literal colours for Recharts. Site supply areas reuse the route colours. */
const COLOR = {
  baseline: '#fda4af', // rose-300
  headroom: SLATE[100],
  grid: ROUTE_COLORS.GRID_NOW,
  battery: ROUTE_COLORS.STORAGE_NOW,
  flywheel: '#bef264', // lime-300
  moved: ROUTE_COLORS.MOVE_REGION,
  pace: SLATE[400],
  stress: '#f87171', // red-400
} as const;

const AREAS = [
  { key: 'grid_kw', name: 'Grid (kW)', color: COLOR.grid, pattern: 'diagonal' },
  { key: 'battery_kw', name: 'Battery, + discharge / − charge (kW)', color: COLOR.battery, pattern: 'cross' },
  { key: 'flywheel_kw', name: 'Flywheel, + discharge / − charge (kW)', color: COLOR.flywheel, pattern: 'dots' },
  { key: 'moved_kw', name: 'Moved to another region (kW)', color: COLOR.moved, pattern: 'vertical' },
] as const satisfies ReadonlyArray<{ key: string; name: string; color: string; pattern: PatternKind }>;

const BASELINE_DASH = '6 4';
const PACE_DASH = '1 5';
const GHOST_OPACITY = 0.4;
const STRESS_OPACITY = 0.14;

// Plot-area insets shared with CarbonBand so both use the same x-domain.
const MARGIN = { top: 8, right: 8, bottom: 0, left: 8 };
const Y_AXIS_WIDTH = 64;
const INSET_LEFT = MARGIN.left + Y_AXIS_WIDTH;
const INSET_RIGHT = MARGIN.right + Y_AXIS_WIDTH;

const numberFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const formatAxis = (v: number) => numberFormat.format(v);

function Pattern({ id, color, kind }: { id: string; color: string; kind: PatternKind }) {
  return (
    <pattern id={id} patternUnits="userSpaceOnUse" width={8} height={8}>
      <rect width={8} height={8} fill={color} fillOpacity={0.18} />
      {kind === 'diagonal' && <path d="M-2,2 l4,-4 M0,8 l8,-8 M6,10 l4,-4" stroke={color} strokeWidth={1.5} />}
      {kind === 'cross' && <path d="M0,0 L8,8 M8,0 L0,8" stroke={color} strokeWidth={1.2} />}
      {kind === 'dots' && <circle cx={4} cy={4} r={1.8} fill={color} />}
      {kind === 'vertical' && <path d="M4,0 V8" stroke={color} strokeWidth={2} />}
    </pattern>
  );
}

type LegendItem =
  | { kind: 'line'; name: string; color: string; width: number; dash?: string; opacity?: number }
  | { kind: 'area'; name: string; color: string; pattern: PatternKind }
  | { kind: 'shade'; name: string; color: string };

function Swatch({ item, id }: { item: LegendItem; id: string }) {
  return (
    <svg width={28} height={14} aria-hidden="true" className="shrink-0">
      {item.kind === 'line' && (
        <line
          x1={1}
          y1={7}
          x2={27}
          y2={7}
          stroke={item.color}
          strokeWidth={item.width}
          strokeDasharray={item.dash}
          strokeOpacity={item.opacity}
          strokeLinecap={item.dash === PACE_DASH ? 'round' : undefined}
        />
      )}
      {item.kind === 'area' && (
        <>
          <defs>
            <Pattern id={id} color={item.color} kind={item.pattern} />
          </defs>
          <rect x={0.5} y={0.5} width={27} height={13} fill={`url(#${id})`} stroke={item.color} />
        </>
      )}
      {item.kind === 'shade' && <rect width={28} height={14} fill={item.color} fillOpacity={STRESS_OPACITY * 2} />}
    </svg>
  );
}

interface Props {
  scenario: Scenario;
  mode: Mode;
  /** Height of the plot in px (the carbon band and legends sit below it). */
  height?: number;
  /** Slot of the decision open in the Decision_Log, marked with a vertical line. */
  markT?: IsoUtc | null;
}

export function MainChart({ scenario, mode, height = 420, markT = null }: Props) {
  // useId may contain characters that are awkward inside url(#…); keep [A-Za-z0-9_-].
  const uid = useId().replace(/[^A-Za-z0-9_-]/g, '');
  const patternId = (key: string) => `mc-${uid}-${key}`;

  const rows = useMemo(() => buildRows(scenario), [scenario]);
  const runs = useMemo(() => stressRuns(scenario.grid), [scenario]);
  const kw = useMemo(() => kwAxis(rows), [rows]);
  const mw = useMemo(() => mwAxis(rows), [rows]);
  const n = scenario.grid.length;
  const headroom = mode === 'headroom';
  const ariaLabel = chartAriaLabel(scenario, mode);
  const markSlot = markT === null ? -1 : scenario.grid.findIndex((g) => g.t === markT);

  const legend: LegendItem[] = headroom
    ? [
        { kind: 'line', name: 'Headroom grid draw (kW)', color: COLOR.headroom, width: 3 },
        { kind: 'line', name: 'Baseline grid draw (kW)', color: COLOR.baseline, width: 2, dash: BASELINE_DASH, opacity: GHOST_OPACITY },
        ...AREAS.map((a): LegendItem => ({ kind: 'area', name: a.name, color: a.color, pattern: a.pattern })),
        { kind: 'line', name: 'PACE demand (MW)', color: COLOR.pace, width: 2, dash: PACE_DASH },
        { kind: 'shade', name: 'Grid stress slots', color: COLOR.stress },
      ]
    : [
        { kind: 'line', name: 'Baseline grid draw (kW)', color: COLOR.baseline, width: 2 },
        { kind: 'line', name: 'PACE demand (MW)', color: COLOR.pace, width: 2, dash: PACE_DASH },
        { kind: 'shade', name: 'Grid stress slots', color: COLOR.stress },
      ];

  return (
    <section aria-labelledby={`mc-${uid}-title`} className="flex flex-col gap-2">
      <h2 id={`mc-${uid}-title`} className="text-sm font-semibold text-slate-100">
        Site grid draw (kW) and PACE demand (MW)
      </h2>
      <p className="text-sm text-slate-300">
        {headroom
          ? 'Where each kilowatt came from, slot by slot, with baseline faint behind it. Lower draw inside the shaded stress hours is headroom handed back to the grid.'
          : 'The site’s grid draw when every job runs on arrival, against total PacifiCorp East demand. Shaded bands are the grid’s stress hours, the busiest 5% of these 48 hours.'}
      </p>

      <div role="img" aria-label={ariaLabel}>
        <ResponsiveContainer width="100%" height={height} initialDimension={{ width: 800, height }}>
          <ComposedChart
            data={rows}
            margin={MARGIN}
            stackOffset="sign"
            accessibilityLayer={false}
          >
            <defs>
              {AREAS.map((a) => (
                <Pattern key={a.key} id={patternId(a.key)} color={a.color} kind={a.pattern} />
              ))}
            </defs>

            <CartesianGrid stroke={SLATE[800]} vertical={false} yAxisId="kw" />

            {runs.map((r) => (
              <ReferenceArea
                key={r.start}
                yAxisId="kw"
                x1={r.start}
                x2={r.end}
                fill={COLOR.stress}
                fillOpacity={STRESS_OPACITY}
                stroke="none"
              />
            ))}

            <XAxis
              dataKey="x"
              type="number"
              domain={[0, n]}
              ticks={xTicks(n)}
              tickFormatter={(v: number) => tickLabel(scenario.grid, v)}
              tick={{ fill: SLATE[300], fontSize: 12 }}
              stroke={SLATE[700]}
              allowDataOverflow
            />
            <YAxis
              yAxisId="kw"
              orientation="left"
              width={Y_AXIS_WIDTH}
              domain={kw.domain}
              ticks={kw.ticks}
              tickFormatter={formatAxis}
              tick={{ fill: SLATE[300], fontSize: 12 }}
              stroke={SLATE[700]}
              label={{ value: 'kW', angle: -90, position: 'insideLeft', fill: SLATE[300], fontSize: 12 }}
              allowDataOverflow
            />
            <YAxis
              yAxisId="mw"
              orientation="right"
              width={Y_AXIS_WIDTH}
              domain={mw.domain}
              ticks={mw.ticks}
              tickFormatter={formatAxis}
              tick={{ fill: SLATE[300], fontSize: 12 }}
              stroke={SLATE[700]}
              label={{ value: 'MW', angle: 90, position: 'insideRight', fill: SLATE[300], fontSize: 12 }}
              allowDataOverflow
            />

            {markSlot >= 0 && (
              <ReferenceLine
                yAxisId="kw"
                x={markSlot + 0.5}
                stroke={SLATE[100]}
                strokeWidth={1.5}
                strokeDasharray="3 3"
                label={{ value: 'selected decision', position: 'insideTopLeft', fill: SLATE[100], fontSize: 12 }}
              />
            )}

            {/* Stacked supply, bottom to top: grid, battery, flywheel, moved (Req 19.4). */}
            {headroom &&
              AREAS.map((a) => (
                <Area
                  key={a.key}
                  yAxisId="kw"
                  dataKey={a.key}
                  name={a.name}
                  stackId="supply"
                  type="linear"
                  stroke={a.color}
                  strokeWidth={1}
                  fill={`url(#${patternId(a.key)})`}
                  fillOpacity={1}
                  dot={false}
                  activeDot={false}
                  isAnimationActive={false}
                />
              ))}

            {headroom && (
              <Line
                yAxisId="kw"
                dataKey="baseline_kw"
                name="Baseline grid draw (kW)"
                type="linear"
                stroke={COLOR.baseline}
                strokeWidth={2}
                strokeDasharray={BASELINE_DASH}
                strokeOpacity={GHOST_OPACITY}
                dot={false}
                activeDot={false}
                isAnimationActive={false}
              />
            )}
            {headroom ? (
              <Line
                yAxisId="kw"
                dataKey="headroom_kw"
                name="Headroom grid draw (kW)"
                type="linear"
                stroke={COLOR.headroom}
                strokeWidth={3}
                dot={false}
                activeDot={false}
                isAnimationActive={false}
              />
            ) : (
              <Line
                yAxisId="kw"
                dataKey="baseline_kw"
                name="Baseline grid draw (kW)"
                type="linear"
                stroke={COLOR.baseline}
                strokeWidth={2}
                dot={false}
                activeDot={false}
                isAnimationActive={false}
              />
            )}

            <Line
              yAxisId="mw"
              dataKey="pace_mw"
              name="PACE demand (MW)"
              type="linear"
              stroke={COLOR.pace}
              strokeWidth={2}
              strokeDasharray={PACE_DASH}
              strokeLinecap="round"
              dot={false}
              activeDot={false}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <CarbonBand grid={scenario.grid} insetLeft={INSET_LEFT} insetRight={INSET_RIGHT} />

      <ul aria-label="Chart legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-300">
        {legend.map((item, i) => (
          <li key={item.name} className="flex items-center gap-1.5">
            <Swatch item={item} id={`mc-${uid}-legend-${i}`} />
            <span>{item.name}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
