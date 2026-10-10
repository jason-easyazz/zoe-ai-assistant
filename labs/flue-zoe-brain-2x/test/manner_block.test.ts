/**
 * The manner block on the live brain lane (src/manner.ts): the sidecar half of flag-dark ZOE_MANNER_BLOCK
 * (zoe-data `manner_block.py`). LAB-ONLY, offline.
 *
 * Pins, on the bytes the model actually receives:
 *   - no envelope (flag off, a minor, a guest) = the system prompt is ZOE_INSTRUCTIONS (+ the user-model card) byte for
 *     byte, and the SAME Context object comes back from `withMannerBlock` (the negative control for everything below);
 *   - an envelope = the block is appended ONCE, after the doctrines and BEFORE the user-model card (the bytes up to the card
 *     are identical for every member: one warm prefix), the envelope and identity lines never reach the model;
 *   - persona and manner coexist (persona swap first, manner after); nothing carries from one turn or member to the next;
 *   - an unacceptable envelope (not JSON, not a string, over budget, control characters) adds nothing and is still stripped.
 *
 * Run: npm test (Node 22, type-stripping), or this file alone with --test.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

process.env.ZOE_BRAIN_USER_ID = 'jason';

const { ZOE_INSTRUCTIONS } = await import('../src/agents/zoe.ts');
const manner = await import('../src/manner.ts');
const persona = await import('../src/persona.ts');
const { applyPolicies } = await import('../src/providers/capped-completions.ts');
const { wrapMessageWithIdentity } = await import('../src/request-identity.ts');
const { wrapMessageWithReplay } = await import('../src/replay-mode.ts');

const BLOCK =
  'With people: when someone shares a feeling, first say back what you heard in a few words of your own, then stop or ask ONE question. ' +
  'Ask one question at a time.';
const CARD = '\n\nCARD: pescatarian.';

type Sent = { messages: { role: string; content: unknown }[]; systemPrompt: string };
const user = (content: string) => ({ role: 'user', content, timestamp: 0 }) as never;
const assistant = (text: string) => ({ role: 'assistant', content: [{ type: 'text', text }], timestamp: 0 }) as never;
const ctx = (...messages: unknown[]) => ({ systemPrompt: ZOE_INSTRUCTIONS, messages, tools: [] }) as never;
// The wire exactly as zoe-data builds it: identity innermost, then manner, then persona, then replay.
const wire = (block: string, words: string, uid = 'jason', personaBlock = '') =>
  persona.wrapMessageWithPersona(manner.wrapMessageWithManner(wrapMessageWithIdentity(words, uid), block), personaBlock);
const policies = (c: unknown, card = '') => applyPolicies(c as never, card) as unknown as Sent;
const count = (hay: string, needle: string) => hay.split(needle).length - 1;

describe('no envelope: the prompt is today prompt, byte for byte', () => {
  it('applyPolicies without an envelope leaves the system prompt untouched (with and without the card)', () => {
    const c = ctx(user(wrapMessageWithIdentity('hello there', 'jason')));
    assert.equal(policies(c).systemPrompt, ZOE_INSTRUCTIONS);
    assert.equal(policies(c, CARD).systemPrompt, ZOE_INSTRUCTIONS + CARD);
  });

  it('withMannerBlock with an empty block returns the SAME context object', () => {
    const c = ctx(user('hi'));
    assert.equal(manner.withMannerBlock(c, ''), c);
  });

  it('wrapMessageWithManner with an empty block returns the message unchanged', () => {
    assert.equal(manner.wrapMessageWithManner('hi', ''), 'hi');
  });
});

describe('an envelope appends the block to the system prompt', () => {
  const sent = () => policies(ctx(user(wire(BLOCK, 'hello there'))), CARD).systemPrompt;

  it('the block is present exactly once, after the doctrines and BEFORE the user-model card', () => {
    const s = sent();
    assert.equal(count(s, BLOCK), 1);
    assert.equal(s, ZOE_INSTRUCTIONS + '\n\n' + BLOCK + CARD);
  });

  it('the bytes up to the card are the same whoever the card belongs to (one warm prefix)', () => {
    const a = policies(ctx(user(wire(BLOCK, 'hi', 'ada'))), '\n\nCARD A').systemPrompt;
    const b = policies(ctx(user(wire(BLOCK, 'hi', 'bea'))), '\n\nCARD B').systemPrompt;
    const prefix = ZOE_INSTRUCTIONS + '\n\n' + BLOCK;
    assert.ok(a.startsWith(prefix) && b.startsWith(prefix));
  });

  it('neither the manner line nor the identity line reaches the model', () => {
    const out = policies(ctx(user(wire(BLOCK, 'hello there'))));
    assert.equal(out.messages[0].content, 'hello there');
  });

  it('works with the replay line outside it, and with the persona line between them', () => {
    const inner = wire(BLOCK, 'hello there', 'jason', 'You are Zoe, warm and plain.');
    const out = policies(ctx(user(wrapMessageWithReplay(inner, true))));
    assert.equal(out.messages[0].content, 'hello there');
    assert.equal(count(out.systemPrompt, BLOCK), 1);
    assert.ok(out.systemPrompt.includes('You are Zoe, warm and plain.'));
    assert.ok(out.systemPrompt.indexOf('You are Zoe, warm and plain.') < out.systemPrompt.indexOf(BLOCK), 'persona swap first, manner after');
  });

  it('every tool round of one turn sees the same prompt (the newest user message decides)', () => {
    const call = { type: 'toolCall', id: 'c1', name: 'get_time', arguments: {} };
    const result = { role: 'toolResult', toolCallId: 'c1', toolName: 'get_time', content: [{ type: 'text', text: '10:00' }], isError: false, timestamp: 0 };
    const msgs = [user(wire(BLOCK, 'what is on today')), { role: 'assistant', content: [call], timestamp: 0 }, result];
    assert.equal(policies(ctx(...msgs), CARD).systemPrompt, sent());
  });
});

describe('nothing carries from one turn or member to the next', () => {
  const turn = (older: string, newest: string) => policies(ctx(user(older), assistant('Sure.'), user(newest))).systemPrompt;

  it('an older turn that carried the block does not leak into a newer turn that did not (a minor after an adult)', () => {
    const s = turn(wire(BLOCK, 'first', 'ada'), wrapMessageWithIdentity('second', 'kid'));
    assert.equal(s, ZOE_INSTRUCTIONS);
  });

  it('the newest turn decides when only it carries the block', () => {
    const s = turn(wrapMessageWithIdentity('first', 'kid'), wire(BLOCK, 'second', 'ada'));
    assert.equal(s, ZOE_INSTRUCTIONS + '\n\n' + BLOCK);
  });

  it('the older stored turn has its line stripped from the history the model sees', () => {
    const out = policies(ctx(user(wire(BLOCK, 'first')), assistant('Sure.'), user(wire(BLOCK, 'second'))));
    assert.deepEqual(out.messages.filter((m) => m.role === 'user').map((m) => m.content), ['first', 'second']);
  });
});

describe('an unacceptable envelope adds nothing and is still stripped', () => {
  const bad = (payload: string) => policies(ctx(user(manner.MANNER_ENVELOPE_PREFIX + payload + '\n' + wrapMessageWithIdentity('hi', 'jason'))));

  for (const [why, payload] of [
    ['not JSON', 'not json at all'],
    ['not a string', JSON.stringify({ block: 'x' })],
    ['over budget', JSON.stringify('x'.repeat(manner.MANNER_MAX_CHARS + 1))],
    ['control characters', JSON.stringify('a\u0001b')],
  ] as const) {
    it(why, () => {
      const out = bad(payload);
      assert.equal(out.systemPrompt, ZOE_INSTRUCTIONS);
      assert.equal(out.messages[0].content, 'hi');
    });
  }

  it('an empty string is no block, and a newline inside the block is allowed', () => {
    assert.equal(manner.parseMannerPayload(JSON.stringify('  ')), '');
    assert.equal(manner.parseMannerPayload(JSON.stringify('a\nb')), 'a\nb');
  });
});
