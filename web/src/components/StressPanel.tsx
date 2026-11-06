import { useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { loadStress } from '../data/loadStress';
import { CHART_FOCUS, CHART_REFERENCE, SLATE } from '../lib/colors';
import { formatKw, formatUsd } from '../lib/format';
import {
  bandChartData,
  bandHits,
  demandChargeSharePct,
  positionPct,
  rangeText,
  runsText,
  savedDomain,
} from '../lib/stressView';
import { dateDenver, hhmmDenver, weekdayDenver } from '../lib/time';
import type { BandPoint, ReplaySummary, ScenarioStress, StressFamily, StressFile } from '../stressTypes';
import type { ScenarioName } from '../types';

// The stress test: what happens to the savings without the foresight the panels above assume.
// Precomputed by sim/stress.py and read once from scenarios/stress.json. Copy states what's
// real and what's assumed, and never reads the spread across draws as a probability.

const HOME_REGION = 'PACE';
const SHOWN_LEADS = [1, 6, 24];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <h3 className="text-sm font-semibold text-slate-100">{title}</h3>
      {children}
    </div>
  );
}

function RangeBar({ family, domain, published }: { family: StressFamily; domain: [number, number]; published: number }) {
  const { p10, p50, p90 } = family.usd_saved;
  const left = positionPct(p10, domain);
  const right = positionPct(p90, domain);
  return (
    <div
      className="relative h-5 w-full min-w-40"
      title={`${family.label}: median ${formatUsd(p50)}, range ${rangeText(family.usd_saved, family.samples)}`}
      aria-hidden="true"
    >
      <div className="absolute top-1/2 h-px w-full bg-slate-700" />
      <div className="absolute top-0 h-full w-px bg-slate-400" style={{ left: `${positionPct(0, domain)}%` }} />
      <div
        className="absolute top-0 h-full border-l border-dashed"
        style={{ left: `${positionPct(published, domain)}%`, borderColor: CHART_REFERENCE }}
      />
      {family.samples > 1 && (
        <div
          className="absolute top-1/2 h-1 -translate-y-1/2 rounded"
          style={{ left: `${left}%`, width: `${right - left}%`, background: CHART_FOCUS }}
        />
      )}
      <div
        className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-slate-900"
        style={{ left: `${positionPct(p50, domain)}%`, background: CHART_FOCUS }}
      />
    </div>
  );
}

function FamilyTable({ stress }: { stress: ScenarioStress }) {
  const domain = useMemo(() => savedDomain(stress.families), [stress]);
  const published = stress.families[0].usd_saved.p50;
  return (
    <div className="min-w-0 overflow-x-auto">
      <table className="w-full min-w-[56rem] border-collapse text-left text-sm">
        <caption className="sr-only">Money saved against baseline under each test, with the bill, deadlines and demand-response result</caption>
        <thead>
          <tr className="border-b border-slate-700 align-bottom text-slate-400">
            <th scope="col" className="py-1 pr-3 font-medium">Test</th>
            <th scope="col" className="py-1 pr-3 font-medium">
              <span className="flex justify-between gap-2">
                <span>{formatUsd(domain[0])}</span>
                <span>Saved</span>
                <span>{formatUsd(domain[1])}</span>
              </span>
            </th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">Saved, median</th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">Middle 80% of runs</th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">Bill, median</th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">Lost money</th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">Deadlines missed, worst run</th>
            <th scope="col" className="py-1 text-right font-medium">Demand-response cut missed</th>
          </tr>
        </thead>
        <tbody>
          {stress.families.map((f) => (
            <tr key={f.id} className="border-b border-slate-800">
              <th scope="row" className="max-w-64 py-2 pr-3 font-normal text-slate-300">{f.label}</th>
              <td className="py-2 pr-3"><RangeBar family={f} domain={domain} published={published} /></td>
              <td className="py-2 pr-3 text-right whitespace-nowrap text-slate-100 tabular-nums">{formatUsd(f.usd_saved.p50)}</td>
              <td className="py-2 pr-3 text-right whitespace-nowrap text-slate-300 tabular-nums">
                {f.samples === 1 ? 'one run' : rangeText(f.usd_saved, f.samples)}
              </td>
              <td className="py-2 pr-3 text-right whitespace-nowrap text-slate-300 tabular-nums">{formatUsd(f.bill_usd.p50)}</td>
              <td className="py-2 pr-3 text-right whitespace-nowrap text-slate-300 tabular-nums">{runsText(f.negative_runs, f.samples)}</td>
              <td className="py-2 pr-3 text-right whitespace-nowrap text-slate-300 tabular-nums">
                {f.deadlines_missed_max} (baseline {f.baseline_deadlines_missed_max})
              </td>
              <td className="py-2 text-right whitespace-nowrap text-slate-300 tabular-nums">
                {runsText(f.dr_missed_runs, f.samples)}
                {f.dr_missed_runs > 0 && <>, by {f.samples > 1 && 'up to '}{formatKw(f.dr_shortfall_max_kw)}</>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BandChart({ title, unit, points, forecastName }: { title: string; unit: string; points: readonly BandPoint[]; forecastName: string }) {
  const data = useMemo(() => bandChartData(points), [points]);
  const { inside, total } = bandHits(points);
  return (
    <figure className="flex min-w-0 flex-col gap-1">
      <figcaption className="text-sm font-semibold text-slate-100">{title}</figcaption>
      <div className="h-52 w-full" aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
            <CartesianGrid stroke={SLATE[700]} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="t" tickFormatter={hhmmDenver} minTickGap={48} stroke={SLATE[400]} tick={{ fontSize: 12 }} />
            <YAxis stroke={SLATE[400]} width={56} tick={{ fontSize: 12 }} tickFormatter={(v: number) => v.toLocaleString('en-US')} domain={['auto', 'auto']} />
            <Tooltip
              labelFormatter={(t) => `${weekdayDenver(String(t))} ${hhmmDenver(String(t))}`}
              formatter={(v) =>
                Array.isArray(v)
                  ? `${Math.round(Number(v[0])).toLocaleString('en-US')} to ${Math.round(Number(v[1])).toLocaleString('en-US')} ${unit}`
                  : `${Math.round(Number(v)).toLocaleString('en-US')} ${unit}`
              }
              contentStyle={{ background: SLATE[800], border: 'none', color: SLATE[100] }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: SLATE[300] }} />
            <Area type="stepAfter" dataKey="band" name="Usual error range" stroke="none" fill={CHART_FOCUS} fillOpacity={0.25} connectNulls={false} isAnimationActive={false} />
            <Line type="stepAfter" dataKey="forecast" name={forecastName} stroke={CHART_FOCUS} strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line type="stepAfter" dataKey="actual" name="What happened" stroke={SLATE[100]} strokeWidth={2} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="text-sm text-slate-400">
        {total > 0 ? `${inside} of ${total} hours fell inside the range, which aims for 80%.` : 'Too little history for a range.'}
      </p>
    </figure>
  );
}

function ReplayChart({ replay }: { replay: ReplaySummary }) {
  return (
    <figure className="flex min-w-0 flex-col gap-1">
      <figcaption className="text-sm font-semibold text-slate-100">Saved in each 48-hour window, June to September</figcaption>
      <div className="h-56 w-full" aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={replay.windows as ReplaySummary['windows']} margin={{ top: 4, right: 8, bottom: 0, left: 8 }} barCategoryGap={2}>
            <CartesianGrid stroke={SLATE[700]} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="start" tickFormatter={dateDenver} minTickGap={48} stroke={SLATE[400]} tick={{ fontSize: 12 }} />
            <YAxis tickFormatter={(v: number) => formatUsd(v)} stroke={SLATE[400]} width={72} tick={{ fontSize: 12 }} />
            <ReferenceLine y={0} stroke={SLATE[400]} />
            <Tooltip
              labelFormatter={(t) => `Window starting ${dateDenver(String(t))}`}
              formatter={(v) => formatUsd(Number(v))}
              cursor={{ fill: SLATE[800] }}
              contentStyle={{ background: SLATE[800], border: 'none', color: SLATE[100] }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: SLATE[300] }} />
            <Bar dataKey="forecast_usd" name="With the forecast" fill={CHART_FOCUS} radius={[2, 2, 0, 0]} isAnimationActive={false} />
            <Scatter dataKey="foresight_usd" name="With foresight" fill={CHART_REFERENCE} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

function AccuracyTable({ stress }: { stress: StressFile }) {
  const rows = stress.carbon_accuracy.filter((r) => r.region === HOME_REGION && SHOWN_LEADS.includes(r.lead_hours));
  const demand = stress.demand_accuracy;
  return (
    <div className="min-w-0 overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <caption className="pb-1 text-left text-slate-300">How the forecasts did over all four months</caption>
        <thead>
          <tr className="border-b border-slate-700 text-slate-400">
            <th scope="col" className="py-1 pr-3 font-medium">Forecast</th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">Typical error</th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">Same hour yesterday</th>
            <th scope="col" className="py-1 text-right font-medium">Inside the range</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.lead_hours} className="border-b border-slate-800">
              <th scope="row" className="py-1 pr-3 font-normal text-slate-300">
                Carbon, {r.lead_hours} {r.lead_hours === 1 ? 'hour' : 'hours'} ahead
              </th>
              <td className="py-1 pr-3 text-right whitespace-nowrap text-slate-100 tabular-nums">{r.mae_g_per_kwh.toFixed(0)} g/kWh</td>
              <td className="py-1 pr-3 text-right whitespace-nowrap text-slate-300 tabular-nums">{r.naive_mae_g_per_kwh.toFixed(0)} g/kWh</td>
              <td className="py-1 text-right whitespace-nowrap text-slate-300 tabular-nums">{r.band_coverage_pct.toFixed(0)}%</td>
            </tr>
          ))}
          <tr className="border-b border-slate-800">
            <th scope="row" className="py-1 pr-3 font-normal text-slate-300">Utility's demand forecast, day ahead</th>
            <td className="py-1 pr-3 text-right whitespace-nowrap text-slate-100 tabular-nums">{demand.mape_pct.toFixed(1)}%</td>
            <td className="py-1 pr-3 text-right whitespace-nowrap text-slate-300">n/a</td>
            <td className="py-1 text-right whitespace-nowrap text-slate-300 tabular-nums">{demand.band_coverage_pct.toFixed(0)}%</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function Findings({ stress, file }: { stress: ScenarioStress; file: StressFile }) {
  const byId = (id: string) => stress.families.find((f) => f.id === id) as StressFamily;
  const published = byId('published');
  const forecast = byId('forecast');
  const runtime = byId('runtime');
  const share = demandChargeSharePct(published);
  return (
    <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-slate-300">
      <li>
        In this scenario, swapping foresight for a real carbon forecast moves the saving from {formatUsd(published.usd_saved.p50)} to{' '}
        {formatUsd(forecast.usd_saved.p50)}.
        {share !== null && ` ${share}% of the published saving comes from a lower peak, so a small shift in load can move it a lot.`}
      </li>
      <li>
        Savings are measured against baseline, so read them beside the bill. When jobs run long, the median bill goes from{' '}
        {formatUsd(forecast.bill_usd.p50)} to {formatUsd(runtime.bill_usd.p50)}, and baseline's rises from{' '}
        {formatUsd(forecast.baseline_bill_usd.p50)} to {formatUsd(runtime.baseline_bill_usd.p50)}.
      </li>
      <li>
        A missed demand-response cut costs nothing on this bill, because the program's penalties aren't modeled. So a battery that sits
        out the event can show a higher saving than one that delivers it.
      </li>
      <li>
        Run-time and outage sizes are assumptions: true run times centered on {Math.round((file.meta.runtime_median - 1) * 100)}% over the
        estimate, and unannounced outages of {file.meta.outage_hours_min} to {file.meta.outage_hours_max} hours. The spread shows
        sensitivity to those assumptions, not how likely any result is.
      </li>
      <li>
        Not tested yet: surprise demand-response events (the planner always knows the event is coming), jobs arriving differently than
        expected, and inference load that differs from its profile.
      </li>
    </ul>
  );
}

function ReplayText({ replay }: { replay: ReplaySummary }) {
  const windows = replay.windows.length;
  return (
    <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-slate-300">
      <li>
        With the forecast, the median window saves {formatUsd(replay.forecast_usd.p50)}. Windows that lose money: {replay.forecast_negative} of{' '}
        {windows}. Each window has its own simulated jobs, so this spread mixes job timing with grid conditions.
      </li>
      <li>
        Compared with foresight, the median window changes by {formatUsd(replay.change_median_usd)}, but single windows swing from{' '}
        {formatUsd(replay.change_worst_usd)} to {formatUsd(replay.change_best_usd)}: {replay.windows_lower} are lower, {replay.windows_higher} higher,
        and the rest within $100.
      </li>
      <li>
        The demand-response cut is missed in {replay.forecast_dr_missed} windows. {replay.forecast_dr_missed_late} of those are among the{' '}
        {replay.late_event_windows} windows with the event in their last 8 hours. The two chosen scenarios have earlier events, so they
        don't test this.
      </li>
      <li>
        Each window bills a full month's demand charge on its own 48-hour peak, as the scenarios do. Compare windows, but don't add them up
        or read them as yearly savings.
      </li>
    </ul>
  );
}

export function StressPanel({ scenario }: { scenario: ScenarioName }) {
  const [file, setFile] = useState<StressFile | null | undefined>(undefined);

  useEffect(() => {
    let live = true;
    void loadStress().then((result) => {
      if (live) setFile(result);
    });
    return () => {
      live = false;
    };
  }, []);

  if (file === undefined) return null;

  return (
    <section aria-labelledby="stress-heading" className="flex flex-col gap-4 rounded-lg bg-slate-900 p-4">
      <div className="flex flex-col gap-1">
        <h2 id="stress-heading" className="text-base font-semibold text-slate-100">
          How sturdy are the savings?
        </h2>
        <p className="text-sm text-slate-300">
          The panels above give the planner two things a real site wouldn't have: the grid's actual carbon for the next 24 hours, and
          every job's exact run time. Here it gets a real forecast instead, and then things go wrong. The grid data and forecast errors
          are real. The site is simulated, and the size of each failure is an assumption.
        </p>
      </div>
      {file === null ? (
        <p className="text-sm text-slate-300">The stress test results couldn't be loaded. The rest of the page isn't affected.</p>
      ) : (
        <>
          <Section title="Each test, against the published run (dashed line)">
            <FamilyTable stress={file.scenarios[scenario]} />
            <Findings stress={file.scenarios[scenario]} file={file} />
          </Section>
          <Section title="The forecasts, as issued at the start of this scenario">
            <div className="grid gap-4 lg:grid-cols-2">
              <BandChart title="Carbon intensity, our forecast (g/kWh)" unit="g/kWh" points={file.scenarios[scenario].carbon_band} forecastName="Forecast" />
              <BandChart title="PACE demand, the utility's forecast (MW)" unit="MW" points={file.scenarios[scenario].demand_band} forecastName="Forecast" />
            </div>
            <p className="text-sm text-slate-400">
              Each range is built only from errors known by the start of the scenario. The sunny day comes early in the data, with about 4
              days before it, so its ranges have little to go on and miss more often.
            </p>
            <AccuracyTable stress={file} />
          </Section>
          <Section title="Beyond the two chosen scenarios">
            <ReplayChart replay={file.replay} />
            <ReplayText replay={file.replay} />
          </Section>
        </>
      )}
    </section>
  );
}

export default StressPanel;
