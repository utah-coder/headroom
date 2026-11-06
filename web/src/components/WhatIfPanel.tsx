import { useId, useMemo, useRef, useState, type FormEvent } from 'react';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { WHATIF_MAX_CHARS } from '../config';
import { ROUTE_COLORS, SLATE } from '../lib/colors';
import { formatKw } from '../lib/format';
import { hhmmDenver } from '../lib/time';
import { askWhatIf, changeText, visibleFigures, type WhatIfReply, type WhatIfRow, type WhatIfSeries } from '../lib/whatifApi';
import type { ScenarioName } from '../types';

// What-if analyst panel. Claude turns a question into inputs, the real engine
// reruns, and code checks that every figure came from the engine. Sending a
// question is the only network use after page load, and the rest of the page
// doesn't depend on it.

const EXAMPLES = [
  'What if the demand charge doubled?',
  'What if we doubled the battery?',
  'What if jobs couldn’t move to other regions?',
];

const ENGINE_ONLY_NOTE = 'Written by code from engine figures. The AI text was unavailable or failed the figure check.';

function Table({ caption, rows, withChange }: { caption: string; rows: readonly WhatIfRow[]; withChange: boolean }) {
  return (
    <div className="min-w-0 overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">
        <caption className="pb-1 text-left font-semibold text-slate-100">{caption}</caption>
        <thead>
          <tr className="border-b border-slate-700 text-slate-400">
            <th scope="col" className="py-1 pr-3 font-medium">
              <span className="sr-only">Item</span>
            </th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">
              Today
            </th>
            <th scope="col" className="py-1 pr-3 text-right font-medium">
              What if
            </th>
            {withChange && (
              <th scope="col" className="py-1 text-right font-medium">
                Change
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-b border-slate-800">
              <th scope="row" className="py-1 pr-3 font-normal text-slate-300">
                {row.label}
              </th>
              <td className="py-1 pr-3 text-right whitespace-nowrap text-slate-300 tabular-nums">{row.current}</td>
              <td className="py-1 pr-3 text-right whitespace-nowrap text-slate-100 tabular-nums">{row.whatif}</td>
              {withChange && (
                <td className="py-1 text-right whitespace-nowrap text-slate-300 tabular-nums">{changeText(row)}</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GridChart({ series }: { series: WhatIfSeries }) {
  const data = useMemo(
    () => series.t.map((t, i) => ({ t, today: series.current_grid_kw[i], whatif: series.whatif_grid_kw[i] })),
    [series],
  );
  return (
    <figure className="flex min-w-0 flex-col gap-1">
      <figcaption className="text-sm font-semibold text-slate-100">Site grid draw with Headroom</figcaption>
      <div className="h-52 w-full" aria-hidden="true">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
            <CartesianGrid stroke={SLATE[700]} strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey="t" tickFormatter={hhmmDenver} minTickGap={48} stroke={SLATE[400]} tick={{ fontSize: 12 }} />
            <YAxis tickFormatter={(v: number) => `${(v / 1000).toFixed(v % 1000 === 0 ? 0 : 1)} MW`} stroke={SLATE[400]} width={60} tick={{ fontSize: 12 }} />
            <Tooltip
              labelFormatter={(t) => hhmmDenver(String(t))}
              formatter={(v) => formatKw(Number(v))}
              contentStyle={{ background: SLATE[800], border: 'none', color: SLATE[100] }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: SLATE[300] }} />
            <Line type="stepAfter" dataKey="today" name="Today" stroke={SLATE[400]} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
            <Line type="stepAfter" dataKey="whatif" name="What if" stroke={ROUTE_COLORS.GRID_NOW} strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

function Result({ reply }: { reply: WhatIfReply }) {
  if (reply.status === 'answered') {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <p className="text-base text-slate-100">{reply.answer}</p>
          {reply.answer_source === 'engine_only' && (
            <p className="text-sm" style={{ color: ROUTE_COLORS.DEFER }}>
              {ENGINE_ONLY_NOTE}
            </p>
          )}
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-3">
            {reply.inputs.length > 0 && <Table caption={`Changed in ${reply.scenario_label}`} rows={reply.inputs} withChange={false} />}
            <Table caption="Engine results" rows={visibleFigures(reply.figures)} withChange />
          </div>
          <GridChart series={reply.series} />
        </div>
        <p className="text-sm text-slate-400">{reply.caveat}</p>
      </div>
    );
  }
  if (reply.status === 'engine_refused') {
    return (
      <div className="flex flex-col gap-3">
        <p className="text-base text-slate-100">{reply.message}</p>
        <Table caption={`Tried in ${reply.scenario_label}`} rows={reply.inputs} withChange={false} />
      </div>
    );
  }
  return <p className="text-base text-slate-300">{reply.message}</p>;
}

export function WhatIfPanel({ scenario }: { scenario: ScenarioName }) {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<WhatIfReply | null>(null);
  const canAsk = question.trim().length > 0 && !busy;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canAsk) return;
    setBusy(true);
    setReply(await askWhatIf(question.trim(), scenario));
    setBusy(false);
  };

  return (
    <section aria-labelledby="whatif-heading" className="flex flex-col gap-3 rounded-lg border border-sky-300 bg-slate-900 p-4">
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="whatif-heading" className="text-base font-semibold text-slate-100">
            Ask what if
          </h2>
          <span className="rounded-full bg-sky-300 px-2 py-0.5 text-xs font-semibold text-slate-950">Real-time AI</span>
        </div>
        <p className="text-base text-slate-100">Fine-tune Headroom to your own assumptions, live.</p>
        <p className="text-sm text-slate-300">
          The panels above replay precomputed results. This one runs when you ask: change the tariff, carbon price,
          storage, demand-response cut or region moves in plain English. Claude turns your question into inputs and the
          real engine reruns on them, so you see the effect in seconds.
        </p>
      </div>

      <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-2 sm:flex-row">
        <label htmlFor={inputId} className="sr-only">
          Your what-if question
        </label>
        <input
          id={inputId}
          ref={inputRef}
          type="text"
          value={question}
          maxLength={WHATIF_MAX_CHARS}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="What if the demand charge doubled?"
          className="min-w-0 flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-400 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300"
        />
        <button
          type="submit"
          aria-disabled={canAsk ? undefined : 'true'}
          className={
            'rounded-md px-4 py-2 text-sm font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300 ' +
            (canAsk
              ? 'bg-sky-300 text-slate-950 hover:bg-sky-200'
              : 'cursor-not-allowed border border-slate-700 bg-slate-800 text-slate-400')
          }
        >
          {busy ? 'Running…' : 'Ask'}
        </button>
      </form>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-400">Try:</span>
        {EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => {
              setQuestion(example);
              inputRef.current?.focus();
            }}
            className="rounded-md border border-slate-700 px-2 py-1 text-sm text-slate-300 hover:bg-slate-800 hover:text-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300"
          >
            {example}
          </button>
        ))}
      </div>

      <div aria-live="polite" className="flex flex-col gap-3">
        {busy && <p className="text-sm text-slate-300">Understanding the question and rerunning the engine…</p>}
        {!busy && reply !== null && <Result reply={reply} />}
      </div>
    </section>
  );
}

export default WhatIfPanel;
