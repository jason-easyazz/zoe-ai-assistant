/**
 * Every tool returns a shape Flue 2.x accepts — table-driven across all 21.
 *
 * THE FAILURE MODE THIS EXISTS FOR IS A RUNTIME THROW, NOT A TYPE ERROR. On 2.x
 * `run()` returns a result envelope `{ output?, terminate? }`. A bare `string` is
 * still legal sugar, but "any other bare value (object, number, boolean, array,
 * null) NOW THROWS AT RUNTIME" (@flue/runtime migration guide). Since `run` is
 * typed loosely enough that a wrong-shaped return can survive `tsc`, and since a
 * throw only surfaces when the model happens to call that particular tool, a
 * missed tool could sit green through typecheck, through the whole suite, and
 * through a smoke turn — then fail live the first time someone asks for the one
 * ability nobody exercised.
 *
 * So: call EVERY tool, assert EVERY return is a legal shape. No enumeration by
 * hand — the table is derived from `zoeTools`, so a tool added without a case
 * here fails the completeness check rather than being silently skipped.
 *
 * Offline and side-effect-free by construction: `ZOE_DATA_URL` points at a dead
 * port and `ZOE_BRAIN_ALLOW_WRITES` is unset, so read tools take their
 * transport-failure path and write tools take their dry-run path. Both return
 * strings — which is the point being asserted.
 */
process.env.ZOE_DATA_URL = 'http://127.0.0.1:9';
process.env.ZOE_BRAIN_ALLOW_WRITES = 'false';
process.env.ZOE_BRAIN_USER_ID = 'jason';
process.env.ZOE_BRAIN_TOOL_TIMEOUT_MS = '150';

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { zoeTools } from '../src/tools/zoe-tools.ts';

/** Representative arguments per tool. Keys must cover every tool in `zoeTools`. */
const ARGS: Record<string, Record<string, unknown>> = {
  get_time: {},
  recall_memory: { query: 'me' },
  shopping_list_add: { item: 'oat milk' },
  get_weather: { forecast: false },
  list_reminders: {},
  show_calendar: { qualifier: 'today' },
  show_list: { list_type: 'shopping' },
  set_timer: { minutes: 5, label: 'eggs' },
  add_reminder: { title: 'bins out', time: '19:00' },
  add_calendar_event: { title: 'dentist', date: '2026-08-04' },
  create_note: { content: 'wifi password is hunter2' },
  add_to_list: { item: 'gym', list_type: 'tasks' },
  list_remove: { item: 'bread', list_type: 'shopping' },
  note_search: { query: 'wifi' },
  journal: { action: 'create', content: 'A good day.' },
  people: { action: 'search', query: 'Sarah' },
  media: { action: 'play', query: 'jazz' },
  home: { action: 'on', room: 'kitchen' },
  remember_fact: { fact: 'anniversary is June 3rd' },
  remember_emotional_moment: { moment: 'Jason is anxious about settlement', valence: 'neg' },
  activate_abilities: { group: 'calendar' },
};

/** Additional argument shapes worth exercising per tool (branchy run bodies). */
const EXTRA_ARGS: Record<string, Record<string, unknown>[]> = {
  journal: [{ action: 'prompt' }, { action: 'streak' }],
  people: [{ action: 'create', name: 'Sarah', relationship: 'colleague' }],
  media: [
    { action: 'control', command: 'pause' },
    { action: 'set_music_volume', level: 30 },
    { action: 'system_volume', direction: 'up' },
    { action: 'system_volume', direction: 'set', level: 40 },
    { action: 'setup' },
  ],
  home: [{ action: 'off' }, { action: 'dim', room: 'living room' }],
  remember_emotional_moment: [{ moment: 'A milestone', intensity: 0.8 }],
  get_weather: [{ forecast: true, location: 'Perth' }],
};

type RunnableTool = {
  name: string;
  description: string;
  input?: unknown;
  run: (ctx: {
    data: Record<string, unknown>;
    signal?: AbortSignal;
    toolCallId: string;
    log: unknown;
  }) => unknown;
};

const TOOLS = zoeTools as unknown as RunnableTool[];

/**
 * The legal 2.x return shapes. A bare string, an envelope object, or nothing —
 * anything else throws inside the runtime.
 */
function assertLegalReturn(toolName: string, value: unknown, argLabel: string): void {
  const where = `${toolName}(${argLabel})`;
  if (typeof value === 'string') return;
  if (value === undefined) return;
  assert.ok(
    value !== null && typeof value === 'object' && !Array.isArray(value),
    `${where} returned ${Array.isArray(value) ? 'an array' : JSON.stringify(value)} — on Flue 2.x ` +
      'any bare non-string value throws at runtime. Return a string or wrap it as { output: value }.',
  );
  const keys = Object.keys(value as object);
  assert.ok(
    keys.every((k) => k === 'output' || k === 'terminate'),
    `${where} returned an object with unexpected keys ${JSON.stringify(keys)} — the 2.x envelope ` +
      'is { output?, terminate? }. A plain result object must be wrapped as { output: ... }.',
  );
}

function ctxFor(data: Record<string, unknown>) {
  return {
    data,
    toolCallId: 'test-call',
    log: { info() {}, warn() {}, error() {}, debug() {} },
  };
}

describe('tool result shapes (Flue 2.x envelope contract)', () => {
  it('the argument table covers every registered tool', () => {
    const registered = TOOLS.map((t) => t.name).sort();
    const covered = Object.keys(ARGS).sort();
    assert.deepEqual(
      registered,
      covered,
      'zoeTools and the ARGS table have diverged — a tool with no case here would be ' +
        'silently unexercised, which is exactly how a runtime-throwing return reaches production',
    );
    assert.equal(registered.length, 21, 'expected 21 tools');
  });

  it('every tool declares a name and a description', () => {
    for (const tool of TOOLS) {
      assert.ok(tool.name.length > 0, 'tool with an empty name');
      assert.ok(
        tool.description.length > 20,
        `${tool.name} has no usable description — the 4B brain routes on it`,
      );
    }
  });

  for (const tool of TOOLS) {
    it(`${tool.name} returns a legal shape for every argument set`, async () => {
      const cases = [ARGS[tool.name], ...(EXTRA_ARGS[tool.name] ?? [])];
      for (const args of cases) {
        const out = await tool.run(ctxFor(args));
        assertLegalReturn(tool.name, out, JSON.stringify(args));
      }
    });
  }

  it('NEGATIVE CONTROL: the shape checker rejects the values 2.x throws on', () => {
    // Without this, `assertLegalReturn` could be vacuously true and every case
    // above would pass no matter what the tools returned.
    for (const bad of [{ ok: true }, 42, true, null, ['a'], { output: 'x', extra: 1 }]) {
      assert.throws(
        () => assertLegalReturn('probe', bad, 'negative-control'),
        /throws at runtime|unexpected keys/,
        `the checker accepted ${JSON.stringify(bad)}, which Flue 2.x would reject`,
      );
    }
    // ...and accepts the shapes that ARE legal.
    for (const good of ['text', undefined, { output: 'x' }, { output: 'x', terminate: true }]) {
      assertLegalReturn('probe', good, 'negative-control');
    }
  });
});
