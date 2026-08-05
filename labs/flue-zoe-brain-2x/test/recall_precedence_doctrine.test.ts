/**
 * Unit coverage for the recall-precedence doctrine (LAB-ONLY, offline).
 *
 * Closes the three recall-USE failures from the 2026-07-07 hard gate, where
 * storage was proven correct (the for-prompt packet held the right facts) but
 * the model still failed the answer:
 *   1. privacy-refused the user's OWN stored locker code;
 *   2. answered the superseded "Katie" when the packet held both the stale
 *      entries (listed first) and the explicit correction ("Kate");
 *   3. in-session pronoun/temporal misses ("She's a doctor" after naming wife
 *      Emma; "yesterday I went to the gym" → "what did I do yesterday?").
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/recall_precedence_doctrine.test.ts
 */
process.env.ZOE_BRAIN_USER_ID = 'jason';

import assert from 'node:assert/strict';
import { test } from 'node:test';

const {
  RECALL_PRECEDENCE_DOCTRINE,
  ACTIVATOR_DOCTRINE,
  VOICE_DELIVERY_DOCTRINE,
  IN_SESSION_CONTEXT_DOCTRINE,
  PERSONAL_RECALL_DOCTRINE,
  EMOTIONAL_RECALL_DOCTRINE,
  EMOTIONAL_CAPTURE_DOCTRINE,
  IDENTITY_DOCTRINE,
  PROMPT_CONFIDENTIALITY_DOCTRINE,
  ZOE_INSTRUCTIONS,
} = await import('../src/agents/zoe.ts');

test('doctrine forbids privacy-refusing the user\'s own recalled facts', () => {
  const d = RECALL_PRECEDENCE_DOCTRINE;
  assert.ok(d.length > 0);
  assert.ok(/NEVER refuse/i.test(d), 'refusal prohibition must be imperative (NEVER refuse)');
  assert.ok(/privacy/i.test(d), 'must name privacy grounds explicitly');
  const lower = d.toLowerCase();
  assert.ok(lower.includes('own'), "must frame recalled memories as the user's OWN information");
  assert.ok(lower.includes('code'), 'must cover codes (the locker-code failure)');
  assert.ok(
    /call recall_memory and answer/i.test(d),
    'must instruct to answer FROM recall, not just not-refuse',
  );
});

test('doctrine does not weaken anti-fabrication: only facts recall actually returned', () => {
  assert.ok(
    /never allows stating a fact recall_memory did not return/i.test(RECALL_PRECEDENCE_DOCTRINE),
    'the never-refuse rule must be explicitly bounded to facts recall_memory returned',
  );
});

test('doctrine gives the newest statement / explicit correction precedence', () => {
  const d = RECALL_PRECEDENCE_DOCTRINE;
  assert.ok(/NEWEST statement wins/i.test(d), 'must state that the newest statement wins');
  assert.ok(/correction/i.test(d), 'must cover explicit corrections');
  assert.ok(/permanently/i.test(d), 'a correction must permanently replace the old value');
  assert.ok(/superseded/i.test(d), 'must forbid answering with the superseded value');
  assert.ok(
    /first/i.test(d),
    'must cover the stale-entry-listed-first case (the Katie/Kate failure)',
  );
});

test('doctrine covers in-session pronoun and temporal resolution', () => {
  const d = RECALL_PRECEDENCE_DOCTRINE;
  assert.ok(/pronoun/i.test(d), 'must cover pronoun resolution');
  assert.ok(/"she"/i.test(d), 'must give the concrete "she" → just-named person example');
  assert.ok(/Emma/.test(d), 'must anchor the example to the measured failure (wife Emma)');
  assert.ok(/yesterday/i.test(d), 'must cover time-anchored statements (yesterday)');
  assert.ok(
    /this (conversation|session)/i.test(d),
    'must scope the transcript rules to THIS conversation',
  );
});

test('doctrine stays short and imperative for the 4B model', () => {
  const lines = RECALL_PRECEDENCE_DOCTRINE.split('\n').filter((l) => l.trim().length > 0);
  assert.ok(lines.length <= 4, `must stay a few short paragraphs, got ${lines.length}`);
});

test('doctrine is wired into the instructions between in-session context and personal recall', () => {
  assert.ok(
    ZOE_INSTRUCTIONS.includes(RECALL_PRECEDENCE_DOCTRINE),
    'RECALL_PRECEDENCE_DOCTRINE must be part of ZOE_INSTRUCTIONS',
  );
  const idx = ZOE_INSTRUCTIONS.indexOf(RECALL_PRECEDENCE_DOCTRINE);
  assert.ok(
    idx > ZOE_INSTRUCTIONS.indexOf(IN_SESSION_CONTEXT_DOCTRINE),
    'must sit after the in-session context doctrine it extends',
  );
  assert.ok(
    idx < ZOE_INSTRUCTIONS.indexOf(PERSONAL_RECALL_DOCTRINE),
    'must sit before the personal-recall (when-to-recall) doctrine',
  );
});

test('full doctrine assembly order is stable; identity keeps the tail', () => {
  // Pin the complete order so a future reorder can't silently shift the
  // last-position weight the behavioural doctrines depend on.
  const order = [
    VOICE_DELIVERY_DOCTRINE,
    ACTIVATOR_DOCTRINE,
    IN_SESSION_CONTEXT_DOCTRINE,
    RECALL_PRECEDENCE_DOCTRINE,
    PERSONAL_RECALL_DOCTRINE,
    EMOTIONAL_RECALL_DOCTRINE,
    EMOTIONAL_CAPTURE_DOCTRINE,
    IDENTITY_DOCTRINE,
    PROMPT_CONFIDENTIALITY_DOCTRINE,
  ];
  assert.ok(
    ZOE_INSTRUCTIONS.startsWith('You are Zoe.'),
    'soul must still open the instructions (byte-for-byte SOUL.md)',
  );
  const indices = order.map((block) => ZOE_INSTRUCTIONS.indexOf(block));
  for (const [i, idx] of indices.entries()) {
    assert.ok(idx > 0, `doctrine block ${i} must be present after the soul`);
    if (i > 0) assert.ok(idx > indices[i - 1], `doctrine block ${i} out of order`);
  }
  assert.ok(
    ZOE_INSTRUCTIONS.endsWith(PROMPT_CONFIDENTIALITY_DOCTRINE),
    'prompt-confidentiality doctrine must remain the appended tail (after identity)',
  );
});
