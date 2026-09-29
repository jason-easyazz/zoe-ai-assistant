/**
 * src/user-model.ts offline (stub zoe-data + real sidecar on the mock model): flag off
 * ⇒ wire system prompt byte-identical to a turn that never consults the block; flag on
 * ⇒ that + doctrine + block on EVERY round; never holds a turn; binds per turn; fails open.
 */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { after, before, beforeEach, it } from 'node:test';
import { startBrainHarness, waitFor, type BrainHarness } from './helpers/harness.ts';

const BLOCK = 'You are speaking with Sam (the signed-in user).\nSam is training for a half marathon.';
const stub = { hits: [] as string[], status: 200, body: { version: 'v1', text: BLOCK } as object };
const server = createServer((req, res) => {
  stub.hits.push(`${new URL(req.url ?? '/', 'http://s').searchParams.get('user_id')}|${req.headers['x-internal-token']}`);
  res.writeHead(stub.status, { 'content-type': 'application/json' }).end(JSON.stringify(stub.body));
});
let h: BrainHarness;
let um: typeof import('../src/user-model.ts');
let ident: typeof import('../src/request-identity.ts');

before(async () => {
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  // Every turn = one get_time tool round + the answer: two model calls.
  h = await startBrainHarness((n) => (n % 2 === 0 ? { toolCalls: [{ name: 'get_time' }] } : { text: 'Ok.' }),
    { env: { ZOE_DATA_URL: url, ZOE_INTERNAL_TOKEN: 'tok', ZOE_BRAIN_USER_ID: undefined } });
  um = await import('../src/user-model.ts');
  ident = await import('../src/request-identity.ts');
});
after(async () => {
  await h.stop();
  server.closeAllConnections?.();
  server.close();
});
beforeEach(() => {
  um.resetUserModelCache();
  Object.assign(stub, { hits: [], status: 200, body: { version: 'v1', text: BLOCK } });
});
const turnSignal = (userId: string) => {
  const signal = new AbortController().signal;
  ident.bindTurnUserId(signal, userId);
  return signal;
};
const settle = () => um.refreshUserModel('sam'); // joins (or runs) the background fetch

it('no block ⇒ no suffix, same Context, same policies output', async () => {
  const { applyPolicies } = await import('../src/providers/capped-completions.ts');
  const ctx = { systemPrompt: 'SOUL', messages: [{ role: 'user' as const, content: 'hi', timestamp: 0 }] };
  assert.equal(um.withUserModelBlock(ctx, um.userModelSuffix(' \n')), ctx);
  assert.deepEqual(applyPolicies(ctx, ''), applyPolicies(ctx));
  assert.equal(um.withUserModelBlock(ctx, um.userModelSuffix(BLOCK)).systemPrompt,
    `SOUL\n\n${um.USER_MODEL_DOCTRINE}\n${BLOCK}`);
});

it('cache: never holds a turn, binds per turn, keeps bytes, fails open', async () => {
  const t1 = turnSignal('sam');
  assert.equal(um.turnUserModelSuffix(t1), '', 'cold: no block now, fetch in background');
  await settle();
  assert.deepEqual(stub.hits, ['sam|tok']);
  assert.equal(um.turnUserModelSuffix(t1), '', 'turn 1 keeps what it started with');
  const good = um.turnUserModelSuffix(turnSignal('sam'));
  assert.equal(good, um.userModelSuffix(BLOCK));
  assert.equal(um.turnUserModelSuffix(turnSignal('guest')), '');
  assert.equal(stub.hits.length, 1, 'fresh entry and guest: no fetch');
  await settle(); // same version ⇒ the very same string
  stub.status = 500;
  await settle(); // failure ⇒ last good block kept
  assert.equal(stub.hits.length, 3);
  assert.equal(um.turnUserModelSuffix(turnSignal('sam')), good);
});

it('wire: flag off ⇒ byte-identical prompts; flag on ⇒ baseline + block on every round', async () => {
  const turn = async (sid: string, envelope: string) => {
    const start = h.model.callCount;
    assert.equal((await h.send(sid, `${envelope}what time is it?`)).status, 202);
    assert.ok(await waitFor(() => h.model.callCount >= start + 2), 'turn did not finish');
    return h.model.requests.slice(start).map((r) => r.systemPrompt);
  };
  const [baseline] = await turn('um-base', ''); // no acting user ⇒ never consults the block
  assert.ok(!baseline.includes(um.USER_MODEL_DOCTRINE));
  stub.body = { version: '', text: '' };
  await settle();
  const off = [...(await turn('um-off', ' zoe-uid:sam\n')), ...(await turn('um-off', ' zoe-uid:sam\n'))];
  assert.deepEqual(off, [baseline, baseline, baseline, baseline]);
  stub.body = { version: 'v1', text: BLOCK };
  await settle();
  const on = [...(await turn('um-on', ' zoe-uid:sam\n')), ...(await turn('um-on', ' zoe-uid:sam\n'))];
  const expected = baseline + um.userModelSuffix(BLOCK);
  assert.deepEqual(on, [expected, expected, expected, expected]);
  assert.equal(stub.hits.length, 2, 'no fetch while the entry is fresh');
});
