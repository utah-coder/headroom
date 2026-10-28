import type { Decision } from '../types';
import { COST_PART_COLORS } from '../lib/colors';
import { formatUsdCents } from '../lib/format';
import { COST_PARTS, optionRows, type OptionRow } from '../lib/optionRows';
import { hhmmDenver } from '../lib/time';
import { RouteChip } from './RouteChip';

// How one decision's routes were priced (DECISIONS.md, "Explainable decisions"):
// one row per route with a stacked bar of its cost parts, all bars on one scale.

const NOT_OFFERED = 'not offered this slot';

function targetText(row: OptionRow): string | null {
  const target = row.option?.target ?? null;
  if (target === null) return null;
  return target === 'BPAT' || target === 'CISO' ? `to ${target}` : `to ${hhmmDenver(target)}`;
}

/** One sentence per row for screen readers; the bar itself is hidden from them. */
function spokenRow(row: OptionRow): string {
  const status = row.chosen ? 'chosen' : !row.offered ? NOT_OFFERED : null;
  const head = [row.route, targetText(row), status].filter(Boolean).join(', ');
  if (row.option === null) return `${head}.`;
  const parts = row.segments.map((s) => `${s.label.toLowerCase()} ${formatUsdCents(s.usd)}`).join(', ');
  return `${head}: ${formatUsdCents(row.option.total_usd)}${parts ? `, of which ${parts}` : ''}.`;
}

function Row({ row }: { row: OptionRow }) {
  const target = targetText(row);
  const status = row.chosen ? 'chosen' : !row.offered ? NOT_OFFERED : null;
  return (
    <li
      // relative: keeps the absolutely positioned sr-only text inside the log's
      // scroll region, or it would stretch the page.
      className={`relative grid grid-cols-[8.5rem_minmax(0,1fr)_auto] items-center gap-x-2 rounded px-2 py-1 ${
        row.chosen ? 'bg-slate-800' : ''
      }`}
    >
      <span className="sr-only">{spokenRow(row)}</span>
      <span aria-hidden="true" className="flex flex-col items-start gap-0.5">
        <RouteChip route={row.route} />
        {(target !== null || status !== null) && (
          <span className={`text-xs ${row.chosen ? 'font-semibold text-slate-100' : 'text-slate-300'}`}>
            {[target, status].filter(Boolean).join(' · ')}
          </span>
        )}
      </span>
      <span aria-hidden="true" className="flex h-3 min-w-0 gap-px">
        {row.segments.map((s) => (
          <span
            key={s.key}
            title={`${s.label} ${formatUsdCents(s.usd)}`}
            className="h-full first:rounded-l-sm last:rounded-r-sm"
            style={{ width: `${s.pct}%`, backgroundColor: COST_PART_COLORS[s.key] }}
          />
        ))}
      </span>
      <span aria-hidden="true" className="text-right font-mono text-sm tabular-nums text-slate-100">
        {row.option === null ? '' : formatUsdCents(row.option.total_usd)}
      </span>
    </li>
  );
}

export function OptionBreakdown({ decision }: { decision: Decision }) {
  const rows = optionRows(decision);
  return (
    <div className="mt-2 flex flex-col gap-2">
      <ul aria-label={`How job#${decision.job}'s options were priced`} className="flex flex-col gap-1">
        {rows.map((row) => (
          <Row key={row.route} row={row} />
        ))}
      </ul>
      <ul aria-label="Cost parts" className="flex flex-wrap gap-x-3 gap-y-1 px-2 text-xs text-slate-300">
        {COST_PARTS.map((p) => (
          <li key={p.key} className="flex items-center gap-1">
            <span aria-hidden="true" className="inline-block size-2.5 rounded-sm" style={{ backgroundColor: COST_PART_COLORS[p.key] }} />
            {p.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default OptionBreakdown;
