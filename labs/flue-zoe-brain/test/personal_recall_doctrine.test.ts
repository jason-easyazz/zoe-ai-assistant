/**
 * Unit coverage for the personal-recall doctrine (LAB-ONLY, offline).
 *
 * Closes the measured live gap where OBLIQUE factual questions about the user's
 * own life ("where do I live?", "do I have any allergies?", "do I prefer tea?")
 * only triggered recall_memory ~60-80% of the time. With this doctrine the live
 * 4B brain went 11/15 → 15/15 on those questions, while a general-knowledge
 * control ("what's 12 times 8?") stayed answered-directly (no recall drift).
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/personal_recall_doctrine.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';

const {
  PERSONAL_RECALL_DOCTRINE,
  IN_SESSION_CONTEXT_DOCTRINE,
  EMOTIONAL_CAPTURE_DOCTRINE,
  ZOE_INSTRUCTIONS,
} = await import('../src/agents/zoe.ts');

test('personal-recall doctrine is wired into the instructions', () => {
  assert.ok(PERSONAL_RECALL_DOCTRINE.length > 0);
  assert.ok(
    ZOE_INSTRUCTIONS.includes(PERSONAL_RECALL_DOCTRINE),
    'PERSONAL_RECALL_DOCTRINE must be part of ZOE_INSTRUCTIONS',
  );
});

test('doctrine widens recall to oblique personal questions', () => {
  const d = PERSONAL_RECALL_DOCTRINE.toLowerCase();
  assert.ok(d.includes('recall_memory'), 'must name the recall_memory tool');
  // the exact oblique phrasings it targets
  assert.ok(d.includes('where do i live'), 'targets "where do I live"');
  assert.ok(d.includes('allergies'), 'targets health/allergy questions');
});

test('doctrine sits in the trailing behavioural block (last-position weight)', () => {
  // Deliberate placement: AFTER the in-session-context doctrine and within the
  // trailing behavioural cluster (before emotional-capture, the last doctrine),
  // so the recall rule keeps last-position weight. A future reorder of the
  // ${...} segments would otherwise silently move it and regress recall.
  const iContext = ZOE_INSTRUCTIONS.indexOf(IN_SESSION_CONTEXT_DOCTRINE);
  const iPersonal = ZOE_INSTRUCTIONS.indexOf(PERSONAL_RECALL_DOCTRINE);
  const iCapture = ZOE_INSTRUCTIONS.indexOf(EMOTIONAL_CAPTURE_DOCTRINE);
  assert.ok(iContext >= 0 && iPersonal >= 0 && iCapture >= 0);
  assert.ok(iPersonal > iContext, 'personal-recall must follow in-session-context');
  assert.ok(iPersonal < iCapture, 'personal-recall must stay in the trailing behavioural block');
});


test('doctrine keeps the general-knowledge bound (no recall on world facts)', () => {
  // Without this bound the model would add a tool round-trip to every recipe/
  // maths/world-fact turn. The live control ("12 times 8") depends on it.
  const d = PERSONAL_RECALL_DOCTRINE.toLowerCase();
  assert.ok(/general[-\s]?knowledge|world/.test(d), 'must carve out general-knowledge');
  assert.ok(/no recall|answer directly|without .*recall/.test(d), 'world facts answered directly');
});
