/**
 * The lab provider: the STOCK pi-ai OpenAI-completions wire, pointed at the already-running llama
 * server. Deliberately NOT the live sidecar's capped provider: that one carries progressive tool
 * disclosure keyed to Zoe's own tool names and would hide these tools. Consequences stated in
 * docs/architecture/zoe-os-brain-bridge.md: no text-tool-call recovery, no prompt-window policy.
 *
 * A hard output cap per model call (OSLAB_MAX_TOKENS, default 200) and a low temperature are
 * applied by wrapping the stream calls, so a runaway reply cannot hold the household's one slot.
 */
import { labFetch, localOrigin } from './local-network.mjs';
import { createProvider } from '@earendil-works/pi-ai';
import {
  stream as streamOpenAI,
  streamSimple as streamSimpleOpenAI,
} from '@earendil-works/pi-ai/api/openai-completions';
import type { Model, Provider } from '@earendil-works/pi-ai';
// ARM B (OSLAB_POLICY=sidecar): the live sidecar's text-tool-call recovery, imported unchanged and read-only
// from labs/flue-zoe-brain-2x/src/text-tool-calls.ts (it only needs pi-ai). The sidecar applies it in
// src/providers/capped-completions.ts cappedStream*: a call the model PRINTED as text becomes a real call.
import { recoverTextToolCalls } from '../../../flue-zoe-brain-2x/src/text-tool-calls.ts';

export const sidecarPolicy = (): boolean => (process.env.OSLAB_POLICY ?? '') === 'sidecar';

export const LAB_PROVIDER_ID = 'zoelab';
export const LAB_MODEL_SPECIFIER = `${LAB_PROVIDER_ID}/local`;

export function maxTokens(): number {
  const n = Number(process.env.OSLAB_MAX_TOKENS ?? '200');
  return Number.isFinite(n) && n > 0 && n <= 400 ? Math.floor(n) : 200;
}

export function labModel(): Model<'openai-completions'> {
  const baseUrl = process.env.OSLAB_LLAMA ?? 'http://127.0.0.1:11434/v1';
  localOrigin(baseUrl);
  return {
    id: 'local',
    name: 'Zoe OS lab (local llama-server)',
    api: 'openai-completions',
    provider: LAB_PROVIDER_ID,
    baseUrl,
    reasoning: false,
    input: ['text'],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 8192,
    maxTokens: maxTokens(),
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const capped = (fn: any) => (model: any, context: any, options?: any) => {
  // pi-ai 0.83.0 forwards options.fetch into OpenAI's client (dist/api/openai-completions.js).
  const inner = fn(model, context, { ...options, fetch: labFetch, temperature: 0.1, maxTokens: maxTokens() });
  return sidecarPolicy() ? recoverTextToolCalls(inner, context.tools) : inner;
};

export function createLabProvider(): Provider {
  return createProvider({
    id: LAB_PROVIDER_ID,
    name: 'Zoe OS lab brain',
    auth: { apiKey: { name: 'local llama-server', resolve: async () => ({ auth: { apiKey: 'local-no-key' } }) } },
    models: [labModel()],
    api: { stream: capped(streamOpenAI), streamSimple: capped(streamSimpleOpenAI) },
  });
}
