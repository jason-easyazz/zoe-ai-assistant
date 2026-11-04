/**
 * Voice history width (Samantha initiative lane E3, 2026-11-05).
 *
 * MEASURED: the live Flue lane ignores zoe-data's `history` kwarg; the prompt carries the sidecar's own durable session,
 * windowed by `windowHeadIndex`. Until the session overflows the token budget nothing is dropped; at the first overflow
 * the quantised head jumps (a 1664-token quantum, ~25 short turns) and only the FLOOR (`minKeepTurns`, was 3) decides how
 * much conversation survives. So the floor is the history the brain keeps through every long voice session.
 *
 * Each assertion has a negative control: the rollback value (ZOE_BRAIN_KEEP_TURNS=3) fails the same scenario.
 */
process.env.ZOE_BRAIN_USER_ID = 'jason';

import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';
import type { Context, Message, Tool } from '@earendil-works/pi-ai';
import {
  contextWindowTokens,
  estimateMessageTokens,
  estimateTextTokens,
  estimateToolTokens,
  minKeepTurns,
  replyReserveTokens,
  windowHeadIndex,
} from '../src/context-window.ts';
import { applyPolicies } from '../src/providers/capped-completions.ts';

const user = (text: string): Message => ({ role: 'user', content: text, timestamp: 0 }) as Message;
const assistant = (text: string): Message =>
  ({ role: 'assistant', content: [{ type: 'text', text }], timestamp: 0 }) as unknown as Message;
const TOOLS: Tool[] = ['get_time', 'recall_memory'].map(
  (name) => ({ name, description: `the ${name} tool`, parameters: { type: 'object' } }) as Tool,
);
const budget = () => contextWindowTokens() - replyReserveTokens(contextWindowTokens());

function systemLeaving(room: number): string {
  const fixed = 64 + estimateToolTokens(TOOLS);
  return 'x'.repeat((budget() - fixed - room) * 4);
}
function turns(n: number, body = 100): Message[] {
  const out: Message[] = [];
  for (let i = 0; i < n; i++) {
    out.push(user(`turn ${i}: ${'q'.repeat(body)}`));
    out.push(assistant(`reply ${i}: ${'a'.repeat(body + 20)}`));
  }
  out.push(user('and then?'));
  return out;
}
const make = (n: number, body = 100): Context => ({ systemPrompt: systemLeaving(1400), messages: turns(n, body), tools: TOOLS });
const earlierKept = (c: Context): number => {
  const head = windowHeadIndex(c);
  const kept = head === null ? c.messages : c.messages.slice(head);
  return kept.filter((m) => m.role === 'user').length - 1;
};
const costOf = (c: Context, head: number | null): number =>
  64 + estimateTextTokens(c.systemPrompt ?? '') + estimateToolTokens(c.tools) +
  (head === null ? c.messages : c.messages.slice(head)).reduce((n, m) => n + estimateMessageTokens(m), 0);

afterEach(() => {
  delete process.env.ZOE_BRAIN_KEEP_TURNS;
});

describe('the history floor is 8 turns by default, 3 on rollback', () => {
  it('defaults to 8, honours 3, and falls back to 8 on junk', () => {
    assert.equal(minKeepTurns(), 8);
    process.env.ZOE_BRAIN_KEEP_TURNS = '3';
    assert.equal(minKeepTurns(), 3);
    for (const junk of ['', 'abc', '-1', '2.5', ' ']) {
      process.env.ZOE_BRAIN_KEEP_TURNS = junk;
      assert.equal(minKeepTurns(), 8, `junk ${JSON.stringify(junk)}`);
    }
    process.env.ZOE_BRAIN_KEEP_TURNS = '9999';
    assert.equal(minKeepTurns(), 64, 'capped');
  });

  it('count: an over-budget session keeps >= 8 earlier turns (negative control: the rollback keeps fewer)', () => {
    const sizes = [24, 26, 28, 30, 40, 80];
    for (const n of sizes) assert.ok(earlierKept(make(n)) >= 8, `${n} turns: kept ${earlierKept(make(n))}`);
    process.env.ZOE_BRAIN_KEEP_TURNS = '3';
    assert.ok(sizes.some((n) => earlierKept(make(n)) < 8), 'the rollback value must fail the same scenario');
  });

  it('token cap: long turns override the floor; the result always fits the budget and keeps the newest turn', () => {
    for (const body of [100, 400, 900, 3000, 20000]) {
      for (const n of [10, 30, 60]) {
        const c = make(n, body);
        const head = windowHeadIndex(c);
        const kept = head === null ? c.messages : c.messages.slice(head);
        assert.equal(kept[kept.length - 1], c.messages[c.messages.length - 1], 'newest message survives');
        if (head !== null && kept.length > 1) assert.ok(costOf(c, head) <= budget(), `${n}x${body}: ${costOf(c, head)} > ${budget()}`);
      }
    }
    // negative control: a floor honoured regardless of size would overflow here (8 such turns alone exceed the budget)
    const c = make(30, 3000);
    assert.ok((8 * 2 * 3000) / 4 > budget(), 'precondition: 8 such turns alone exceed the budget');
    assert.ok(earlierKept(c) < 8, 'the budget wins over the floor');
  });

  it('prefix stability: system prompt and tools are byte-identical across floors; the 3-turn window is a suffix of the 8-turn one; head never moves backwards', () => {
    // a size inside the band where the floors differ (past the first overflow, before the quantised head overtakes both)
    let n = 20;
    for (; n < 60; n++) {
      process.env.ZOE_BRAIN_KEEP_TURNS = '3';
      const a = windowHeadIndex(make(n));
      delete process.env.ZOE_BRAIN_KEEP_TURNS;
      if (a !== windowHeadIndex(make(n))) break;
    }
    assert.ok(n < 60, 'precondition: some session length separates the floors');
    const c = make(n);
    const w8 = applyPolicies(c, '');
    process.env.ZOE_BRAIN_KEEP_TURNS = '3';
    const w3 = applyPolicies(c, '');
    assert.equal(w8.systemPrompt, w3.systemPrompt);
    assert.deepEqual(w8.tools?.map((t) => t.name), w3.tools?.map((t) => t.name));
    assert.ok(w8.messages.length > w3.messages.length, 'wider history on the wire');
    assert.deepEqual(w8.messages.slice(w8.messages.length - w3.messages.length), w3.messages);
    delete process.env.ZOE_BRAIN_KEEP_TURNS;
    let prev = -1;
    for (let n = 15; n <= 60; n++) {
      const h = windowHeadIndex(make(n)) ?? 0;
      assert.ok(h >= prev, `head moved backwards at ${n}`);
      prev = h;
    }
  });

  it('cost: the extra history is bounded by the extra turns only', () => {
    const kept = (floor: string): number => {
      process.env.ZOE_BRAIN_KEEP_TURNS = floor;
      const c = make(40);
      return c.messages.slice(windowHeadIndex(c) ?? 0).reduce((n, m) => n + estimateMessageTokens(m), 0);
    };
    const t3 = kept('3');
    const t8 = kept('8');
    assert.ok(t8 > t3, 'negative control: the wider floor really carries more');
    assert.ok(t8 - t3 < 5 * 100, `extra history ${t8 - t3} tokens for 5 more turns`);
  });
});
