/**
 * The member register line on the live brain lane (src/register.ts): the sidecar half of flag-dark ZOE_MEMBER_REGISTER
 * (zoe-data `member_register.py`). LAB-ONLY, offline.
 *
 * Pins, on the bytes the model actually receives:
 *   - no envelope (flag off, a guest) = the system prompt is ZOE_INSTRUCTIONS (+ the card) byte for byte, and the SAME Context
 *     object comes back from `withRegisterBlock` (the negative control for everything below);
 *   - an envelope = the line is appended ONCE after the doctrines and the manner block and BEFORE the user-model card, so the
 *     stable cached prefix is identical for every member; the envelope and identity lines never reach the model;
 *   - persona, manner and register coexist; nothing carries from one turn or member to the next;
 *   - an unacceptable envelope (not JSON, not a string, over budget, control characters) adds nothing and is still stripped.
 *
 * Run: npm test (Node 22, type-stripping), or this file alone with --test.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

process.env.ZOE_BRAIN_USER_ID = 'jason';

const { ZOE_INSTRUCTIONS } = await import('../src/agents/zoe.ts');
const register = await import('../src/register.ts');
const manner = await import('../src/manner.ts');
const { applyPolicies } = await import('../src/providers/capped-completions.ts');
const { wrapMessageWithIdentity } = await import('../src/request-identity.ts');

const LINE = 'With this person (their own preferences): keep replies very short.';
const MANNER = 'With people: ask one question at a time.';
const CARD = '\n\nCARD: pescatarian.';

type Sent = { messages: { role: string; content: unknown }[]; systemPrompt: string };
const user = (content: string) => ({ role: 'user', content, timestamp: 0 }) as never;
const ctx = (...messages: unknown[]) => ({ systemPrompt: ZOE_INSTRUCTIONS, messages, tools: [] }) as never;
// The wire exactly as zoe-data builds it: identity innermost, then register, then manner.
const wire = (line: string, words: string, uid = 'jason', mannerBlock = '') =>
  manner.wrapMessageWithManner(register.wrapMessageWithRegister(wrapMessageWithIdentity(words, uid), line), mannerBlock);
const policies = (c: unknown, card = '') => applyPolicies(c as never, card) as unknown as Sent;
const count = (hay: string, needle: string) => hay.split(needle).length - 1;

describe('no envelope: the prompt is today prompt, byte for byte', () => {
  it('applyPolicies without an envelope leaves the system prompt untouched (with and without the card)', () => {
    const c = ctx(user(wrapMessageWithIdentity('hello there', 'jason')));
    assert.equal(policies(c).systemPrompt, ZOE_INSTRUCTIONS);
    assert.equal(policies(c, CARD).systemPrompt, ZOE_INSTRUCTIONS + CARD);
  });

  it('withRegisterBlock with an empty line returns the SAME context object', () => {
    const c = ctx(user('hi'));
    assert.equal(register.withRegisterBlock(c, ''), c);
  });

  it('wrapMessageWithRegister with an empty line returns the message unchanged', () => {
    assert.equal(register.wrapMessageWithRegister('hi', ''), 'hi');
  });
});

describe('an envelope appends the line to the system prompt', () => {
  const sent = () => policies(ctx(user(wire(LINE, 'hello there'))), CARD);

  it('appends once, after the doctrines and before the card (positive control)', () => {
    const s = sent();
    assert.equal(s.systemPrompt, ZOE_INSTRUCTIONS + '\n\n' + LINE + CARD);
    assert.equal(count(s.systemPrompt, LINE), 1);
  });

  it('the stable prefix is byte-identical with and without the line, for different members', () => {
    const other = policies(ctx(user(wire('With this person (their own preferences): a more formal tone.', 'hi', 'sam'))), CARD);
    const none = policies(ctx(user(wrapMessageWithIdentity('hi', 'sam'))), CARD);
    assert.ok(sent().systemPrompt.startsWith(ZOE_INSTRUCTIONS));
    assert.ok(other.systemPrompt.startsWith(ZOE_INSTRUCTIONS));
    assert.notEqual(other.systemPrompt, sent().systemPrompt);
    assert.equal(none.systemPrompt, ZOE_INSTRUCTIONS + CARD);
  });

  it('the envelope and identity lines never reach the model', () => {
    const s = sent();
    assert.equal(s.messages.length, 1);
    assert.equal(s.messages[0].content, 'hello there');
  });

  it('manner then register then card, in that order', () => {
    const s = policies(ctx(user(wire(LINE, 'hello there', 'jason', MANNER))), CARD);
    assert.equal(s.systemPrompt, ZOE_INSTRUCTIONS + '\n\n' + MANNER + '\n\n' + LINE + CARD);
  });

  it('register alone (no manner line) still parses', () => {
    const s = policies(ctx(user(wire(LINE, 'hello there', 'jason', ''))), CARD);
    assert.equal(s.systemPrompt, ZOE_INSTRUCTIONS + '\n\n' + LINE + CARD);
  });
});

describe('only the newest turn counts', () => {
  it('an older member line does not carry into a newer turn without one', () => {
    const older = user(wire(LINE, 'first'));
    const newer = user(wrapMessageWithIdentity('second', 'sam'));
    const s = policies(ctx(older, newer));
    assert.equal(count(s.systemPrompt, LINE), 0);
    assert.equal(s.messages.length, 2);
    assert.equal(s.messages[0].content, 'first');
  });

  it('the newest line wins when both turns carry one', () => {
    const newer = 'With this person (their own preferences): an unhurried pace.';
    const s = policies(ctx(user(wire(LINE, 'first')), user(wire(newer, 'second'))));
    assert.equal(count(s.systemPrompt, newer), 1);
    assert.equal(count(s.systemPrompt, LINE), 0);
  });
});

describe('an unacceptable envelope adds nothing and is still stripped', () => {
  const bad: [string, string][] = [
    ['not JSON', 'not json at all'],
    ['not a string', JSON.stringify({ a: 1 })],
    ['over budget', JSON.stringify('x'.repeat(register.REGISTER_MAX_CHARS + 1))],
    ['control characters', JSON.stringify('bad\u0007bell')],
  ];
  for (const [name, payload] of bad) {
    it(name, () => {
      const msg = register.REGISTER_ENVELOPE_PREFIX + payload + '\n' + wrapMessageWithIdentity('hello', 'jason');
      const s = policies(ctx(user(msg)), CARD);
      assert.equal(s.systemPrompt, ZOE_INSTRUCTIONS + CARD);
      assert.equal(s.messages[0].content, 'hello');
    });
  }

  it('a within-budget line is accepted (positive control for the cases above)', () => {
    assert.equal(register.parseRegisterPayload(JSON.stringify('x'.repeat(register.REGISTER_MAX_CHARS))).length, register.REGISTER_MAX_CHARS);
  });
});
