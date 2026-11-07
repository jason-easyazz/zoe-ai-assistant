/**
 * The now card on the live brain lane (src/now-card.ts): the sidecar half of flag-dark ZOE_MEMORY_NOW_CARD (zoe-data `now_card.py`).
 * LAB-ONLY, offline. Pins: no envelope = today's prompt (same Context object); an envelope = the card once, AFTER the user-model card,
 * neither line reaches the model and the identity still binds; an unacceptable envelope adds nothing and is still stripped.
 *
 * Run: npm test (Node 22, type-stripping), or this file alone with --test.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

process.env.ZOE_BRAIN_USER_ID = 'jason';

const { ZOE_INSTRUCTIONS } = await import('../src/agents/zoe.ts');
const nowCard = await import('../src/now-card.ts');
const manner = await import('../src/manner.ts');
const { applyPolicies, bindIdentityForRound } = await import('../src/providers/capped-completions.ts');
const { wrapMessageWithIdentity } = await import('../src/request-identity.ts');
const { actingUserId } = await import('../src/tools/zoe-tools.ts');

const CARD = 'Now card (Wed 4 Nov; from what they told you, checked overnight).\n- Speaking with: Zed\n- Coming up: Mia\'s birthday, Sun 8 Nov';
const UM = '\n\nUSER-MODEL CARD';
type Sent = { messages: { role: string; content: unknown }[]; systemPrompt: string };
const user = (content: string) => ({ role: 'user', content, timestamp: 0 }) as never;
const ctx = (...messages: unknown[]) => ({ systemPrompt: ZOE_INSTRUCTIONS, messages, tools: [] }) as never;
// The wire as zoe-data builds it: identity innermost, then the now card, then manner.
const wire = (card: string, words: string, uid = 'zed', mannerBlock = '') =>
  manner.wrapMessageWithManner(nowCard.wrapMessageWithNowCard(wrapMessageWithIdentity(words, uid), card), mannerBlock);
const policies = (c: unknown) => applyPolicies(c as never, UM) as unknown as Sent;

describe('now card envelope', () => {
  it('no envelope: the system prompt is today prompt and withNowCardBlock returns the same object', () => {
    assert.equal(policies(ctx(user(wire('', 'hello')))).systemPrompt, ZOE_INSTRUCTIONS + UM);
    const c = ctx(user('hi'));
    assert.equal(nowCard.withNowCardBlock(c, ''), c);
  });

  it('an envelope appends the card once after the user-model card; the model sees only the words; identity binds', () => {
    const c = ctx(user(wire(CARD, 'hello there', 'zed', 'Ask one question at a time.')));
    const sent = policies(c);
    assert.equal(sent.systemPrompt, ZOE_INSTRUCTIONS + '\n\nAsk one question at a time.' + UM + '\n\n' + CARD);
    assert.deepEqual(sent.messages.map((m) => m.content), ['hello there']);
    const signal = new AbortController().signal;
    bindIdentityForRound(c, signal);
    assert.equal(actingUserId(signal), 'zed');
  });

  it('reads the newest user message only', () => {
    const older = user(wire(CARD, 'first'));
    const newer = user(wire('', 'second', 'guest'));
    assert.ok(!policies(ctx(older, newer)).systemPrompt.includes(CARD));
    assert.equal(policies(ctx(newer, older)).systemPrompt.split(CARD).length - 1, 1);
  });

  it('an unacceptable payload adds nothing and is still stripped', () => {
    for (const bad of ['not json', '42', JSON.stringify('x'.repeat(nowCard.NOW_CARD_MAX_CHARS + 1)), JSON.stringify('a\u0007b')]) {
      const c = ctx(user(nowCard.NOW_CARD_ENVELOPE_PREFIX + bad + '\n' + wrapMessageWithIdentity('hello', 'zed')));
      const sent = policies(c);
      assert.equal(sent.systemPrompt, ZOE_INSTRUCTIONS + UM);
      assert.deepEqual(sent.messages.map((m) => m.content), ['hello']);
    }
  });
});
