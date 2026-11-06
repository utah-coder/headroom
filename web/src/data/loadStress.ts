import type { StressFile } from '../stressTypes';

// Loads scenarios/stress.json once per page load, same-origin like the Scenario_Files.
// Only the stress panel reads it, so a failure there leaves the rest of the page alone.

let once: Promise<StressFile | null> | null = null;

export function loadStress(): Promise<StressFile | null> {
  if (once === null) once = fetchStress();
  return once;
}

export function resetLoadStressForTests(): void {
  once = null;
}

async function fetchStress(): Promise<StressFile | null> {
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}scenarios/stress.json`);
    if (!res.ok) return null;
    const data: unknown = JSON.parse(await res.text());
    return isStressFile(data) ? data : null;
  } catch {
    return null;
  }
}

// A missing file can reach the browser as index.html with status 200, so check the shape.
export function isStressFile(data: unknown): data is StressFile {
  if (typeof data !== 'object' || data === null) return false;
  const obj = data as Record<string, unknown>;
  return ['meta', 'carbon_accuracy', 'demand_accuracy', 'scenarios', 'replay'].every((k) => k in obj);
}
