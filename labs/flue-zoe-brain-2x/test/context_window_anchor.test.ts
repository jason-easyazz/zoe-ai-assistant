/**
 * PROMPT-PREFIX STABILITY — the fix for the windowing instability measured
 * 2026-08-03, and the coverage the original 12-case suite never had.
 *
 * WHAT WAS WRONG. `windowContextToBudget` recomputed its head from scratch on
 * every model call with a greedy fill-to-budget rule. Three consequences, all
 * invisible to a correctness-only test suite (the output was always a valid
 * in-budget window — just a DIFFERENT one each time):
 *
 *   1. the head slid forward on every tool round of a turn, because the turn's
 *      own growing tool results counted toward the budget;
 *   2. `applyCap` appended its wrap-up note to the SYSTEM PROMPT — the very
 *      first bytes on the wire;
 *   3. `discloseTools` derived its active groups from the WINDOWED messages, so
 *      a group activated by a tool call that later aged out silently retracted
 *      and the rendered tool block oscillated.
 *
 * All three move the prompt PREFIX, and llama-server's KV cache keys on a
 * byte-identical prefix. The cost is a full re-prefill per round — on a
 * latency-gated voice path, with an 8-round cap, up to 8 cold prefills per turn.
 *
 * NONE of the original suite's 12 cases assert stability: they check that the
 * result fits budget and keeps the right messages, which the buggy version also
 * did. That is the gap this file closes. The sibling fix on the prod side is
 * services/zoe-data/tests/test_zoe_agent_kv_prefix.py (PR #1612).
 *
 * EVERY assertion here is paired with a NEGATIVE CONTROL running the OLD greedy
 * rule (`legacyHead`, a faithful transcription of the pre-fix algorithm) over the
 * IDENTICAL inputs. If the old rule does not move where the new one holds still,
 * the scenario is not exercising the instability and the green result above is
 * meaningless.
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
import { activeToolNames, discloseTools } from '../src/tools/tool-groups.ts';

// ─── fixtures ────────────────────────────────────────────────────────────────

const SYSTEM = 'You are Zoe. '.repeat(40);

function userMsg(text: string): Message {
  return { role: 'user', content: text, timestamp: 0 } as Message;
}
function assistantMsg(text: string): Message {
  return {
    role: 'assistant',
    content: [{ type: 'text', text }],
    timestamp: 0,
  } as unknown as Message;
}
function assistantToolCall(name: string, id: string): Message {
  return {
    role: 'assistant',
    content: [{ type: 'toolCall', id, name, arguments: {} }],
    timestamp: 0,
  } as unknown as Message;
}
function toolResult(name: string, id: string, text: string): Message {
  return {
    role: 'toolResult',
    toolCallId: id,
    toolName: name,
    content: [{ type: 'text', text }],
    isError: false,
    timestamp: 0,
  } as unknown as Message;
}

const TOOLS: Tool[] = ['get_time', 'recall_memory', 'activate_abilities', 'get_weather'].map(
  (name) => ({ name, description: `the ${name} tool`, parameters: { type: 'object' } }) as Tool,
);

function ctx(messages: Message[], tools: Tool[] = TOOLS): Context {
  return { systemPrompt: SYSTEM, messages, tools };
}

/** A conversation of `turns` completed exchanges, each a user + assistant pair. */
function history(turns: number): Message[] {
  const out: Message[] = [];
  for (let i = 0; i < turns; i++) {
    out.push(userMsg(`user turn ${i}: ${'q'.repeat(180)}`));
    out.push(assistantMsg(`assistant reply ${i}: ${'a'.repeat(180)}`));
  }
  return out;
}

// ─── the pre-fix algorithm, for negative control ─────────────────────────────

/**
 * The OLD greedy head rule, transcribed from the pre-fix `windowContextToBudget`:
 * keep the newest block, then extend backwards while the running total (INCLUDING
 * the in-flight tool rounds) still fits the budget. Returns the message index.
 */
function legacyHead(context: Context): number {
  const windowTokens = contextWindowTokens();
  const budget = windowTokens - replyReserveTokens(windowTokens);
  const { messages } = context;
  const blockStarts: number[] = [];
  for (let i = 0; i < messages.length; i++) {
    if (messages[i].role === 'user') blockStarts.push(i);
  }
  if (blockStarts.length === 0) return 0;
  const fixed =
    64 + estimateTextTokens(context.systemPrompt ?? '') + estimateToolTokens(context.tools);
  const blockTokens = blockStarts.map((start, k) => {
    const end = k + 1 < blockStarts.length ? blockStarts[k + 1] : messages.length;
    let tokens = 0;
    for (let i = start; i < end; i++) tokens += estimateMessageTokens(messages[i]);
    return tokens;
  });
  let keptFrom = blockStarts.length - 1;
  let used = fixed + blockTokens[keptFrom];
  while (keptFrom > 0 && used + blockTokens[keptFrom - 1] <= budget) {
    keptFrom -= 1;
    used += blockTokens[keptFrom];
  }
  return blockStarts[keptFrom];
}

const budget = () => contextWindowTokens() - replyReserveTokens(contextWindowTokens());

// ─── 1. the head must not move DURING a turn ─────────────────────────────────

describe('anchor stability within a turn', () => {
  /** The same turn observed at each tool round: base history + a growing tail. */
  function roundsOfOneTurn(): Context[] {
    const base = [...history(220), userMsg('what is the weather like today?')];
    const contexts: Context[] = [ctx(base)];
    const tail: Message[] = [];
    for (let round = 0; round < 8; round++) {
      tail.push(assistantToolCall('get_weather', `call-${round}`));
      tail.push(toolResult('get_weather', `call-${round}`, `Sunny. ${'r'.repeat(400)}`));
      contexts.push(ctx([...base, ...tail]));
    }
    return contexts;
  }

  /** Estimated wire cost of a context windowed at `head`. */
  function costFromHead(context: Context, head: number): number {
    return (
      64 +
      estimateTextTokens(context.systemPrompt ?? '') +
      estimateToolTokens(context.tools) +
      context.messages.slice(head).reduce((sum, m) => sum + estimateMessageTokens(m), 0)
    );
  }

  it('the head holds still through a turn, and only ever moves to avoid overflow', () => {
    const contexts = roundsOfOneTurn();
    assert.ok(
      windowHeadIndex(contexts[contexts.length - 1]) !== null,
      'precondition: the session must be over budget, or there is nothing to stabilise',
    );
    const heads = contexts.map((c) => windowHeadIndex(c) ?? 0);

    // THE CONTRACT IS NOT "never moves" — it is "never moves unless staying put
    // would overflow". `windowHeadIndex` documents a safety valve: the in-flight
    // tail is deliberately excluded from the budget arithmetic (that exclusion is
    // what makes the head stable), so a turn whose tool results are large enough
    // to exhaust the quantum headroom must still be rescued. A 400 `exceeds the
    // available context size` is a hard failure; a cold cache is only slow.
    //
    // This fixture is deliberately harsh — eight rounds of 400-character tool
    // results — precisely so the valve fires and gets asserted rather than
    // assumed. Real voice-turn tool results are far smaller.
    let moves = 0;
    for (let i = 1; i < heads.length; i++) {
      if (heads[i] === heads[i - 1]) continue;
      moves += 1;
      assert.ok(
        heads[i] > heads[i - 1],
        `the head moved backwards mid-turn (${heads[i - 1]} → ${heads[i]})`,
      );
      assert.ok(
        costFromHead(contexts[i], heads[i - 1]) > budget(),
        `the head moved from ${heads[i - 1]} to ${heads[i]} on round ${i} even though holding ` +
          'still would still have fit the budget — that is the instability this fix removes, ' +
          'not the safety valve',
      );
    }
    assert.ok(
      moves <= 2,
      `the head moved ${moves} times across ${heads.length} rounds of ONE turn ` +
        `(${heads.join(' → ')}) — that is sliding, not a valve firing`,
    );
    // And the bulk of the turn must genuinely share one anchor.
    assert.ok(
      heads.filter((h) => h === heads[0]).length >= heads.length - 2,
      `most rounds should share the opening anchor (${heads.join(' → ')})`,
    );
  });

  it('NEGATIVE CONTROL: the pre-fix greedy rule DID move mid-turn', () => {
    const heads = roundsOfOneTurn().map(legacyHead);
    assert.ok(
      new Set(heads).size > 1,
      `the legacy rule held still (${heads.join(' → ')}) — this scenario does not reproduce the ` +
        'instability, so the stability assertion above proves nothing',
    );
  });

  it('the prompt actually sent stays under budget on every round', () => {
    for (const context of roundsOfOneTurn()) {
      const out = applyPolicies(context);
      const total =
        64 +
        estimateTextTokens(out.systemPrompt ?? '') +
        estimateToolTokens(out.tools) +
        out.messages.reduce((sum, m) => sum + estimateMessageTokens(m), 0);
      assert.ok(
        total <= budget(),
        `round assembled ${total} tokens against a ${budget()} budget — stability must never ` +
          'come at the cost of overflowing the model context',
      );
    }
  });
});

// ─── 2. the head must STEP, not creep, across turns ──────────────────────────

describe('anchor stability across turns', () => {
  function growingSession(): Context[] {
    const messages = [...history(200)];
    const contexts: Context[] = [];
    for (let turn = 0; turn < 30; turn++) {
      messages.push(userMsg(`later turn ${turn}: ${'q'.repeat(180)}`));
      contexts.push(ctx([...messages]));
      messages.push(assistantMsg(`later reply ${turn}: ${'a'.repeat(180)}`));
    }
    return contexts;
  }

  function moves(heads: (number | null)[]): number {
    let count = 0;
    for (let i = 1; i < heads.length; i++) if (heads[i] !== heads[i - 1]) count += 1;
    return count;
  }

  it('the head steps a handful of times over 30 turns, not every turn', () => {
    const heads = growingSession().map(windowHeadIndex);
    const moved = moves(heads);
    assert.ok(
      heads.some((h) => h !== null),
      'precondition: the session must go over budget within 30 turns',
    );
    assert.ok(
      moved <= 6,
      `the head moved ${moved} times over 30 turns (${heads.join(',')}) — that is creeping, ` +
        'not stepping; the quantum is not holding the anchor',
    );
  });

  it('NEGATIVE CONTROL: the pre-fix greedy rule moved on nearly every turn', () => {
    const contexts = growingSession();
    const legacyMoves = moves(contexts.map(legacyHead));
    const newMoves = moves(contexts.map(windowHeadIndex));
    assert.ok(
      legacyMoves > newMoves * 2,
      `the legacy rule moved ${legacyMoves} times vs ${newMoves} now — without a clear gap this ` +
        'scenario is not exercising the creep the fix targets',
    );
  });

  it('the head only ever moves FORWARD (history is never re-added)', () => {
    const heads = growingSession()
      .map(windowHeadIndex)
      .filter((h): h is number => h !== null);
    for (let i = 1; i < heads.length; i++) {
      assert.ok(
        heads[i] >= heads[i - 1],
        `the head moved backwards (${heads[i - 1]} → ${heads[i]}) — re-adding dropped history ` +
          'invalidates the prefix just as badly as dropping more',
      );
    }
  });
});

// ─── 3. the rendered tool block must be monotone ─────────────────────────────

describe('tool-block stability (disclosure basis)', () => {
  /**
   * A session where the tool call that activated a group is old enough to be
   * windowed out, while the group's tool is still relevant to the conversation.
   */
  function agedActivation(): { full: Message[]; windowed: Message[] } {
    const full: Message[] = [
      userMsg('what is the weather like?'),
      assistantToolCall('get_weather', 'old-call'),
      toolResult('get_weather', 'old-call', 'Sunny.'),
      ...history(30),
      userMsg('and how about that thing we discussed?'),
    ];
    // What the window would leave: everything after the aged-out activation.
    const windowed = full.slice(3);
    return { full, windowed };
  }

  it('a group activated by an aged-out tool call stays disclosed', () => {
    const { full, windowed } = agedActivation();
    const namesFromWindow = activeToolNames(windowed);
    const namesFromBasis = activeToolNames(full);

    assert.ok(
      !namesFromWindow.has('get_weather'),
      'precondition: the windowed view alone must have LOST the weather group, or there is ' +
        'no retraction to guard against',
    );
    assert.ok(
      namesFromBasis.has('get_weather'),
      'the full basis should still carry the weather group',
    );

    // discloseTools with the pre-window basis keeps it; without it, it retracts.
    const disclosedWithBasis = discloseTools(ctx(windowed), full);
    const disclosedWithoutBasis = discloseTools(ctx(windowed));
    assert.ok(
      disclosedWithBasis.tools?.some((t) => t.name === 'get_weather'),
      'the disclosure basis is not being honoured — the tool block will oscillate',
    );
    assert.ok(
      !disclosedWithoutBasis.tools?.some((t) => t.name === 'get_weather'),
      'NEGATIVE CONTROL: deriving from the windowed list alone should have retracted the ' +
        'group; if it does not, this scenario proves nothing',
    );
  });

  it('applyPolicies uses the pre-window basis end to end', () => {
    const { full } = agedActivation();
    const out = applyPolicies(ctx(full));
    // Whatever windowing decided, the weather schema must still be offered.
    assert.ok(
      out.tools?.some((t) => t.name === 'get_weather'),
      'applyPolicies retracted a group whose activation aged out of the window',
    );
  });
});
