/**
 * Brick 4 foundation: Zoe's capability registry.
 *
 * Auto-discovers `abilities/*.ts` (each default-exports CapabilityEntry[]),
 * registers them as Pi tools wrapped with permission-envelope enforcement, and
 * does PROGRESSIVE DISCLOSURE — only the always-on core plus relevance-matched
 * tools are active each turn, so a ~2B local model isn't drowned in 56 tools.
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

function isRelevant(entry: CapabilityEntry, msg: string): boolean {
  if (entry.tier === "core") return true;
  if ((entry.triggers ?? []).some((re) => re.test(msg))) return true;
  const normalizedMsg = msg.replace(/[^a-z0-9 ]/g, "");
  return entry.examples.some((ex) => {
    const key = ex.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
    return key.length >= 4 && normalizedMsg.includes(key.slice(0, Math.min(key.length, 16)));
  });
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

  // Progressive disclosure — surface core + relevance-matched tools each turn.
  pi.on("before_agent_start", async (event) => {
    const msg = String((event as { prompt?: unknown })?.prompt ?? "").toLowerCase();
    const active = abilities.filter((a) => isRelevant(a, msg)).map((a) => a.name);
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
  });
}
