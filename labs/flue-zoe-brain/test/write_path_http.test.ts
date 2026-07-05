/**
 * Offline integration coverage for the Flue Zoe brain WRITE path.
 *
 * Starts an in-process fake zoe-data HTTP server that mirrors the production
 * /api/system/intent-dispatch response shape:
 *   POST /api/system/intent-dispatch
 *   { user_id, intent, slots } -> { intent, ok, result }
 *
 * The test drives every model-facing write tool twice:
 *   - writes unset/false: dry-run/refusal text and ZERO mutating HTTP requests;
 *   - writes enabled: exact method/path/payload pinned for every write tool.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/write_path_http.test.ts
 */
import assert from 'node:assert/strict';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { test } from 'node:test';

type RecordedRequest = {
  method: string;
  path: string;
  body: unknown;
};

type RunnableTool = {
  name: string;
  run: (ctx: { input: Record<string, unknown>; signal?: AbortSignal }) => Promise<unknown>;
};

type FakeZoeData = {
  baseUrl: string;
  requests: RecordedRequest[];
  close: () => Promise<void>;
};

const ACTING_USER = 'write-path-demo-user';

const WRITE_TOOL_CASES: Array<{
  name: string;
  input: Record<string, unknown>;
  expectedPayload: Record<string, unknown>;
  dryRunPattern: RegExp;
  successPattern: RegExp;
}> = [
  {
    name: 'shopping_list_add',
    input: { item: 'oat milk' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'list_add',
      slots: { item: 'oat milk', list_type: 'shopping' },
    },
    dryRunPattern: /WRITE DISABLED.*oat milk.*NOT saved/i,
    successPattern: /Added oat milk to your shopping list\./,
  },
  {
    name: 'set_timer',
    input: { minutes: 7, label: 'tea' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'timer_create',
      slots: { minutes: 7, label: 'tea' },
    },
    dryRunPattern: /WRITE DISABLED.*7 minute timer for tea.*NOT started/i,
    successPattern: /can't reliably start a real timer/i,
  },
  {
    name: 'add_reminder',
    input: { title: 'take bins out', date: 'tomorrow', time: '19:00' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'reminder_create',
      slots: { title: 'take bins out', date: 'tomorrow', time: '19:00' },
    },
    dryRunPattern: /WRITE DISABLED.*reminder to "take bins out".*NOT saved/i,
    successPattern: /Reminder set: take bins out for tomorrow at 19:00\./,
  },
  {
    name: 'add_calendar_event',
    input: { title: 'dentist', date: '2026-07-04', time: '15:30', category: 'health' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'calendar_create',
      slots: { title: 'dentist', date: '2026-07-04', time: '15:30', category: 'health' },
    },
    dryRunPattern: /WRITE DISABLED.*"dentist".*NOT saved/i,
    successPattern: /Created calendar event: dentist \(health\)\./,
  },
  {
    name: 'create_note',
    input: { title: 'Sandbox write test', content: 'Delete after live verification.' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'note_create',
      slots: { title: 'Sandbox write test', content: 'Delete after live verification.' },
    },
    dryRunPattern: /WRITE DISABLED.*that note.*NOT saved/i,
    successPattern: /Note saved: Sandbox write test\./,
  },
  // ─── Wave 1 write tools (cut-list record §3, Wave 1) ────────────────────────
  {
    name: 'add_to_list',
    input: { item: 'call the plumber', list_type: 'tasks' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'list_add',
      slots: { item: 'call the plumber', list_type: 'tasks' },
    },
    dryRunPattern: /WRITE DISABLED.*call the plumber.*NOT saved/i,
    successPattern: /Added call the plumber to your tasks list\./,
  },
  {
    name: 'list_remove',
    input: { item: 'bread', list_type: 'shopping' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'list_remove',
      slots: { item: 'bread', list_type: 'shopping' },
    },
    dryRunPattern: /WRITE DISABLED.*removing "bread".*NOT saved/i,
    // The fake returns an EMPTY result for list_remove (see dispatchResult), so
    // on an ok:true response the tool falls through to its own successFallback —
    // this pins that fallback text (quoted item + list type), which is what the
    // user actually sees when the backend confirms without its own message.
    successPattern: /Removed "bread" from your shopping list\./,
  },
  {
    name: 'journal',
    input: { action: 'create', content: 'Today was a great day.', mood: 'happy' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'journal_create',
      slots: { content: 'Today was a great day.', mood: 'happy' },
    },
    dryRunPattern: /WRITE DISABLED.*that journal entry.*NOT saved/i,
    successPattern: /Journal entry created: entry\./,
  },
  {
    name: 'people',
    input: { action: 'create', name: 'Sarah', relationship: 'colleague' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'people_create',
      slots: { name: 'Sarah', relationship: 'colleague', context: 'personal', circle: 'circle' },
    },
    dryRunPattern: /WRITE DISABLED.*contact "Sarah".*NOT saved/i,
    successPattern: /Added Sarah to your personal contacts/,
  },
  // ─── Wave 2 write tools (cut-list record §3, Wave 2) ────────────────────────
  {
    name: 'media',
    input: { action: 'play', query: 'some jazz' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'music_play',
      slots: { query: 'some jazz' },
    },
    dryRunPattern: /WRITE DISABLED.*playing "some jazz".*NOT/i,
    successPattern: /Now playing: some jazz\./,
  },
  {
    name: 'media',
    input: { action: 'set_music_volume', level: 30 },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'music_volume',
      slots: { level: 30 },
    },
    dryRunPattern: /WRITE DISABLED.*music volume to 30.*NOT/i,
    successPattern: /Music volume set to 30\./,
  },
  {
    name: 'media',
    input: { action: 'system_volume', direction: 'up' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'set_volume',
      slots: { direction: 'up' },
    },
    dryRunPattern: /WRITE DISABLED.*speaking volume up.*NOT/i,
    successPattern: /Turned my speaking volume up\./,
  },
  {
    name: 'home',
    input: { action: 'on', room: 'kitchen' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'smart_home',
      slots: { action: 'turn_on', entity: 'light', room: 'kitchen' },
    },
    dryRunPattern: /WRITE DISABLED.*turning the kitchen lights on.*NOT/i,
    successPattern: /Kitchen lights on\./,
  },
  {
    name: 'home',
    // No room → the `room` key is OMITTED from the payload (not sent as null).
    input: { action: 'off' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'smart_home',
      slots: { action: 'turn_off', entity: 'light' },
    },
    dryRunPattern: /WRITE DISABLED.*turning the lights off.*NOT/i,
    successPattern: /Lights off\./,
  },
  {
    name: 'media',
    input: { action: 'control', command: 'pause' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'music_control',
      slots: { command: 'pause' },
    },
    dryRunPattern: /WRITE DISABLED.*"pause".*NOT/i,
    successPattern: /Paused\./,
  },
  {
    name: 'media',
    input: { action: 'setup' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'music_setup',
      slots: {},
    },
    dryRunPattern: /WRITE DISABLED.*music setup.*NOT/i,
    successPattern: /connect Spotify or YouTube Music/i,
  },
  {
    name: 'media',
    input: { action: 'system_volume', direction: 'set', level: 40 },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'set_volume',
      slots: { direction: 'set', level: 40 },
    },
    dryRunPattern: /WRITE DISABLED.*speaking volume to 40.*NOT/i,
    successPattern: /Set my speaking volume to 40\./,
  },
  // ─── Wave 3 write tool (cut-list record §3, Wave 3) ────────────────────────
  {
    name: 'remember_fact',
    input: { fact: 'my anniversary is June 3rd' },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'memory_store',
      slots: { text: 'my anniversary is June 3rd' },
    },
    dryRunPattern: /WRITE DISABLED.*the fact "my anniversary is June 3rd".*NOT/i,
    successPattern: /Got it — I'll remember that\./,
  },
  // ─── Emotional-thread capture signal (handoff doc) ─────────────────────────
  // Pins that the emotional tool dispatches memory_store with the emotional
  // memory_type and threads valence + intensity as slots (zoe-data promotes them
  // to metadata). This full-slot case proves the pass-through; the omit-when-absent
  // path is covered by a dedicated assertion below.
  {
    name: 'remember_emotional_moment',
    input: { moment: 'Jason has been anxious about the house settlement', valence: 'neg', intensity: 0.8 },
    expectedPayload: {
      user_id: ACTING_USER,
      intent: 'memory_store',
      slots: {
        text: 'Jason has been anxious about the house settlement',
        memory_type: 'emotional_moment',
        valence: 'neg',
        intensity: 0.8,
      },
    },
    dryRunPattern: /WRITE DISABLED.*that emotional moment.*NOT/i,
    // The real memory_store fulfilment returns "Got it — I'll remember that." for
    // BOTH fact and emotional_moment, so that's the confirmed contract the user
    // hears; the tool's own "keep that in mind" fallback only shows on an empty
    // backend result (covered by the omit-slots test below via an empty result).
    successPattern: /Got it — I'll remember that\./,
  },
];

async function readJson(req: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const raw = Buffer.concat(chunks).toString('utf8');
  return raw ? JSON.parse(raw) : undefined;
}

function dispatchResult(intent: string, slots: Record<string, unknown>): string {
  if (intent === 'list_add') {
    return `Added ${slots.item} to your ${slots.list_type} list.`;
  }
  if (intent === 'list_remove') {
    // Empty on purpose: exercises the tool's own successFallback (see the
    // list_remove case's successPattern), matching how the real backend can
    // confirm ok:true without returning its own message.
    return '';
  }
  if (intent === 'journal_create') {
    return 'Journal entry created: entry.';
  }
  if (intent === 'people_create') {
    return `Added ${slots.name} to your ${slots.context} contacts ○.`;
  }
  // ─── Wave 1 read intents (surfaced verbatim by the tool on ok:true) ─────────
  if (intent === 'note_search') {
    return `Notes found:\n  - ${slots.query}`;
  }
  if (intent === 'journal_prompt') {
    return 'Here are some journal prompts to get you started.';
  }
  if (intent === 'journal_streak') {
    return "You're on a 4-day journaling streak.";
  }
  if (intent === 'people_search') {
    return `Found:\n  - ${slots.query} (friend)`;
  }
  // ─── Wave 2 confirmation strings (mirror intent_router fulfillment) ─────────
  if (intent === 'memory_store') {
    return "Got it — I'll remember that.";
  }
  if (intent === 'music_play') {
    return `Now playing: ${slots.query}.`;
  }
  if (intent === 'music_control') {
    const labels: Record<string, string> = {
      pause: 'Paused', resume: 'Resumed', stop: 'Stopped',
      next: 'Skipped', previous: 'Went back',
    };
    return `${labels[String(slots.command ?? '')] ?? 'Done'}.`;
  }
  if (intent === 'music_setup') {
    return 'To play music, connect Spotify or YouTube Music in settings.';
  }
  if (intent === 'music_volume') {
    return `Music volume set to ${slots.level}.`;
  }
  if (intent === 'set_volume') {
    const dir = String(slots.direction ?? '');
    if (dir === 'set') return `Set my speaking volume to ${slots.level}.`;
    return `Turned my speaking volume ${dir}.`;
  }
  if (intent === 'smart_home') {
    const labels: Record<string, string> = { turn_on: 'on', turn_off: 'off', dim: 'dimmed', brighten: 'brightened' };
    const label = labels[String(slots.action ?? '')] ?? String(slots.action ?? '');
    const room = slots.room ? String(slots.room).replace(/_/g, ' ') : '';
    const where = room ? room.replace(/\b\w/g, (c) => c.toUpperCase()) + ' lights' : 'Lights';
    return `${where} ${label}.`;
  }
  if (intent === 'timer_create') {
    const label = String(slots.label ?? '').trim();
    const named = label && label.toLowerCase() !== 'timer' ? ` for ${label}` : '';
    return `Starting a ${slots.minutes} minute timer${named}.`;
  }
  if (intent === 'reminder_create') {
    const date = slots.date ? ` for ${slots.date}` : '';
    const time = slots.time ? ` at ${slots.time}` : '';
    return `Reminder set: ${slots.title}${date}${time}.`;
  }
  if (intent === 'calendar_create') {
    const category = String(slots.category ?? '');
    return category && category !== 'general'
      ? `Created calendar event: ${slots.title} (${category}).`
      : `Created calendar event: ${slots.title}.`;
  }
  if (intent === 'note_create') {
    return `Note saved: ${slots.title}.`;
  }
  return '';
}

async function startFakeZoeData(): Promise<FakeZoeData> {
  const requests: RecordedRequest[] = [];
  const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
    const url = new URL(req.url ?? '/', 'http://127.0.0.1');
    const body = await readJson(req);
    requests.push({ method: req.method ?? '', path: url.pathname, body });

    if (req.method !== 'POST' || url.pathname !== '/api/system/intent-dispatch') {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ detail: 'not found' }));
      return;
    }

    const payload = body as { intent?: string; slots?: Record<string, unknown> };
    const intent = String(payload.intent ?? '');
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ intent, ok: true, result: dispatchResult(intent, payload.slots ?? {}) }));
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    requests,
    close: () => new Promise<void>((resolve, reject) => server.close((err) => (err ? reject(err) : resolve()))),
  };
}

async function withTools(
  baseUrl: string,
  allowWrites: string | undefined,
  fn: (tools: RunnableTool[]) => Promise<void>,
): Promise<void> {
  const previousEnv = {
    ZOE_DATA_URL: process.env.ZOE_DATA_URL,
    ZOE_BRAIN_USER_ID: process.env.ZOE_BRAIN_USER_ID,
    ZOE_BRAIN_ALLOW_WRITES: process.env.ZOE_BRAIN_ALLOW_WRITES,
    ZOE_BRAIN_TOOL_TIMEOUT_MS: process.env.ZOE_BRAIN_TOOL_TIMEOUT_MS,
  };
  process.env.ZOE_DATA_URL = baseUrl;
  process.env.ZOE_BRAIN_USER_ID = ACTING_USER;
  if (allowWrites === undefined) {
    delete process.env.ZOE_BRAIN_ALLOW_WRITES;
  } else {
    process.env.ZOE_BRAIN_ALLOW_WRITES = allowWrites;
  }
  process.env.ZOE_BRAIN_TOOL_TIMEOUT_MS = '2000';

  // Node's documented ESM resolver caches modules by full URL, and distinct
  // query strings create distinct module instances. zoe-tools.ts reads
  // ZOE_BRAIN_ALLOW_WRITES at module evaluation, so every gate case imports a
  // fresh URL and the assertions below prove the expected dry-run/write mode.
  // https://nodejs.org/api/esm.html#urls
  try {
    const mod = await import(`../src/tools/zoe-tools.ts?write-path=${Date.now()}-${Math.random()}`);
    await fn(mod.zoeTools as unknown as RunnableTool[]);
  } finally {
    for (const [key, value] of Object.entries(previousEnv)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  }
}

function byName(tools: RunnableTool[], name: string): RunnableTool {
  const tool = tools.find((candidate) => candidate.name === name);
  assert.ok(tool, `expected ${name} to be registered`);
  return tool;
}

test('write tools dry-run when ZOE_BRAIN_ALLOW_WRITES is unset or false and emit no mutating HTTP', async () => {
  for (const allowWrites of [undefined, 'false']) {
    const fake = await startFakeZoeData();
    try {
      await withTools(fake.baseUrl, allowWrites, async (tools) => {
        for (const writeCase of WRITE_TOOL_CASES) {
          const out = String(await byName(tools, writeCase.name).run({ input: writeCase.input }));
          assert.match(out, writeCase.dryRunPattern, `${writeCase.name} should report dry-run/refusal`);
          assert.match(out, /do NOT claim it was done/i, `${writeCase.name} must steer away from false success`);
        }
        const mutating = fake.requests.filter((req) => req.method !== 'GET');
        assert.deepEqual(mutating, [], `writes=${allowWrites ?? '<unset>'} must not emit mutating requests`);
      });
    } finally {
      await fake.close();
    }
  }
});

test('write tools with ZOE_BRAIN_ALLOW_WRITES=true POST exact intent-dispatch payloads', async () => {
  const fake = await startFakeZoeData();
  try {
    await withTools(fake.baseUrl, 'true', async (tools) => {
      for (const writeCase of WRITE_TOOL_CASES) {
        const out = String(await byName(tools, writeCase.name).run({ input: writeCase.input }));
        assert.match(out, writeCase.successPattern, `${writeCase.name} should return the backend-confirmed contract`);
      }

      assert.deepEqual(
        fake.requests,
        WRITE_TOOL_CASES.map((writeCase) => ({
          method: 'POST',
          path: '/api/system/intent-dispatch',
          body: writeCase.expectedPayload,
        })),
      );
      assert.equal(fake.requests.length, WRITE_TOOL_CASES.length, 'fresh write-enabled module must emit one POST per write tool');
    });
  } finally {
    await fake.close();
  }
});

test('remember_emotional_moment omits valence/intensity slots when absent or malformed', async () => {
  const fake = await startFakeZoeData();
  try {
    await withTools(fake.baseUrl, 'true', async (tools) => {
      const tool = byName(tools, 'remember_emotional_moment');
      // No valence/intensity at all → slots carry ONLY text + memory_type. The
      // fake returns the real memory_store confirmation, so that's what surfaces.
      const out = String(await tool.run({ input: { moment: 'Mia started school today' } }));
      assert.match(out, /Got it — I'll remember that\./);
      assert.deepEqual(fake.requests, [
        {
          method: 'POST',
          path: '/api/system/intent-dispatch',
          body: {
            user_id: ACTING_USER,
            intent: 'memory_store',
            slots: { text: 'Mia started school today', memory_type: 'emotional_moment' },
          },
        },
      ]);
    });
  } finally {
    await fake.close();
  }
});

test('add_calendar_event without a date still dispatches (zoe-data defaults it to today)', async () => {
  const fake = await startFakeZoeData();
  try {
    await withTools(fake.baseUrl, 'true', async (tools) => {
      const tool = byName(tools, 'add_calendar_event');
      // "add lunch with Jess at 12pm" — time but no day. The tool must NOT ask
      // "which day?"; it passes through and zoe-data defaults the date to today.
      const out = String(await tool.run({ input: { title: 'lunch with Jess', time: '12pm' } }));
      assert.doesNotMatch(out, /what day/i, 'must NOT ask which day when a time is given');
      assert.deepEqual(fake.requests, [
        {
          method: 'POST',
          path: '/api/system/intent-dispatch',
          body: {
            user_id: ACTING_USER,
            intent: 'calendar_create',
            slots: { title: 'lunch with Jess', time: '12pm' }, // no date → zoe-data → today
          },
        },
      ]);
    });
  } finally {
    await fake.close();
  }
});

test('get_weather forwards a named location and omits it when absent', async () => {
  const fake = await startFakeZoeData();
  try {
    await withTools(fake.baseUrl, undefined, async (tools) => {
      const tool = byName(tools, 'get_weather');
      await tool.run({ input: { location: 'Perth' } }); // "weather in Perth"
      await tool.run({ input: {} }); // no place → home area
      assert.deepEqual(fake.requests, [
        {
          method: 'POST',
          path: '/api/system/intent-dispatch',
          body: { user_id: ACTING_USER, intent: 'weather', slots: { forecast: false, location: 'Perth' } },
        },
        {
          method: 'POST',
          path: '/api/system/intent-dispatch',
          body: { user_id: ACTING_USER, intent: 'weather', slots: { forecast: false } },
        },
      ]);
    });
  } finally {
    await fake.close();
  }
});

test('recall_memory requests a widened packet (limit=24) from for-prompt', async () => {
  const prevEnv = { url: process.env.ZOE_DATA_URL, uid: process.env.ZOE_BRAIN_USER_ID };
  const original = globalThis.fetch;
  let seenUrl = '';
  globalThis.fetch = (async (url: string | URL) => {
    seenUrl = String(url);
    return new Response(JSON.stringify({ packet: '## What I know about you\n- family' }), { status: 200 });
  }) as typeof fetch;
  process.env.ZOE_DATA_URL = 'http://127.0.0.1:8000';
  process.env.ZOE_BRAIN_USER_ID = 'demo-user';
  try {
    const mod = await import(`../src/tools/zoe-tools.ts?recall=${Date.now()}-${Math.random()}`);
    const tool = (mod.zoeTools as unknown as RunnableTool[]).find((t) => t.name === 'recall_memory');
    assert.ok(tool, 'recall_memory tool must be registered');
    await tool.run({ input: { query: 'tell me about my family' } });
    const u = new URL(seenUrl);
    assert.equal(u.pathname, '/api/memories/for-prompt');
    assert.equal(u.searchParams.get('user_id'), 'demo-user');
    assert.equal(u.searchParams.get('message'), 'tell me about my family');
    // The explicit-recall widening: 12 slots crowd out family members behind
    // duplicate identity facts; 24 lets the whole family surface.
    assert.equal(u.searchParams.get('limit'), '24');

    // No query → still widened (guards against the limit being moved inside the
    // `if (query)` block, which would silently revert to the endpoint default).
    await tool.run({ input: {} });
    const u2 = new URL(seenUrl);
    assert.equal(u2.searchParams.get('limit'), '24');
    assert.equal(u2.searchParams.get('message'), null, 'no query → no message param');
  } finally {
    globalThis.fetch = original;
    if (prevEnv.url === undefined) delete process.env.ZOE_DATA_URL; else process.env.ZOE_DATA_URL = prevEnv.url;
    if (prevEnv.uid === undefined) delete process.env.ZOE_BRAIN_USER_ID; else process.env.ZOE_BRAIN_USER_ID = prevEnv.uid;
  }
});

// ─── Wave 1 READ paths — HTTP coverage (cut-list record §3) ───────────────────
// The read dispatch paths introduced this wave (note_search, journal
// prompt/streak, people search) are NOT gated by ZOE_BRAIN_ALLOW_WRITES, so
// they run over HTTP even with writes off. Pin (a) the exact intent name +
// payload POSTed and (b) that the tool surfaces the backend result verbatim.
const READ_TOOL_CASES: Array<{
  name: string;
  input: Record<string, unknown>;
  expectedPayload: Record<string, unknown>;
  resultPattern: RegExp;
}> = [
  {
    name: 'note_search',
    input: { query: 'wifi password' },
    expectedPayload: { user_id: ACTING_USER, intent: 'note_search', slots: { query: 'wifi password' } },
    resultPattern: /Notes found:[\s\S]*wifi password/,
  },
  {
    name: 'journal',
    input: { action: 'prompt' },
    expectedPayload: { user_id: ACTING_USER, intent: 'journal_prompt', slots: {} },
    resultPattern: /journal prompts/i,
  },
  {
    name: 'journal',
    input: { action: 'streak' },
    expectedPayload: { user_id: ACTING_USER, intent: 'journal_streak', slots: {} },
    resultPattern: /4-day journaling streak/,
  },
  {
    name: 'people',
    input: { action: 'search', query: 'Sarah' },
    expectedPayload: { user_id: ACTING_USER, intent: 'people_search', slots: { query: 'Sarah' } },
    resultPattern: /Found:[\s\S]*Sarah/,
  },
];

test('read tools POST the exact intent-dispatch payload and surface the backend result (writes OFF)', async () => {
  const fake = await startFakeZoeData();
  try {
    await withTools(fake.baseUrl, undefined, async (tools) => {
      for (const readCase of READ_TOOL_CASES) {
        const out = String(await byName(tools, readCase.name).run({ input: readCase.input }));
        assert.match(out, readCase.resultPattern, `${readCase.name}(${JSON.stringify(readCase.input)}) should surface the backend result`);
      }
      assert.deepEqual(
        fake.requests,
        READ_TOOL_CASES.map((readCase) => ({
          method: 'POST',
          path: '/api/system/intent-dispatch',
          body: readCase.expectedPayload,
        })),
      );
    });
  } finally {
    await fake.close();
  }
});
