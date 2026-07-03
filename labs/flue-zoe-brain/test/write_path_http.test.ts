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
    return `Added ${slots.item} to your shopping list.`;
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
  const mod = await import(`../src/tools/zoe-tools.ts?write-path=${Date.now()}-${Math.random()}`);
  try {
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
