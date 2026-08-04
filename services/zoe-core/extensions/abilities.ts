/**
 * Brick 4 foundation: Zoe's capability registry.
 *
 * Auto-discovers `abilities/*.ts` (each default-exports CapabilityEntry[]),
 * registers them as Pi tools wrapped with permission-envelope enforcement, and
 * does PROGRESSIVE DISCLOSURE — only the always-on core plus recently-relevant
 * tools are active each turn, so a ~2B local model isn't drowned in 56 tools.
 *
 * Disclosure is MONOTONE over the retained window (see `nextActiveTools`): a
 * domain stays disclosed for `ZOE_CORE_DISCLOSURE_WINDOW_TURNS` turns after it
 * was last relevant, rather than being recomputed from the last message alone.
 * That keeps the rendered tool block byte-stable turn-to-turn, which is what
 * makes llama.cpp's exact-prefix KV reuse possible at all.
 *
 * Relevance is keyword/example based for now (deterministic, no embedder);
 * vector Tool-RAG is the documented upgrade. Domain tools are independent files
 * — adding one needs no edit here.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import type { AbilityContext, CapabilityEntry, Permission } from "../abilities/types";

const _here = dirname(fileURLToPath(import.meta.url));
const ABILITIES_DIR = join(_here, "..", "abilities");

// The acting user is resolved PER TURN (not baked at module load), so a single
// Pi process is never pinned to one identity. zoe-data drives one Pi session per
// user-conversation and sets ZOE_CORE_USER_ID for that session; we read it fresh
// each call. There is NO default user — if the identity is unknown, user-scoped
// tools fail closed (below) rather than silently acting as someone else.
function abilityContext(): AbilityContext {
  return {
    zoeDataUrl: process.env.ZOE_DATA_URL ?? "http://127.0.0.1:8000",
    internalToken: process.env.ZOE_INTERNAL_TOKEN ?? "",
    userId: (process.env.ZOE_CORE_USER_ID ?? "").trim(),
  };
}

// A tool touches the user's data/devices if it needs anything beyond read-only/
// network — those MUST have a known user. Pure-info tools (e.g. time/weather) do not.
const USER_SCOPED_PERMS: Permission[] = [
  "user-data:read",
  "user-data:write",
  "home-device:action",
  "credential:access",
];
function needsUser(entry: CapabilityEntry): boolean {
  return entry.permissions.some((p) => USER_SCOPED_PERMS.includes(p));
}

// Lab permission policy: reads are free; writes/device/credential/code need an
// explicit allow (stand-in for per-action human approval — see harness kernel).
const ALLOW_WRITES = (process.env.ZOE_CORE_ALLOW_WRITES ?? "false").toLowerCase() === "true";
const FREELY_ALLOWED: Permission[] = ["read-only", "user-data:read", "network"];

function permissionDenial(entry: CapabilityEntry): string | null {
  const escalations = entry.permissions.filter((p) => !FREELY_ALLOWED.includes(p));
  if (escalations.length === 0 || ALLOW_WRITES) return null;
  return `That needs permission not enabled here (${escalations.join(", ")}). Ask the user to confirm.`;
}

async function loadAbilities(): Promise<CapabilityEntry[]> {
  let files: string[] = [];
  try {
    files = readdirSync(ABILITIES_DIR).filter(
      (f) => f.endsWith(".ts") && f !== "types.ts" && !f.startsWith("_"),
    );
  } catch {
    return [];
  }
  const entries: CapabilityEntry[] = [];
  for (const file of files.sort()) {
    try {
      const mod = await import(pathToFileURL(join(ABILITIES_DIR, file)).href);
      const list: unknown = mod.default ?? mod.abilities;
      if (Array.isArray(list)) entries.push(...(list as CapabilityEntry[]));
    } catch (err) {
      console.warn(`[zoe-core/abilities] failed to load ${file}: ${(err as Error)?.message ?? err}`);
    }
  }
  return entries;
}

export function isRelevant(entry: CapabilityEntry, msg: string): boolean {
  if (entry.tier === "core") return true;
  if ((entry.triggers ?? []).some((re) => re.test(msg))) return true;
  const normalizedMsg = msg.replace(/[^a-z0-9 ]/g, "");
  return entry.examples.some((ex) => {
    const key = ex.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
    return key.length >= 4 && normalizedMsg.includes(key.slice(0, Math.min(key.length, 16)));
  });
}

// ── Monotone disclosure (KV-prefix stability) ────────────────────────────────
//
// Pi's setActiveToolsByName rebuilds the tool set handed to the provider, and the
// tool definitions are rendered into the front of the request by the chat
// template. llama.cpp reuses cached KV only for an EXACT common prefix (Gemma's
// shared-KV + SWA attention has no `--cache-reuse`), so a tool block that
// oscillates turn-to-turn re-prefills the entire conversation every turn.
//
// Matching on the LAST MESSAGE ONLY oscillated maximally: no ability currently
// declares `tier: "core"`, so an off-topic turn ("thanks!") disclosed ZERO tools
// and the next on-topic turn disclosed them again. The fix keeps a domain
// disclosed for a bounded window after it was last relevant, so the set is
// non-decreasing across the window and only ever changes in rare, bounded jumps
// — the same "anchor, don't slide" shape as the legacy lane's history pruning.
//
// Grouping is by `domain`, not by tool name: a domain is what the user's message
// is actually about, and disclosing a domain's tools together keeps the set
// stable when a domain later grows a second entry.

// Default 6 turns = zoe-data's retained window. `_compose_message` replays
// `history[-12:]` — 12 messages, i.e. ~6 user turns — so a domain stays disclosed
// for exactly as long as the turn that raised it is still visible to the model.
const DEFAULT_DISCLOSURE_WINDOW_TURNS = 6;

export function disclosureWindowTurns(): number {
  const raw = Number(process.env.ZOE_CORE_DISCLOSURE_WINDOW_TURNS);
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : DEFAULT_DISCLOSURE_WINDOW_TURNS;
}

// `event.prompt` is NOT the user's utterance — it is the whole composed prompt
// zoe-data's `_compose_message` sends: portrait, memory directive + packet,
// `[Recent conversation]` (the replayed history[-12:]), then the utterance.
// Verified live by instrumenting this handler and capturing what arrives.
//
// Matching relevance against all of that re-armed a domain on EVERY turn as long
// as one keyword sat anywhere in the retained window — history the user is no
// longer talking about kept `lastRelevantTurn` fresh, so the window below could
// never decay and tools stayed disclosed indefinitely. That defeats the whole
// point of progressive disclosure on a ~2B model.
//
// The seam therefore introduces the user's turn with this marker, and disclosure
// matches only what follows it. Kept byte-for-byte in sync with `_UTTERANCE_MARKER`
// in services/zoe-data/zoe_core_client.py (pinned by a test).
export const UTTERANCE_MARKER = "[The user just said]";

/**
 * The latest user utterance out of a composed prompt.
 *
 * Falls back to the whole prompt when the marker is absent — a STANDALONE `pi`
 * run, or a seam turn with no context blocks at all, where the prompt IS the
 * utterance. Splits on the LAST occurrence so a user who types the marker
 * themselves can only narrow their own text, never reach back into history.
 */
export function latestUtterance(prompt: string): string {
  const needle = `${UTTERANCE_MARKER}\n`;
  const at = prompt.lastIndexOf(needle);
  return at === -1 ? prompt : prompt.slice(at + needle.length);
}

// ── Seeding a FRESH state from the replayed history ──────────────────────────
//
// `createDisclosureState()` starts empty, but a Pi worker is not immortal: it is
// restarted and LRU-evicted, and zoe-data keeps replaying `history[-12:]` into the
// composed prompt across that boundary. So the model can see "add a meeting
// tomorrow" in the retained window while this extension has no record of it, and a
// context-dependent continuation on the very next turn — "yes, do that" — matches
// no domain and gets NO tools. Before the marker scoping above, whole-prompt
// matching covered that case by accident; the accident is gone, so the fix has to
// be deliberate.
//
// The seed replays those turns ONCE, at session start, exactly as if they had been
// observed live: turn i of the replayed history is credited as turn i, and
// `state.turn` is left at the count, so the ordinary decay window applies to them
// unchanged from the first live turn onward.
//
// WHAT COUNTS, and why it is this narrow:
//
//   * ONLY the `[Recent conversation]` block — never the portrait, the recall
//     context or the memory packet. Those are things Zoe knows ABOUT the user, not
//     things the user asked for, and matching them is the round-two bug verbatim:
//     one keyword in a memory would arm a domain the user has never mentioned.
//   * ONLY `user:` lines inside it (and their continuation lines). An assistant
//     line is Zoe's own text — "I can add that to your calendar" would otherwise
//     arm `calendar` off her own offer rather than the user's request.
//   * ONCE. `state.seeded` makes this a session-start event, not a per-turn scan.
//     Re-running it every turn would refresh `lastRelevantTurn` from history that
//     keeps replaying, and the bounded window could never decay — again, exactly
//     the round-two bug.
//
// The block label is composition-owned and `zoe_core_client._neutralize_markers`
// escapes it in content, so the anchor cannot be forged by a stored memory. Kept
// byte-for-byte in sync with `_HISTORY_LABEL` there (pinned by a test).
export const HISTORY_MARKER = "[Recent conversation]";

/** `role: text` opens a replayed turn; anything else continues the current one. */
const REPLAYED_ROLE_RE = /^([A-Za-z][A-Za-z0-9_-]*): ?/;

/**
 * The replayed-history block of a composed prompt, or "" when there is none.
 *
 * Anchored between the LAST history label and the LAST utterance marker: the seam
 * composes the history immediately before the user's turn, so everything in that
 * span is history and everything before it is context Zoe supplied. With no
 * utterance marker there is no seam composition at all (a standalone `pi` run), so
 * there is no replayed history either.
 */
export function replayedHistory(prompt: string): string {
  const utteranceAt = prompt.lastIndexOf(`${UTTERANCE_MARKER}\n`);
  if (utteranceAt === -1) return "";
  const context = prompt.slice(0, utteranceAt);
  const needle = `${HISTORY_MARKER}\n`;
  const at = context.lastIndexOf(needle);
  return at === -1 ? "" : context.slice(at + needle.length);
}

/** The USER utterances inside the replayed history, oldest first. */
export function replayedUserTurns(prompt: string): string[] {
  const block = replayedHistory(prompt);
  if (!block) return [];
  const turns: string[] = [];
  let inUserTurn = false;
  for (const line of block.split("\n")) {
    const match = REPLAYED_ROLE_RE.exec(line);
    if (match) {
      inUserTurn = match[1].toLowerCase() === "user";
      if (inUserTurn) turns.push(line.slice(match[0].length));
      continue;
    }
    // A continuation line of a multi-line message — it belongs to whoever spoke.
    if (inUserTurn && turns.length) turns[turns.length - 1] += `\n${line}`;
  }
  return turns.filter((t) => t.trim() !== "");
}

/** Per-session disclosure memory: which domain was last relevant, and when. */
export interface DisclosureState {
  turn: number;
  lastRelevantTurn: Map<string, number>;
  /** Whether the replayed history has already been folded in (session start only). */
  seeded: boolean;
}

export function createDisclosureState(): DisclosureState {
  return { turn: 0, lastRelevantTurn: new Map(), seeded: false };
}

/**
 * Fold the replayed history into a fresh state — ONCE, at session start.
 *
 * A no-op on an already-seeded state, and on a prompt with no replayed history
 * (a first-ever conversation, or a standalone `pi` run), which leaves the empty
 * state exactly as it was. See the note above for what counts and why.
 */
export function seedDisclosureState(
  abilities: CapabilityEntry[],
  prompt: string,
  state: DisclosureState,
): DisclosureState {
  if (state.seeded) return state;
  state.seeded = true;
  const turns = replayedUserTurns(prompt);
  for (let i = 0; i < turns.length; i++) {
    const msg = turns[i].toLowerCase();
    for (const entry of abilities) {
      // `core` is disclosed unconditionally, so seeding it would say nothing.
      if (entry.tier !== "core" && isRelevant(entry, msg)) {
        state.lastRelevantTurn.set(entry.domain, i + 1);
      }
    }
  }
  // Credit the replayed turns as elapsed, so the FIRST live turn is turns.length+1
  // and the decay window measures from there — identical to having seen them live.
  state.turn = turns.length;
  return state;
}

/**
 * The tools to disclose this turn: every `core` ability, plus every ability whose
 * DOMAIN was relevant within the last `windowTurns` turns (this one included).
 *
 * Order follows the ability load order, so an unchanged set always renders the
 * same bytes. Mutates `state` — one state object per Pi session.
 */
export function nextActiveTools(
  abilities: CapabilityEntry[],
  msg: string,
  state: DisclosureState,
  windowTurns: number = disclosureWindowTurns(),
): string[] {
  state.turn += 1;
  for (const entry of abilities) {
    if (isRelevant(entry, msg)) state.lastRelevantTurn.set(entry.domain, state.turn);
  }
  const cutoff = state.turn - windowTurns;
  const names: string[] = [];
  for (const entry of abilities) {
    if (entry.tier === "core") {
      names.push(entry.name);
      continue;
    }
    const last = state.lastRelevantTurn.get(entry.domain);
    if (last !== undefined && last > cutoff) names.push(entry.name);
  }
  return names;
}

/**
 * The per-turn disclosure handler.
 *
 * Split out from the extension entrypoint so it is testable without loading the
 * ability modules (which pull typebox at runtime).
 *
 * SAFETY FLOOR: this calls setActiveTools on EVERY turn, unconditionally, even
 * when the set is unchanged. That call is what strips Pi's coding builtins
 * (bash/read/edit) — the active set is only ever Zoe's own abilities. Never
 * short-circuit it as a "nothing changed" optimisation; skipping it is how the
 * builtins would come back.
 */
export function createDisclosureHandler(pi: ExtensionAPI, abilities: CapabilityEntry[]) {
  const state = createDisclosureState();
  return async (event: unknown) => {
    const composed = String((event as { prompt?: unknown })?.prompt ?? "");
    // ONCE per session, before anything else: fold in the history zoe-data is
    // replaying, so a worker that was restarted or LRU-evicted mid-conversation
    // does not answer a continuation ("yes, do that") with no domain disclosed.
    // Guarded by `state.seeded` — a per-turn rescan would re-arm from replayed
    // history forever and the window could never decay.
    seedDisclosureState(abilities, composed, state);
    // Scope to the latest utterance — see UTTERANCE_MARKER. Matching the whole
    // composed prompt keeps every domain in the retained window permanently armed.
    const msg = latestUtterance(composed).toLowerCase();
    const active = nextActiveTools(abilities, msg, state);
    const setActiveTools = (pi as { setActiveTools?: (names: string[]) => void }).setActiveTools;
    if (typeof setActiveTools !== "function") {
      // Observability: if the Pi build lacks setActiveTools, progressive
      // disclosure is a no-op (ALL tools stay active) — surface it once so a
      // 2B model getting drowned in tools is diagnosable, not silent.
      if (!(globalThis as Record<string, unknown>).__zoeAbilitiesDisclosureWarned) {
        (globalThis as Record<string, unknown>).__zoeAbilitiesDisclosureWarned = true;
        console.warn(
          "[zoe-core/abilities] setActiveTools unavailable — progressive disclosure disabled; all tools stay active.",
        );
      }
      return;
    }
    try {
      setActiveTools(active);
    } catch (err) {
      console.warn(
        `[zoe-core/abilities] setActiveTools failed (${active.length} tools intended): ${(err as Error)?.message ?? err}`,
      );
    }
  };
}

export default async function (pi: ExtensionAPI) {
  const abilities = await loadAbilities();

  for (const entry of abilities) {
    pi.registerTool({
      name: entry.name,
      label: entry.name,
      description: entry.description,
      parameters: entry.parameters,
      async execute(_toolCallId: string, params: Record<string, unknown>) {
        const ctx = abilityContext();
        // Fail closed: a user-scoped tool with no known acting user must NOT run
        // (never silently act as a default identity).
        if (needsUser(entry) && !ctx.userId) {
          return {
            content: [
              { type: "text", text: "I'm not sure whose account I'd be acting on, so I can't do that safely right now." },
            ],
          };
        }
        const denied = permissionDenial(entry);
        if (denied) return { content: [{ type: "text", text: denied }] };
        if (entry.gate && !entry.gate(ctx)) {
          return { content: [{ type: "text", text: `${entry.name} is unavailable right now.` }] };
        }
        try {
          const out = await entry.execute(params, ctx);
          return { content: [{ type: "text", text: String(out) }] };
        } catch (err) {
          return { content: [{ type: "text", text: `${entry.name} failed: ${(err as Error)?.message ?? err}` }] };
        }
      },
    });
  }

  // Progressive disclosure — core + every domain relevant within the retained
  // window (monotone, so the rendered tool block stays KV-prefix stable).
  pi.on("before_agent_start", createDisclosureHandler(pi, abilities));
}
