/**
 * B10.1 — the flag-gated `web_search` tool.
 *
 * Offline: an in-process fake zoe-data answers POST /api/system/web-search with
 * each B10.0 outcome shape in turn. Asserts, in both directions of the flag:
 *   - flag off (default): the tool is NOT registered (`optionalZoeTools()` is
 *     empty) and `zoeTools` is still the 21-tool set — the negative control;
 *   - flag on: registered under the name `web_search`, calls the exact
 *     path/method/payload, formats `results`, and surfaces no_results / blocked /
 *     error / off HONESTLY (never as "nothing found" when the lookup was walled);
 *   - zoe-data 404 (its side of the flag off) → an explicit "switched off" line;
 *   - a dead port → the calm transport line, no throw.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/web_search_tool.test.ts
 */
process.env.ZOE_BRAIN_TOOL_TIMEOUT_MS = '500';
delete process.env.ZOE_WEB_SEARCH_TOOL;

import assert from 'node:assert/strict';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { test } from 'node:test';
import { optionalZoeTools, webSearchToolEnabled, zoeTools } from '../src/tools/zoe-tools.ts';

type Captured = { method: string; path: string; body: unknown; token: string };

async function startFake(reply: { status: number; json: unknown }): Promise<{
  url: string;
  calls: Captured[];
  close(): Promise<void>;
}> {
  const calls: Captured[] = [];
  const server: Server = createServer((req, res) => {
    void (async () => {
      let raw = '';
      for await (const part of req) raw += part;
      calls.push({
        method: req.method ?? '',
        path: req.url ?? '',
        body: raw ? JSON.parse(raw) : null,
        token: String(req.headers['x-internal-token'] ?? ''),
      });
      res.writeHead(reply.status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(reply.json));
    })();
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const { port } = server.address() as AddressInfo;
  return {
    url: `http://127.0.0.1:${port}`,
    calls,
    close: () => new Promise<void>((r) => server.close(() => r())),
  };
}

function ctx(data: Record<string, unknown>) {
  return { data, toolCallId: 'test-call', log: { info() {}, warn() {}, error() {}, debug() {} } };
}

function theTool() {
  process.env.ZOE_WEB_SEARCH_TOOL = '1';
  const tools = optionalZoeTools();
  assert.equal(tools.length, 1);
  assert.equal(tools[0].name, 'web_search');
  return tools[0] as unknown as { name: string; run: (c: unknown) => Promise<unknown> };
}

test('negative control — flag off: not registered, zoeTools untouched', () => {
  delete process.env.ZOE_WEB_SEARCH_TOOL;
  assert.equal(webSearchToolEnabled(), false);
  assert.deepEqual(optionalZoeTools(), []);
  assert.equal(zoeTools.length, 21);
  assert.ok(!zoeTools.some((t) => t.name === 'web_search'));
  for (const off of ['0', '', 'false', 'no', 'off']) {
    process.env.ZOE_WEB_SEARCH_TOOL = off;
    assert.equal(webSearchToolEnabled(), false, `expected ${JSON.stringify(off)} to be off`);
  }
});

test('flag on: registered as web_search; truthy spellings accepted', () => {
  for (const on of ['1', 'true', 'YES', ' on ']) {
    process.env.ZOE_WEB_SEARCH_TOOL = on;
    assert.equal(webSearchToolEnabled(), true, `expected ${JSON.stringify(on)} to be on`);
  }
  assert.equal(theTool().name, 'web_search');
  // Still not in the always-registered set — the flag adds, never mutates.
  assert.equal(zoeTools.length, 21);
});

test('results: exact wire call + formatted rows with the source cited', async () => {
  const fake = await startFake({
    status: 200,
    json: {
      status: 'results',
      provider: 'tavily',
      message: '',
      detail: '',
      result_count: 2,
      results: [
        { title: 'Bali flights from Perth', url: 'https://example.com/bali', snippet: 'From $199 return.' },
        { title: 'Second', url: 'https://example.com/2', snippet: '' },
      ],
    },
  });
  try {
    process.env.ZOE_DATA_URL = fake.url;
    process.env.ZOE_INTERNAL_TOKEN = 'tok-123';
    const out = String(await theTool().run(ctx({ query: 'flights to Bali now' })));
    assert.equal(fake.calls.length, 1);
    assert.equal(fake.calls[0].method, 'POST');
    assert.equal(fake.calls[0].path, '/api/system/web-search');
    assert.deepEqual(fake.calls[0].body, { query: 'flights to Bali now', max_results: 5 });
    assert.equal(fake.calls[0].token, 'tok-123');
    assert.match(out, /Web results \(tavily\)/);
    assert.match(out, /1\. Bali flights from Perth — https:\/\/example\.com\/bali\n {3}From \$199 return\./);
    assert.match(out, /2\. Second — https:\/\/example\.com\/2/);
  } finally {
    await fake.close();
    delete process.env.ZOE_INTERNAL_TOKEN;
  }
});

for (const [status, detail, expect] of [
  ['no_results', '', /found nothing/],
  ['blocked', 'challenge page (HTTP 202)', /BLOCKED by the search provider \(challenge page \(HTTP 202\)\)/],
  ['error', 'unrecognised page (HTTP 200)', /Web lookup failed \(unrecognised page \(HTTP 200\)\)/],
  ['off', 'ZOE_WEB_FALLBACK_PROVIDER=off', /switched off on this box/],
] as const) {
  test(`outcome ${status} is surfaced honestly, never as results`, async () => {
    const fake = await startFake({
      status: 200,
      json: { status, provider: 'duckduckgo', message: 'x', detail, result_count: 0, results: [] },
    });
    try {
      process.env.ZOE_DATA_URL = fake.url;
      const out = String(await theTool().run(ctx({ query: 'are you sure' })));
      assert.match(out, expect);
      assert.doesNotMatch(out, /Web results/);
      if (status !== 'no_results') assert.doesNotMatch(out, /found nothing/);
    } finally {
      await fake.close();
    }
  });
}

test('zoe-data 404 (its flag off) says so explicitly', async () => {
  const fake = await startFake({ status: 404, json: { detail: 'web search tool disabled' } });
  try {
    process.env.ZOE_DATA_URL = fake.url;
    const out = String(await theTool().run(ctx({ query: 'anything' })));
    assert.match(out, /switched off on this box \(ZOE_WEB_SEARCH_TOOL\)/);
  } finally {
    await fake.close();
  }
});

test('empty query makes no request; dead backend is a calm line, not a throw', async () => {
  const fake = await startFake({ status: 200, json: {} });
  try {
    process.env.ZOE_DATA_URL = fake.url;
    assert.equal(await theTool().run(ctx({ query: '   ' })), 'I need something to search for.');
    assert.equal(fake.calls.length, 0);
  } finally {
    await fake.close();
  }
  process.env.ZOE_DATA_URL = 'http://127.0.0.1:9';
  assert.equal(await theTool().run(ctx({ query: 'x' })), "I couldn't reach web search right now.");
});
