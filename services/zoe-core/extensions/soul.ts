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
    console.warn(
      `[zoe-core/soul] SOUL.md at ${SOUL_PATH} is empty; using inline persona fallback.`,
    );
  } catch (err) {
    // Don't degrade Zoe's identity silently — surface why the persona file
    // couldn't be loaded so a misconfigured deploy is diagnosable.
    console.warn(
      `[zoe-core/soul] could not read SOUL.md at ${SOUL_PATH}; using inline persona fallback: ${
        (err as Error)?.message ?? err
      }`,
    );
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
