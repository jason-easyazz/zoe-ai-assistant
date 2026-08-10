/**
 * Application entrypoint for the lab-only Zoe-brain sidecar (Flue 2.x).
 *
 * This is Phase 2 of docs/architecture/zoe-flue-integration.md: a Flue-hosted Pi
 * `Agent` on Zoe's local Gemma brain behind the
 * `run_zoe_core(message, session_id, user_id)` seam.
 *
 * WHAT CHANGED FROM THE BETA, and why each line is now explicit:
 *
 *   - `registerProvider('zoe', { api, baseUrl, apiKey })` → `setProvider(...)`
 *     with a Pi provider object. The global wire-protocol registry (and the
 *     `registerApiProvider` call that used to seat the tool-cap) is deleted in
 *     2.x; the `{ stream, streamSimple }` pair now rides on the provider itself.
 *     See src/providers/capped-completions.ts.
 *   - `app.route('/', flue())` → `app.route('/agents/zoe', createAgentRouter(Zoe))`.
 *     The auto-mounted router and directory discovery are gone; every route is
 *     mounted by hand. The URL SHAPE IS DELIBERATELY UNCHANGED — zoe-data's seam
 *     addresses `POST /agents/zoe/:id` and 2.x lets us keep it.
 *   - the agent module's `export const route` auth convention → ordinary Hono
 *     middleware, mounted BEFORE the agent router (src/auth.ts).
 *
 * MOUNTING IS NOT REGISTRATION. `createAgentRouter(Zoe)` serves the routes; what
 * makes `Zoe` an agent at all is the `'use agent'` directive scan run by the
 * `@flue/vite` plugin at build time. Both are required.
 *
 * LIVE — the deployed Zoe brain (flue-zoe-brain-2x.service on :3579,
 * ZOE_BRAIN_BACKEND=flue + ZOE_FLUE_WIRE=2; sole brain since the 2026-08-09 cutover).
 */
import { setProvider } from '@flue/runtime';
import { createAgentRouter } from '@flue/runtime/routing';
import { Hono } from 'hono';
import { Zoe } from './agents/zoe.ts';
import { requireBrainToken } from './auth.ts';
import { createZoeProvider } from './providers/capped-completions.ts';
import { seamAStreamingMiddleware } from './streaming.ts';

/**
 * Build the sidecar's HTTP app, registering the `zoe` provider as a side effect.
 *
 * A FACTORY, not a module-level block, for one reason: the provider snapshots
 * `ZOE_BRAIN_BASE_URL` when it is built, so a test that stands up an in-process
 * mock model on an ephemeral port needs to construct it AFTER setting that env.
 * Exporting the factory means the tests exercise THIS wiring — the real mount
 * order, the real auth gate — rather than a hand-rolled copy that could drift.
 * The default export below still calls it at module load, so the deployed
 * behaviour ("setProvider at module top level, before any agent runs") is
 * unchanged.
 */
export function createApp(): Hono {
  // Seam M: the Gemma rock. The same OpenAI-compatible llama-server the live Pi
  // `local-gemma` extension points at, wrapped by the capped wire handler that
  // imposes the per-turn tool-iteration ceiling and progressive tool disclosure.
  setProvider(createZoeProvider());

  const app = new Hono();

  // Liveness probe for the lab sidecar (not part of Flue's agent API). Mounted
  // before the auth gate on purpose: /health must answer without a token so an
  // operator and systemd can tell a wedged process from a mis-tokened one.
  app.get('/health', (c) =>
    c.json({ ok: true, service: 'flue-zoe-brain', at: new Date().toISOString() }),
  );

  // FAIL-CLOSED AUTH — first thing on the agent path space. Registered before the
  // agent router so nothing (not admission, not payload validation) runs for an
  // unauthorized caller. See src/auth.ts for the trust model.
  app.use('/agents/*', requireBrainToken());

  // Seam-A sentinel streaming (content-negotiated): a POST with
  // `Accept: application/x-ndjson` gets the live text-delta + __TOOL__/__THINKING__
  // sentinel stream instead of the bare 202 admission. Registered AFTER the auth
  // gate and BEFORE the agent router, so a 401 short-circuits ahead of it and a 202
  // admission can be upgraded to a stream. Kill switch: ZOE_BRAIN_STREAM=0.
  // See src/streaming.ts for the pinned prod contract.
  app.use('/agents/*', seamAStreamingMiddleware());

  // The agent's HTTP surface: POST /:id (202 admission), GET|HEAD /:id (stream
  // read), POST /:id/abort, GET /:id/attachments/:attachmentId — all relative to
  // this mount, so the deployed `POST /agents/zoe/:id` shape is preserved.
  app.route('/agents/zoe', createAgentRouter(Zoe));

  return app;
}

export default createApp();
