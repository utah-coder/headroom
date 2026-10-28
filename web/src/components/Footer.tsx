import { useLayoutEffect, useRef } from 'react';
import { REPO_URL } from '../config';
import type { Scenario } from '../types';
import '../footer.css';

// Always-visible provenance bar (Requirements 15.8, 22.1–22.5, 22.12).

/** Exact provenance sentence (Requirement 22.2). */
const PROVENANCE_SENTENCE =
  'Grid data: EIA-930, PacifiCorp East (real). Site, jobs, storage, tariff: simulated and illustrative.';

interface FooterProps {
  /** The displayed scenario; its `meta.built_at` is shown (Requirement 22.5). */
  scenario: Scenario;
}

export function Footer({ scenario }: FooterProps) {
  const ref = useRef<HTMLElement>(null);

  // Publish the footer's height as --footer-h so <main> can pad by it.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const root = document.documentElement;
    const write = () => root.style.setProperty('--footer-h', `${Math.ceil(el.getBoundingClientRect().height)}px`);
    write();
    const ro = new ResizeObserver(write);
    ro.observe(el);
    return () => {
      ro.disconnect();
      root.style.removeProperty('--footer-h');
    };
  }, []);

  const builtAt = scenario.meta.built_at;

  return (
    <footer
      ref={ref}
      className="fixed inset-x-0 bottom-0 z-10 border-t border-slate-800 bg-slate-900 px-4 py-2 text-xs leading-snug text-slate-300"
    >
      <div className="mx-auto flex max-w-screen-2xl flex-wrap items-baseline gap-x-6 gap-y-1">
        <p className="min-w-0 break-words text-slate-100">{PROVENANCE_SENTENCE}</p>
        <p className="min-w-0 break-words">
          <span className="font-semibold text-slate-100">National planning context:</span> Duke Nicholas
          Institute, <cite>Rethinking Load Growth</cite> (Feb 2025): the largest US balancing areas could
          integrate about 76 GW of new load with 0.25% average annual curtailment.
        </p>
        <p className="flex min-w-0 flex-wrap gap-x-4 gap-y-1 text-slate-400">
          <a
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="break-all text-slate-100 underline underline-offset-2 hover:text-slate-300 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-100"
          >
            Source on GitHub<span className="sr-only"> (opens in a new tab)</span>
          </a>
          <span>
            Built <time dateTime={builtAt}>{builtAt}</time>
          </span>
        </p>
      </div>
    </footer>
  );
}
