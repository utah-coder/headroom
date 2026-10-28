import { useEffect, useRef, useState } from 'react';

// Event_Zoom playback (Requirements 20.3, 20.9, design "Playback").
// A requestAnimationFrame loop advances 60 zoom seconds per real second:
// idx = min(599, floor(elapsedMs * 60 / 1000)), so s = 599 is drawn at
// about 9.98 s. onDone fires once when idx reaches 599. The loop is cancelled
// on unmount or when `active` becomes false. A new `run` value restarts
// playback from s = 0.

export const ZOOM_LAST_INDEX = 599;
const ZOOM_SECONDS_PER_MS = 60 / 1000;

/** Frame index for a given elapsed time since playback start. */
export function zoomIndexAt(elapsedMs: number): number {
  if (!(elapsedMs > 0)) return 0;
  return Math.min(ZOOM_LAST_INDEX, Math.floor(elapsedMs * ZOOM_SECONDS_PER_MS));
}

interface Frame {
  run: number;
  idx: number;
}

/** Returns the frame index 0..599 to draw for the current run. */
export function useZoomPlayback(run: number, active: boolean, onDone: () => void): number {
  const [frame, setFrame] = useState<Frame>({ run, idx: 0 });

  // Keep the latest callback without restarting the loop when its identity changes.
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  // The run for which onDone has already fired, so it fires once per run.
  const doneRunRef = useRef<number | null>(null);

  useEffect(() => {
    if (!active) return;

    let rafId = 0;
    let start: number | null = null;

    const tick = (now: number) => {
      if (start === null) start = now;
      const idx = zoomIndexAt(now - start);
      setFrame((prev) => (prev.run === run && prev.idx === idx ? prev : { run, idx }));
      if (idx >= ZOOM_LAST_INDEX) {
        if (doneRunRef.current !== run) {
          doneRunRef.current = run;
          onDoneRef.current();
        }
        return;
      }
      rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [run, active]);

  // A new run shows s = 0 until its first frame is drawn.
  return frame.run === run ? frame.idx : 0;
}
