import { useCallback, useEffect, useReducer, useState, type ReactNode } from 'react';
import { DecisionLog } from './components/DecisionLog';
import { DrPanel } from './components/DrPanel';
import ErrorBanner from './components/ErrorBanner';
import { Footer } from './components/Footer';
import { Guide, type GuideStep } from './components/Guide';
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
// Layout, >= 1280 px: header, guide, KPI row, [chart + carbon band | DR + zoom + log] at 2:1,
// fixed footer. The decision log fills the rest of the side column, so at
// 1920x1080 everything down to the footer fits without scrolling, with or
// without the KPI DR strip (Requirement 22.9). Below that: a single column.

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

  // The Guide shows on first visit; hiding it is remembered in this browser
  // only. Storage can be unavailable (private mode, blocked site data), so
  // every access is guarded and the guide simply shows.
  const [guideShown, setGuideShown] = useState(() => readGuideShown());
  const onToggleGuide = useCallback((shown: boolean) => {
    setGuideShown(shown);
    writeGuideShown(shown);
  }, []);

  // Reduced motion or a cut animation shows the static zoom at once
  // (Requirements 20.7, 20.8, 24.9). The reducer ignores fireDr in Baseline
  // and while playing (20.2, 20.9).
  const reducedMotion = useReducedMotion();
  const instantDr = reducedMotion || !ANIMATED_ZOOM;
  const onFireDr = useCallback(() => dispatch({ type: 'fireDr', instant: instantDr }), [instantDr]);
  const onDrDone = useCallback(() => dispatch({ type: 'drComplete' }), []);

  // Guide steps: 1 = Baseline, 2 = Headroom, 3 = Headroom and fire the event.
  // setMode resets the DR phase, so step 3 replays the event from s = 0.
  const onStep = useCallback(
    (step: GuideStep) => {
      dispatch({ type: 'setMode', mode: step === 1 ? 'baseline' : 'headroom' });
      if (step === 3) dispatch({ type: 'fireDr', instant: instantDr });
    },
    [instantDr],
  );

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
        onShowGuide={guideShown ? undefined : () => onToggleGuide(true)}
      />
      {/* Footer.tsx writes its measured height to --footer-h; the inline padding
          keeps the last row of <main> clear of the fixed footer (Requirement 22.12). */}
      <main
        className="grid flex-1 grid-cols-1 content-start gap-4 p-4 xl:grid-cols-3 xl:gap-3"
        style={{ paddingBottom: 'calc(var(--footer-h, 6rem) + 1rem)' }}
        data-scenario={scenario.meta.scenario}
        data-mode={mode}
        data-dr={dr}
      >
        {guideShown && (
          <div data-area="guide" className="xl:col-span-3">
            <Guide scenario={scenario} mode={mode} dr={dr} onStep={onStep} onHide={() => onToggleGuide(false)} />
          </div>
        )}
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

const GUIDE_KEY = 'headroom.guideHidden';

function readGuideShown(): boolean {
  try {
    return window.localStorage.getItem(GUIDE_KEY) !== '1';
  } catch {
    return true;
  }
}

function writeGuideShown(shown: boolean): void {
  try {
    if (shown) window.localStorage.removeItem(GUIDE_KEY);
    else window.localStorage.setItem(GUIDE_KEY, '1');
  } catch {
    // Not remembered; the choice still holds for this page view.
  }
}

function StatusScreen({ children }: { children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center gap-4 p-6">
      <h1 className="text-3xl font-semibold tracking-tight text-slate-100">Headroom</h1>
      {children}
    </main>
  );
}
