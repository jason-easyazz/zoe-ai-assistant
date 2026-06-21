/**
 * Registers the local llama.cpp OpenAI-compatible server as Flue's LLM provider.
 *
 * This is the single seam that keeps the whole harness LOCAL: every agent in the
 * pipeline talks to http://127.0.0.1:11434/v1 (or a LAN equivalent) instead of a
 * cloud API. No Anthropic/OpenAI key required.
 *
 * FLUE-API: `registerProvider` is written against the documented/expected Flue
 * provider shape. On first `npm install`, confirm the exact signature against the
 * installed @flue/sdk and adjust here if needed (see ../FINDINGS.md).
 *
 * LAB ONLY.
 */
// FLUE-API: confirm the import path/name against the installed package.
import { registerProvider } from '@flue/sdk';
import type { SpikeConfig } from './config.ts';

/** Provider id other parts of the spike reference when picking a model. */
export const LOCAL_PROVIDER_ID = 'local-llamacpp';

/**
 * Register the local OpenAI-compatible provider and return the `provider/model`
 * string the agents use as their `model`.
 */
export function registerLocalLlm(cfg: SpikeConfig): string {
  // FLUE-API: shape mirrors the "OpenAI-compatible custom provider" pattern —
  // id + baseUrl + apiKey. Verify field names against @flue/sdk on install.
  registerProvider({
    id: LOCAL_PROVIDER_ID,
    // OpenAI-compatible endpoint exposed by llama.cpp's server.
    baseUrl: cfg.llmBaseUrl,
    // llama.cpp typically ignores the key, but OpenAI clients require one.
    apiKey: cfg.llmApiKey,
    // Optional: some providers want the model list declared up front.
    models: [cfg.llmModel],
  });

  // Agents reference models as `${providerId}/${modelName}`.
  return `${LOCAL_PROVIDER_ID}/${cfg.llmModel}`;
}
