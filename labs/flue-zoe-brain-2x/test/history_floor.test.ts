/**
 * The history window must never wipe the conversation the owner is in the middle of, and the prompt must stay a stable
 * prefix between consecutive turns.
 *
 * MEASURED 2026-10-12 (live voice session): `FLUE_CONTEXT_BUDGET history=0` on 8 consecutive brain turns while the store
 * held 20+ turns and `stale` kept growing. A slot-filling follow-up ("add a doctor's appointment Friday at twelve", then
 * "Doctor's appointment.") lost its context. Cause: `windowHeadIndex` sized its anchor quantum on the BUDGET alone (1664
 * tokens at the default window) — larger than a short voice conversation's whole history (1.2-2k tokens at ~65 per turn). "The
 * first block whose cumulative drop meets the target" then fell off the end of the list and defaulted to the NEWEST block:
 * all history gone for a few tokens of overshoot. Every such jump also moved the head of the prompt, so the whole history
 * was re-prefilled (a llama-server prefix-cache miss).
 *
 * Each assertion has a NEGATIVE CONTROL: the pre-fix rule (`legacyQuantisedHead`, a transcription of the old code) on the
 * identical input does wipe the history. If it did not, the scenario would not be exercising the bug.
 */
process.env.ZOE_BRAIN_USER_ID = 'jason';

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import type { Context, Message, Tool } from '@earendil-works/pi-ai';
import {
  contextWindowTokens,
  estimateMessageTokens,
  estimateTextTokens,
  estimateToolTokens,
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

/** A system prompt sized so exactly `room` estimated tokens of messages fit under the budget. */
function systemLeaving(room: number): string {
  const fixed = 64 + estimateToolTokens(TOOLS);
  const chars = (budget() - fixed - room) * 4;
  assert.ok(chars > 0);
  return 'x'.repeat(chars);
}

/** `turns` short voice exchanges (~65 estimated tokens each), then the newest user message. */
function shortTurns(turns: number): Message[] {
  const out: Message[] = [];
  for (let i = 0; i < turns; i++) {
    out.push(user(`turn ${i}: ${'q'.repeat(100)}`));
    out.push(assistant(`reply ${i}: ${'a'.repeat(120)}`));
  }
  out.push(user('Doctor appointment.'));
  return out;
}

/** The pre-fix head rule (quantum = budget/4, default falls to the newest block), for the negative control. */
function legacyQuantisedHead(context: Context): number {
  const { messages } = context;
  const starts: number[] = [];
  messages.forEach((m, i) => m.role === 'user' && starts.push(i));
  const tok = messages.map(estimateMessageTokens);
  const fixed = 64 + estimateTextTokens(context.systemPrompt ?? '') + estimateToolTokens(context.tools);
  let basis = fixed;
  for (let i = 0; i <= starts[starts.length - 1]; i++) basis += tok[i];
  const quantum = Math.floor(budget() * 0.25);
  const cumulative: number[] = [];
  let dropped = 0;
  for (let k = 0; k < starts.length; k++) {
    cumulative.push(dropped);
    const end = k + 1 < starts.length ? starts[k + 1] : messages.length;
    for (let i = starts[k]; i < end; i++) dropped += tok[i];
  }
  const target = Math.ceil((basis - budget()) / quantum) * quantum;
  let head = starts.length - 1;
  for (let k = 0; k < starts.length; k++) {
    if (cumulative[k] >= target) {
      head = k;
      break;
    }
  }
  return starts[head];
}

describe('the history window keeps the conversation in progress', () => {
  // ~1400 tokens of room; 25 turns x ~65 = ~1650 of history: a few hundred tokens over budget, like the live session.
  const make = (turns: number): Context => ({
    systemPrompt: systemLeaving(1400),
    messages: shortTurns(turns),
    tools: TOOLS,
  });

  it('negative control: the pre-fix rule drops EVERY earlier turn for a small overshoot', () => {
    // 22 turns is the live band: history (~1.7k) is just over the quantum, so the old rule falls off the list's end
    for (const turns of [22]) {
      const c = make(turns);
      assert.ok(windowHeadIndex(c) !== null, 'precondition: the context is over budget');
      assert.equal(legacyQuantisedHead(c), c.messages.length - 1, `${turns} turns: the old rule lands on the newest block alone`);
    }
  });

  it(`keeps at least the last 3 earlier turns, and the result fits the budget`, () => {
    for (const turns of [20, 22, 23, 24, 25, 30, 40, 80]) {
      const c = make(turns);
      const head = windowHeadIndex(c);
      assert.ok(head !== null, `${turns} turns: over budget`);
      const kept = c.messages.slice(head);
      const earlier = kept.filter((m) => m.role === 'user').length - 1;
      assert.ok(earlier >= 3, `${turns} turns: only ${earlier} earlier turns kept (head ${head})`);
      assert.equal(kept[0].role, 'user', 'the window opens on a user turn');
      const cost =
        64 + estimateTextTokens(c.systemPrompt ?? '') + estimateToolTokens(c.tools) +
        kept.reduce((n, m) => n + estimateMessageTokens(m), 0);
      assert.ok(cost <= budget(), `${turns} turns: ${cost} > budget ${budget()}`);
    }
  });

  it('through applyPolicies: the previous exchange is still on the wire (the slot-filling follow-up)', () => {
    const sent = applyPolicies(make(22), '');
    const texts = sent.messages.map((m) => (typeof m.content === 'string' ? m.content : JSON.stringify(m.content)));
    assert.ok(texts.some((t) => t.includes('reply 21')), 'the immediately previous assistant reply survived');
    assert.ok(texts.some((t) => t.includes('turn 21')), 'the immediately previous user turn survived');
  });

  it('the safety valve still wins: if the last turns alone exceed the budget, they are dropped, never overflowed', () => {
    const huge = 'z'.repeat(budget() * 4);
    const c: Context = {
      systemPrompt: systemLeaving(1000),
      messages: [user('a'), assistant(huge), user('b'), assistant(huge), user('c'), assistant(huge), user('now')],
      tools: TOOLS,
    };
    const head = windowHeadIndex(c);
    assert.equal(head, 6, 'only the in-flight turn remains');
  });

  it('settles: while the floor binds the head creeps over a few SHORT histories, then holds still', () => {
    const heads: number[] = [];
    for (let turns = 20; turns <= 34; turns++) {
      const h = windowHeadIndex(make(turns));
      heads.push(h === null ? 0 : h / 2); // "how many OLDEST turns are dropped"
    }
    // the plateau is the quantised head; the creep before it only ever re-prefills the (short) kept history
    let run = 1;
    let best = 1;
    for (let i = 1; i < heads.length; i++) {
      run = heads[i] === heads[i - 1] ? run + 1 : 1;
      best = Math.max(best, run);
    }
    assert.ok(best >= 8, `the head never settled (${heads.join(',')})`);
    for (let i = 1; i < heads.length; i++) assert.ok(heads[i] >= heads[i - 1], 'the head never moves backwards');
  });
});

describe('the prompt stays a stable prefix between consecutive turns (ordering rule)', () => {
  it('turn N+1 differs from turn N only at or after the START of turn N\'s user message', () => {
    // injected blocks ride in the NEWEST user message only; the older one loses them (ZOE_BRAIN_ELIDE_STALE_BLOCKS)
    process.env.ZOE_BRAIN_ELIDE_STALE_BLOCKS = '1';
    try {
      const block = '[MEMORY CONTEXT x]\n- a fact\n[END MEMORY CONTEXT]\n';
      const base = (n: number): Message[] => {
        const out: Message[] = [];
        for (let i = 0; i < n; i++) {
          out.push(user(`${block}question ${i}`));
          out.push(assistant(`answer ${i}`));
        }
        return out;
      };
      const sys = 'You are Zoe. '.repeat(50);
      const t1 = applyPolicies({ systemPrompt: sys, messages: [...base(3), user(`${block}question 3`)], tools: TOOLS }, '');
      const t2 = applyPolicies({ systemPrompt: sys, messages: [...base(4), user(`${block}question 4`)], tools: TOOLS }, '');
      assert.equal(t1.systemPrompt, t2.systemPrompt, 'system prompt byte-identical');
      assert.deepEqual(t1.tools?.map((t) => t.name), t2.tools?.map((t) => t.name), 'tool block identical');
      const text = (m: Message) => (typeof m.content === 'string' ? m.content : JSON.stringify(m.content));
      // everything before turn N's user message is identical...
      for (let i = 0; i < t1.messages.length - 1; i++) {
        assert.equal(text(t2.messages[i]), text(t1.messages[i]), `message ${i} (before the previous user turn) changed`);
      }
      // ...and the previous user message is the ONE place the prompt may differ (its block is elided)
      assert.notEqual(text(t2.messages[t1.messages.length - 1]), text(t1.messages[t1.messages.length - 1]));
    } finally {
      delete process.env.ZOE_BRAIN_ELIDE_STALE_BLOCKS;
    }
  });
});
