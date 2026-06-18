/**
 * Brick 1: point Pi (zoe-core's brain) at the local Gemma 4 model server.
 *
 * Registers a Pi provider named "local-gemma" backed by the host's
 * OpenAI-compatible endpoint (the same one zoe_agent.py already uses via
 * GEMMA_SERVER_URL). Models are discovered from /v1/models when reachable,
 * falling back to a configured default so the provider still registers offline.
 *
 * Select it with:  pi --provider local-gemma --model <id>
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const BASE_URL =
  process.env.ZOE_CORE_MODEL_URL ??
  process.env.GEMMA_SERVER_URL ??
  "http://127.0.0.1:11434/v1";
// llama-server / ollama don't require a real key, but OpenAI clients want a
// non-empty one.
const API_KEY = process.env.ZOE_CORE_MODEL_API_KEY ?? "local";
const DEFAULT_MODEL_ID = process.env.ZOE_CORE_MODEL_ID ?? "gemma-4-E2B-it-Q4_K_M.gguf";
const CONTEXT_WINDOW = Number(process.env.ZOE_CORE_MODEL_CONTEXT ?? 32768);
const MAX_TOKENS = Number(process.env.ZOE_CORE_MODEL_MAXTOKENS ?? 2048);

async function discoverModelIds(): Promise<string[]> {
  try {
    const res = await fetch(`${BASE_URL}/models`, {
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return [];
    const payload = (await res.json()) as { data?: Array<{ id?: string }> };
    return (payload.data ?? [])
      .map((m) => m.id)
      .filter((id): id is string => typeof id === "string" && id.length > 0);
  } catch {
    return [];
  }
}

export default async function (pi: ExtensionAPI) {
  const discovered = await discoverModelIds();
  const ids = discovered.length > 0 ? discovered : [DEFAULT_MODEL_ID];

  pi.registerProvider("local-gemma", {
    baseUrl: BASE_URL,
    apiKey: API_KEY,
    api: "openai-completions",
    models: ids.map((id) => ({
      id,
      name: id,
      reasoning: false,
      input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: CONTEXT_WINDOW,
      maxTokens: MAX_TOKENS,
    })),
  });
}
