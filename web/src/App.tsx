import { useCallback, useEffect, useReducer, useState, type ReactNode } from 'react';
import { DecisionLog } from './components/DecisionLog';
import { DrPanel } from './components/DrPanel';
import ErrorBanner from './components/ErrorBanner';
import { Footer } from './components/Footer';
import Header from './components/Header';
import { KpiRow } from './components/KpiRow';
import { MainChart } from './components/MainChart';
import { ANIMATED_ZOOM } from './config';
import { loadScenarios, type Loaded } from './data/loadScenarios';
import { useReducedMotion } from './lib/useReducedMotion';
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

  // Reduced motion or a cut animation shows the static zoom at once
  // (Requirements 20.7, 20.8, 24.9). The reducer ignores fireDr in Baseline
  // and while playing (20.2, 20.9).
  const reducedMotion = useReducedMotion();
  const instantDr = reducedMotion || !ANIMATED_ZOOM;
  const onFireDr = useCallback(() => dispatch({ type: 'fireDr', instant: instantDr }), [instantDr]);
  const onDrDone = useCallback(() => dispatch({ type: 'drComplete' }), []);

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
          {/* drComplete (or an instant fire) sets dr = 'done', which shows the KPI
              DR strip and lists DR_Event decisions first (Requirements 20.5, 20.6). */}
          <DrPanel
            scenario={scenario}
            mode={mode}
            dr={dr}
            drRun={state.drRun}
            onFire={onFireDr}
            onDone={onDrDone}
          />
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
