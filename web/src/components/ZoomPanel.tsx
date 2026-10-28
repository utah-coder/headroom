import { useMemo } from 'react';
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';
import type { DrEvent } from '../types';
import type { DrPhase } from '../state/appState';
import { ROUTE_COLORS, SLATE } from '../lib/colors';
import { zoomKwAxis } from '../lib/chartData';
import { formatKw } from '../lib/format';
import { ZOOM_LAST_INDEX, useZoomPlayback } from '../lib/useZoomPlayback';

// Zoom_Panel (Requirements 20.3, 20.4, 20.7–20.10, 24.9). Lines for grid,
// flywheel and battery over s = 0..599 with the cap as a reference line.
// While playing, entries 0..frame are drawn; when DR is done (after playback,
// or instantly under reduced motion / ANIMATED_ZOOM=false) all 600 are drawn;
// when DR returns to idle the series are cleared. Each series differs by line
// style as well as colour.

const COLOR = {
  grid: ROUTE_COLORS.GRID_NOW,
  flywheel: '#bef264', // lime-300, as in MainChart
  battery: ROUTE_COLORS.STORAGE_NOW,
  cap: '#f87171', // red-400
} as const;

const SERIES = [
  { key: 'grid_kw', name: 'Grid draw (kW)', color: COLOR.grid, width: 3, dash: undefined },
  { key: 'flywheel_kw', name: 'Flywheel discharge (kW)', color: COLOR.flywheel, width: 2, dash: '8 4' },
  { key: 'battery_kw', name: 'Battery discharge (kW)', color: COLOR.battery, width: 2, dash: '2 3' },
] as const;

const CAP_DASH = '12 4 2 4';
const X_TICKS = [0, 100, 200, 300, 400, 500, ZOOM_LAST_INDEX];
const MARGIN = { top: 8, right: 16, bottom: 16, left: 8 };

const numberFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const formatAxis = (v: number) => numberFormat.format(v);

interface Props {
  drEvent: DrEvent;
  dr: DrPhase;
  drRun: number;
  /** Called once when animated playback reaches s = 599. Must be stable. */
  onDone: () => void;
  height?: number;
}

function zoomAriaLabel(drEvent: DrEvent): string {
  const max = (f: (z: DrEvent['zoom'][number]) => number) =>
    drEvent.zoom.length ? Math.max(...drEvent.zoom.map(f)) : 0;
  return (
    `Demand-response event, seconds 0 to ${ZOOM_LAST_INDEX}. ` +
    `Grid draw peaks at ${formatKw(max((z) => z.grid_kw))} against a cap of ${formatKw(drEvent.cap_kw)}. ` +
    `Flywheel discharge peaks at ${formatKw(max((z) => z.flywheel_kw))}; ` +
    `battery discharge peaks at ${formatKw(max((z) => z.battery_kw))}.`
  );
}

function Swatch({ color, width, dash }: { color: string; width: number; dash?: string }) {
  return (
    <svg width={28} height={14} aria-hidden="true" className="shrink-0">
      <line x1={1} y1={7} x2={27} y2={7} stroke={color} strokeWidth={width} strokeDasharray={dash} />
    </svg>
  );
}

export function ZoomPanel({ drEvent, dr, drRun, onDone, height = 240 }: Props) {
  const frame = useZoomPlayback(drRun, dr === 'playing', onDone);
  const { zoom, cap_kw } = drEvent;

  // Fixed for the scenario, so the axes do not move during playback.
  const axis = useMemo(() => zoomKwAxis(zoom, cap_kw), [zoom, cap_kw]);
  const ariaLabel = useMemo(() => zoomAriaLabel(drEvent), [drEvent]);

  const count = dr === 'idle' ? 0 : dr === 'playing' ? Math.min(frame + 1, zoom.length) : zoom.length;
  const data = useMemo(() => zoom.slice(0, count), [zoom, count]);
  const shownS = count > 0 ? data[count - 1].s : null;

  return (
    <div className="flex flex-col gap-2" data-zoom-count={count}>
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-100">First 10 minutes of the event (kW)</h3>
        {shownS !== null && (
          <span aria-hidden="true" className="font-mono text-xs tabular-nums text-slate-300">
            s = {shownS}
          </span>
        )}
      </div>

      {count === 0 ? (
        <div
          className="flex items-center justify-center rounded border border-dashed border-slate-700 px-4 text-center text-sm text-slate-300"
          style={{ height }}
        >
          Fire the event to see the flywheel and battery take over.
        </div>
      ) : (
        <div role="img" aria-label={ariaLabel}>
          <ResponsiveContainer width="100%" height={height} initialDimension={{ width: 400, height }}>
            <ComposedChart data={data} margin={MARGIN} accessibilityLayer={false}>
              <CartesianGrid stroke={SLATE[800]} vertical={false} />
              <XAxis
                dataKey="s"
                type="number"
                domain={[0, ZOOM_LAST_INDEX]}
                ticks={X_TICKS}
                tick={{ fill: SLATE[300], fontSize: 12 }}
                stroke={SLATE[700]}
                label={{ value: 'Seconds from event start', position: 'insideBottom', offset: -10, fill: SLATE[300], fontSize: 12 }}
                allowDataOverflow
              />
              <YAxis
                width={56}
                domain={axis.domain}
                ticks={axis.ticks}
                tickFormatter={formatAxis}
                tick={{ fill: SLATE[300], fontSize: 12 }}
                stroke={SLATE[700]}
                label={{ value: 'kW', angle: -90, position: 'insideLeft', fill: SLATE[300], fontSize: 12 }}
                allowDataOverflow
              />
              {SERIES.map((sr) => (
                <Line
                  key={sr.key}
                  dataKey={sr.key}
                  name={sr.name}
                  type="linear"
                  stroke={sr.color}
                  strokeWidth={sr.width}
                  strokeDasharray={sr.dash}
                  dot={false}
                  activeDot={false}
                  isAnimationActive={false}
                />
              ))}
              {/* Drawn after the lines so the cap stays visible where grid draw sits on it. */}
              <ReferenceLine
                y={cap_kw}
                stroke={COLOR.cap}
                strokeWidth={2}
                strokeDasharray={CAP_DASH}
                ifOverflow="extendDomain"
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      <ul aria-label="Event zoom legend" className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-300">
        {SERIES.map((sr) => (
          <li key={sr.key} className="flex items-center gap-1.5">
            <Swatch color={sr.color} width={sr.width} dash={sr.dash} />
            <span>{sr.name}</span>
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <Swatch color={COLOR.cap} width={2} dash={CAP_DASH} />
          <span>Grid cap, {formatKw(cap_kw)}</span>
        </li>
      </ul>
    </div>
  );
}
