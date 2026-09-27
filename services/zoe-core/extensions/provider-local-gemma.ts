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
const DEFAULT_MODEL_ID = process.env.ZOE_CORE_MODEL_ID ?? "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf";
// Must match what llama-server can actually serve: ONE slot of --ctx-size 8192
// (scripts/setup/systemd/llama-server.service, B6.6). Pi compacts against this
// window, so declaring more than the server holds lets sessions grow until the
// server refuses them. Max output stays 2048: measured p99 prompts are ~3.3k
// tokens, so prompt + a full 2048-token reply (~5.3k) still fits the slot.
// Compaction thresholds for this window live in ../.pi/settings.json (reserve
// 2048 = this max output); Pi's defaults (reserve 16384 / keep 20000) assume a
// far larger window. tests/unit/test_llama_server_unit_flags.py pins all of it.
const CONTEXT_WINDOW = Number(process.env.ZOE_CORE_MODEL_CONTEXT) || 8192;
const MAX_TOKENS = Number(process.env.ZOE_CORE_MODEL_MAXTOKENS) || 2048;

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
