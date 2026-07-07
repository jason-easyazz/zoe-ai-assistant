/**
 * Unit coverage for the identity doctrine (LAB-ONLY, offline).
 *
 * Closes the parity persona leak (FIX-PACKET-2026-07-07 item 2): the live
 * sidecar answered "What's your name again?" with "My name is Gemma 4. I'm a
 * large language model developed by Google DeepMind." — worst-in-class for a
 * companion. The soul says "You are Zoe" descriptively; this doctrine states
 * it imperatively (the technique that took recall from 67% to 97%,
 * parity/RELIABILITY.md).
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/identity_doctrine.test.ts
 */
process.env.ZOE_BRAIN_USER_ID = 'jason';

import assert from 'node:assert/strict';
import { test } from 'node:test';

const {
  IDENTITY_DOCTRINE,
  ACTIVATOR_DOCTRINE,
  VOICE_DELIVERY_DOCTRINE,
  IN_SESSION_CONTEXT_DOCTRINE,
  PERSONAL_RECALL_DOCTRINE,
  EMOTIONAL_RECALL_DOCTRINE,
  EMOTIONAL_CAPTURE_DOCTRINE,
  PROMPT_CONFIDENTIALITY_DOCTRINE,
  ZOE_INSTRUCTIONS,
} = await import('../src/agents/zoe.ts');

test('identity doctrine carries the Zoe imperative and the Gemma/Google prohibition', () => {
  const d = IDENTITY_DOCTRINE;
  assert.ok(d.length > 0);
  assert.ok(/you are Zoe/i.test(d), 'must state the Zoe identity imperatively');
  assert.ok(/NEVER/.test(d), 'prohibition must be imperative (NEVER)');
  const lower = d.toLowerCase();
  assert.ok(lower.includes('gemma'), 'must prohibit identifying as Gemma');
  assert.ok(lower.includes('google'), 'must prohibit identifying as a Google model');
  assert.ok(lower.includes('deepmind'), 'must prohibit identifying as a DeepMind model');
  assert.ok(lower.includes('large language model'), 'must prohibit "a large language model"');
  assert.ok(/name|what you are/i.test(d), 'must cover "your name / what you are" questions');
});

test('identity doctrine stays short for the 4B model (2-3 short lines)', () => {
  const lines = IDENTITY_DOCTRINE.split('\n').filter((l) => l.trim().length > 0);
  assert.ok(lines.length <= 3, `must stay 2-3 lines, got ${lines.length}`);
});

test('identity doctrine is wired into the instructions, after the soul', () => {
  assert.ok(
    ZOE_INSTRUCTIONS.includes(IDENTITY_DOCTRINE),
    'IDENTITY_DOCTRINE must be part of ZOE_INSTRUCTIONS',
  );
  // The soul opens the instructions; identity must sit after it.
  assert.ok(
    ZOE_INSTRUCTIONS.startsWith('You are Zoe.'),
    'soul must still open the instructions (byte-for-byte SOUL.md)',
  );
  assert.ok(
    ZOE_INSTRUCTIONS.indexOf(IDENTITY_DOCTRINE) > 0,
    'identity doctrine must come after the soul',
  );
});

test('existing doctrine order is undisturbed; identity is appended last', () => {
  // A future reorder of the ${...} segments would silently shift the
  // last-position weight the behavioural doctrines depend on — pin the full
  // established order and require identity strictly after all of them.
  const order = [
    VOICE_DELIVERY_DOCTRINE,
    ACTIVATOR_DOCTRINE,
    IN_SESSION_CONTEXT_DOCTRINE,
    PERSONAL_RECALL_DOCTRINE,
    EMOTIONAL_RECALL_DOCTRINE,
    EMOTIONAL_CAPTURE_DOCTRINE,
    IDENTITY_DOCTRINE,
    PROMPT_CONFIDENTIALITY_DOCTRINE,
  ];
  const indices = order.map((block) => ZOE_INSTRUCTIONS.indexOf(block));
  for (const [i, idx] of indices.entries()) {
    assert.ok(idx >= 0, `doctrine block ${i} must be present in ZOE_INSTRUCTIONS`);
    if (i > 0) assert.ok(idx > indices[i - 1], `doctrine block ${i} out of order`);
  }
  // Identity stays after every earlier doctrine; prompt-confidentiality is now
  // the appended tail (identity + confidentiality are the two trailing persona
  // rules, in that order).
  assert.ok(
    ZOE_INSTRUCTIONS.indexOf(IDENTITY_DOCTRINE) >
      ZOE_INSTRUCTIONS.indexOf(EMOTIONAL_CAPTURE_DOCTRINE),
    'identity doctrine must come after all behavioural doctrines',
  );
  assert.ok(
    ZOE_INSTRUCTIONS.endsWith(PROMPT_CONFIDENTIALITY_DOCTRINE),
    'prompt-confidentiality doctrine must be the appended tail (existing order stable)',
  );
});
