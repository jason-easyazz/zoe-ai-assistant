/**
 * Brick 3: inject Zoe's memory into the Pi brain.
 *
 * Each turn, fetches a compact, cited memory packet from zoe-data's internal
 * `/api/memories/for-prompt` endpoint and hands it to the brain. MemPalace-backed
 * today; the Samantha plan's Hindsight/Graphiti layers compose into the same
 * packet server-side later.
 *
 * Fails OPEN: if memory is slow or unavailable, chat continues without it —
 * memory must never block or break a turn (latency budget honored via timeout).
 *
 * ── KV-PREFIX CONTRACT (why the packet is NOT in the system prompt) ──────────
 *
 * llama.cpp reuses cached KV only for an EXACT common prefix of the tokenized
 * request (`--cache-reuse` is off for Gemma's shared-KV + SWA attention — KV
 * shifting is unsupported there), so every byte before the first difference is
 * free and every byte after it is re-prefilled.
 *
 * The packet is VOLATILE: it is keyed on `message.slice(0, 500)`, so it changes
 * on essentially every turn. Composing it onto the system prompt therefore put a
 * turn-varying string at the very FRONT of the request and made the reusable
 * prefix end at the last byte of SOUL.md — the whole conversation was
 * re-prefilled every turn.
 *
 * So the two halves are split by how often they change:
 *
 *   • MEMORY_USAGE_DIRECTIVE — STATIC. Stays on the system prompt. Its only
 *     input is whether an acting user exists, which is a per-PROCESS constant
 *     (zoe-data bakes ZOE_CORE_USER_ID into each worker's spawn env and keeps one
 *     Pi process per (user, session) — see services/zoe-data/zoe_core_client.py
 *     `_worker_env` / `_worker_for`). Gating on the packet instead would let one
 *     slow memory fetch flip the system prompt and void the cache for the rest of
 *     the session.
 *
 *   • the packet itself — VOLATILE. Returned as a `custom` message, which Pi
 *     appends AFTER the user message (core/agent-session.js) and converts to a
 *     `role: "user"` turn on the wire (pi-agent-core harness/messages.js
 *     `convertToLlm`). It therefore lands in the TAIL, past every cacheable byte,
 *     and the model still reads it verbatim — the packet text is unchanged, so
 *     what the brain is told is identical; only WHERE it is told changed.
 *
 * ── ONE injection site, not two ──────────────────────────────────────────────
 *
 * This extension is the ONLY place the core lane fetches /api/memories/for-prompt.
 * zoe-data's `zoe_core_client._compose_message` deliberately does not add it:
 * `routers/chat.py` passes `db_memory_context=None` by default precisely because
 * this extension already injects it (`_CHAT_INJECT_DB_MEMORY`, default OFF). Keep
 * the fetch here — moving it to the Python seam would double-inject whenever that
 * flag is on, and would strip memory from every non-zoe-data caller of the agent
 * (pi CLI, the bench/ harness, services/zoe-core/test).
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const ZOE_DATA_URL = process.env.ZOE_DATA_URL ?? "http://127.0.0.1:8000";
const INTERNAL_TOKEN = process.env.ZOE_INTERNAL_TOKEN ?? "";
const TIMEOUT_MS = Number(process.env.ZOE_CORE_MEMORY_TIMEOUT_MS ?? 2000);

// How the brain should USE the packet. Without this the model treats the facts
// as a passive dump and answers only what it is directly asked (Samantha
// criterion #3 — "surfaces relevant memory unprompted" — fails). This tells it
// to weave in what is timely or relevant even when not asked, while bounding it
// so it does not recite the whole list or force a fact when none fits.
//
// "below" still reads true: the packet arrives later in the SAME context, as a
// turn appended after the user's message rather than as system-prompt text.
export const MEMORY_USAGE_DIRECTIVE = [
  "Use what you know about the user (below) naturally, the way a close friend would.",
  "When they greet you or open a conversation, and you know of something timely — a",
  "date in the next day or two, or a worry they've been carrying — LEAD with it warmly",
  "(e.g. \"Morning! Don't forget the dentist at 3 today.\"), then ask how they are.",
  "If something is clearly relevant to what they just said, bring it up even unasked.",
  "Don't recite the whole list, don't force a fact when none fits, never mention citation ids.",
].join(" ");

/** customType tag on the injected packet turn (context-only; never rendered). */
export const MEMORY_PACKET_CUSTOM_TYPE = "zoe-memory-packet";

// The acting user is resolved PER TURN (not baked at module load), with NO
// default identity: if the user is unknown we inject NO memory packet rather
// than leak a default user's memories. zoe-data drives one Pi session per
// user-conversation and sets ZOE_CORE_USER_ID for that session.
export function currentUserId(): string {
  return (process.env.ZOE_CORE_USER_ID ?? "").trim();
}

/**
 * The system-prompt contribution — STATIC for the life of the process.
 *
 * `hasUser` is the only input and it is a per-process constant, so two turns of
 * one session always produce byte-identical output. Nothing turn-varying may
 * ever be added here; that is the whole point of the split above.
 */
export function memorySystemPrompt(base: string, hasUser: boolean): string {
  if (!hasUser) return base; // unknown user → no memory scaffolding at all
  return base ? `${base}\n\n${MEMORY_USAGE_DIRECTIVE}` : MEMORY_USAGE_DIRECTIVE;
}

export async function fetchMemoryPacket(message: string): Promise<string> {
  const userId = currentUserId();
  if (!userId) return ""; // fail closed: unknown user → inject no memory
  try {
    const url = new URL("/api/memories/for-prompt", ZOE_DATA_URL);
    url.searchParams.set("user_id", userId);
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
  // Consecutive-duplicate suppression. The packet now ACCUMULATES in the session
  // (each turn appends one) instead of replacing the system prompt, so re-sending
  // an unchanged packet would grow context for nothing — the previous copy is
  // still in the conversation and still readable. Skipping appends zero bytes, so
  // it is prefix-safe by construction. Reset semantics follow the process: a
  // worker restart clears the session and this cache together.
  let lastPacket = "";
  pi.on("before_agent_start", async (event) => {
    const hasUser = Boolean(currentUserId());
    const systemPrompt = memorySystemPrompt(String(event.systemPrompt ?? ""), hasUser);
    if (!hasUser) return { systemPrompt };
    const packet = await fetchMemoryPacket(String((event as { prompt?: unknown })?.prompt ?? ""));
    if (!packet || packet === lastPacket) return { systemPrompt };
    lastPacket = packet;
    return {
      systemPrompt,
      message: {
        customType: MEMORY_PACKET_CUSTOM_TYPE,
        content: [{ type: "text" as const, text: packet }],
        display: false, // context-only, exactly as invisible as the old system-prompt block
      },
    };
  });
}
