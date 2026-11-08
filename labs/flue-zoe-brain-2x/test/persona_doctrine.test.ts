/**
 * PERSONA_DOCTRINE (QA round 6a): fixed tastes, banter, take-first decisions, no empty promises, short replies, lazy-possessive
 * names. Code-held (no overlay slot), ahead of identity, and consistent with the AI-honesty line.
 */
import assert from 'node:assert/strict';
import test from 'node:test';

import {
  COMPILED_DOCTRINES,
  IDENTITY_DOCTRINE,
  OVERLAY_DOCTRINE_SLOTS,
  PERSONA_DOCTRINE,
  PROMPT_CONFIDENTIALITY_DOCTRINE,
  ZOE_INSTRUCTIONS,
  composeInstructions,
} from '../src/doctrines.ts';
import { ZOE_SOUL } from '../src/soul.ts';

test('fixed tastes are named, and "no preferences" is forbidden', () => {
  for (const w of ['teal', 'otter', 'autumn', 'bossa nova', 'ramen']) assert.ok(PERSONA_DOCTRINE.includes(w), w);
  assert.match(PERSONA_DOCTRINE, /Never say you have no preferences/);
});

test('opinions, banter and the no-recycled-joke rule are stated', () => {
  assert.match(PERSONA_DOCTRINE, /what would you do/);
  assert.match(PERSONA_DOCTRINE, /fight me/);
  assert.match(PERSONA_DOCTRINE, /never repeat one you already told/);
});

test('decisions lead with a first take then at most ONE question', () => {
  assert.match(PERSONA_DOCTRINE, /first take/);
  assert.match(PERSONA_DOCTRINE, /at most ONE question/);
  assert.match(PERSONA_DOCTRINE, /Never reply with only questions/);
});

test('never promises a check it is not making this turn', () => {
  assert.match(PERSONA_DOCTRINE, /Only say you will check or look something up[^]*this same turn/);
});

test('short replies answer her last message and never read as a mood', () => {
  for (const w of ['k', 'ok', 'yep', 'nah', 'nope', 'cool', 'sure']) assert.ok(PERSONA_DOCTRINE.includes(w), w);
  assert.match(PERSONA_DOCTRINE, /Never ask what they mean or read a mood/);
});

test('lazy possessive names are people or pets, not words', () => {
  assert.match(PERSONA_DOCTRINE, /person or pet, not a word to define/);
});

test('consistent with AI honesty: never denies being an AI, no human claims', () => {
  assert.ok(!/\bhuman\b|not an AI|I'm a person/i.test(PERSONA_DOCTRINE), 'no claim to be human');
    assert.ok(!/not an? (AI|robot)/i.test(PERSONA_DOCTRINE), 'never scripts a denial of being a machine');
});

test('placement: code-held (not an overlay slot), after the slots, before identity; tail unchanged', () => {
  assert.ok(!(OVERLAY_DOCTRINE_SLOTS as readonly string[]).includes('persona'));
  const i = ZOE_INSTRUCTIONS.indexOf(PERSONA_DOCTRINE);
  assert.ok(i > ZOE_INSTRUCTIONS.indexOf(COMPILED_DOCTRINES['emotional-capture']), 'after the overlay slots');
  assert.ok(i < ZOE_INSTRUCTIONS.indexOf(IDENTITY_DOCTRINE), 'before identity');
  assert.ok(ZOE_INSTRUCTIONS.endsWith(PROMPT_CONFIDENTIALITY_DOCTRINE));
});

test('negative control: a hostile overlay cannot remove the persona', () => {
  const hostile = Object.fromEntries(OVERLAY_DOCTRINE_SLOTS.map((s) => [s, 'x'])) as typeof COMPILED_DOCTRINES;
  assert.ok(composeInstructions(ZOE_SOUL, hostile).includes(PERSONA_DOCTRINE));
});

test('token budget: the doctrine stays short', () => {
  assert.ok(PERSONA_DOCTRINE.length < 1200, `persona doctrine is ${PERSONA_DOCTRINE.length} chars`);
});
