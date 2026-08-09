/**
 * The placeholder agent actually EXISTS — Flue 2.x's quietest failure mode.
 *
 * Registration on 2.x comes from the build-time `'use agent'` source-directive
 * scan, and "a converted module without the directive is silently not an agent"
 * (@flue/runtime docs/guide/migration.md). Silently: no error, no warning, no log
 * line. Mounting does not help — `createAgentRouter(...)` publishes routes
 * whether or not the export is an agent.
 *
 * On THIS channel the agent is a placeholder that is never dispatched, so the
 * failure would be invisible forever — which is exactly why it is worth pinning
 * here rather than discovering it on the brain sidecar, where an unregistered
 * agent is a dead voice path. The test carries its own negative control: the
 * same POST against a runtime started with NO agents must not be admitted.
 *
 * `start()` from `@flue/runtime/node` is upstream's bootstrap for standalone
 * scripts and test suites — it takes agent functions explicitly and defaults
 * persistence to in-memory SQLite, so this registers the agent without the Vite
 * build, touches no file, and opens no socket.
 *
 * EVERY `src/*` IMPORT IS DYNAMIC. `src/telegram.ts` throws at module load
 * without `TELEGRAM_BOT_TOKEN`, and `src/app.ts` starts long-polling at module
 * load — ESM hoists static imports above any assignment in this file, so a
 * static import here would fail before the env is in place (measured, not
 * assumed: the first draft of this file did exactly that).
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { after, before, describe, it } from 'node:test';
import { fileURLToPath } from 'node:url';

process.env.TELEGRAM_BOT_TOKEN = '123456:test-token-not-real';
// Dead port: the module-load poll bootstrap must never reach api.telegram.org.
process.env.TELEGRAM_API_ROOT = 'http://127.0.0.1:9';
process.env.ZOE_DATA_URL = 'http://127.0.0.1:9';

const { start } = await import('@flue/runtime/node');
type Flue = Awaited<ReturnType<typeof start>>;
const { ZoeTelegram } = await import('../src/agents/zoe.ts');
const { createApp } = await import('../src/app.ts');
const { bot } = await import('../src/telegram.ts');

after(async () => {
  try {
    await bot.stop();
  } catch {
    // The loop never started against the dead port; nothing to stop.
  }
});

function post(app: ReturnType<typeof createApp>, id: string, body: unknown) {
  return app.fetch(
    new Request(`http://telegram.test/agents/zoe/${id}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }),
  );
}

describe('agent registration (2.x `use agent`)', () => {
  it('the agent module carries the `use agent` directive as its FIRST statement', () => {
    const source = readFileSync(
      fileURLToPath(new URL('../src/agents/zoe.ts', import.meta.url)),
      'utf8',
    );
    // A directive prologue only counts before any other code, and the build scan
    // looks for it there.
    assert.match(
      source,
      /^\s*'use agent';/,
      'src/agents/zoe.ts lost the `use agent` directive: it would silently stop being an agent',
    );
  });

  it('ZoeTelegram is a synchronous function with a pinned durable identity', () => {
    assert.equal(typeof ZoeTelegram, 'function');
    // The 2.x contract: the agent function must be synchronous.
    assert.notEqual(ZoeTelegram.constructor.name, 'AsyncFunction');
    // Pinned so the storage slug matches the `/agents/zoe` mount the beta served.
    assert.equal(ZoeTelegram.agentName, 'zoe');
  });

  it('/health fails closed while the poll loop is down', async () => {
    const app = createApp({ polling: false });
    const res = await app.fetch(new Request('http://telegram.test/health'));
    assert.equal(res.status, 503);
    assert.deepEqual(await res.json(), {
      ok: false,
      service: 'flue-zoe-telegram',
      polling: false,
    });
  });

  // Runs FIRST, before any start() below has configured a runtime — mounting on
  // its own admits nothing.
  it('NEGATIVE CONTROL: mounting without a configured runtime does not admit', async () => {
    const res = await post(createApp({ polling: true }), 'telegram:chat:41', {
      kind: 'user',
      body: 'hello',
    });
    assert.notEqual(res.status, 202, 'createAgentRouter must not admit on its own');
  });

  describe('with the agent registered', () => {
    let flue: Flue;

    before(async () => {
      flue = await start({ agents: [ZoeTelegram] });
    });
    after(async () => {
      await flue.stop();
    });

    it('the mounted route admits a turn — proof the agent is registered', async () => {
      // The conversation id is the channel's own `telegram:chat:<id>` key, the
      // same shape the beta's auto-router received: the agent parses the chat id
      // out of it to bind its reply tool, so an arbitrary id fails at render.
      // THE 2.x BODY SHAPE. Upstream's migration guide documents a NESTED
      // `{"message": {...}}`; that is rejected. The real contract is the
      // DeliveredMessage fields at TOP LEVEL — measured on the brain port and
      // re-confirmed here on a second, independent application.
      const res = await post(createApp({ polling: true }), 'telegram:chat:42', {
        kind: 'user',
        body: 'hello',
      });
      assert.equal(res.status, 202, 'POST /agents/zoe/:id should return a 202 admission');
    });

    it('NEGATIVE CONTROL: the documented-but-wrong nested body shape is refused', async () => {
      const res = await post(createApp({ polling: true }), 'telegram:chat:43', {
        message: { kind: 'user', body: 'hello' },
      });
      assert.notEqual(res.status, 202, 'the nested shape from the migration guide must not admit');
    });
  });

  describe('NEGATIVE CONTROL: a runtime that registered a DIFFERENT agent', () => {
    let flue: Flue;

    // `start({ agents: [] })` is refused outright ("requires at least one
    // agent"), so the isolating control registers some other agent instead: the
    // runtime is up, the route is mounted, and only ZoeTelegram is missing.
    function Decoy(): string {
      return 'decoy';
    }

    before(async () => {
      flue = await start({ agents: [Decoy] });
    });
    after(async () => {
      await flue.stop();
    });

    it('the same POST is NOT admitted when this agent was never registered', async () => {
      const res = await post(createApp({ polling: true }), 'telegram:chat:44', {
        kind: 'user',
        body: 'hello',
      });
      assert.notEqual(
        res.status,
        202,
        'if this is 202 with no agent registered, the 202 above proves nothing',
      );
    });
  });
});
