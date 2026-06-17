/**
 * Brick 2: Zoe's soul.
 *
 * Replaces Pi's default coding-assistant system prompt with Zoe's persona, so
 * the core speaks and behaves as Zoe rather than a generic coding agent. The
 * persona text lives in SOUL.md (editable/versioned); operational and tool
 * guidance is composed on top of this persona in a later brick.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const _here = dirname(fileURLToPath(import.meta.url));
const SOUL_PATH = process.env.ZOE_CORE_SOUL_PATH ?? join(_here, "..", "SOUL.md");

const _FALLBACK_SOUL =
  "You are Zoe — warm, curious, and genuinely present. Speak naturally, in your own voice; you're someone who cares about the people you talk with, not a task executor.";

function loadSoul(): string {
  try {
    const text = readFileSync(SOUL_PATH, "utf8").trim();
    if (text) return text;
  } catch {
    // fall through to the inline persona below
  }
  return _FALLBACK_SOUL;
}

export default function (pi: ExtensionAPI) {
  const soul = loadSoul();
  pi.on("before_agent_start", async () => {
    // Make Zoe's persona the system prompt for every turn.
    return { systemPrompt: soul };
  });
}
