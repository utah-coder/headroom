import { useCallback, useEffect, useReducer, useState, type ReactNode } from 'react';
import ErrorBanner from './components/ErrorBanner';
import Header from './components/Header';
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

  // The selected Scenario; task 12.6 passes it, with mode and dr, to the panels.
  const scenario = loaded.scenarios[state.scenario];

  return (
    <div className="flex min-h-screen flex-col">
      <Header
        scenario={state.scenario}
        mode={state.mode}
        onScenarioChange={onScenarioChange}
        onModeChange={onModeChange}
      />
      <main
        className="grid flex-1 grid-cols-1 gap-4 p-4 xl:grid-cols-3"
        style={{ paddingBottom: 'calc(var(--footer-h, 0px) + 1rem)' }}
        data-scenario={scenario.meta.scenario}
        data-mode={state.mode}
        data-dr={state.dr}
      >
        <section aria-label="Key results" data-area="kpis" className="xl:col-span-3">
          <Placeholder name="KPI row" className="min-h-36" />
        </section>
        <section aria-label="Grid draw" data-area="chart" className="flex flex-col gap-2 xl:col-span-2">
          <Placeholder name="Main chart" className="min-h-80 flex-1 xl:min-h-[520px]" />
          <Placeholder name="Carbon band" className="min-h-12" />
        </section>
        <section aria-label="Demand response and decisions" data-area="side" className="flex flex-col gap-4">
          <Placeholder name="Demand-response button and zoom" className="min-h-48" />
          <Placeholder name="Decision log" className="min-h-48 flex-1" />
        </section>
      </main>
      <div data-area="footer">
        <Placeholder name="Footer" className="min-h-16" />
      </div>
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

// Temporary grid-area marker; task 12.6 replaces each one with its panel.
function Placeholder({ name, className = '' }: { name: string; className?: string }) {
  return (
    <div
      className={`flex items-center justify-center rounded-lg border border-dashed border-slate-700 bg-slate-900 text-sm text-slate-400 ${className}`}
    >
      {name}
    </div>
  );
}
