/**
 * Flue app entry for the lab executor's workers.
 *
 * Registers a deliberately dead-end provider: the synthetic phase worker never
 * opens a model session, so nothing is ever sent anywhere. A real phase worker
 * (Phase 2+) swaps this for the harness model registration exactly as
 * labs/flue-harness-spike/src/app.ts does — model choice is per-agent, which is
 * the answer to migration-doc unknown 3 (see FINDINGS.md).
 *
 * LAB ONLY.
 */
import { registerProvider } from '@flue/runtime';
import { flue } from '@flue/runtime/routing';
import { Hono } from 'hono';

// FLUE-API: registration is config-only; no request is made unless an agent
// session actually prompts. The synthetic worker never prompts.
registerProvider('deadend', {
  api: 'openai-completions',
  baseUrl: 'http://127.0.0.1:1/v1',
  apiKey: 'unused-lab-only',
});

const app = new Hono();
app.route('/', flue());

export default app;
