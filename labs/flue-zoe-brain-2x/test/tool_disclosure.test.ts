/**
 * Unit coverage for progressive tool disclosure (LAB-ONLY, offline, no network).
 *
 * Proves the wire-level active-set derivation in src/tools/tool-groups.ts:
 *   - a plain turn discloses ONLY the always-on core;
 *   - keyword relevance on the session's user messages pre-discloses matching
 *     groups (session-sticky; ZOE_BRAIN_STICKY_DISCLOSURE=false → last message);
 *   - the disclosed tool block is APPEND-ONLY across a session's turns (the
 *     llama-server prompt-cache contract), in activation order;
 *   - an `activate_abilities` tool call in the transcript discloses its group
 *     on the next model request (the within-turn unlock path);
 *   - a previously-used grouped tool keeps its group disclosed (sticky);
 *   - unknown tool names always survive the filter;
 *   - ZOE_BRAIN_PROGRESSIVE_TOOLS=false restores all-schemas behaviour;
 *   - past the iteration cap, ALL tools are stripped regardless of disclosure.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/tool_disclosure.test.ts
 */
process.env.ZOE_BRAIN_USER_ID = 'jason';

import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Context, Message, Tool } from '@earendil-works/pi-ai';

const {
  activeToolNames,
  groupActivationOrder,
  discloseTools,
  stripCodingBuiltins,
  CORE_TOOL_NAMES,
  TOOL_GROUPS,
  CODING_BUILTIN_TOOL_NAMES,
} = await import('../src/tools/tool-groups.ts');
const { applyPolicies } = await import('../src/providers/capped-completions.ts');
const { zoeTools } = await import('../src/tools/zoe-tools.ts');

// ─── helpers ──────────────────────────────────────────────────────────────────

function userMsg(text: string): Message {
  return { role: 'user', content: text, timestamp: 0 } as Message;
}

function assistantToolCall(name: string, args: Record<string, unknown> = {}): Message {
  return {
    role: 'assistant',
    content: [{ type: 'toolCall', id: 't1', name, arguments: args }],
  } as unknown as Message;
}

function toolResult(name: string, text: string): Message {
  return {
    role: 'toolResult',
    toolCallId: 't1',
    toolName: name,
    content: [{ type: 'text', text }],
    isError: false,
    timestamp: 0,
  } as Message;
}

/** Wire-level Tool stubs for every agent tool (schema content irrelevant here). */
const ALL_WIRE_TOOLS: Tool[] = zoeTools.map(
  (t) => ({ name: t.name, description: t.description, parameters: {} }) as unknown as Tool,
);

/**
 * The pi/Flue coding built-ins the harness injects on EVERY turn (verified from
 * @flue/runtime `createTools`). These are what leaked into the voice brain's
 * wire before the denylist. A representative `context.tools` is Zoe tools PLUS
 * these — mirroring exactly what the framework hands the model.
 */
const CODING_WIRE_TOOLS: Tool[] = [...CODING_BUILTIN_TOOL_NAMES].map(
  (name) => ({ name, description: `pi built-in ${name}`, parameters: {} }) as unknown as Tool,
);

/** Zoe tools + the injected coding built-ins — the real on-the-wire tool list. */
const WIRE_TOOLS_WITH_CODING: Tool[] = [...ALL_WIRE_TOOLS, ...CODING_WIRE_TOOLS];

function ctx(messages: Message[], tools: Tool[] = ALL_WIRE_TOOLS): Context {
  return { systemPrompt: 'You are Zoe.', messages, tools };
}

const names = (c: Context) => (c.tools ?? []).map((t) => t.name).sort();

// ─── active-set derivation ────────────────────────────────────────────────────

test('plain turn → only the always-on core is disclosed', () => {
  const active = activeToolNames([userMsg('how do I poach an egg?')]);
  assert.deepEqual([...active].sort(), [...CORE_TOOL_NAMES].sort());
});

test('keyword relevance pre-discloses the matching group', () => {
  const active = activeToolNames([userMsg('add milk to my shopping list')]);
  for (const name of TOOL_GROUPS.lists) assert.ok(active.has(name), name);
  assert.ok(!active.has('get_weather'));
  assert.ok(!active.has('create_note'));
});

test('keyword relevance is session-sticky: an earlier user message keeps its group', () => {
  const active = activeToolNames([
    userMsg('what is the weather like?'),
    userMsg('thanks. how do I poach an egg?'),
  ]);
  assert.ok(active.has('get_weather'));
});

test('ZOE_BRAIN_STICKY_DISCLOSURE=false restores last-user-message-only matching', () => {
  process.env.ZOE_BRAIN_STICKY_DISCLOSURE = 'false';
  try {
    const active = activeToolNames([
      userMsg('what is the weather like?'),
      userMsg('thanks. how do I poach an egg?'),
    ]);
    assert.ok(!active.has('get_weather'));
  } finally {
    delete process.env.ZOE_BRAIN_STICKY_DISCLOSURE;
  }
});

test('groupActivationOrder lists groups in the order they first became active', () => {
  const order = groupActivationOrder([
    userMsg('set a timer for ten minutes'),
    assistantToolCall('set_timer'),
    toolResult('set_timer', 'Timer set.'),
    userMsg('can you check something for me?'),
    assistantToolCall('activate_abilities', { group: 'calendar' }),
    toolResult('activate_abilities', 'Activated.'),
    userMsg('and what is the weather tomorrow? also any timers left?'),
  ]);
  assert.deepEqual(order, ['timers', 'calendar', 'weather']);
});

// ─── prompt-cache stability: the tool block is append-only per session ───────

/**
 * A realistic multi-turn voice session: each entry is the transcript as the
 * provider sees it at the START of that turn (the new user message is last).
 * Domains arrive, recur and go quiet — the shape that made the old
 * last-message-only disclosure retract and re-insert groups.
 */
const SESSION_TURNS: Message[][] = (() => {
  const script: Array<Message[]> = [
    [userMsg('hey zoe, how are you?'), { role: 'assistant', content: [{ type: 'text', text: 'Good!' }] } as unknown as Message],
    [userMsg('what is the weather like today?'), assistantToolCall('get_weather'), toolResult('get_weather', 'Sunny.')],
    [userMsg('nice. tell me a joke'), { role: 'assistant', content: [{ type: 'text', text: 'Ha.' }] } as unknown as Message],
    // A keyword turn the model answered WITHOUT a tool call: nothing makes the
    // group sticky except the keyword itself — the retraction case.
    [userMsg('play something relaxing'), { role: 'assistant', content: [{ type: 'text', text: 'Sure.' }] } as unknown as Message],
    [userMsg('add milk to my shopping list'), assistantToolCall('shopping_list_add'), toolResult('shopping_list_add', 'Added.')],
    [userMsg('set a timer for 5 minutes'), assistantToolCall('set_timer'), toolResult('set_timer', 'Set.')],
    [userMsg('how do I poach an egg?'), { role: 'assistant', content: [{ type: 'text', text: 'Simmer.' }] } as unknown as Message],
    [userMsg('is it going to rain?')],
  ];
  const turns: Message[][] = [];
  let transcript: Message[] = [];
  for (const [user, ...rest] of script) {
    transcript = [...transcript, user];
    turns.push(transcript); // what the FIRST model call of this turn sees
    transcript = [...transcript, ...rest];
  }
  return turns;
})();

/** The rendered tool-block order the model sees on the first call of each turn. */
function toolBlocks(disclose: (c: Context) => Context): string[][] {
  return SESSION_TURNS.map((messages) => (disclose(ctx(messages)).tools ?? []).map((t) => t.name));
}

function assertAppendOnly(blocks: string[][]): void {
  for (let i = 1; i < blocks.length; i++) {
    const prev = blocks[i - 1];
    const next = blocks[i];
    assert.deepEqual(
      next.slice(0, prev.length),
      prev,
      `turn ${i}: tool block must extend turn ${i - 1}'s (cached prefix), got\n  ${prev.join(',')}\n→ ${next.join(',')}`,
    );
  }
}

test('the tool block is append-only across consecutive turns of one session', () => {
  const blocks = toolBlocks((c) => discloseTools(c));
  assertAppendOnly(blocks);
  // Not vacuous: the session really did grow the block.
  assert.ok(blocks[blocks.length - 1].length > blocks[0].length);
});

test('via applyPolicies too (the real wire path), with the same append-only block', () => {
  assertAppendOnly(toolBlocks((c) => applyPolicies(c)));
});

test('NEGATIVE CONTROL: last-message-only matching retracts groups and breaks the prefix', () => {
  process.env.ZOE_BRAIN_STICKY_DISCLOSURE = 'false';
  try {
    assert.throws(() => assertAppendOnly(toolBlocks((c) => discloseTools(c))));
  } finally {
    delete process.env.ZOE_BRAIN_STICKY_DISCLOSURE;
  }
});

test('NEGATIVE CONTROL: registration-order emission inserts a new group mid-block', () => {
  // The pre-fix emission order: filter the registry in place. A group whose
  // tools sit EARLY in the registry (lists) arriving after a later one
  // (timers) lands in the middle and shifts every tool after it.
  const registrationOrder = (c: Context): Context => {
    const active = new Set((discloseTools(c).tools ?? []).map((t) => t.name));
    return { ...c, tools: (c.tools ?? []).filter((t) => active.has(t.name)) };
  };
  const reordered: Message[][] = [
    [userMsg('set a timer for 5 minutes')],
    [userMsg('set a timer for 5 minutes'), assistantToolCall('set_timer'), toolResult('set_timer', 'Set.'), userMsg('add eggs to my shopping list')],
  ];
  const legacy = reordered.map((m) => (registrationOrder(ctx(m)).tools ?? []).map((t) => t.name));
  assert.throws(() => assertAppendOnly(legacy));
  const fixed = reordered.map((m) => (discloseTools(ctx(m)).tools ?? []).map((t) => t.name));
  assertAppendOnly(fixed);
});

test('activate_abilities call in the transcript unlocks its group', () => {
  const messages = [
    userMsg('can you check something outside for me?'),
    assistantToolCall('activate_abilities', { group: 'weather' }),
    toolResult('activate_abilities', 'Activated the weather tools: get_weather.'),
  ];
  const active = activeToolNames(messages);
  assert.ok(active.has('get_weather'));
});

test('activate_abilities with a garbage group unlocks nothing', () => {
  const messages = [
    userMsg('hello'),
    assistantToolCall('activate_abilities', { group: 'everything' }),
    toolResult('activate_abilities', 'nope'),
  ];
  const active = activeToolNames(messages);
  assert.deepEqual([...active].sort(), [...CORE_TOOL_NAMES].sort());
});

test('a previously-used grouped tool keeps its group disclosed (sticky)', () => {
  const messages = [
    userMsg('what is the weather like?'),
    assistantToolCall('get_weather', { forecast: false }),
    toolResult('get_weather', 'Sunny, 21°C.'),
    userMsg('and tomorrow?'), // no weather keyword
  ];
  const active = activeToolNames(messages);
  assert.ok(active.has('get_weather'));
});

// ─── wire filtering ───────────────────────────────────────────────────────────

test('discloseTools filters the wire copy down to the active set', () => {
  const out = discloseTools(ctx([userMsg('set a timer for 10 minutes')]));
  assert.deepEqual(names(out), [...CORE_TOOL_NAMES, 'set_timer'].sort());
});

test('discloseTools never mutates the original context', () => {
  const original = ctx([userMsg('hello there')]);
  const before = names(original);
  discloseTools(original);
  assert.deepEqual(names(original), before);
});

test('unknown tool names always survive the filter', () => {
  const withUnknown = [
    ...ALL_WIRE_TOOLS,
    { name: 'future_ungrouped_tool', description: 'x', parameters: {} } as unknown as Tool,
  ];
  const out = discloseTools(ctx([userMsg('hello')], withUnknown));
  assert.ok(names(out).includes('future_ungrouped_tool'));
});

// ─── pi/Flue coding built-ins are stripped, Zoe tools preserved ────────────────

test('discloseTools strips every pi coding built-in from a representative wire list', () => {
  const out = discloseTools(ctx([userMsg('how do I poach an egg?')], WIRE_TOOLS_WITH_CODING));
  const outNames = new Set((out.tools ?? []).map((t) => t.name));
  for (const builtin of CODING_BUILTIN_TOOL_NAMES) {
    assert.ok(!outNames.has(builtin), `coding built-in ${builtin} must be stripped`);
  }
});

test('discloseTools keeps all 20 Zoe tools + activate_abilities in a full-relevance turn', () => {
  // A message that trips no group still keeps the core; to prove NO real Zoe
  // tool is ever collateral-stripped, disclose the sticky-maximal set: mark
  // every group used so activeToolNames == all Zoe tools, then confirm each
  // survives while the coding built-ins are gone.
  const usedEvery = zoeTools.map((t) => assistantToolCall(t.name));
  const messages = [userMsg('do everything'), ...usedEvery];
  const out = discloseTools(ctx(messages, WIRE_TOOLS_WITH_CODING));
  const outNames = new Set((out.tools ?? []).map((t) => t.name));
  for (const t of zoeTools) assert.ok(outNames.has(t.name), `Zoe tool ${t.name} must survive`);
  assert.ok(outNames.has('activate_abilities'), 'activator must survive');
  for (const builtin of CODING_BUILTIN_TOOL_NAMES) {
    assert.ok(!outNames.has(builtin), `coding built-in ${builtin} must be stripped`);
  }
});

test('stripCodingBuiltins removes ONLY the coding built-ins, never a Zoe tool', () => {
  const out = stripCodingBuiltins(ctx([userMsg('hi')], WIRE_TOOLS_WITH_CODING));
  const outNames = (out.tools ?? []).map((t) => t.name).sort();
  assert.deepEqual(outNames, ALL_WIRE_TOOLS.map((t) => t.name).sort());
});

test('stripCodingBuiltins never mutates the original context', () => {
  const original = ctx([userMsg('hi')], WIRE_TOOLS_WITH_CODING);
  const before = original.tools?.length;
  stripCodingBuiltins(original);
  assert.equal(original.tools?.length, before);
});

test('applyPolicies strips coding built-ins even with disclosure OFF (safety floor)', () => {
  process.env.ZOE_BRAIN_PROGRESSIVE_TOOLS = 'false';
  try {
    const out = applyPolicies(ctx([userMsg('hello')], WIRE_TOOLS_WITH_CODING));
    const outNames = new Set((out.tools ?? []).map((t) => t.name));
    for (const builtin of CODING_BUILTIN_TOOL_NAMES) {
      assert.ok(!outNames.has(builtin), `built-in ${builtin} must be stripped with disclosure off`);
    }
    // With disclosure off, every Zoe tool still passes through.
    for (const t of zoeTools) assert.ok(outNames.has(t.name), `Zoe tool ${t.name} must survive`);
  } finally {
    delete process.env.ZOE_BRAIN_PROGRESSIVE_TOOLS;
  }
});

// ─── applyPolicies: kill switch + cap interplay ───────────────────────────────

test('ZOE_BRAIN_PROGRESSIVE_TOOLS=false → all schemas pass through', () => {
  process.env.ZOE_BRAIN_PROGRESSIVE_TOOLS = 'false';
  try {
    const out = applyPolicies(ctx([userMsg('hello')]));
    assert.equal(out.tools?.length, ALL_WIRE_TOOLS.length);
  } finally {
    delete process.env.ZOE_BRAIN_PROGRESSIVE_TOOLS;
  }
});

test('disclosure ON by default: applyPolicies shrinks a plain turn to the core', () => {
  const out = applyPolicies(ctx([userMsg('how do I poach an egg?')]));
  assert.deepEqual(names(out), [...CORE_TOOL_NAMES].sort());
});

test('past the iteration cap, ALL tools are stripped regardless of disclosure', () => {
  process.env.ZOE_BRAIN_MAX_TOOL_ITERS = '2';
  try {
    const messages: Message[] = [
      userMsg('what is the weather like?'),
      assistantToolCall('get_weather'),
      toolResult('get_weather', 'Sunny.'),
      assistantToolCall('get_weather'),
      toolResult('get_weather', 'Sunny.'),
    ];
    const before = ctx(messages);
    const out = applyPolicies(before);
    assert.equal(out.tools?.length, 0);
    // PORTED: the wrap-up note moved OUT of the system prompt and onto a
    // trailing user message. The system prompt is the prompt PREFIX, so
    // mutating it per-round invalidated llama-server's whole KV cache; the
    // tail after the last user message is already cold every round. The
    // enforcement is `tools: []`, which is asserted above — the note is only
    // steering, so what matters is that it is present and NOT in the prefix.
    assert.equal(
      out.systemPrompt,
      before.systemPrompt,
      'the cap must not mutate the system prompt — that is the cached prefix',
    );
    const last = out.messages[out.messages.length - 1];
    assert.equal(last.role, 'user');
    assert.match(String(last.content), /reached the tool-call limit/i);
  } finally {
    delete process.env.ZOE_BRAIN_MAX_TOOL_ITERS;
  }
});

// ─── the activator tool itself ────────────────────────────────────────────────

test('activate_abilities run() names the unlocked tools and steers the model', async () => {
  const activator = zoeTools.find((t) => t.name === 'activate_abilities')! as unknown as {
    run: (c: { data: Record<string, unknown> }) => Promise<string>;
  };
  const out = await activator.run({ data: { group: 'calendar' } });
  assert.match(out, /calendar/);
  assert.match(out, /show_calendar/);
  assert.match(out, /add_calendar_event/);
  assert.match(out, /call the one you need/i);
});
