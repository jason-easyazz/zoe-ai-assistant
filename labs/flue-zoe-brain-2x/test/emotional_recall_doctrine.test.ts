/**
 * Unit coverage for the emotional-recall doctrine (LAB-ONLY, offline).
 *
 * Samantha criterion #2 (emotional-thread recall): the soul only pulled memory
 * when directly asked about stored FACTS, so an emotional turn ("how have I
 * been?") didn't reliably call recall_memory. This doctrine widens WHEN to call
 * it. Measured on the live 4B brain at 4/4 (vs a flaky baseline).
 *
 * This test pins the doctrine into the instructions and guards against
 * regressions — including that the DROPPED "volunteer memory on a bare greeting"
 * rule (measured ~1/5 on the 4B brain, deflecting to "nothing on your calendar")
 * does NOT creep back in; unprompted surfacing is the proactive engine's morning
 * brief, not an in-turn model behaviour.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/emotional_recall_doctrine.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

const { EMOTIONAL_RECALL_DOCTRINE, ZOE_INSTRUCTIONS } = await import('../src/agents/zoe.ts');

test('emotional-recall doctrine is wired into the instructions', () => {
  assert.ok(EMOTIONAL_RECALL_DOCTRINE.length > 0);
  assert.ok(
    ZOE_INSTRUCTIONS.includes(EMOTIONAL_RECALL_DOCTRINE),
    'EMOTIONAL_RECALL_DOCTRINE must be part of ZOE_INSTRUCTIONS',
  );
});

test('doctrine widens recall to emotional turns', () => {
  const d = EMOTIONAL_RECALL_DOCTRINE.toLowerCase();
  assert.ok(d.includes('recall_memory'), 'must name the recall_memory tool');
  // an emotional-state cue and the "how have I been" phrasing it targets
  assert.ok(/mood|stress|worr|feeling/.test(d), 'must mention an emotional cue');
  assert.ok(d.includes('how have i been'), 'must target the generic emotional query');
  // anti-fabrication preserved
  assert.ok(/don'?t invent|empty recall/.test(d), 'must keep the anti-fabrication guard');
});

test('the dropped greeting/proactive rule has not crept back', () => {
  // These are the tells of the flaky in-turn "volunteer on greeting" rule that
  // was measured and removed. If they reappear, the ~1/5 greeting regression is
  // back — fail loudly.
  const d = EMOTIONAL_RECALL_DOCTRINE.toLowerCase();
  assert.ok(!d.includes('greeting'), 'greeting-surfacing rule was intentionally dropped');
  assert.ok(!/\bopen(?:s|ing)?\b.*conversation|first action/.test(d), 'no opening-turn recall rule');
});
