/**
 * Flue application entrypoint (authored).
 *
 * Two jobs:
 *   1. Register the **harness model provider** — OpenRouter, an OpenAI-compatible
 *      endpoint. This is the model the harness AGENTS run on. It is deliberately
 *      SEPARATE from Zoe's live voice brain on `:11434` (Gemma-4-E4B) so the
 *      harness never competes for the live GPU slot. Point `HARNESS_LLM_*` at a
 *      local endpoint later to go fully local — no code change, only env.
 *   2. Mount Flue's public HTTP API so `flue run`/`flue dev` can drive the
 *      discovered `workflows/harness.ts` workflow.
 *
 * LAB ONLY. Nothing here is imported by any production Zoe service.
 */
import { registerProvider } from '@flue/runtime';
import { flue } from '@flue/runtime/routing';
import { Hono } from 'hono';

// Register the harness provider once, at module load, before any workflow runs.
// `openai-completions` is Flue's built-in OpenAI-compatible wire protocol, which
// OpenRouter speaks. Model strings then look like `openrouter/<model-id>`.
registerProvider('openrouter', {
  api: 'openai-completions',
  baseUrl: process.env.HARNESS_LLM_BASE_URL ?? 'https://openrouter.ai/api/v1',
  // The box already holds this key (used by Hermes). The spike reads it from the
  // environment / its gitignored .env — it is never written into tracked files.
  apiKey: process.env.OPENROUTER_API_KEY ?? process.env.HARNESS_LLM_API_KEY,
});

const app = new Hono();
app.route('/', flue());

export default app;
