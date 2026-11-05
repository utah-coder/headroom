import { describe, expect, it } from 'vitest';
import { NOT_CONNECTED, askWhatIf, changeText, parseReply, visibleFigures, type WhatIfRow } from './whatifApi';

const row = (id: string, direction: WhatIfRow['direction'], change = '$5'): WhatIfRow => ({
  id,
  label: id,
  current: '$1',
  whatif: '$6',
  change,
  direction,
});

describe('parseReply', () => {
  it('reads an answer', () => {
    const reply = parseReply('application/json', JSON.stringify({ status: 'answered', answer: 'ok' }));
    expect(reply.status).toBe('answered');
  });

  it('reads a message reply', () => {
    const reply = parseReply('application/json', JSON.stringify({ status: 'rate_limited', message: 'later' }));
    expect(reply).toEqual({ status: 'rate_limited', message: 'later' });
  });

  it('treats the CloudFront HTML fallback as not connected, not as an answer', () => {
    // A refused or missing backend reaches the browser as index.html with status 200.
    expect(parseReply('text/html', '<!doctype html><title>Headroom</title>')).toEqual({
      status: 'unavailable',
      message: NOT_CONNECTED,
    });
  });

  it.each([
    [null, '{}'],
    ['application/json', 'not json'],
    ['application/json', '[]'],
    ['application/json', 'null'],
    ['application/json', '{"status":"out_of_scope"}'],
    ['application/json', '{"status":"error","message":5}'],
  ])('treats %s %s as not connected', (type, text) => {
    expect(parseReply(type, text).status).toBe('unavailable');
  });
});

describe('askWhatIf', () => {
  const reply = (body: string, type = 'application/json') =>
    new Response(body, { headers: { 'content-type': type } });

  it('posts the question with the body hash CloudFront needs', async () => {
    let init: RequestInit | undefined;
    let url = '';
    const fake = (async (u: string, i: RequestInit) => {
      url = u;
      init = i;
      return reply('{"status":"out_of_scope","message":"no"}');
    }) as unknown as typeof fetch;

    await askWhatIf('why?', 'sun', fake);

    expect(url).toBe('/api/whatif');
    expect(init?.method).toBe('POST');
    const body = init?.body as string;
    expect(JSON.parse(body)).toEqual({ question: 'why?', scenario: 'sun' });
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(body));
    const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
    expect((init?.headers as Record<string, string>)['x-amz-content-sha256']).toBe(hex);
  });

  it('hashes non-ASCII text by its UTF-8 bytes, as AWS does', async () => {
    let init: RequestInit | undefined;
    const fake = (async (_: string, i: RequestInit) => {
      init = i;
      return reply('{"status":"error","message":"x"}');
    }) as unknown as typeof fetch;

    await askWhatIf('CO₂?', 'peak', fake);

    const bytes = new TextEncoder().encode(init?.body as string);
    expect(bytes.length).toBeGreaterThan((init?.body as string).length);
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const hex = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
    expect((init?.headers as Record<string, string>)['x-amz-content-sha256']).toBe(hex);
  });

  it('turns a network failure into a message and never throws', async () => {
    const fake = (async () => {
      throw new TypeError('failed to fetch');
    }) as unknown as typeof fetch;
    await expect(askWhatIf('q', 'peak', fake)).resolves.toEqual({ status: 'unavailable', message: NOT_CONNECTED });
  });

  it('says so when the request timed out', async () => {
    const fake = (async () => {
      throw new DOMException('timed out', 'TimeoutError');
    }) as unknown as typeof fetch;
    const out = await askWhatIf('q', 'peak', fake);
    expect(out.status).toBe('unavailable');
    expect('message' in out && out.message).toContain('too long');
  });
});

describe('visibleFigures', () => {
  it('keeps headline figures and hides unchanged detail rows', () => {
    const shown = visibleFigures([
      row('usd_saved', 'unchanged'),
      row('baseline_bill_usd', 'unchanged'),
      row('move_decisions', 'unchanged'),
      row('defer_decisions', 'up'),
    ]);
    expect(shown.map((f) => f.id)).toEqual(['usd_saved', 'defer_decisions']);
  });
});

describe('changeText', () => {
  it('adds the sign the engine reported', () => {
    expect(changeText(row('a', 'up'))).toBe('+$5');
    expect(changeText(row('a', 'down'))).toBe('−$5');
    expect(changeText(row('a', 'unchanged', '$0'))).toBe('no change');
  });
});
