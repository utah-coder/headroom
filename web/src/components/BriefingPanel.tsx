import { useState } from 'react';
import { askBriefing, pointEvidence, type BriefingEvidence, type BriefingReply } from '../lib/briefingApi';
import { ROUTE_COLORS } from '../lib/colors';
import type { ScenarioName } from '../types';
import { AboutResult } from './AboutResult';

// The operator briefing. Gemini summarizes the plan and its risks from facts code chose,
// and every point cites evidence code chose. Each citation opens that decision in the log
// or scrolls to the stress test, so a reader can check the claim. Nothing is fetched until
// someone asks, and the rest of the page doesn't depend on it.

const ENGINE_ONLY_NOTE = 'Written by code from engine figures. The AI text was unavailable or failed the checks.';

function EvidenceButton({ evidence, onOpenDecision }: { evidence: BriefingEvidence; onOpenDecision: (key: string) => void }) {
  const onClick = () => {
    if (evidence.kind === 'decision' && evidence.key !== null) {
      onOpenDecision(evidence.key);
    } else {
      document.getElementById('stress-heading')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border border-slate-700 px-2 py-0.5 text-left text-xs text-slate-300 hover:bg-slate-800 hover:text-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300"
    >
      {evidence.kind === 'decision' ? 'Decision: ' : 'Stress test: '}
      {evidence.label}
    </button>
  );
}

function Result({ reply, onOpenDecision }: { reply: BriefingReply; onOpenDecision: (key: string) => void }) {
  if (reply.status !== 'briefed') return <p className="text-base text-slate-300">{reply.message}</p>;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-base font-semibold text-slate-100">{reply.headline}</p>
      <ul className="flex flex-col gap-3">
        {reply.points.map((point, i) => (
          <li key={i} className="flex flex-col gap-1">
            <p className="text-sm text-slate-100">{point.text}</p>
            <div className="flex flex-wrap gap-1">
              {pointEvidence(point, reply.evidence).map((e) => (
                <EvidenceButton key={e.id} evidence={e} onOpenDecision={onOpenDecision} />
              ))}
            </div>
          </li>
        ))}
      </ul>
      <details className="text-sm text-slate-300">
        <summary className="cursor-pointer text-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300">
          Figures in this briefing
        </summary>
        <p className="mt-1 text-slate-400">
          Every figure comes from the engine, but the AI can still attach one to the wrong meaning. Check it here.
        </p>
        <table className="mt-1 w-full border-collapse text-left">
          <caption className="sr-only">Each figure the briefing quotes and what it measures</caption>
          <thead>
            <tr className="border-b border-slate-700 text-slate-400">
              <th scope="col" className="py-1 pr-3 font-medium">What it measures</th>
              <th scope="col" className="py-1 text-right font-medium">Figure</th>
            </tr>
          </thead>
          <tbody>
            {reply.figures.map((f) => (
              <tr key={f.id} className="border-b border-slate-800">
                <th scope="row" className="py-1 pr-3 font-normal">{f.means}</th>
                <td className="py-1 text-right whitespace-nowrap text-slate-100 tabular-nums">{f.text}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      {reply.source === 'engine_only' && (
        <p className="text-sm" style={{ color: ROUTE_COLORS.DEFER }}>
          {ENGINE_ONLY_NOTE}
        </p>
      )}
      <p className="text-sm text-slate-400">{reply.note}</p>
      <AboutResult notice={reply.notice} provenance={reply.provenance} />
    </div>
  );
}

export function BriefingPanel({ scenario, onOpenDecision }: { scenario: ScenarioName; onOpenDecision: (key: string) => void }) {
  // One reply per scenario, so switching back shows the briefing already written.
  const [replies, setReplies] = useState<Partial<Record<ScenarioName, BriefingReply>>>({});
  const [busy, setBusy] = useState<ScenarioName | null>(null);
  const reply = replies[scenario];

  const briefed = reply?.status === 'briefed';

  const onAsk = async () => {
    if (busy !== null) return;
    setBusy(scenario);
    const result = await askBriefing(scenario);
    setReplies((prev) => ({ ...prev, [scenario]: result }));
    setBusy(null);
  };

  // Clears this scenario's briefing so it can be written again.
  const onReset = () => {
    setReplies((prev) => {
      const next = { ...prev };
      delete next[scenario];
      return next;
    });
  };

  return (
    <section aria-labelledby="briefing-heading" className="flex flex-col gap-3 rounded-lg border border-sky-300 bg-slate-900 p-4">
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="briefing-heading" className="text-base font-semibold text-slate-100">
            Operator briefing
          </h2>
          <span className="rounded-full bg-sky-300 px-2 py-0.5 text-xs font-semibold text-slate-950">Real-time AI</span>
        </div>
        <p className="text-sm text-slate-300">
          A short read on this plan and its risks, for whoever runs the site. Gemini writes it from the engine's results and the
          stress test, and every point links to the decision or test behind it.
        </p>
      </div>
      <div>
        {/* One button that changes role, so focus stays on it after a reset. */}
        <button
          type="button"
          onClick={briefed ? onReset : () => void onAsk()}
          aria-disabled={!briefed && busy !== null ? 'true' : undefined}
          className={
            'rounded-md px-4 py-2 text-sm font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300 ' +
            (briefed
              ? 'border border-slate-700 text-slate-300 hover:bg-slate-800 hover:text-slate-100'
              : busy === null
                ? 'bg-sky-300 text-slate-950 hover:bg-sky-200'
                : 'cursor-not-allowed border border-slate-700 bg-slate-800 text-slate-400')
          }
        >
          {briefed ? 'Reset' : busy === scenario ? 'Writing…' : 'Write the briefing'}
        </button>
      </div>
      <div aria-live="polite" className="flex flex-col gap-3">
        {busy === scenario && <p className="text-sm text-slate-300">Reading the plan and the stress test…</p>}
        {busy !== scenario && reply !== undefined && <Result reply={reply} onOpenDecision={onOpenDecision} />}
      </div>
    </section>
  );
}

export default BriefingPanel;
