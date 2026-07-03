/**
 * Application entrypoint for the lab-only Zoe-brain sidecar.
 *
 * This is Phase 2 of docs/architecture/zoe-flue-integration.md: stand up a
 * Flue-hosted Pi `Agent` on Zoe's local Gemma brain as a THIRD implementation
 * behind the `run_zoe_core(message, session_id, user_id)` seam — without cutting
 * anything over to production. zoe-data is untouched; the live brain lane is
 * untouched; this just proves the sidecar can speak as Zoe via Gemma.
 *
 * Seam M (the model) — the blessed first-party way, mirroring Flue's own
 * `hello-world` example which registers local OpenAI-compatible servers
 * (ollama/lmstudio). We register `zoe` against the live llama-server on :11434
 * (OpenAI-compatible). Agents reference it as `zoe/<model>`.
 *
 * LAB ONLY.
 */
import { registerProvider } from '@flue/runtime';
import { flue } from '@flue/runtime/routing';
import { Hono } from 'hono';
import { CAPPED_COMPLETIONS_API, registerCappedCompletions } from './providers/capped-completions.js';
import { seamAStreamingMiddleware } from './streaming.js';

// Register the capped wire-protocol handler BEFORE the provider that uses it. It
// wraps the built-in openai-completions handler and imposes a hard per-turn
// tool-iteration ceiling so a turn can never loop on tool calls forever (the Flue
// HTTP agent route otherwise runs the agent loop unbounded). See
// src/providers/capped-completions.ts. Cap via ZOE_BRAIN_MAX_TOOL_ITERS (default 8).
registerCappedCompletions();

// Seam M: the Gemma rock. Same OpenAI-compatible llama-server that the live Pi
// `local-gemma` extension already points at — registered here so the Flue Agent
// reaches it the first-party way. The base URL is overridable via env for the
// lab; defaults to the live local endpoint. The `api` points at our capped
// handler (CAPPED_COMPLETIONS_API), which delegates to openai-completions.
registerProvider('zoe', {
  api: CAPPED_COMPLETIONS_API,
  baseUrl: process.env.ZOE_BRAIN_BASE_URL ?? 'http://127.0.0.1:11434/v1',
  // llama-server ignores the key, but the OpenAI-completions client requires a
  // non-empty one. Use a harmless placeholder (overridable via env).
  apiKey: process.env.ZOE_BRAIN_API_KEY ?? 'local-no-key',
});

const app = new Hono();

// Liveness probe for the lab sidecar (not part of Flue's agent API).
app.get('/health', (c) =>
  c.json({ ok: true, service: 'flue-zoe-brain', at: new Date().toISOString() }),
);

// Seam-A sentinel streaming (content-negotiated): a POST with
// `Accept: application/x-ndjson` (and no ?wait=result) gets the live
// text-delta + __TOOL__/__THINKING__ sentinel stream instead of the 202
// admission. Registered BEFORE the flue() mount so it can upgrade the
// response; auth/validation still run inside flue() via next(). All other
// requests (incl. ?wait=result) pass through untouched. Kill switch:
// ZOE_BRAIN_STREAM=0. See src/streaming.ts for the pinned prod contract.
app.use('/agents/*', seamAStreamingMiddleware());

// Mount Flue's built-in agent API. Exposes POST /agents/zoe/:id etc. because
// src/agents/zoe.ts exports `route`.
app.route('/', flue());

export default app;
