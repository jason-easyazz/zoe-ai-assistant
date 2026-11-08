/**
 * The household-locale line on the live brain lane (src/household-locale.ts; #2326 regression, QA round 3b 2026-11-07).
 * LAB-ONLY, offline.
 *
 * Pins, on the bytes the model receives:
 *   - no envelope = the system prompt exactly as before (ZOE_INSTRUCTIONS + the card) - the negative control;
 *   - an envelope = the line is appended ONCE to the system prompt, labelled as background, after the doctrines and BEFORE the
 *     manner block and the card; the user's words reach the model byte-identical and no message carries the line;
 *   - the newest turn's line wins, older turns' lines are stripped; an unacceptable payload adds nothing and is still stripped;
 *   - the label carries the background-context rule for the bracketed blocks (the doctrine-level hint, in this slot so the
 *     compiled instructions stay byte-identical).
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

process.env.ZOE_BRAIN_USER_ID = 'demo';

const { ZOE_INSTRUCTIONS } = await import('../src/agents/zoe.ts');
const loc = await import('../src/household-locale.ts');
const manner = await import('../src/manner.ts');
const { applyPolicies } = await import('../src/providers/capped-completions.ts');
const { wrapMessageWithIdentity } = await import('../src/request-identity.ts');

const LINE = 'This household is in Testland: give measurements in metric and oven settings in Celsius.';
const CARD = '\n\nCARD: likes tea.';
const WORDS = 'do i have a pet?';

type Sent = { messages: { role: string; content: unknown }[]; systemPrompt: string };
const user = (content: string) => ({ role: 'user', content, timestamp: 0 }) as never;
const ctx = (...messages: unknown[]) => ({ systemPrompt: ZOE_INSTRUCTIONS, messages, tools: [] }) as never;
// The wire exactly as zoe-data builds it: the locale line innermost (after the identity line), then manner outside.
const wire = (line: string, words: string, mannerBlock = '') =>
  manner.wrapMessageWithManner(wrapMessageWithIdentity(loc.wrapMessageWithLocale(words, line), 'demo'), mannerBlock);
const policies = (c: unknown, card = '') => applyPolicies(c as never, card) as unknown as Sent;

describe('household-locale envelope', () => {
  it('NEGATIVE CONTROL: no envelope is the prompt exactly as before', () => {
    const out = policies(ctx(user(wire('', WORDS))), CARD);
    assert.equal(out.systemPrompt, ZOE_INSTRUCTIONS + CARD);
    assert.deepEqual(out.messages.map((m) => m.content), [WORDS]);
  });

  it('the line joins the system prompt as background; the words are byte-identical', () => {
    const out = policies(ctx(user(wire(LINE, WORDS))), CARD);
    assert.equal(out.systemPrompt, ZOE_INSTRUCTIONS + '\n\n' + loc.LOCALE_LABEL + '\n' + LINE + CARD);
    assert.deepEqual(out.messages.map((m) => m.content), [WORDS]);
    assert.ok(!JSON.stringify(out.messages).includes('Testland'));
  });

  it('goes ahead of the manner block (shared by every member) and the card', () => {
    const block = 'With people: ask one question at a time.';
    const out = policies(ctx(user(wire(LINE, WORDS, block))), CARD);
    assert.equal(out.systemPrompt, ZOE_INSTRUCTIONS + '\n\n' + loc.LOCALE_LABEL + '\n' + LINE + '\n\n' + block + CARD);
    assert.deepEqual(out.messages.map((m) => m.content), [WORDS]);
  });

  it('the newest turn decides; older lines never reach the model', () => {
    const out = policies(ctx(user(wire(LINE, 'first')), user(wire('', 'second'))));
    assert.equal(out.systemPrompt, ZOE_INSTRUCTIONS);
    assert.deepEqual(out.messages.map((m) => m.content), ['first', 'second']);
  });

  it('an unacceptable payload adds nothing and is still stripped', () => {
    for (const bad of [' zoe-locale:not json\n', ' zoe-locale:42\n', ' zoe-locale:' + JSON.stringify('x'.repeat(500)) + '\n']) {
      const out = policies(ctx(user(wrapMessageWithIdentity(bad + WORDS, 'demo'))));
      assert.equal(out.systemPrompt, ZOE_INSTRUCTIONS, bad);
      assert.deepEqual(out.messages.map((m) => m.content), [WORDS], bad);
    }
  });

  it('the label carries the background-context rule for every bracketed block', () => {
    assert.match(loc.LOCALE_LABEL, /not something the user said/);
    for (const block of ['[Today', '[MEMORY CONTEXT', '[PENDING CONTACT OFFER', '[RAISE']) assert.ok(loc.LOCALE_LABEL.includes(block), block);
    assert.match(loc.LOCALE_LABEL, /never thank the user for them, say you have noted them, or promise to follow them/);
  });
});
