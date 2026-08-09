/**
 * The agent actually EXISTS — the test that catches Flue 2.x's quietest failure.
 *
 * Registration on 2.x comes from the build-time `'use agent'` source-directive
 * scan, and "a converted module without the directive is silently not an agent"
 * (@flue/runtime migration guide). Silently: no error, no warning, no log line.
 * Mounting does not help — `createAgentRouter(Zoe)` publishes routes whether or
 * not `Zoe` is an agent. So a port that drops the directive looks completely
 * healthy right up until the first real turn, which is exactly the kind of defect
 * that must not reach a live voice brain.
 *
 * These assertions therefore go past "the module loaded": the identity resolves,
 * the mounted route accepts a turn, and the model is genuinely reached.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, before, describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';
import { Zoe } from '../src/agents/zoe.ts';
import { startBrainHarness, waitFor, type BrainHarness } from './helpers/harness.ts';

describe('agent registration (2.x `use agent`)', () => {
  it('the agent module carries the `use agent` directive', () => {
    const source = readFileSync(
      fileURLToPath(new URL('../src/agents/zoe.ts', import.meta.url)),
      'utf8',
    );
    // Must be the FIRST statement in the module — a directive prologue only
    // counts before any other code, and the build scan looks for it there.
    assert.match(
      source,
      /^\s*'use agent';/,
      'src/agents/zoe.ts lost the `use agent` directive: it would silently stop being an agent',
    );
  });

  it('Zoe is a synchronous function with a pinned durable identity', () => {
    assert.equal(typeof Zoe, 'function');
    // The 2.x contract: the agent function must be synchronous (async work moves
    // into tools and lifecycle hooks).
    assert.notEqual(Zoe.constructor.name, 'AsyncFunction');
    assert.equal(Zoe.agentName, 'zoe');
  });

  describe('over HTTP', () => {
    let harness: BrainHarness;

    before(async () => {
      harness = await startBrainHarness([{ text: 'Hello there.' }]);
    });
    after(async () => {
      await harness.stop();
    });

    it('/health answers without a token', async () => {
      const res = await harness.app.fetch(new Request('http://brain.test/health'));
      assert.equal(res.status, 200);
      assert.equal(((await res.json()) as { service: string }).service, 'flue-zoe-brain');
    });

    it('the mounted route admits a turn and the agent reaches the model', async () => {
      const res = await harness.send('registration-smoke', 'hello');
      assert.equal(res.status, 202, 'POST /agents/zoe/:id should return a 202 admission');

      const reached = await waitFor(() => harness.model.callCount > 0);
      assert.ok(reached, 'the agent never called the model — is it registered?');

      // A registered agent renders its instructions; an unregistered one could
      // not have produced a system prompt at all.
      const first = harness.model.requests[0];
      assert.match(first.systemPrompt, /You are Zoe\./);
    });
  });
});
