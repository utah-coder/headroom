import type { ScenarioName } from './types';

// Scenarios the app loads and offers. Set to ['peak'] when the sun scenario is
// cut (cut order step 2, with sim/config.ENABLE_SUN_SCENARIO = False); a single
// entry also hides the scenario toggle (Requirement 24.7).
// Keep this declaration on one line: scripts/deploy.ps1 -SkipSim parses it.
export const SCENARIOS: readonly ScenarioName[] = ['peak', 'sun'];

// Animated Event_Zoom playback. Set to false when the animation is cut
// (cut order step 4); the Zoom_Panel then draws all 600 entries at once
// (Requirements 20.8, 24.9).
export const ANIMATED_ZOOM: boolean = true;

// Longest what-if question. Must equal sim/config.ANALYST_QUESTION_MAX_CHARS (tested).
export const WHATIF_MAX_CHARS = 400;

// Public GitHub repository, linked from the Footer (Requirement 22.4).
export const REPO_URL = 'https://github.com/utah-coder/headroom';

// The responsible-use notice. Must equal analyst/notice.py NOTICE (tested), which every
// analyst reply also carries. Shown once in the footer and with each AI result.
export const RESPONSIBLE_USE =
  'Simulation for demonstration and evaluation. Headroom combines historical grid data with simulated ' +
  'workloads and storage and a published utility tariff. Results are scenario estimates, not forecasts, ' +
  'engineering specifications, utility quotations, or operational instructions. Independently validate ' +
  'inputs and outputs before making financial or operational decisions.';
