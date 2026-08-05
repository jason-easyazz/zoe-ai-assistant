/**
 * The fail-closed bearer gate, with the negative controls the beta suite never had.
 *
 * This is the security boundary the entire forwarded-identity trust model rests
 * on: `src/agents/zoe.ts` states plainly that the envelope `user_id` is trusted
 * PRECISELY BECAUSE the token gate means zoe-data is the sole caller. On 2.x the
 * gate had to be rewritten — `export const route` is a deleted convention, so it
 * became ordinary Hono middleware — and a rewritten security boundary gets
 * re-proven, not eyeballed.
 *
 * Every case below is a NEGATIVE control in the sense that matters: each asserts
 * a request is REFUSED and that the downstream handler never ran. The one that
 * earns its place most is `no token configured` — a gate that fails OPEN when
 * unconfigured is the classic way this goes wrong, it looks fine in every
 * happy-path test, and it is the difference between "a LAN caller cannot drive
 * the voice brain" and "anyone on the network can".
 */
import assert from 'node:assert/strict';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { Hono } from 'hono';
import { requireBrainToken } from '../src/auth.ts';

const SAVED: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const key of ['ZOE_BRAIN_TOKEN', 'ZOE_BRAIN_OPEN']) SAVED[key] = process.env[key];
  delete process.env.ZOE_BRAIN_TOKEN;
  delete process.env.ZOE_BRAIN_OPEN;
});
afterEach(() => {
  for (const [key, value] of Object.entries(SAVED)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

/** Mount the gate exactly as app.ts does, with a downstream that records if it ran. */
function gatedApp(): { app: Hono; ran: () => boolean } {
  let ran = false;
  const app = new Hono();
  app.use('/agents/*', requireBrainToken());
  app.post('/agents/zoe/:id', (c) => {
    ran = true;
    return c.json({ ok: true }, 202);
  });
  return { app, ran: () => ran };
}

async function post(app: Hono, headers: Record<string, string> = {}): Promise<Response> {
  return app.request('/agents/zoe/sess-1', {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...headers },
    body: JSON.stringify({ kind: 'user', body: 'hi' }),
  });
}

describe('fail-closed bearer gate', () => {
  it('REFUSES when no token is configured at all (fail closed, not open)', async () => {
    const { app, ran } = gatedApp();
    const res = await post(app);
    assert.equal(
      res.status,
      401,
      'an unconfigured sidecar must refuse every request — failing open here would let any ' +
        'caller that can reach the port drive the live Gemma brain',
    );
    assert.equal(ran(), false, 'the downstream handler must never run');
  });

  it('REFUSES when no token is configured even if the caller presents one', async () => {
    const { app, ran } = gatedApp();
    const res = await post(app, { authorization: 'Bearer anything' });
    assert.equal(res.status, 401);
    assert.equal(ran(), false);
  });

  it('REFUSES a configured token with no Authorization header', async () => {
    process.env.ZOE_BRAIN_TOKEN = 'secret';
    const { app, ran } = gatedApp();
    const res = await post(app);
    assert.equal(res.status, 401);
    assert.equal(ran(), false);
  });

  it('REFUSES a wrong token', async () => {
    process.env.ZOE_BRAIN_TOKEN = 'secret';
    const { app, ran } = gatedApp();
    const res = await post(app, { authorization: 'Bearer wrong' });
    assert.equal(res.status, 401);
    assert.equal(ran(), false);
  });

  it('REFUSES the right secret under the wrong scheme, and a bare token', async () => {
    process.env.ZOE_BRAIN_TOKEN = 'secret';
    for (const header of ['Basic secret', 'secret', 'bearer secret', 'Bearer  secret']) {
      const { app, ran } = gatedApp();
      const res = await post(app, { authorization: header });
      assert.equal(res.status, 401, `authorization: ${header} must be refused`);
      assert.equal(ran(), false);
    }
  });

  it('REFUSES a token that is a prefix or extension of the real one', async () => {
    process.env.ZOE_BRAIN_TOKEN = 'secret';
    // NOTE a trailing-space variant ('Bearer secret ') is deliberately absent:
    // the Fetch `Headers` implementation strips leading/trailing whitespace from
    // header VALUES before any application code runs, so that request is
    // indistinguishable from the correct one at this layer. Asserting a 401
    // there would be asserting against HTTP itself, and it measured 202.
    // Internal whitespace IS preserved, so 'Bearer  secret' (below) is a real case.
    for (const value of ['Bearer secre', 'Bearer secrets', 'Bearer  secret', 'Bearer secret\tx']) {
      const { app, ran } = gatedApp();
      assert.equal(
        (await post(app, { authorization: value })).status,
        401,
        `authorization: ${JSON.stringify(value)} must be refused`,
      );
      assert.equal(ran(), false);
    }
  });

  it('ADMITS the matching bearer token', async () => {
    process.env.ZOE_BRAIN_TOKEN = 'secret';
    const { app, ran } = gatedApp();
    const res = await post(app, { authorization: 'Bearer secret' });
    assert.equal(res.status, 202);
    assert.equal(ran(), true);
  });

  it('ADMITS everything under the explicit ZOE_BRAIN_OPEN=1 lab escape', async () => {
    process.env.ZOE_BRAIN_OPEN = '1';
    const { app, ran } = gatedApp();
    const res = await post(app);
    assert.equal(res.status, 202);
    assert.equal(ran(), true);
  });

  it('treats any ZOE_BRAIN_OPEN value other than exactly "1" as closed', async () => {
    for (const value of ['true', 'yes', '0', 'TRUE', ' 1']) {
      process.env.ZOE_BRAIN_OPEN = value;
      const { app, ran } = gatedApp();
      assert.equal(
        (await post(app)).status,
        401,
        `ZOE_BRAIN_OPEN=${JSON.stringify(value)} must not open the gate`,
      );
      assert.equal(ran(), false);
    }
  });

  it('reads env per request, so rotating the token needs no restart', async () => {
    process.env.ZOE_BRAIN_TOKEN = 'old';
    const { app } = gatedApp();
    assert.equal((await post(app, { authorization: 'Bearer old' })).status, 202);
    process.env.ZOE_BRAIN_TOKEN = 'new';
    assert.equal((await post(app, { authorization: 'Bearer old' })).status, 401);
    assert.equal((await post(app, { authorization: 'Bearer new' })).status, 202);
  });
});
