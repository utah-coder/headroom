import type { LoadError } from '../data/loadScenarios';

// Names each Scenario_File that failed and whether it failed to load or to
// parse (Requirement 17.3). There is no retry button: reloading the page is
// the only retry.

interface ErrorBannerProps {
  errors: readonly LoadError[];
}

export default function ErrorBanner({ errors }: ErrorBannerProps) {
  return (
    <div role="alert" className="rounded-lg border border-red-800 bg-red-950 p-4 text-red-100">
      <ul className="space-y-1">
        {errors.map((e) => (
          <li key={e.file}>
            <span className="font-semibold">
              Could not {e.kind === 'load' ? 'load' : 'parse'} {e.file}
            </span>
            {e.detail && <span className="text-red-200"> ({e.detail})</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
