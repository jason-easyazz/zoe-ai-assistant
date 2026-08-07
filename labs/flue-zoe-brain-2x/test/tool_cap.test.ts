/**
 * The per-turn tool-iteration cap, proven end-to-end against a model that never
 * stops calling tools — WITH the negative control that makes the proof mean
 * something.
 *
 * WHY THIS MATTERS ON 2.x SPECIFICALLY. The whole reason to check was the
 * hypothesis that Flue 2.x / pi-agent-core 0.83 might finally ship a first-party
 * iteration ceiling, making this bespoke mechanism deletable. It does not:
 * pi-agent-core 0.83's `runLoop` is still `while (true)` with no `maxIterations`
 * symbol anywhere in its dist, and `MAX_FOLLOWUPS = 32` still bounds follow-up
 * PROMPTS on the result-tools path, not tool-call rounds. So the cap survives the
 * port as our own code, and it has to be re-proven on the new provider seam —
 * the mechanism moved from `registerApiProvider` to `createProvider({ api })`,
 * which is exactly the kind of move that silently stops taking effect.
 *
 * THE ASSERTION IS MADE ON THE WIRE. The mock model records the tools it was
 * OFFERED on each call. "The cap works" means: after N rounds the model is
 * handed an EMPTY tool list, so it physically cannot request another tool and
 * the agent loop exits with a real assistant message — no error, no hang.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { startBrainHarness, waitFor } from './helpers/harness.ts';

const CAP = 3;

/** A model that ALWAYS asks for another tool, as long as it is offered any. */
function alwaysLoops(_call: number, req: { toolNames: string[] }) {
  return req.toolNames.length > 0
    ? { toolCalls: [{ name: 'get_time' }] }
    : { text: 'Fine — here is my answer without more tools.' };
}

describe('per-turn tool-iteration cap', () => {
  it('stops a runaway tool loop at ZOE_BRAIN_MAX_TOOL_ITERS', async () => {
    const harness = await startBrainHarness(alwaysLoops, {
      env: { ZOE_BRAIN_MAX_TOOL_ITERS: String(CAP) },
    });

    try {
      const res = await harness.send('cap-runaway', 'what time is it, repeatedly?');
      assert.equal(res.status, 202);

      // The turn must SETTLE. If the cap were not applied this never happens —
      // which is precisely what the negative control below demonstrates.
      const settled = await waitFor(
        () => harness.model.requests.some((r) => r.toolNames.length === 0),
        20_000,
      );
      assert.ok(
        settled,
        `the model was never handed an empty tool list — the cap did not engage. ` +
          `Calls seen: ${harness.model.requests.map((r) => r.toolNames.length).join(',')}`,
      );

      const offered = harness.model.requests.map((r) => r.toolNames.length);
      const strippedAt = offered.indexOf(0);

      // Exactly CAP rounds are allowed to carry tools before the strip.
      assert.equal(
        strippedAt,
        CAP,
        `tools were stripped after ${strippedAt} rounds, expected ${CAP} (offered: ${offered.join(',')})`,
      );
      for (let i = 0; i < CAP; i++) {
        assert.ok(offered[i] > 0, `round ${i} should still have been offered tools`);
      }

      // And the capped call must carry the wrap-up steer — on a trailing user
      // message, NOT in the system prompt (which is the cached prompt prefix).
      const capped = harness.model.requests[strippedAt];
      const last = capped.messages[capped.messages.length - 1];
      assert.equal(last.role, 'user');
      assert.match(last.text, /reached the tool-call limit/i);
      assert.equal(
        capped.systemPrompt,
        harness.model.requests[0].systemPrompt,
        'the cap must not mutate the system prompt — that is the KV-cached prefix',
      );

      // The loop really ended: no further calls after the model answered.
      const afterAnswer = harness.model.callCount;
      await new Promise((r) => setTimeout(r, 500));
      assert.equal(harness.model.callCount, afterAnswer, 'the agent loop kept running past the cap');
    } finally {
      await harness.stop();
    }
  });

  it('NEGATIVE CONTROL: with the cap raised out of reach, the same model loops unbounded', async () => {
    // The control for "the cap is what stopped it". Same runaway model, same
    // everything — only the ceiling moves out of reach. If the loop stopped here
    // anyway, something OTHER than our cap was terminating the turn and the test
    // above would be proving nothing. (Raising the cap is the honest way to run
    // this: deleting `applyCap` would need a source edit, and this reaches the
    // identical code path with the ceiling never satisfied.)
    const harness = await startBrainHarness(alwaysLoops, {
      env: { ZOE_BRAIN_MAX_TOOL_ITERS: '100000' },
    });

    try {
      const res = await harness.send('cap-uncapped', 'what time is it, repeatedly?');
      assert.equal(res.status, 202);

      // Let it run well past where the capped run had already finished.
      await waitFor(() => harness.model.callCount > CAP * 3, 15_000);

      assert.ok(
        harness.model.callCount > CAP * 3,
        `expected an unbounded loop but saw only ${harness.model.callCount} calls — ` +
          'if the framework now bounds tool rounds by itself, re-check whether this cap is still needed',
      );
      assert.ok(
        harness.model.requests.every((r) => r.toolNames.length > 0),
        'tools were stripped even with the cap out of reach — the negative control is broken, ' +
          'so the positive result above cannot be trusted',
      );

      // Leave nothing running: abort the conversation before tearing down.
      await harness.app.fetch(
        new Request('http://brain.test/agents/zoe/cap-uncapped/abort', {
          method: 'POST',
          headers: { authorization: `Bearer ${harness.token}` },
        }),
      );
    } finally {
      await harness.stop();
    }
  });
});
