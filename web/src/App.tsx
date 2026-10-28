import { useCallback, useEffect, useReducer, useState, type ReactNode } from 'react';
import { DecisionLog } from './components/DecisionLog';
import ErrorBanner from './components/ErrorBanner';
import { Footer } from './components/Footer';
import Header from './components/Header';
import { KpiRow } from './components/KpiRow';
import { MainChart } from './components/MainChart';
import { loadScenarios, type Loaded } from './data/loadScenarios';
import { initialState, reducer, type Mode } from './state/appState';
import type { ScenarioName } from './types';

// App shell (design "Components" and "Layout and style"). Loads both
// Scenario_Files once, then every toggle reads in-memory data only
// (Requirements 17.1–17.3). One page, no routes or in-app links (Requirement 22.8).
//
// Layout, >= 1280 px: header, KPI row, [chart + carbon band | DR + zoom + log] at 2:1,
// fixed footer. Below that: a single column.

export default function App() {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [state, dispatch] = useReducer(reducer, initialState);

  useEffect(() => {
    let live = true;
    // loadScenarios caches its promise, so StrictMode's second effect run
    // makes no extra request.
    void loadScenarios().then((result) => {
      if (live) setLoaded(result);
    });
    return () => {
      live = false;
    };
  }, []);

  const onScenarioChange = useCallback(
    (scenario: ScenarioName) => dispatch({ type: 'setScenario', scenario }),
    [],
  );
  const onModeChange = useCallback((mode: Mode) => dispatch({ type: 'setMode', mode }), []);

  if (loaded === null) {
    return (
      <StatusScreen>
        <p role="status" className="text-slate-300">
          Loading scenarios…
        </p>
      </StatusScreen>
    );
  }

  if (!loaded.ok) {
    return (
      <StatusScreen>
        <ErrorBanner errors={loaded.errors} />
      </StatusScreen>
    );
  }

  // The selected Scenario. Both scenarios are already in memory, so a toggle
  // only re-renders from `loaded` and never fetches (Requirements 17.2, 17.6).
  const scenario = loaded.scenarios[state.scenario];
  const { mode, dr } = state;

  return (
    <div className="flex min-h-screen flex-col">
      <Header
        scenario={state.scenario}
        mode={mode}
        onScenarioChange={onScenarioChange}
        onModeChange={onModeChange}
      />
      {/* Footer.tsx writes its measured height to --footer-h; the inline padding
          keeps the last row of <main> clear of the fixed footer (Requirement 22.12). */}
      <main
        className="grid flex-1 grid-cols-1 content-start gap-4 p-4 xl:grid-cols-3"
        style={{ paddingBottom: 'calc(var(--footer-h, 6rem) + 1rem)' }}
        data-scenario={scenario.meta.scenario}
        data-mode={mode}
        data-dr={dr}
      >
        <div data-area="kpis" className="xl:col-span-3">
          <KpiRow scenario={scenario} mode={mode} dr={dr} />
        </div>
        <div data-area="chart" className="min-w-0 rounded-lg bg-slate-900 p-4 xl:col-span-2">
          {/* MainChart renders the CarbonBand below the plot on the same x-domain. */}
          <MainChart scenario={scenario} mode={mode} />
        </div>
        <div data-area="side" className="flex min-w-0 flex-col gap-4">
          {/* Task 14.2 replaces this slot with DrPanel and ZoomPanel. */}
          <Placeholder name="Demand-response button and zoom (task 14.2)" className="min-h-48" />
          <DecisionLog scenario={scenario} mode={mode} dr={dr} />
        </div>
      </main>
      <Footer scenario={scenario} />
    </div>
  );
}

function StatusScreen({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-4 p-6">
      <h1 className="text-3xl font-semibold tracking-tight text-slate-100">Headroom</h1>
      {children}
    </main>
  );
}

// Temporary grid-area marker for the DR slot; task 14.2 replaces it.
function Placeholder({ name, className = '' }: { name: string; className?: string }) {
  return (
    <div
      className={`flex items-center justify-center rounded-lg border border-dashed border-slate-700 bg-slate-900 text-sm text-slate-400 ${className}`}
    >
      {name}
    </div>
  );
}
