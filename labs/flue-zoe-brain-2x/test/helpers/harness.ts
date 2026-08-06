/**
 * Boot the real sidecar in-process, against a mock model, with no ports and no
 * disk.
 *
 * HOW AN AGENT GETS REGISTERED WITHOUT THE VITE BUILD. On 2.x, registration is
 * the build-time `'use agent'` scan, not the mount — which would make the whole
 * agent surface untestable from `node --test` if there were no other door. There
 * is: `start()` from `@flue/runtime/node` is upstream's bootstrap "for standalone
 * scripts and TEST SUITES", taking the agent functions explicitly and defaulting
 * persistence to in-memory SQLite. So a test process registers `Zoe` directly,
 * touches no file, opens no socket for the runtime, and still exercises the real
 * agent loop.
 *
 * The HTTP surface is driven through Hono's `app.fetch(request)` — no listening
 * socket at all. The only socket in a test run belongs to the mock model.
 *
 * `createApp()` (not a hand-rolled Hono app) is deliberate: the tests then assert
 * against the SAME mount order, auth gate, and streaming middleware the deployed
 * server uses, so a wiring regression cannot hide behind a test-only copy.
 */
import { start, type Flue } from '@flue/runtime/node';
import type { Hono } from 'hono';
import { startMockModel, type MockModelServer, type MockScript } from './mock-model.ts';

export interface BrainHarness {
  app: Hono;
  model: MockModelServer;
  /** Bearer token the harness configured; send it or be rejected. */
  token: string;
  /** POST a user turn to `/agents/zoe/:id`, authorized, in the 2.x body shape. */
  send(instanceId: string, body: string, init?: RequestInit): Promise<Response>;
  stop(): Promise<void>;
}

export interface HarnessOptions {
  /** Extra env applied before the provider snapshots it; restored on stop(). */
  env?: Record<string, string | undefined>;
  /** Bearer token to configure. Defaults to a fixed test token. */
  token?: string;
}

const DEFAULT_TOKEN = 'test-brain-token';

/**
 * THE 2.x REQUEST BODY — and a correction to upstream's own migration guide.
 *
 * The beta accepted `{ "message": "..." }`. The guide says the replacement is
 * "a bare message object — `{ "message": { "kind": "user", "body": "..." } }`",
 * which reads as the message object NESTED under a `message` key. It is not, and
 * that shape is rejected with HTTP 400 "Request is malformed. Delivered messages
 * must be { kind: "user", body: string, ... }" — measured here, not inferred.
 *
 * The runtime's actual contract (`parseDeliveredInput` → `parseDeliveredMessage`
 * in @flue/runtime dist): the DeliveredMessage fields are TOP LEVEL, with
 * `initialData` / `uid` / `idempotencyKey` as optional SIBLINGS —
 *
 *     { "kind": "user", "body": "hello" }
 *
 * The nested form only parses when one of those three sibling keys is present,
 * because that branch strips them and validates the REST — at which point a
 * leftover `message` key fails the variant anyway.
 *
 * This matters well beyond the tests: it is the exact body
 * services/zoe-data/zoe_flue_client.py must send (PHASE2-SPEC). Encoding it in
 * one helper, verified against the real handler, keeps every test honest about
 * the real contract instead of about the documentation.
 */
export function userMessageBody(body: string): string {
  return JSON.stringify({ kind: 'user', body });
}

export async function startBrainHarness(
  script: MockScript,
  options: HarnessOptions = {},
): Promise<BrainHarness> {
  const model = await startMockModel(script);
  const token = options.token ?? DEFAULT_TOKEN;

  const saved = new Map<string, string | undefined>();
  const setEnv = (key: string, value: string | undefined) => {
    if (!saved.has(key)) saved.set(key, process.env[key]);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  };

  setEnv('ZOE_BRAIN_BASE_URL', model.baseUrl);
  setEnv('ZOE_BRAIN_TOKEN', token);
  setEnv('ZOE_BRAIN_OPEN', undefined);
  // Tools must never reach the real zoe-data from a test. Point them at a dead
  // port so any leak is a fast connection refusal, not a live write.
  setEnv('ZOE_DATA_URL', 'http://127.0.0.1:9');
  setEnv('ZOE_BRAIN_ALLOW_WRITES', 'false');
  for (const [key, value] of Object.entries(options.env ?? {})) setEnv(key, value);

  // EVERY src/* import is dynamic and happens HERE, after the env above is in
  // place. Two module-load captures depend on it: `zoeLocalModel()` snapshots
  // ZOE_BRAIN_BASE_URL when the provider is built, and a static import of the
  // agent transitively loads the tool module. Static imports at the top of this
  // file would run before the assignments above — which is exactly how an early
  // draft of this harness pointed the tools at the LIVE zoe-data on :8000.
  const { createApp } = await import('../../src/app.ts');
  const { Zoe } = await import('../../src/agents/zoe.ts');
  const { createZoeProvider } = await import('../../src/providers/capped-completions.ts');
  const app = createApp();

  let flue: Flue;
  try {
    flue = await start({ agents: [Zoe], providers: [createZoeProvider()] });
  } catch (err) {
    await model.close();
    throw err;
  }

  return {
    app,
    model,
    token,
    send: async (instanceId, body, init) =>
      app.fetch(
        new Request(`http://brain.test/agents/zoe/${instanceId}`, {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            authorization: `Bearer ${token}`,
            ...(init?.headers as Record<string, string> | undefined),
          },
          body: userMessageBody(body),
          ...init,
        }),
      ),
    stop: async () => {
      await flue.stop();
      await model.close();
      for (const [key, value] of saved) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    },
  };
}

/** Poll until `predicate` holds or the deadline passes. Returns whether it held. */
export async function waitFor(
  predicate: () => boolean,
  timeoutMs = 10_000,
  stepMs = 20,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, stepMs));
  }
  return predicate();
}
