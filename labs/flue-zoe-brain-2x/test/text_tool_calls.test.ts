/**
 * A tool call the model PRINTED as text (src/text-tool-calls.ts; QA round 3b, 2026-11-07). LAB-ONLY, offline.
 *
 * The live miss: in a fresh session, "chuck swim goggles on my to-do list" was answered with the reply TEXT
 * `add_to_list(item="swim goggles", list_type="tasks/to-do")` - no tool call, nothing written, code on screen.
 *
 * Pins:
 *   - the parser: one call, keyword (or one JSON object) arguments, literal values; positional args / two calls / prose = no call;
 *   - the schema check: only a DISCLOSED tool, only its properties, the right scalar types, the required ones present;
 *   - end to end through the real agent loop + mock model + mock zoe-data: the printed call is RUN (the dispatch reaches
 *     zoe-data with the name the model used, for zoe-data to resolve), the model answers from the result, and the call text
 *     is never stored as the assistant's words. NEGATIVE CONTROLS: the same script with the kill switch, and with a tool
 *     that was not disclosed, writes nothing;
 *   - the list tools never turn an unrecognised list name into the household shopping list.
 *
 * Run: npm test (Node 22, type-stripping), or this file alone with --test.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

const ttc = await import('../src/text-tool-calls.ts');
const { normalizeListType } = await import('../src/tools/zoe-tools.ts');
const { wrapMessageWithIdentity } = await import('../src/request-identity.ts');
const { startBrainHarness, waitFor } = await import('./helpers/harness.ts');
const { startMockZoeData } = await import('./helpers/mock-zoe-data.ts');

const ADD_TOOL = {
  name: 'add_to_list',
  description: 'add',
  parameters: {
    type: 'object',
    properties: { item: { type: 'string' }, list_type: { type: 'string' } },
    required: ['item'],
  },
} as never;

describe('parseTextToolCall', () => {
  it('parses keyword calls in every shape the 4B prints', () => {
    for (const text of [
      'add_to_list(item="swim goggles", list_type="tasks/to-do")',
      "add_to_list(item='swim goggles', list_type='tasks/to-do')",
      '`add_to_list(item="swim goggles", list_type="tasks/to-do")`',
      '```python\nadd_to_list(item="swim goggles", list_type="tasks/to-do")\n```',
      'default_api.add_to_list(item: "swim goggles", list_type: "tasks/to-do");',
      'add_to_list({"item": "swim goggles", "list_type": "tasks/to-do"})',
    ]) {
      assert.deepEqual(ttc.parseTextToolCall(text), {
        name: 'add_to_list',
        args: { item: 'swim goggles', list_type: 'tasks/to-do' },
      }, text);
    }
    assert.deepEqual(ttc.parseTextToolCall('set_timer(minutes=7, label="tea")'), {
      name: 'set_timer', args: { minutes: 7, label: 'tea' },
    });
  });

  it('string literals are decoded faithfully, or the call is refused', () => {
    assert.deepEqual(ttc.parseTextToolCall('add_to_list(item="caf\\u00e9")'), { name: 'add_to_list', args: { item: 'café' } });
    assert.deepEqual(ttc.parseTextToolCall('add_to_list(item="say \\"hi\\"")'), { name: 'add_to_list', args: { item: 'say "hi"' } });
    assert.equal(ttc.parseTextToolCall('add_to_list(item="a\\qb")'), null);      // an unknown escape: never guessed
    assert.equal(ttc.parseTextToolCall('add_to_list(item="caf\\u00")'), null);   // a truncated \\u
  });

  it('NEGATIVE CONTROL: anything ambiguous is not a call', () => {
    for (const text of [
      'add_to_list("swim goggles")',                         // positional
      'add_to_list(item="a")\nadd_to_list(item="b")',        // two calls
      'Sure! add_to_list(item="a")',                         // prose first
      'add_to_list(item="a") and done',                      // prose after
      'add_to_list(item=goggles)',                           // a bare word, not a literal
      'add_to_list(item="a", item="b")',                     // a repeated key
      "I've added swim goggles to your to-do list.",
    ]) {
      assert.equal(ttc.parseTextToolCall(text), null, text);
    }
  });
});

describe('toolCallFor', () => {
  const parsed = (args: Record<string, unknown>) => ({ name: 'add_to_list', args }) as never;
  it('a disclosed tool with fitting arguments becomes a real call', () => {
    const call = ttc.toolCallFor(parsed({ item: 'goggles', list_type: 'personal' }), [ADD_TOOL], 'id1');
    assert.deepEqual(call, { type: 'toolCall', id: 'id1', name: 'add_to_list', arguments: { item: 'goggles', list_type: 'personal' } });
  });
  it('the full scalar schema is checked: enum, integer, length, range, null, non-scalar properties', () => {
    const tool = {
      name: 'add_to_list', description: 'd',
      parameters: {
        type: 'object', required: ['item'],
        properties: {
          item: { type: 'string', minLength: 1, maxLength: 10 },
          list_type: { type: 'string', enum: ['shopping', 'personal'] },
          n: { type: 'integer', minimum: 1, maximum: 5 },
          tags: { type: 'array' },
        },
      },
    } as never;
    assert.ok(ttc.toolCallFor(parsed({ item: 'milk', list_type: 'shopping', n: 2 }), [tool], 'ok'));  // positive control
    assert.equal(ttc.toolCallFor(parsed({ item: 'milk', list_type: 'tasks/to-do' }), [tool], 'x'), null);
    assert.equal(ttc.toolCallFor(parsed({ item: 'milk', n: 2.5 }), [tool], 'x'), null);
    assert.equal(ttc.toolCallFor(parsed({ item: 'milk', n: 9 }), [tool], 'x'), null);
    assert.equal(ttc.toolCallFor(parsed({ item: '' }), [tool], 'x'), null);
    assert.equal(ttc.toolCallFor(parsed({ item: 'a very long item' }), [tool], 'x'), null);
    assert.equal(ttc.toolCallFor(parsed({ item: 'milk', list_type: null }), [tool], 'x'), null);
    assert.equal(ttc.toolCallFor(parsed({ item: 'milk', tags: 'x' }), [tool], 'x'), null);
  });

  it('NEGATIVE CONTROL: undisclosed tool, unknown key, wrong type or a missing required one is refused', () => {
    assert.equal(ttc.toolCallFor(parsed({ item: 'goggles' }), [], 'x'), null);
    assert.equal(ttc.toolCallFor(parsed({ item: 'goggles', colour: 'red' }), [ADD_TOOL], 'x'), null);
    assert.equal(ttc.toolCallFor(parsed({ item: 3 }), [ADD_TOOL], 'x'), null);
    assert.equal(ttc.toolCallFor(parsed({ list_type: 'personal' }), [ADD_TOOL], 'x'), null);
  });
});

describe('classifyLead', () => {
  it('holds only while the reply could still open a call', () => {
    assert.equal(ttc.classifyLead(''), 'undecided');
    assert.equal(ttc.classifyLead('add_to'), 'undecided');
    assert.equal(ttc.classifyLead('```py'), 'undecided');
    assert.equal(ttc.classifyLead('add_to_list('), 'call');
    assert.equal(ttc.classifyLead('Sure'), 'undecided');
    assert.equal(ttc.classifyLead('Sure,'), 'prose');
    assert.equal(ttc.classifyLead("I'm"), 'prose');
    assert.equal(ttc.classifyLead('Rome (the'), 'prose');
  });
});

describe('normalizeListType', () => {
  it('a to-do name is the personal list; an unknown name is passed through, never "shopping"', () => {
    assert.equal(normalizeListType('to-do'), 'personal');
    assert.equal(normalizeListType('tasks'), 'tasks');
    assert.equal(normalizeListType('Groceries'), 'shopping');
    assert.equal(normalizeListType(''), 'shopping');                       // no list named: the household list
    assert.equal(normalizeListType('tasks/to-do'), 'tasks/to-do');         // zoe-data resolves it (-> the member's own)
    assert.equal(normalizeListType('pharmacy study list'), 'pharmacy study list');
  });
});

const PRINTED = 'add_to_list(item="swim goggles", list_type="tasks/to-do")';
const WORDS = 'chuck swim goggles on my to-do list would ya';

async function runTurn(env: Record<string, string>, words = WORDS) {
  const zoeData = await startMockZoeData();
  const harness = await startBrainHarness(
    (call) => (call === 0 ? { text: PRINTED } : { text: 'Done - swim goggles are on your to-do list.' }),
    { env: { ZOE_DATA_URL: zoeData.url, ZOE_BRAIN_ALLOW_WRITES: 'true', ...env } },
  );
  try {
    const res = await harness.send('text-call-' + Math.random().toString(36).slice(2), wrapMessageWithIdentity(words, 'demo-user'));
    assert.equal(res.status, 202);
    await waitFor(() => harness.model.callCount >= 1, 10_000);
    // a recovered call makes a second model round; give an unrecovered turn the same time to (not) make one
    await waitFor(() => harness.model.callCount >= 2, 3_000);
    await new Promise((r) => setTimeout(r, 200));
    return { requests: [...harness.model.requests], dispatches: zoeData.calls.filter((c) => c.path === '/api/system/intent-dispatch') };
  } finally {
    await harness.stop();
    await zoeData.close();
  }
}

describe('end to end: a printed call is run for real', () => {
  it('the dispatch reaches zoe-data and the call text is never stored as words', async () => {
    const { requests, dispatches } = await runTurn({});
    assert.ok(requests[0].toolNames.includes('add_to_list'), 'the lists group was disclosed for this turn');
    assert.equal(dispatches.length, 1, 'exactly one write');
    assert.equal(dispatches[0].intent, 'list_add');
    assert.equal(dispatches[0].userId, 'demo-user');
    assert.deepEqual(dispatches[0].slots, { item: 'swim goggles', list_type: 'tasks/to-do' });
    assert.ok(requests.length >= 2, 'the model answered from the tool result');
    assert.ok(requests[1].messages.some((m) => m.role === 'tool'), 'the tool result is in the next round');
    for (const m of requests[1].messages) assert.ok(!m.text.includes('add_to_list('), `call text stored as words: ${m.text}`);
  });

  it('NEGATIVE CONTROL: with the kill switch the same script writes nothing', async () => {
    const { dispatches, requests } = await runTurn({ ZOE_BRAIN_TEXT_TOOLCALL_RECOVERY: 'off' });
    assert.equal(dispatches.length, 0);
    assert.equal(requests.length, 1);
  });

  it('NEGATIVE CONTROL: a tool that was not disclosed is never run', async () => {
    // no list word: the lists group is not disclosed, so add_to_list is not offered and the printed call stays text
    const { dispatches, requests } = await runTurn({}, 'hello there');
    assert.ok(!requests[0].toolNames.includes('add_to_list'));
    assert.equal(dispatches.length, 0);
  });
});
