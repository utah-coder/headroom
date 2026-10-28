import { SCENARIOS } from '../config';
import type { Mode } from '../state/appState';
import type { ScenarioName } from '../types';
import ToggleGroup, { type ToggleOption } from './ToggleGroup';

// Title, tagline and the Scenario and Mode toggles (Requirements 18.1–18.3, 24.7).

const SCENARIO_LABELS: Record<ScenarioName, string> = {
  peak: 'Peak week',
  sun: 'Sunny day',
};

const SCENARIO_OPTIONS: readonly ToggleOption<ScenarioName>[] = SCENARIOS.map((s) => ({
  value: s,
  label: SCENARIO_LABELS[s],
}));

const MODE_OPTIONS: readonly ToggleOption<Mode>[] = [
  { value: 'baseline', label: 'Baseline' },
  { value: 'headroom', label: 'Headroom' },
];

interface HeaderProps {
  scenario: ScenarioName;
  mode: Mode;
  onScenarioChange: (scenario: ScenarioName) => void;
  onModeChange: (mode: Mode) => void;
}

export default function Header({ scenario, mode, onScenarioChange, onModeChange }: HeaderProps) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-x-8 gap-y-3 px-4 pt-4">
      <div>
        <h1 className="text-3xl font-semibold tracking-tight text-slate-100">Headroom</h1>
        <p className="text-base text-slate-300">Where should this kilowatt-hour come from?</p>
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {/* With the sun scenario cut there is nothing to choose (Requirement 24.7). */}
        {SCENARIO_OPTIONS.length > 1 && (
          <ToggleGroup
            label="Scenario"
            options={SCENARIO_OPTIONS}
            value={scenario}
            onChange={onScenarioChange}
          />
        )}
        <ToggleGroup label="Mode" options={MODE_OPTIONS} value={mode} onChange={onModeChange} />
      </div>
    </header>
  );
}
