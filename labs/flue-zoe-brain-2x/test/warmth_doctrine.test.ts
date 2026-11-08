/**
 * The warmth doctrine (src/doctrines.ts WARMTH_DOCTRINE, 2026-11-06): how Zoe meets a feeling and a
 * self-disclosure. Offline, prompt-content only - the behaviour itself is measured on the live brain
 * (services/zoe-data warmth_probes.py over a transcript; docs/knowledge/samantha-warmth.md).
 *
 * Pinned here: every counted behaviour is in the text, the emotional-safety floor is in the text
 * (no therapy claim, unsafe -> a human, a trusted adult for a child), the doctrine is phrasing-only
 * and sits after voice delivery and BEFORE every tool doctrine, it names no tool, and it is an
 * overlay slot whose absent file means the compiled text.
 *
 * Run: node --experimental-strip-types --test test/warmth_doctrine.test.ts
 */
process.env.ZOE_BRAIN_USER_ID = 'jason';

import assert from 'node:assert/strict';
import { test } from 'node:test';

const {
  ACTIVATOR_DOCTRINE,
  COMPILED_DOCTRINES,
  OVERLAY_DOCTRINE_SLOTS,
  VOICE_DELIVERY_DOCTRINE,
  WARMTH_DOCTRINE,
  ZOE_INSTRUCTIONS,
  composeInstructions,
} = await import('../src/doctrines.ts');
const { ZOE_SOUL } = await import('../src/soul.ts');

test('the warmth doctrine is in the compiled instructions, once', () => {
  assert.equal(ZOE_INSTRUCTIONS.split(WARMTH_DOCTRINE).length - 1, 1);
});

test('feelings: a specific reflection, ONE open question, nothing unrelated appended, a later follow-up', () => {
  assert.match(WARMTH_DOCTRINE, /name the specific thing they're carrying/);
  assert.match(WARMTH_DOCTRINE, /never echo their sentence/);
  assert.match(WARMTH_DOCTRINE, /ask ONE gentle, open question/);
  assert.match(WARMTH_DOCTRINE, /no weather, offers or news/);
  assert.match(WARMTH_DOCTRINE, /If something hard came up before, ask how it's going/);
});

test('emotional-safety floor: no therapy claim, unsafe -> a human, a trusted adult for a child, child register', () => {
  assert.match(WARMTH_DOCTRINE, /not a therapist: never diagnose/);
  assert.match(WARMTH_DOCTRINE, /reach someone they trust now \(a trusted adult, for a child\) or emergency help/);
  assert.match(WARMTH_DOCTRINE, /With a child, keep it simple and gentle/);
  // never a claim of treatment, counselling or confidentiality
  assert.doesNotMatch(WARMTH_DOCTRINE, /\b(?:treat|counsel|confidential|secret)/i);
});

test('getting to know you: warm to one specific detail, ONE follow-up, no list-back, no stock line', () => {
  assert.match(WARMTH_DOCTRINE, /a job, partner, child, pet, a move/);
  assert.match(WARMTH_DOCTRINE, /react warmly to one specific detail and ask ONE natural follow-up/);
  assert.match(WARMTH_DOCTRINE, /Never list back what they said/);
  assert.match(WARMTH_DOCTRINE, /no stock lines/);
});

test('phrasing only: self-scoped, names no tool, after voice delivery and before the activator', () => {
  assert.match(WARMTH_DOCTRINE, /phrasing only - the tool rules still come first/);
  assert.doesNotMatch(WARMTH_DOCTRINE, /recall_memory|activate_abilities|remember_emotional_moment/);
  const iVoice = ZOE_INSTRUCTIONS.indexOf(VOICE_DELIVERY_DOCTRINE);
  const iWarmth = ZOE_INSTRUCTIONS.indexOf(WARMTH_DOCTRINE);
  const iActivator = ZOE_INSTRUCTIONS.indexOf(ACTIVATOR_DOCTRINE);
  assert.ok(iVoice >= 0 && iVoice < iWarmth && iWarmth < iActivator);
});

test('an overlay slot: second in order, and the compiled text when no overlay replaces it', () => {
  assert.deepEqual(OVERLAY_DOCTRINE_SLOTS.slice(0, 2), ['voice-delivery', 'warmth']);
  assert.equal(COMPILED_DOCTRINES.warmth, WARMTH_DOCTRINE);
  const swapped = composeInstructions(ZOE_SOUL, { ...COMPILED_DOCTRINES, warmth: 'OVERLAY WARMTH' });
  assert.ok(swapped.includes('OVERLAY WARMTH'));
  assert.ok(!swapped.includes(WARMTH_DOCTRINE));
});

test('it stays small: the cached prefix grows by at most ~310 tokens (chars/4) with the unclear-words paragraph', () => {
  assert.ok(WARMTH_DOCTRINE.length <= 1240, `warmth doctrine is ${WARMTH_DOCTRINE.length} chars`);
});

// ── unclear words (2026-11-07): a misheard short command is asked about, never answered with a feeling or a bare name ──
const { IDENTITY_DOCTRINE } = await import('../src/doctrines.ts');

test('unclear words: speech-to-text may mishear; ask briefly, offer the likely reading, no feeling inferred, no bare name', () => {
  assert.match(WARMTH_DOCTRINE, /speech-to-text, which can mishear/);
  assert.match(WARMTH_DOCTRINE, /If a message doesn't make sense, ask briefly what they meant and offer your best guess as a short question/);
  assert.match(WARMTH_DOCTRINE, /Never read a feeling into an unclear or very short message/);
  assert.match(WARMTH_DOCTRINE, /never reply with just your name/);
  // the example is SCOPED to its case, so a small model does not copy it onto every unclear turn
  assert.match(WARMTH_DOCTRINE, /for a garbled light command, something like "Did you want the light on\?"/);
  // the unclear-words rule comes BEFORE "Feelings:", so an unclear turn is never read as a disclosure first
  assert.ok(WARMTH_DOCTRINE.indexOf('Unclear words:') < WARMTH_DOCTRINE.indexOf('Feelings:'));
});

test('no copyable stock reply survives: no "I\'m Zoe." identity line, no "Ugh, rough." anywhere in the instructions', () => {
  assert.doesNotMatch(IDENTITY_DOCTRINE, /"I'm Zoe\."/);
  assert.match(IDENTITY_DOCTRINE, /never answer anything else with just your name/);
  assert.doesNotMatch(ZOE_INSTRUCTIONS, /Ugh, rough/);
});
