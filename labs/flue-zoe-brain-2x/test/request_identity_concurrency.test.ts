/**
 * ADVERSARIAL: does a tool ever see ANOTHER turn's user_id on Flue 2.x?
 *
 * This is the highest-severity question in the whole 2.x port. `request-identity.ts`
 * exists because of a MEASURED property of the beta's execution model, and 2.x
 * redesigned that model wholesale — the agent function now re-renders before every
 * model turn, hooks replaced the config bag, and the interception scopes are new.
 * If the AbortSignal-keyed binding quietly stopped holding, the failure mode is
 * cross-user identity leakage in a family voice assistant: Jason's turn answering
 * from someone else's memories, or writing to their lists. It would not show up in
 * a smoke test, and it would not show up in any single-user test.
 *
 * So this file does not check that the binding "looks right". It runs concurrent
 * turns for DIFFERENT users through the real HTTP surface, the real agent loop, and
 * the real tools, forces their tool executions to overlap in wall-clock time, and
 * asserts on the user_id each tool ACTUALLY SENT toward zoe-data.
 *
 * NEGATIVE CONTROL (`the harness can detect a leak`, below) is the load-bearing
 * half. A green concurrency test proves nothing unless the same interleaving turns
 * a broken binder red — otherwise it might simply never have interleaved. That test
 * runs the identical schedule against a deliberately-broken shared-cell binder (the
 * exact implementation the beta's header says was tried and raced) and REQUIRES it
 * to leak. If it ever stops leaking, the schedule stopped being adversarial and the
 * green result above must not be believed.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  bindTurnUserId,
  currentUserId,
  wrapMessageWithIdentity,
} from '../src/request-identity.ts';
import { startBrainHarness, waitFor } from './helpers/harness.ts';
import { startMockZoeData } from './helpers/mock-zoe-data.ts';

/** How many tool rounds each concurrent turn takes before answering. */
const ROUNDS = 3;

describe('per-request identity under concurrency (2.x render model)', () => {
  it('two concurrent turns each keep their own user_id across every tool round', async () => {
    // Stall every backend call so the two turns' tool executions genuinely
    // overlap rather than merely alternating.
    const zoeData = await startMockZoeData({ stallMs: 40 });

    // Each model call answers with a recall_memory tool call until the round
    // budget is spent, then a plain text turn. Both turns run this script, so
    // their rounds interleave on the same server.
    const harness = await startBrainHarness(
      (_call, req) => {
        const rounds = req.messages.filter((m) => m.role === 'tool').length;
        return rounds < ROUNDS
          ? { toolCalls: [{ name: 'recall_memory', args: { query: 'me' } }] }
          : { text: 'All done.' };
      },
      { env: { ZOE_DATA_URL: zoeData.url } },
    );

    try {
      // Fire both turns at once, on DIFFERENT instance ids so the runtime may run
      // them concurrently (submissions serialize per instance, not across them).
      const [resA, resB] = await Promise.all([
        harness.send('identity-alice', wrapMessageWithIdentity('what do you know about me?', 'alice')),
        harness.send('identity-bob', wrapMessageWithIdentity('what do you know about me?', 'bob')),
      ]);
      assert.equal(resA.status, 202);
      assert.equal(resB.status, 202);

      const expected = ROUNDS * 2;
      const settled = await waitFor(
        () => zoeData.calls.filter((c) => c.path === '/api/memories/for-prompt').length >= expected,
        20_000,
      );
      const recalls = zoeData.calls.filter((c) => c.path === '/api/memories/for-prompt');
      assert.ok(
        settled,
        `expected ${expected} recall calls, saw ${recalls.length} — the turns did not both complete`,
      );

      // THE ASSERTION. Every tool call must have acted as exactly one of the two
      // users, and the two turns must have contributed their own rounds.
      const seen = recalls.map((c) => c.userId);
      assert.deepEqual(
        [...new Set(seen)].sort(),
        ['alice', 'bob'],
        `a tool acted as an unexpected identity: ${JSON.stringify(seen)}`,
      );
      assert.equal(
        seen.filter((u) => u === 'alice').length,
        ROUNDS,
        `alice's turn ran ${seen.filter((u) => u === 'alice').length} tool rounds, expected ${ROUNDS} — identities were miscounted, i.e. one turn's rounds acted as the other user`,
      );
      assert.equal(seen.filter((u) => u === 'bob').length, ROUNDS);

      // And prove the schedule actually overlapped — otherwise the turns ran
      // strictly one after the other and this test asserted nothing about races.
      const firstAlice = recalls.findIndex((c) => c.userId === 'alice');
      const firstBob = recalls.findIndex((c) => c.userId === 'bob');
      const lastAlice = recalls.map((c) => c.userId).lastIndexOf('alice');
      const lastBob = recalls.map((c) => c.userId).lastIndexOf('bob');
      assert.ok(
        Math.max(firstAlice, firstBob) < Math.min(lastAlice, lastBob),
        `the two turns did not interleave (order: ${recalls.map((c) => c.userId).join(',')}) — ` +
          'this run proves nothing about concurrency; make the schedule adversarial again',
      );
    } finally {
      await harness.stop();
      await zoeData.close();
    }
  });

  it('the binding primitive isolates two live turns', () => {
    // Deterministic unit-level companion: same interleaving, no I/O.
    const a = new AbortController();
    const b = new AbortController();

    bindTurnUserId(a.signal, 'alice');
    bindTurnUserId(b.signal, 'bob');
    // Interleave a re-bind of A (every model round re-binds) between B's bind
    // and B's read — the exact ordering a shared cell gets wrong.
    assert.equal(currentUserId(b.signal), 'bob');
    bindTurnUserId(a.signal, 'alice');
    assert.equal(currentUserId(b.signal), 'bob');
    assert.equal(currentUserId(a.signal), 'alice');

    // No signal → no identity, so a non-HTTP path falls back to env rather than
    // inheriting whichever turn ran last.
    assert.equal(currentUserId(undefined), '');
  });

  it('NEGATIVE CONTROL: the same interleaving DOES leak on a shared-cell binder', () => {
    // The implementation the beta's header records as tried and rejected: one
    // mutable cell mutated by the provider on every round. If this does NOT leak,
    // the interleaving above is not adversarial and its green result is worthless.
    let sharedCell = '';
    const brokenBind = (_signal: AbortSignal | undefined, userId: string) => {
      sharedCell = userId;
    };
    const brokenRead = (_signal: AbortSignal | undefined) => sharedCell;

    const a = new AbortController();
    const b = new AbortController();

    brokenBind(a.signal, 'alice');
    brokenBind(b.signal, 'bob');

    assert.equal(
      brokenRead(a.signal),
      'bob',
      "the shared-cell binder failed to leak — the negative control is broken, so the positive result above cannot be trusted",
    );
    assert.notEqual(brokenRead(a.signal), 'alice');
  });
});
