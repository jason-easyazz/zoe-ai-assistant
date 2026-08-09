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
import { HISTORY_CLOSE, HISTORY_MARKER } from "./abilities.ts";

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
  // Neutralize BEFORE delimiting: the packet is user content (stored memory text
  // spliced in by routers/memories.py), the delimiters are ours. See
  // `neutralizeMarkers`.
  const trimmed = neutralizeMarkers((packet ?? "").trim());
  if (!trimmed) return "";
  // Delimited identically to the seam's `_memory_block`, so one strip rule covers
  // both. (Standalone puts this on the SYSTEM prompt, which Pi replaces each turn,
  // so nothing accumulates there — the markers are for symmetry, not for elision.)
  return `${MEMORY_BLOCK_OPEN}\n${MEMORY_USAGE_DIRECTIVE}\n\n${trimmed}\n${MEMORY_BLOCK_CLOSE}`;
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

// ── Superseded-CONTEXT elision (Pi retains every user message it is sent) ────
//
// The seam folds its context blocks into the user message, and Pi keeps that
// message in its conversation forever — one long-lived process per
// (user_id, session_id). So without this, turn N's request carried N memory
// snapshots: the 32k window filled with duplicates, corrected facts stayed
// readable in older turns, and an imperative "Ask the user: would you like me to
// add <name> as a contact?" survived being resolved. The old system-prompt
// injection did not have this problem because a system prompt is REPLACED.
//
// EVERY block the seam composes has that shape, not just the memory packet, and
// the memory packet is not even the biggest of them:
//
//   * `[About you]` — a near-constant portrait paragraph, repeated verbatim.
//   * `[What you remember]` — the caller-supplied recall block (the voice path
//     always supplies one), re-queried per turn.
//   * `[Recent conversation]` — `history[-12:]`, REPLAYED ON EVERY TURN. This is
//     the expensive one: an N-turn session carried N copies of an overlapping
//     12-turn window, on top of the real retained conversation those turns are
//     already in. Quadratic in the session length, and entirely superseded.
//
// So the strip is over the whole TABLE of blocks below, not one pair.
//
// Pi's `context` event is the fix, and it is a genuine ephemeral slot rather than
// a history rewrite: the runner hands handlers a `structuredClone` of the
// messages and `transformContext` (pi-agent-core agent-loop.js) feeds the result
// to the provider ONLY — `context.messages`, the retained state, is untouched.
// pi-agent-core documents the hook for exactly this ("Context window management
// (pruning old messages)"). So the model sees one copy; nothing is destroyed.
//
// KV-PREFIX COST, accepted deliberately and measured: eliding the previous turn's
// blocks changes bytes that were already in the cache, so reuse now ends at that
// message instead of running to the end of the conversation — one exchange is
// re-prefilled per turn. That is unavoidable for ANY design that stops resending
// superseded context (an ephemeral insert shifts positions just as an elision
// does), and it is bounded and constant, unlike the pre-PR behaviour where a
// message-keyed packet on the system prompt re-prefilled the ENTIRE conversation
// every turn. On a turn with no context blocks there is nothing to strip and the
// prefix runs the whole way.
//
// WIDENING THE STRIP FROM ONE BLOCK TO FOUR ADDS NOTHING TO THAT COST. The break
// point is already the previous user message — the portrait sits a few hundred
// bytes EARLIER inside that same message, so the boundary moves within one
// message rather than to an earlier one. The bound stays "~one exchange", and the
// tokens reclaimed are far larger: `[Recent conversation]` alone is up to 12
// replayed turns per superseded message.
export const MEMORY_BLOCK_OPEN = "[MEMORY CONTEXT]";
export const MEMORY_BLOCK_CLOSE = "[END MEMORY CONTEXT]";

// The seam's other two labels. `[Recent conversation]` is owned by abilities.ts —
// it PARSES that block to seed disclosure on a restarted worker — and is imported
// rather than copied, so this runtime holds exactly one spelling of it.
//
// Each close is `"[END " + label.slice(1)`, the rule MEMORY_BLOCK_OPEN/CLOSE
// already follow, mirrored by `_close_marker` in zoe_core_client.py.
export const PORTRAIT_BLOCK_OPEN = "[About you]";
export const PORTRAIT_BLOCK_CLOSE = "[END About you]";
export const RECALL_BLOCK_OPEN = "[What you remember]";
export const RECALL_BLOCK_CLOSE = "[END What you remember]";

/**
 * Every delimited block the seam folds into a user message, in composition order.
 *
 * Pinned equal to `_CONTEXT_BLOCKS` in services/zoe-data/zoe_core_client.py by a
 * test: the two runtimes are separate processes, and a drift is SILENT — the strip
 * would simply match nothing and every superseded copy would stay in the request,
 * which is the bug this exists to prevent.
 */
export const CONTEXT_BLOCKS: readonly (readonly [string, string])[] = [
  [PORTRAIT_BLOCK_OPEN, PORTRAIT_BLOCK_CLOSE],
  [RECALL_BLOCK_OPEN, RECALL_BLOCK_CLOSE],
  [MEMORY_BLOCK_OPEN, MEMORY_BLOCK_CLOSE],
  [HISTORY_MARKER, HISTORY_CLOSE],
];

// ── Delimiter-collision guard (mirrors `_neutralize_markers` in zoe_core_client) ─
//
// The delimiters are COMPOSITION-OWNED; the packet is user content —
// routers/memories.py splices stored `ref.text[:200]` straight into it. A stored
// memory whose text is the line `[END MEMORY CONTEXT]` used to close the block
// early, so the REMAINDER of a superseded packet (stale facts, a resolved
// "add Sam as a contact?" offer) survived the strip below and stayed readable for
// the life of the session.
//
// Fix: wedge a U+200B ZERO WIDTH SPACE in after the marker's opening bracket.
// `[END MEMORY CONTEXT]` becomes `[<ZWSP>END MEMORY CONTEXT]` — identical on
// screen and to the model, no longer equal to anything a consumer parses.
// Escaping beats rejecting: dropping the memory would let one poisoned fact
// silence itself. Idempotent, and byte-for-byte a no-op when nothing collides.
//
// Only the two BLOCK delimiters are guarded here. The seam's third marker
// (`_UTTERANCE_MARKER`) has no counterpart in this runtime: it exists only in a
// seam-composed message, and the seam neutralizes it on its own side.
export const MARKER_BREAK = "\u200b"; // an ESCAPE on purpose: the char is invisible in source
export const CONTROL_MARKERS = [MEMORY_BLOCK_OPEN, MEMORY_BLOCK_CLOSE] as const;

/** User content with every composition-owned delimiter rendered inert. */
export function neutralizeMarkers(text: string): string {
  if (!text) return text;
  let out = text;
  for (const marker of CONTROL_MARKERS) {
    if (!out.includes(marker)) continue;
    out = out.split(marker).join(`${marker.slice(0, 1)}${MARKER_BREAK}${marker.slice(1)}`);
  }
  return out;
}

const OPEN_MARKERS = new Set(CONTEXT_BLOCKS.map(([open]) => open));
const OPEN_BY_CLOSE = new Map(CONTEXT_BLOCKS.map(([open, close]) => [close, open]));

/**
 * Every seam-composed context block removed, with the surrounding blank line healed.
 *
 * DEFENSIVE BY CONSTRUCTION, independent of the guard above — the guard is the
 * fix, this is the backstop for content that reached the conversation before it,
 * or by a path that skipped it.
 *
 *   * A delimiter counts only as a WHOLE LINE. Composition always emits it that
 *     way, so an inline mention ("the [MEMORY CONTEXT] marker") is not ours and
 *     the text is returned untouched.
 *   * ONE CONTIGUOUS SPAN, from the first line that is ANY open delimiter to the
 *     last line that is ANY close delimiter — not one pass per pair.
 *   * Every open must be MATCHED by a later close OF ITS OWN TYPE. If any open is
 *     still outstanding when the scan ends, the span runs to the END of the
 *     message instead of stopping at some other block's close.
 *
 * WHY ONE SPAN AND NOT FOUR PASSES. Per-pair passes were tried and are UNSAFE, and
 * the negative control in test/prefix_stability.test.ts reproduces it: a memory
 * whose text is the line `[END About you]` makes the portrait pass — greedy to the
 * LAST close of its own pair — run from the real portrait open right through the
 * `[MEMORY CONTEXT]` OPEN line. The memory pass then finds no open of its own,
 * returns the text untouched, and the rest of the superseded packet (stale facts, a
 * resolved contact offer) LEAKS. Over-eliding one block must never under-elide
 * another; a single span cannot destroy an anchor it has not already passed.
 *
 * WHY THE SPAN END IS NOT SIMPLY "THE LAST CLOSE". Recording one open and then
 * accepting ANY later close as the end of the span was a second under-elide, found
 * by review rather than by the controls above, and it defeated the "unmatched open
 * elides through EOF" rule stated one bullet up:
 *
 *   (a) a WELL-FORMED `[About you]` block followed by an UNTERMINATED
 *       `[What you remember]` ended the span at the portrait's own close, and the
 *       unterminated block — stale recall, the exact thing this removes — survived;
 *   (b) an unterminated `[About you]` followed by a stray `[END What you remember]`
 *       ended the span on a MISMATCHED close, leaking everything after it.
 *
 * Both are answered by counting, per block type, the opens still awaiting a close.
 * A close only balances an open of ITS OWN type that is actually outstanding; a
 * close with nothing outstanding is a stray (an injected line, or a delimiter that
 * reached the conversation by a path that skipped the guard) and cannot be trusted
 * to end the span on its own. If anything is still outstanding at the end of the
 * scan, elide through EOF — the same safe direction the rest of this function takes.
 *
 * A stray close does NOT by itself force elision to EOF: with every open balanced,
 * the structure is intact and the text after the last close is genuinely outside
 * the blocks (the user's own words). Over-eliding is preferred to leaking, but only
 * where something is actually unbalanced.
 *
 * Composition emits the blocks contiguously and balanced, so on well-formed input
 * the span is byte-identical to eliding each block separately.
 *
 * The trade is the same one stated for the memory block alone: this only ever runs
 * on SUPERSEDED user messages, so over-eliding costs already-stale context while
 * under-eliding leaks exactly what the mechanism exists to remove. Prefer
 * over-eliding, always.
 *
 * Returns the SAME string object when there is nothing of ours to remove — the
 * identity the no-op path above depends on.
 */
export function stripContextBlocks(text: string): string {
  const lines = text.split("\n");
  /** Per block type, how many opens are still waiting for a close of that type. */
  const outstanding = new Map<string, number>();
  let first = -1;
  let last = -1;
  for (let i = 0; i < lines.length; i++) {
    // trimEnd only: composition never indents a delimiter, so a leading space
    // means the line is content, not ours.
    const line = lines[i].trimEnd();
    if (OPEN_MARKERS.has(line)) {
      if (first === -1) first = i;
      outstanding.set(line, (outstanding.get(line) ?? 0) + 1);
      continue;
    }
    if (first === -1) continue; // a stray close ahead of every open — not our span
    const open = OPEN_BY_CLOSE.get(line);
    if (open === undefined) continue; // ordinary content
    last = i;
    const pending = outstanding.get(open) ?? 0;
    // pending === 0 → a close for a block that is not open: it balances nothing.
    if (pending > 0) outstanding.set(open, pending - 1);
  }
  if (first === -1) return text; // an inline mention or a stray close — nothing of ours
  let unbalanced = false;
  for (const pending of outstanding.values()) {
    if (pending > 0) {
      unbalanced = true;
      break;
    }
  }
  const head = lines.slice(0, first);
  // unbalanced (or no close at all) → elide to the end rather than leak
  const tail = unbalanced || last === -1 ? [] : lines.slice(last + 1);
  while (head.length && head[head.length - 1].trim() === "") head.pop();
  while (tail.length && tail[0].trim() === "") tail.shift();
  const healed = head.length && tail.length ? [...head, "", ...tail] : [...head, ...tail];
  return healed.join("\n").trim();
}

/** Minimal structural view of a Pi conversation message. */
interface ContextMessage {
  role?: string;
  content?: string | { type?: string; text?: string }[];
}

function stripMessage(message: ContextMessage): ContextMessage {
  const content = message.content;
  if (typeof content === "string") {
    const stripped = stripContextBlocks(content);
    return stripped === content ? message : { ...message, content: stripped };
  }
  if (!Array.isArray(content)) return message;
  let changed = false;
  const parts = content.map((part) => {
    if (part?.type !== "text" || typeof part.text !== "string") return part;
    const stripped = stripContextBlocks(part.text);
    if (stripped === part.text) return part;
    changed = true;
    return { ...part, text: stripped };
  });
  return changed ? { ...message, content: parts } : message;
}

/**
 * The per-request view with every context block except the newest copy removed.
 *
 * The LAST user message keeps ALL of its blocks — that is this turn's context, and
 * the memory block sits ahead of the user's own words where it was measured to
 * belong. Every older user message is superseded WHOLESALE and loses all four
 * blocks, keeping only the words the user actually said.
 *
 * Superseded is decided by POSITION, not per block type: a block missing from this
 * turn is missing because zoe-data chose not to supply it (no portrait for this
 * user, no recall hit), and reviving an older copy would be exactly the stale-fact
 * bug in a new place. So each block type survives at most once, in the newest user
 * message, and never as a leftover from an earlier turn.
 */
export function stripSupersededContext<T extends ContextMessage>(messages: readonly T[]): T[] {
  let newestUser = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i]?.role === "user") {
      newestUser = i;
      break;
    }
  }
  if (newestUser <= 0) return messages as T[]; // nothing is superseded yet
  let changed = false;
  const view = messages.map((message, i) => {
    if (i === newestUser || message?.role !== "user") return message;
    const stripped = stripMessage(message) as T;
    if (stripped !== message) changed = true;
    return stripped;
  });
  // Return the ORIGINAL array when nothing was superseded. Identity matters here:
  // "no memory this turn" must be a true no-op, so the KV prefix runs the whole
  // way instead of paying for an elision that removed nothing.
  return changed ? view : (messages as T[]);
}

export default function (pi: ExtensionAPI) {
  // Fired before EVERY LLM call, including each step of a tool loop — which is
  // correct: the newest user message is the same one throughout a turn, so its
  // blocks are kept and only genuinely older ones are dropped.
  pi.on("context", async (event) => {
    const messages = (event as { messages?: ContextMessage[] })?.messages;
    if (!Array.isArray(messages)) return;
    return { messages: stripSupersededContext(messages) as never };
  });

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
