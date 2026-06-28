/**
 * Registers the spike's LLM providers with Flue.
 *
 * The design lever (see ../README.md "per-agent model knob" + PR #736 §2b):
 * Flue binds models PER AGENT, so we register TWO providers and the harness
 * agents run on the SECOND one — never on the live voice brain:
 *
 *   1. LOCAL voice brain  — the live llama.cpp on :11434 (Gemma E4B). Registered
 *      so the spike is aware of it, but the harness agents are NOT bound here, so
 *      they never contend for the live GPU slot.
 *   2. HARNESS model      — a SEPARATE, CONFIGURABLE endpoint (HARNESS_LLM_*),
 *      default a cloud/dev model for the build. This is what the agents run on.
 *
 * To go FULLY LOCAL later, point HARNESS_LLM_BASE_URL / HARNESS_LLM_MODEL at a
 * local endpoint — `buildAgents(...)` still just receives a model string, so
 * there is NO code change here, only env (the one-line provider swap).
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

/** Provider id for the LIVE local voice brain (harness agents do NOT run here). */
export const LOCAL_PROVIDER_ID = 'local-llamacpp';
/** Provider id for the CONFIGURABLE harness model the agents actually run on. */
export const HARNESS_PROVIDER_ID = 'harness-llm';

/**
 * Register BOTH providers and return the `provider/model` string the HARNESS
 * AGENTS use as their `model`. The returned string points at the harness model,
 * never at the voice brain — that is what keeps the harness off the live GPU.
 */
export function registerProviders(cfg: SpikeConfig): { harnessModel: string } {
  // --- Provider 1: the live local voice brain (registered, but agents not bound here) ---
  // FLUE-API: shape mirrors the "OpenAI-compatible custom provider" pattern —
  // id + baseUrl + apiKey. Verify field names against @flue/sdk on install.
  registerProvider({
    id: LOCAL_PROVIDER_ID,
    // OpenAI-compatible endpoint exposed by the live llama.cpp server (:11434).
    baseUrl: cfg.llmBaseUrl,
    // llama.cpp typically ignores the key, but OpenAI clients require one.
    apiKey: cfg.llmApiKey,
    models: [cfg.llmModel],
  });

  // --- Provider 2: the configurable harness model (default cloud/dev) ---
  // This is the swappable seam: change HARNESS_LLM_* in env to move the harness
  // between a cloud/dev model (now) and a local one (later) with zero code change.
  registerProvider({
    id: HARNESS_PROVIDER_ID,
    baseUrl: cfg.harnessLlmBaseUrl,
    apiKey: cfg.harnessLlmApiKey,
    models: [cfg.harnessLlmModel],
  });

  // Agents reference models as `${providerId}/${modelName}`. The harness binds to
  // the HARNESS provider — see buildAgents(...) in agents.ts, where this string is
  // applied per agent (and can later differ per phase: cheap for scout, strong for
  // implementer/reviewer).
  return { harnessModel: `${HARNESS_PROVIDER_ID}/${cfg.harnessLlmModel}` };
}
