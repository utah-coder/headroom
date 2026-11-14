import type { ScenarioName } from '../types';
import { NOT_CONNECTED, sha256Hex } from './whatifApi';

// The operator briefing's API. Like the what-if analyst, it's a request the page makes
// only when someone asks, and any failure becomes a message in the panel.

export interface BriefingEvidence {
  id: string;
  kind: 'decision' | 'test';
  /** Written by code, never by the model. */
  label: string;
  /** decision: the Decision_Log key ("t|job"); test: the stress family id, or "replay". */
  key: string | null;
}

/** A figure the briefing quotes, with what it measures. */
export interface BriefingFigure {
  id: string;
  text: string;
  means: string;
}

export interface BriefingPoint {
  text: string;
  evidence: string[];
}

export type BriefingReply =
  | {
      status: 'briefed';
      scenario: ScenarioName;
      scenario_label: string;
      headline: string;
      points: BriefingPoint[];
      evidence: BriefingEvidence[];
      figures: BriefingFigure[];
      /** model_verified: Gemini's text passed the checks. engine_only: code wrote it. */
      source: 'model_verified' | 'engine_only';
      note: string;
    }
  | { status: 'rate_limited' | 'unavailable' | 'error'; message: string };

const TIMEOUT_MS = 70_000;
const TIMED_OUT = 'That took too long, so it was stopped. Everything else on this page still works.';

/** Only JSON with a known shape counts; CloudFront's HTML fallback means no backend. */
export function parseBriefing(contentType: string | null, text: string): BriefingReply {
  if (!contentType?.toLowerCase().includes('application/json')) {
    return { status: 'unavailable', message: NOT_CONNECTED };
  }
  try {
    const body: unknown = JSON.parse(text);
    if (typeof body === 'object' && body !== null && 'status' in body) {
      const reply = body as BriefingReply;
      if (reply.status === 'briefed' && Array.isArray(reply.points) && Array.isArray(reply.evidence) && Array.isArray(reply.figures)) {
        return reply;
      }
      if (reply.status !== 'briefed' && typeof (reply as { message?: unknown }).message === 'string') return reply;
    }
  } catch {
    // Falls through: malformed JSON is treated like no backend.
  }
  return { status: 'unavailable', message: NOT_CONNECTED };
}

export async function askBriefing(scenario: ScenarioName, fetchImpl: typeof fetch = fetch): Promise<BriefingReply> {
  const body = JSON.stringify({ scenario });
  try {
    const response = await fetchImpl('/api/briefing', {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-amz-content-sha256': await sha256Hex(body) },
      body,
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    return parseBriefing(response.headers.get('content-type'), await response.text());
  } catch (e) {
    return {
      status: 'unavailable',
      message: e instanceof DOMException && e.name === 'TimeoutError' ? TIMED_OUT : NOT_CONNECTED,
    };
  }
}

/** A point's evidence, in the order it cites them, skipping any id the reply doesn't describe. */
export function pointEvidence(point: BriefingPoint, evidence: readonly BriefingEvidence[]): BriefingEvidence[] {
  const byId = new Map(evidence.map((e) => [e.id, e]));
  return point.evidence.flatMap((id) => {
    const found = byId.get(id);
    return found === undefined ? [] : [found];
  });
}
