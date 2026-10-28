import type { ScenarioName } from '../types';

// App state and its pure reducer (design: "State model"). Used with
// useReducer in App.tsx. Ignored actions return the same state object so
// React can skip the re-render.

// 'naive' is the cheapest-hour comparison (DECISIONS.md, "Cheapest hour").
export type Mode = 'baseline' | 'naive' | 'headroom';
export type DrPhase = 'idle' | 'playing' | 'done';

export interface AppState {
  scenario: ScenarioName;
  mode: Mode;
  dr: DrPhase;
  drRun: number; // bumps on each animated fire so playback restarts at s = 0
}

export type Action =
  | { type: 'setScenario'; scenario: ScenarioName } // dr → 'idle' (Req 18.4, 20.10)
  | { type: 'setMode'; mode: Mode } // dr → 'idle' (Req 18.9, 20.10)
  | { type: 'fireDr'; instant: boolean } // ignored outside Headroom or while playing (Req 20.2, 20.9)
  | { type: 'drComplete' }; // playing → done

// "Peak week" and "Baseline" selected on load (Req 18.2, 18.3).
export const initialState: AppState = {
  scenario: 'peak',
  mode: 'baseline',
  dr: 'idle',
  drRun: 0,
};

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case 'setScenario':
      if (state.scenario === action.scenario && state.dr === 'idle') return state;
      return { ...state, scenario: action.scenario, dr: 'idle' };

    case 'setMode':
      if (state.mode === action.mode && state.dr === 'idle') return state;
      return { ...state, mode: action.mode, dr: 'idle' };

    case 'fireDr':
      if (state.mode !== 'headroom' || state.dr === 'playing') return state;
      // Reduced motion or ANIMATED_ZOOM=false: show the static chart at once (Req 20.7, 20.8).
      if (action.instant) return state.dr === 'done' ? state : { ...state, dr: 'done' };
      // Animated: start (or replay from s = 0) a new run (Req 20.3, 20.9).
      return { ...state, dr: 'playing', drRun: state.drRun + 1 };

    case 'drComplete':
      if (state.dr !== 'playing') return state;
      return { ...state, dr: 'done' };
  }
}
