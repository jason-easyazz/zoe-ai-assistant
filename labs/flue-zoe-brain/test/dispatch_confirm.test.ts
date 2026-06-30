/**
 * Unit coverage for dispatchIntent's confirmation contract (LAB-ONLY, offline).
 *
 * Proves a write tool only claims success when zoe-data EXPLICITLY confirms
 * (`ok: true`), and never fabricates a "done" reply on a 200 that doesn't confirm
 * (ok missing / empty body / ok:false). No network — `globalThis.fetch` is stubbed.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/dispatch_confirm.test.ts
 *
 * Env MUST be set before the module is evaluated (ALLOW_WRITES is read at module
 * load). ESM `import` is hoisted above top-level statements, so the module is
 * loaded via a dynamic `await import()` AFTER the env is set, not a static import.
 */
process.env.ZOE_BRAIN_USER_ID = 'jason';
process.env.ZOE_BRAIN_ALLOW_WRITES = 'true';

import assert from 'node:assert/strict';
import { test } from 'node:test';

const { zoeTools } = await import('../src/tools/zoe-tools.ts');

// `zoeTools` is a union of differently-typed ToolDefinitions; `.find()` collapses
// the input types into an intersection. Narrow to a minimal runnable shape so the
// per-tool `run({ input })` calls below typecheck cleanly.
type RunnableTool = {
  name: string;
  run: (ctx: { input: Record<string, unknown>; signal?: AbortSignal }) => Promise<unknown>;
};
const byName = (n: string) =>
  zoeTools.find((t) => t.name === n)! as unknown as RunnableTool;
const shoppingListAdd = byName('shopping_list_add');
const getWeather = byName('get_weather');
const setTimer = byName('set_timer');

const FABRICATED = 'Added "milk" to your shopping list.';

// The canned line zoe-data's timer_create ALWAYS returns — proof of nothing.
const CANNED_TIMER = 'Starting a 10 minute timer.';

/** Stub global fetch with a single 200 response carrying `body`, then restore. */
async function withFetch200(body: unknown, fn: () => Promise<void>): Promise<void> {
  const real = globalThis.fetch;
  globalThis.fetch = (async () => ({
    ok: true,
    status: 200,
    json: async () => body,
  })) as unknown as typeof fetch;
  try {
    await fn();
  } finally {
    globalThis.fetch = real;
  }
}

/** Stub global fetch with a 200 whose body is not JSON (json() throws). */
async function withFetch200BadBody(fn: () => Promise<void>): Promise<void> {
  const real = globalThis.fetch;
  globalThis.fetch = (async () => ({
    ok: true,
    status: 200,
    json: async () => {
      throw new SyntaxError('Unexpected end of JSON input');
    },
  })) as unknown as typeof fetch;
  try {
    await fn();
  } finally {
    globalThis.fetch = real;
  }
}

test('ok:true with a result → confirmed, returns the backend result text', async () => {
  await withFetch200({ intent: 'list_add', ok: true, result: 'Added milk to shopping.' }, async () => {
    const out = (await shoppingListAdd.run({ input: { item: 'milk' } })) as string;
    assert.equal(out, 'Added milk to shopping.');
  });
});

test('ok:true with empty result → confirmed, uses the success fallback', async () => {
  await withFetch200({ intent: 'list_add', ok: true, result: '' }, async () => {
    const out = (await shoppingListAdd.run({ input: { item: 'milk' } })) as string;
    assert.equal(out, FABRICATED); // legitimate: ok===true is an explicit confirmation
  });
});

test('200 without ok → NOT confirmed, no fabricated success', async () => {
  await withFetch200({ intent: 'list_add', result: 'whatever' }, async () => {
    const out = (await shoppingListAdd.run({ input: { item: 'milk' } })) as string;
    assert.notEqual(out, FABRICATED);
    assert.doesNotMatch(out, /^Added /);
    assert.match(out, /couldn't confirm/i);
  });
});

test('ok:false → NOT confirmed, no fabricated success', async () => {
  await withFetch200({ intent: 'list_add', ok: false, result: '' }, async () => {
    const out = (await shoppingListAdd.run({ input: { item: 'milk' } })) as string;
    assert.notEqual(out, FABRICATED);
    assert.match(out, /couldn't confirm/i);
  });
});

test('ok is a truthy non-true value (e.g. "true" string) → NOT confirmed', async () => {
  await withFetch200({ intent: 'list_add', ok: 'true', result: '' }, async () => {
    const out = (await shoppingListAdd.run({ input: { item: 'milk' } })) as string;
    assert.notEqual(out, FABRICATED);
    assert.match(out, /couldn't confirm/i);
  });
});

test('200 with an unparseable body → NOT confirmed (fail closed)', async () => {
  await withFetch200BadBody(async () => {
    const out = (await shoppingListAdd.run({ input: { item: 'milk' } })) as string;
    assert.notEqual(out, FABRICATED);
    assert.doesNotMatch(out, /^Added /);
  });
});

// ─── set_timer: must NEVER claim a timer started off this path ────────────────
// The /api/system/intent-dispatch timer_create path returns a canned "Starting a
// N minute timer" line whether or not a real countdown was scheduled, so set_timer
// fails closed: it never reports success unless the backend EXPLICITLY confirms a
// real timer (which this path cannot), and treats the canned line as NON-confirmation.

const TIMER_UNCERTAIN = /can't reliably start a real timer/i;

test('set_timer: ok:true carrying ONLY the canned line → NOT confirmed, no fabricated success', async () => {
  await withFetch200({ intent: 'timer_create', ok: true, result: CANNED_TIMER }, async () => {
    const out = (await setTimer.run({ input: { minutes: 10 } })) as string;
    assert.notEqual(out, CANNED_TIMER);
    assert.doesNotMatch(out, /^Starting a /i); // never echoes the canned "timer started" line
    assert.match(out, TIMER_UNCERTAIN);
  });
});

test('set_timer: 200 without ok → NOT confirmed', async () => {
  await withFetch200({ intent: 'timer_create', result: CANNED_TIMER }, async () => {
    const out = (await setTimer.run({ input: { minutes: 5, label: 'eggs' } })) as string;
    assert.doesNotMatch(out, /^Starting a /i);
    assert.match(out, TIMER_UNCERTAIN);
  });
});

test('set_timer: ok:false → NOT confirmed', async () => {
  await withFetch200({ intent: 'timer_create', ok: false, result: '' }, async () => {
    const out = (await setTimer.run({ input: { minutes: 3 } })) as string;
    assert.doesNotMatch(out, /^Starting a /i);
    assert.match(out, TIMER_UNCERTAIN);
  });
});

test('set_timer: 200 with an unparseable body → NOT confirmed (fail closed)', async () => {
  await withFetch200BadBody(async () => {
    const out = (await setTimer.run({ input: { minutes: 10 } })) as string;
    assert.doesNotMatch(out, /^Starting a /i);
    assert.match(out, TIMER_UNCERTAIN);
  });
});

test('read tool: ok:true returns result; ok missing returns a non-confirming line', async () => {
  await withFetch200({ intent: 'weather', ok: true, result: 'Sunny, 21°C.' }, async () => {
    const out = (await getWeather.run({ input: { forecast: false } })) as string;
    assert.equal(out, 'Sunny, 21°C.');
  });
  await withFetch200({ intent: 'weather', result: 'Sunny, 21°C.' }, async () => {
    const out = (await getWeather.run({ input: { forecast: false } })) as string;
    assert.notEqual(out, 'Sunny, 21°C.');
    assert.match(out, /couldn't confirm/i);
  });
});
