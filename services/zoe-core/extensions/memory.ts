/**
 * Brick 3: inject Zoe's memory into the Pi brain.
 *
 * A compact, cited memory packet from zoe-data's internal
 * `/api/memories/for-prompt` endpoint reaches the brain every turn.
 * MemPalace-backed today; the Samantha plan's Hindsight/Graphiti layers compose
 * into the same packet server-side later.
 *
 * Fails OPEN: if memory is slow or unavailable, chat continues without it —
 * memory must never block or break a turn (latency budget honored via timeout).
 *
 * ── KV-PREFIX CONTRACT (why the packet is not composed here in production) ───
 *
 * llama.cpp reuses cached KV only for an EXACT common prefix of the tokenized
 * request (`--cache-reuse` is off for Gemma's shared-KV + SWA attention — KV
 * shifting is unsupported there), so every byte before the first difference is
 * free and every byte after it is re-prefilled.
 *
 * The packet is VOLATILE: it is keyed on `message.slice(0, 500)`, so it changes
 * on essentially every turn. Composing it onto the system prompt put a
 * turn-varying string at the very FRONT of the request and made the reusable
 * prefix end at the last byte of SOUL.md — the whole conversation was
 * re-prefilled every turn.
 *
 * In production the whole block therefore moves OUT of the system prompt: the
 * SEAM (`_compose_message` in services/zoe-data/zoe_core_client.py) folds
 * directive + packet into the user message, ahead of the user's own words. The
 * system prompt is then just SOUL.md — byte-identical for the life of the
 * process, which is exactly what llama.cpp can reuse.
 *
 * ── Two things that were MEASURED, not reasoned ─────────────────────────────
 *
 * Both were tried and rejected against
 * `test_zoe_core_client.py::test_tool_action_dispatches` ("Add bread to my
 * shopping list"), same box, 15 runs each, 14/15 baseline:
 *
 *   1. Keeping MEMORY_USAGE_DIRECTIVE on the system prompt UNCONDITIONALLY (so
 *      it is static, hence cacheable) and moving only the packet: 6/15. The
 *      directive tells the model to lead with what it remembers; when it
 *      remembers nothing it chats instead of calling its tools. Removing the
 *      directive from that same build restored 15/15 — so the coupling is the
 *      cause, not the placement. `memoryBlock()` therefore emits the directive
 *      ONLY with a non-empty packet, exactly as the original code did.
 *
 *   2. Putting the packet in Pi's own tail slot — a `custom` message returned
 *      from `before_agent_start`, which Pi appends AFTER the user message: 9/15.
 *      (Confounded with (1), which was present in that build too, but the slot
 *      is rejected regardless: it costs the user's request the recency position
 *      for no gain the seam does not already give.)
 *
 * ── ONE injection site, never two ───────────────────────────────────────────
 *
 * `ZOE_CORE_MEMORY_SEAM=1` (set by `_worker_env`) tells this extension the seam
 * is doing it, and it then contributes nothing at all. STANDALONE runs — the `pi`
 * CLI, `bench/`, `test/test_brick3_memory.py` — do not set it and keep the
 * original self-service behaviour, so the agent is still useful with no zoe-data
 * driving it. Prefix caching does not apply to those: they are one-shot.
 *
 * MEMORY_USAGE_DIRECTIVE is duplicated on the python side (`zoe_core_client
 * _MEMORY_USAGE_DIRECTIVE`) because the two run in different processes. The copies
 * are pinned byte-for-byte by
 * `services/zoe-data/tests/test_zoe_core_memory_packet_placement.py`.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const ZOE_DATA_URL = process.env.ZOE_DATA_URL ?? "http://127.0.0.1:8000";
const INTERNAL_TOKEN = process.env.ZOE_INTERNAL_TOKEN ?? "";
const TIMEOUT_MS = Number(process.env.ZOE_CORE_MEMORY_TIMEOUT_MS ?? 2000);

/** Env flag by which zoe-data claims the packet injection for itself. */
export const MEMORY_SEAM_ENV = "ZOE_CORE_MEMORY_SEAM";

// How the brain should USE the packet. Without this the model treats the facts
// as a passive dump and answers only what it is directly asked (Samantha
// criterion #3 — "surfaces relevant memory unprompted" — fails). This tells it
// to weave in what is timely or relevant even when not asked, while bounding it
// so it does not recite the whole list or force a fact when none fits.
//
// "below" still reads true under the seam: the packet arrives later in the SAME
// context, folded into the user message rather than into system-prompt text.
export const MEMORY_USAGE_DIRECTIVE = [
  "Use what you know about the user (below) naturally, the way a close friend would.",
  "When they greet you or open a conversation, and you know of something timely — a",
  "date in the next day or two, or a worry they've been carrying — LEAD with it warmly",
  "(e.g. \"Morning! Don't forget the dentist at 3 today.\"), then ask how they are.",
  "If something is clearly relevant to what they just said, bring it up even unasked.",
  "Don't recite the whole list, don't force a fact when none fits, never mention citation ids.",
].join(" ");

// The acting user is resolved PER TURN (not baked at module load), with NO
// default identity: if the user is unknown we inject NO memory packet rather
// than leak a default user's memories. zoe-data drives one Pi session per
// user-conversation and sets ZOE_CORE_USER_ID for that session.
export function currentUserId(): string {
  return (process.env.ZOE_CORE_USER_ID ?? "").trim();
}

/** True when zoe-data's seam already folds the packet into the user message. */
export function seamOwnsPacket(): boolean {
  const raw = (process.env[MEMORY_SEAM_ENV] ?? "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

/**
 * The directive + packet block, or "" when there is no memory this turn.
 *
 * The directive rides WITH the packet and never without it. That coupling is not
 * cosmetic — see the measurement in the header: a directive telling the model to
 * lead with what it remembers, when it remembers nothing, pushes a 4B brain into
 * chatting instead of calling its tools.
 */
export function memoryBlock(packet: string): string {
  const trimmed = (packet ?? "").trim();
  if (!trimmed) return "";
  return `${MEMORY_USAGE_DIRECTIVE}\n\n${trimmed}`;
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
  pi.on("before_agent_start", async (event) => {
    // Production (seam-driven): contribute NOTHING to the system prompt, so it is
    // byte-identical on every turn of the session and the KV prefix survives.
    // The seam folds directive + packet into the user message instead.
    if (seamOwnsPacket()) return;
    // Standalone (`pi` CLI, bench/, test/): no seam to fold it in, so keep the
    // original self-service behaviour — directive + packet on the system prompt.
    const packet = await fetchMemoryPacket(String((event as { prompt?: unknown })?.prompt ?? ""));
    const block = memoryBlock(packet);
    if (!block) return;
    const base = String(event.systemPrompt ?? "");
    return { systemPrompt: base ? `${base}\n\n${block}` : block };
  });
}
