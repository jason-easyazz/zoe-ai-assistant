/**
 * The 2.x tool contract, on the one tool this channel owns.
 *
 * Two things changed under `defineTool` and BOTH fail at runtime rather than at
 * build time (@flue/runtime docs/guide/migration.md, "Tools"):
 *
 *   1. `run({ input })` → `run({ data })`. A ported tool that still destructures
 *      `input` gets `undefined` and throws on first use — from inside a model
 *      turn, where nobody is watching.
 *   2. `run()` must return a RESULT ENVELOPE `{ output?, terminate? }`. A bare
 *      object (which is exactly what the beta returned) "now throws at runtime
 *      with instructions to wrap it".
 *
 * So this file asserts the tool's arguments arrive under `data` and its result is
 * enveloped under `output`, and carries a NEGATIVE CONTROL proving the assertion
 * is capable of failing: the beta-shaped call (`{ input }`) does not reach the
 * body's arguments.
 *
 * The Telegram API is never touched: the transport is pointed at a mock Bot API.
 */
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';

import { startMockTelegram, type MockTelegram } from './helpers/mock-telegram.ts';

let telegram: MockTelegram;
let mod: typeof import('../src/telegram.ts');
const savedEnv = new Map<string, string | undefined>();

function setEnv(key: string, value: string | undefined) {
  if (!savedEnv.has(key)) savedEnv.set(key, process.env[key]);
  if (value === undefined) delete process.env[key];
  else process.env[key] = value;
}

before(async () => {
  telegram = await startMockTelegram();
  setEnv('TELEGRAM_BOT_TOKEN', '123456:test-token-not-real');
  setEnv('TELEGRAM_API_ROOT', telegram.url);
  mod = await import('../src/telegram.ts');
});

after(async () => {
  await telegram.close();
  for (const [key, value] of savedEnv) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

test('post_telegram_message: arguments arrive under `data` and the result is enveloped under `output`', async () => {
  const tool = mod.postMessage(4242);
  assert.equal(tool.name, 'post_telegram_message');

  const result = await tool.run({ data: { text: 'hello from the port' } } as never);

  assert.deepEqual(telegram.sent.at(-1), { chat_id: 4242, text: 'hello from the port' });
  assert.ok(
    result && typeof result === 'object' && 'output' in result,
    'the 2.x result envelope requires an `output` key — a bare value throws at runtime',
  );
  assert.equal(typeof (result as { output: { messageId: number } }).output.messageId, 'number');
});

test('NEGATIVE CONTROL: the beta-shaped `{ input }` call does not deliver the arguments', async () => {
  const tool = mod.postMessage(4242);
  const before$ = telegram.sent.length;

  await assert.rejects(
    async () => tool.run({ input: { text: 'beta shape' } } as never),
    'a 2.x tool reading `data` must fail when handed the beta `input` envelope',
  );
  assert.equal(telegram.sent.length, before$, 'nothing may be sent from a mis-shaped call');
});

// ─── the conversation-key round trip (unchanged by the port, still load-bearing) ─

test('conversationKey / chatIdFromKey round-trip, and a junk key is rejected loudly', () => {
  assert.equal(mod.conversationKey(-100123), 'telegram:chat:-100123');
  assert.equal(mod.chatIdFromKey(mod.conversationKey(-100123)), -100123);
  assert.throws(() => mod.chatIdFromKey('telegram:chat:not-a-number'), /Unparseable/);
});

// ─── the offline hook must default to the real Telegram ─────────────────────

test('TELEGRAM_API_ROOT defaults to the real Bot API when unset', async () => {
  // Read the source rather than re-importing (the module is already loaded with
  // the mock root): the point is that the DEFAULT in the code is production.
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const source = readFileSync(fileURLToPath(new URL('../src/telegram.ts', import.meta.url)), 'utf8');
  assert.match(source, /TELEGRAM_API_ROOT \?\? 'https:\/\/api\.telegram\.org'/);
});
