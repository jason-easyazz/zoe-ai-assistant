/**
 * Brick 3: inject Zoe's memory into the Pi brain.
 *
 * Each turn, fetches a compact, cited memory packet from zoe-data's internal
 * `/api/memories/for-prompt` endpoint and composes it onto the system prompt
 * (on top of the soul). MemPalace-backed today; the Samantha plan's
 * Hindsight/Graphiti layers compose into the same packet server-side later.
 *
 * Fails OPEN: if memory is slow or unavailable, chat continues without it —
 * memory must never block or break a turn (latency budget honored via timeout).
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const ZOE_DATA_URL = process.env.ZOE_DATA_URL ?? "http://127.0.0.1:8000";
const INTERNAL_TOKEN = process.env.ZOE_INTERNAL_TOKEN ?? "";
// NOTE: resolved once per process. Fine for the current CLI-per-session model
// (each `pi` invocation is a fresh process for one user). BEFORE promoting Pi to
// a long-running/multi-session server, this MUST become per-turn/per-session —
// otherwise every caller would receive family-admin's memories (a privacy bug).
// Tracked as a cutover prerequisite.
const USER_ID = process.env.ZOE_CORE_USER_ID ?? "family-admin";
const TIMEOUT_MS = Number(process.env.ZOE_CORE_MEMORY_TIMEOUT_MS ?? 2000);

async function fetchMemoryPacket(message: string): Promise<string> {
  try {
    const url = new URL("/api/memories/for-prompt", ZOE_DATA_URL);
    url.searchParams.set("user_id", USER_ID);
    if (message) url.searchParams.set("message", message.slice(0, 500));
    const headers: Record<string, string> = { Accept: "application/json" };
    if (INTERNAL_TOKEN) headers["X-Internal-Token"] = INTERNAL_TOKEN;
    const res = await fetch(url, { headers, signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!res.ok) return "";
    const data = (await res.json()) as { packet?: string };
    return (data.packet ?? "").trim();
  } catch {
    return ""; // fail open — memory is best-effort, never block a turn
  }
}

export default function (pi: ExtensionAPI) {
  pi.on("before_agent_start", async (event) => {
    const packet = await fetchMemoryPacket(String((event as { prompt?: unknown })?.prompt ?? ""));
    if (!packet) return;
    const base = event.systemPrompt ?? "";
    // Compose under the soul (soul.ts runs earlier and sets the persona).
    return { systemPrompt: base ? `${base}\n\n${packet}` : packet };
  });
}
