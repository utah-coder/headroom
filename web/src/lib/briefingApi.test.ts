import { describe, expect, it, vi } from 'vitest';
import { askBriefing, parseBriefing, pointEvidence, type BriefingEvidence } from './briefingApi';
import { NOT_CONNECTED } from './whatifApi';

const briefed = {
  status: 'briefed',
  scenario: 'peak',
  scenario_label: 'Peak week',
  headline: 'The plan saves $29,334.',
  points: [{ text: 'Jobs wait.', evidence: ['d1', 'test:forecast'] }],
  evidence: [
    { id: 'd1', kind: 'decision', label: 'Mon 17:15, job 4059: wait', key: '2026-07-20T23:15:00Z|4059' },
    { id: 'test:forecast', kind: 'test', label: 'Real carbon forecast', key: 'forecast' },
  ],
  figures: [{ id: 'plan.usd_saved', text: '$29,334', means: 'money saved' }],
  source: 'model_verified',
  note: 'One run.',
};

describe('parseBriefing', () => {
  it('reads a briefing', () => {
    expect(parseBriefing('application/json', JSON.stringify(briefed)).status).toBe('briefed');
  });

  it('reads a message reply', () => {
    expect(parseBriefing('application/json', JSON.stringify({ status: 'rate_limited', message: 'later' }))).toEqual({
      status: 'rate_limited',
      message: 'later',
    });
  });

  it('treats the CloudFront HTML fallback as not connected', () => {
    expect(parseBriefing('text/html', '<!doctype html>')).toEqual({ status: 'unavailable', message: NOT_CONNECTED });
  });

  it('rejects a briefing without its lists, and malformed JSON', () => {
    expect(parseBriefing('application/json', JSON.stringify({ status: 'briefed' })).status).toBe('unavailable');
    const { figures: _dropped, ...noFigures } = briefed;
    expect(parseBriefing('application/json', JSON.stringify(noFigures)).status).toBe('unavailable');
    expect(parseBriefing('application/json', '{').status).toBe('unavailable');
  });
});

describe('pointEvidence', () => {
  it('keeps the cited order and skips ids the reply does not describe', () => {
    const evidence = briefed.evidence as BriefingEvidence[];
    const found = pointEvidence({ text: 'x', evidence: ['test:forecast', 'nope', 'd1'] }, evidence);
    expect(found.map((e) => e.id)).toEqual(['test:forecast', 'd1']);
  });
});

describe('askBriefing', () => {
  it('posts the scenario with the body hash CloudFront signing needs', async () => {
    const fetchImpl = vi.fn(async () => new Response(JSON.stringify(briefed), { headers: { 'content-type': 'application/json' } }));
    const reply = await askBriefing('peak', fetchImpl as unknown as typeof fetch);
    expect(reply.status).toBe('briefed');
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/briefing');
    expect(init.body).toBe('{"scenario":"peak"}');
    expect((init.headers as Record<string, string>)['x-amz-content-sha256']).toMatch(/^[0-9a-f]{64}$/);
  });

  it('turns a network failure into a message', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('offline');
    });
    expect(await askBriefing('sun', fetchImpl as unknown as typeof fetch)).toEqual({ status: 'unavailable', message: NOT_CONNECTED });
  });
});
