import { useSyncExternalStore } from 'react';

// Reduced-motion preference (Requirement 20.7, design "Playback").
// Reads matchMedia('(prefers-reduced-motion: reduce)') and re-renders when the
// OS or browser setting changes. Where matchMedia is unavailable, reports
// false (no preference).

const QUERY = '(prefers-reduced-motion: reduce)';

function getMediaQueryList(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null;
  return window.matchMedia(QUERY);
}

function subscribe(onChange: () => void): () => void {
  const mql = getMediaQueryList();
  if (!mql) return () => {};
  mql.addEventListener('change', onChange);
  return () => mql.removeEventListener('change', onChange);
}

function getSnapshot(): boolean {
  return getMediaQueryList()?.matches ?? false;
}

function getServerSnapshot(): boolean {
  return false;
}

/** True while the user prefers reduced motion. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
