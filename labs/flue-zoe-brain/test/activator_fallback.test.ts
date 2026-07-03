/**
 * Unit coverage for the activate_abilities fallback fixes (LAB-ONLY, offline).
 *
 * E2E finding this locks in: with progressive disclosure ON, 0/3 indirect
 * (keyword-free) prompts ever called activate_abilities, and one reply
 * fabricated a weather forecast. The fixes under test:
 *   - widened GROUP_TRIGGERS: indirect weather phrasings (washing / laundry /
 *     outside / umbrella) and calendar phrasings ("anything on <day>",
 *     "am I free") pre-disclose their group as defence-in-depth;
 *   - the activate_abilities wire schema stays a DEAD-SIMPLE single-enum
 *     object (exact shape pinned against the same @valibot/to-json-schema
 *     conversion Flue applies — node_modules/@flue/runtime dist tool module);
 *   - the agent instructions carry the group catalogue (GROUP_SUMMARY) and
 *     the imperative activator doctrine, not just the tool description.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/activator_fallback.test.ts
 */
process.env.ZOE_BRAIN_USER_ID = 'jason';

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { toJsonSchema } from '@valibot/to-json-schema';

const { activeToolNames, CORE_TOOL_NAMES, GROUP_NAMES, GROUP_SUMMARY } = await import(
  '../src/tools/tool-groups.ts'
);
const { zoeTools } = await import('../src/tools/zoe-tools.ts');
const { ACTIVATOR_DOCTRINE, IN_SESSION_CONTEXT_DOCTRINE, VOICE_DELIVERY_DOCTRINE, ZOE_INSTRUCTIONS } =
  await import('../src/agents/zoe.ts');

import type { Message } from '@earendil-works/pi-ai';

function userMsg(text: string): Message {
  return { role: 'user', content: text, timestamp: 0 } as Message;
}

// ─── widened weather triggers (indirect phrasings) ────────────────────────────

for (const prompt of [
  'can I hang the washing out today?',
  'is it nice outside?',
  'should I take an umbrella?',
  'can I put the laundry on the line?',
]) {
  test(`weather pre-disclosed for indirect phrasing: "${prompt}"`, () => {
    assert.ok(activeToolNames([userMsg(prompt)]).has('get_weather'));
  });
}

// ─── widened calendar triggers ("anything on <day>" family) ──────────────────

for (const prompt of [
  'anything on Friday?',
  'do we have anything happening this weekend?',
  'what do I have on Saturday?',
  'is there something planned for Tuesday?',
  'am I free tomorrow?',
]) {
  test(`calendar pre-disclosed for indirect phrasing: "${prompt}"`, () => {
    assert.ok(activeToolNames([userMsg(prompt)]).has('show_calendar'));
  });
}

test('"what\'s on my shopping list?" trips lists but NOT calendar', () => {
  const active = activeToolNames([userMsg("what's on my shopping list?")]);
  assert.ok(active.has('show_list'));
  assert.ok(!active.has('show_calendar'));
});

test('a genuinely tool-free prompt still discloses only the core', () => {
  const active = activeToolNames([userMsg('tell me a fun fact about octopuses')]);
  assert.deepEqual([...active].sort(), [...CORE_TOOL_NAMES].sort());
});

// ─── the activator wire schema: a dead-simple single-enum object ─────────────

test('activate_abilities wire schema is exactly one required enum-of-groups property', () => {
  const activator = zoeTools.find((t) => t.name === 'activate_abilities');
  assert.ok(activator, 'activate_abilities must be registered in zoeTools');
  // Same conversion Flue applies at the wire (valibotToJsonSchema strips
  // $schema and passes errorMode: 'ignore') — see @flue/runtime dist tool module.
  const { $schema: _schema, ...wire } = toJsonSchema(
    activator.input as Parameters<typeof toJsonSchema>[0],
    { errorMode: 'ignore' },
  ) as Record<string, unknown>;
  const properties = wire.properties as Record<string, Record<string, unknown>>;

  assert.equal(wire.type, 'object');
  assert.deepEqual(wire.required, ['group']);
  assert.deepEqual(Object.keys(properties), ['group']);
  assert.equal(properties.group.type, 'string');
  assert.deepEqual(properties.group.enum, [...GROUP_NAMES]);
  assert.match(String(properties.group.description ?? ''), /one of/i);
});

// ─── the instructions carry the catalogue + imperative doctrine ───────────────

test('agent instructions contain the derived group catalogue (GROUP_SUMMARY)', () => {
  assert.ok(GROUP_SUMMARY.includes('weather'));
  assert.ok(ZOE_INSTRUCTIONS.includes(GROUP_SUMMARY));
});

test('activator doctrine is imperative: MUST activate first, NEVER fabricate', () => {
  assert.ok(ZOE_INSTRUCTIONS.includes(ACTIVATOR_DOCTRINE));
  assert.match(ACTIVATOR_DOCTRINE, /NO weather, calendar, list, timer, reminder/);
  assert.match(ACTIVATOR_DOCTRINE, /MUST use a tool FIRST/);
  assert.match(ACTIVATOR_DOCTRINE, /activate_abilities/);
  assert.match(ACTIVATOR_DOCTRINE, /NEVER claim/);
});

test('activator doctrine carries the ported tool-first directives (no over-clarify, no premature refusal)', () => {
  // Ported from prod _ZOE_SOUL_BASE / _ZOE_SOUL_VOICE (services/zoe-data/zoe_agent.py).
  assert.match(ACTIVATOR_DOCTRINE, /Act proactively/);
  assert.match(ACTIVATOR_DOCTRINE, /don't ask a clarifying question first/);
  assert.match(ACTIVATOR_DOCTRINE, /until a tool has actually tried and failed/);
});

test('voice-delivery doctrine: spoken-length + no-markdown discipline, present in instructions', () => {
  // Ported from prod _ZOE_SOUL_VOICE (services/zoe-data/zoe_agent.py). This is the
  // voice brain, so the same tight spoken discipline applies.
  assert.ok(ZOE_INSTRUCTIONS.includes(VOICE_DELIVERY_DOCTRINE));
  assert.match(VOICE_DELIVERY_DOCTRINE, /spoken aloud/);
  assert.match(VOICE_DELIVERY_DOCTRINE, /No markdown/);
  assert.match(VOICE_DELIVERY_DOCTRINE, /lead with it/);
  assert.match(VOICE_DELIVERY_DOCTRINE, /brief but never clipped/);
  // Must NOT weaken the behavioural doctrine: delivery guidance only, no new
  // recall/activation/fabrication rules that could conflict with the above.
  assert.doesNotMatch(VOICE_DELIVERY_DOCTRINE, /recall_memory/);
  assert.doesNotMatch(VOICE_DELIVERY_DOCTRINE, /activate_abilities/);
});

test('voice delivery is self-scoped + ordered BEFORE the tool doctrine (Greptile #997 P2)', () => {
  // The delivery block must explicitly defer to the tool rules so "lead with the
  // answer" can't nudge a 4B model into a direct reply over a needed activation.
  assert.match(VOICE_DELIVERY_DOCTRINE, /the tool rules above still come first/);
  assert.match(VOICE_DELIVERY_DOCTRINE, /Once you actually have your answer, lead with it/);
  // And the behavioural doctrines (activate-first / never-fabricate) must sit
  // AFTER voice delivery in the assembled instructions, keeping last-position
  // weight closest to the generation boundary.
  const iVoice = ZOE_INSTRUCTIONS.indexOf(VOICE_DELIVERY_DOCTRINE);
  const iActivator = ZOE_INSTRUCTIONS.indexOf(ACTIVATOR_DOCTRINE);
  const iInSession = ZOE_INSTRUCTIONS.indexOf(IN_SESSION_CONTEXT_DOCTRINE);
  assert.ok(iVoice >= 0 && iActivator >= 0 && iInSession >= 0);
  assert.ok(iVoice < iActivator, 'voice delivery must precede the activator doctrine');
  assert.ok(iActivator < iInSession, 'activator then in-session context stay last');
});

test('in-session context doctrine: live transcript beats an empty recall store', () => {
  // The soul's recall imperative stays intact...
  assert.match(ZOE_INSTRUCTIONS, /ALWAYS call recall_memory FIRST/);
  // ...but the in-session doctrine is appended and rebalances precedence so the
  // model trusts facts stated this session even when the recall store is empty.
  assert.ok(ZOE_INSTRUCTIONS.includes(IN_SESSION_CONTEXT_DOCTRINE));
  assert.match(IN_SESSION_CONTEXT_DOCTRINE, /DURING this conversation are true and usable immediately/);
  assert.match(IN_SESSION_CONTEXT_DOCTRINE, /empty recall result means nothing is stored from before/);
  // Anti-fabrication / past-conversation recall must NOT be weakened away: the
  // doctrine must still tell the model to consult recall_memory for the past, and
  // must not use the strong "overrides" framing (Greptile P2 #988) that could
  // suppress genuine past-conversation lookups.
  assert.match(IN_SESSION_CONTEXT_DOCTRINE, /keep calling it first/);
  assert.match(IN_SESSION_CONTEXT_DOCTRINE, /it adds to that rule, it does not cancel it/);
  assert.doesNotMatch(IN_SESSION_CONTEXT_DOCTRINE, /overrides the recall rule/);
});
