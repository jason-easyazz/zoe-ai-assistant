/**
 * Unit coverage for progressive tool disclosure (LAB-ONLY, offline, no network).
 *
 * Proves the wire-level active-set derivation in src/tools/tool-groups.ts:
 *   - a plain turn discloses ONLY the always-on core;
 *   - keyword relevance on the last user message pre-discloses matching groups;
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

test('keyword relevance matches the LAST user message, not earlier ones', () => {
  const active = activeToolNames([
    userMsg('what is the weather like?'),
    userMsg('thanks. how do I poach an egg?'),
  ]);
  assert.ok(!active.has('get_weather'));
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
    const out = applyPolicies(ctx(messages));
    assert.equal(out.tools?.length, 0);
    assert.match(out.systemPrompt ?? '', /reached the tool-call limit/i);
  } finally {
    delete process.env.ZOE_BRAIN_MAX_TOOL_ITERS;
  }
});

// ─── the activator tool itself ────────────────────────────────────────────────

test('activate_abilities run() names the unlocked tools and steers the model', async () => {
  const activator = zoeTools.find((t) => t.name === 'activate_abilities')! as unknown as {
    run: (c: { input: Record<string, unknown> }) => Promise<string>;
  };
  const out = await activator.run({ input: { group: 'calendar' } });
  assert.match(out, /calendar/);
  assert.match(out, /show_calendar/);
  assert.match(out, /add_calendar_event/);
  assert.match(out, /call the one you need/i);
});
