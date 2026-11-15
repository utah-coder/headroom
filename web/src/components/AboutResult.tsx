import { RESPONSIBLE_USE } from '../config';
import type { ResultProvenance } from '../lib/whatifApi';

// What an AI result is and isn't, under the result itself. The notice comes from the reply
// when the backend sends one, so the page and a saved reply say the same thing. Closed by
// default: the footer already carries the notice, and the result's own caveat stays visible.

const ROWS: readonly [keyof ResultProvenance, string][] = [
  ['historical_grid_data', 'Historical grid data'],
  ['simulated', 'Simulated'],
  ['illustrative', 'Illustrative'],
  ['outcome', 'This result'],
  ['model', 'Language model'],
  ['build', 'Build'],
];

export function AboutResult({ notice, provenance }: { notice?: string; provenance?: ResultProvenance }) {
  return (
    <details className="text-sm text-slate-300">
      <summary className="cursor-pointer text-slate-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300">
        About this result
      </summary>
      <p className="mt-1 text-slate-300">{notice ?? RESPONSIBLE_USE}</p>
      {provenance && (
        <dl className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-[max-content_1fr]">
          {ROWS.filter(([key]) => provenance[key] !== undefined && provenance[key] !== null).map(([key, label]) => (
            <div key={key} className="contents">
              <dt className="text-slate-400">{label}</dt>
              <dd className="min-w-0 break-words">{String(provenance[key])}</dd>
            </div>
          ))}
        </dl>
      )}
    </details>
  );
}

export default AboutResult;
