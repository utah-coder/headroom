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

// Public GitHub repository, linked from the Footer (Requirement 22.4).
export const REPO_URL = 'https://github.com/utah-coder/headroom';
