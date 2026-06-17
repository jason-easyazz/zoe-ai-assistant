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

const CTX: AbilityContext = {
  zoeDataUrl: process.env.ZOE_DATA_URL ?? "http://127.0.0.1:8000",
  internalToken: process.env.ZOE_INTERNAL_TOKEN ?? "",
  userId: process.env.ZOE_CORE_USER_ID ?? "family-admin",
};

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
    files = readdirSync(ABILITIES_DIR).filter((f) => f.endsWith(".ts") && f !== "types.ts");
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
        const denied = permissionDenial(entry);
        if (denied) return { content: [{ type: "text", text: denied }] };
        if (entry.gate && !entry.gate(CTX)) {
          return { content: [{ type: "text", text: `${entry.name} is unavailable right now.` }] };
        }
        try {
          const out = await entry.execute(params, CTX);
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
    try {
      (pi as { setActiveTools?: (names: string[]) => void }).setActiveTools?.(active);
    } catch {
      /* setActiveTools is best-effort; if unavailable, all registered tools stay active */
    }
  });
}
