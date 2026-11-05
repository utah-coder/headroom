import type { ScenarioName } from '../types';

// The what-if analyst's API. This is the one request the page makes after it
// loads, and only when someone asks a question. Every other panel keeps working
// without it, so any failure here becomes a message in the panel and never throws.

export interface WhatIfRow {
  id: string;
  label: string;
  current: string;
  whatif: string;
  /** Size of the change with no sign; `direction` carries the sign. */
  change: string;
  direction: 'up' | 'down' | 'unchanged';
}

export interface WhatIfSeries {
  t: string[];
  current_grid_kw: number[];
  whatif_grid_kw: number[];
}

export type WhatIfReply =
  | {
      status: 'answered';
      scenario: ScenarioName;
      scenario_label: string;
      answer: string;
      /** model_verified: Claude's text passed the figure check. engine_only: code wrote it. */
      answer_source: 'model_verified' | 'engine_only';
      caveat: string;
      inputs: WhatIfRow[];
      figures: WhatIfRow[];
      series: WhatIfSeries;
    }
  | { status: 'engine_refused'; scenario_label: string; message: string; inputs: WhatIfRow[] }
  | { status: 'out_of_scope' | 'invalid_input' | 'rate_limited' | 'unavailable' | 'error'; message: string };

export const NOT_CONNECTED =
  'The analyst isn’t connected on this deployment. Everything else on this page still works.';
const TIMEOUT_MS = 70_000;
const TIMED_OUT = 'That took too long, so it was stopped. Everything else on this page still works.';

/**
 * Read a reply. CloudFront rewrites 403 and 404 from any origin to index.html
 * with status 200 (the site's single-page fallback), so a backend that is missing
 * or refusing CloudFront looks like a successful HTML page. Only JSON with a known
 * status counts as an answer.
 */
export function parseReply(contentType: string | null, text: string): WhatIfReply {
  if (!contentType?.toLowerCase().includes('application/json')) {
    return { status: 'unavailable', message: NOT_CONNECTED };
  }
  try {
    const body: unknown = JSON.parse(text);
    if (typeof body === 'object' && body !== null && 'status' in body) {
      const reply = body as WhatIfReply;
      if (reply.status === 'answered') return reply;
      if (typeof (reply as { message?: unknown }).message === 'string') return reply;
    }
  } catch {
    // Falls through: malformed JSON is treated like no backend.
  }
  return { status: 'unavailable', message: NOT_CONNECTED };
}

/** SHA-256 of the body as hex. CloudFront's signed requests to a function URL require it on POST. */
async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}

export async function askWhatIf(
  question: string,
  scenario: ScenarioName,
  fetchImpl: typeof fetch = fetch,
): Promise<WhatIfReply> {
  const body = JSON.stringify({ question, scenario });
  try {
    const response = await fetchImpl('/api/whatif', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-amz-content-sha256': await sha256Hex(body) },
      body,
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    return parseReply(response.headers.get('content-type'), await response.text());
  } catch (e) {
    return {
      status: 'unavailable',
      message: e instanceof DOMException && e.name === 'TimeoutError' ? TIMED_OUT : NOT_CONNECTED,
    };
  }
}

// Always shown. The rest appear only when they changed, to keep the table readable.
const ALWAYS_SHOWN = new Set([
  'usd_saved',
  'headroom_bill_usd',
  'headroom_kw',
  'headroom_site_peak_kw',
  'co2_avoided_t',
  'deadlines_met_pct',
  'inference_sla_pct',
  'dr_delivered_pct',
]);

export function visibleFigures(figures: readonly WhatIfRow[]): WhatIfRow[] {
  return figures.filter((f) => ALWAYS_SHOWN.has(f.id) || f.direction !== 'unchanged');
}

/** The change column: a signed size, or "no change". */
export function changeText(row: WhatIfRow): string {
  if (row.direction === 'unchanged') return 'no change';
  return `${row.direction === 'up' ? '+' : '−'}${row.change}`;
}
