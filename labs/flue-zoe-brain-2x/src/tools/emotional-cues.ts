/**
 * Emotional-weight cues for tool disclosure — read from zoe-data's DATA, never a second copy of the words.
 *
 * WHY (QA round 3b, 2026-11-07). The emotional-capture doctrine tells the brain to call remember_emotional_moment on a turn
 * with real emotional weight, but the tool was only disclosed when this sidecar's OWN memory regex matched — and it missed
 * everyday feeling phrasing ("had a fall", "I just keep worrying", "the embarrassment", "I feel guilty"). With no tool to
 * call, the brain SAID it would remember, and the write-claim guard then replaced that mid-disclosure with a list-failure
 * line. zoe-data decides what an emotional turn is (memory_gate.is_emotional_turn); this module reads the SAME cue file,
 * `services/zoe-data/lexicons_data/emotional_cues.json` (`recall_cues` + `weight_cues`: lower-case substrings, matched
 * after curly apostrophes are straightened), so both sides agree on the turns by construction.
 *
 * WHERE the file is found: `ZOE_EMOTIONAL_CUES_PATH`, else the first ancestor of this module (src/ in tests, dist/ when
 * built — the service runs from the repo checkout) holding `services/zoe-data/lexicons_data/emotional_cues.json`. Re-read
 * when its mtime changes (checked at most every `RECHECK_MS`), so a zoe-data deploy that edits the cues needs no sidecar
 * rebuild. Unreadable = no cues (warned once): disclosure falls back to the regex triggers, nothing throws.
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const REL = join('services', 'zoe-data', 'lexicons_data', 'emotional_cues.json');
const RECHECK_MS = 30_000;

type Cache = { path: string; mtimeMs: number; checkedAt: number; cues: readonly string[] };
let cache: Cache | undefined;
let warned = false;

function warnOnce(why: string): void {
  if (warned) return;
  warned = true;
  console.warn('EMOTIONAL_CUES unavailable (' + why + '); memory disclosure uses its regex only');
}

/** The cue file's path, or '' when none is found. */
export function cuesPath(): string {
  const env = (process.env.ZOE_EMOTIONAL_CUES_PATH ?? '').trim();
  if (env) return env;
  let dir = dirname(fileURLToPath(import.meta.url));
  for (let i = 0; i < 10; i++) {
    const candidate = join(dir, REL);
    if (existsSync(candidate)) return candidate;
    const up = dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return '';
}

/** Parse the cue file's text: every `recall_cues` and `weight_cues` string of the `en` block, lower-cased. */
export function parseCues(raw: string): string[] {
  const data = JSON.parse(raw) as { en?: { recall_cues?: unknown; weight_cues?: unknown } };
  const out: string[] = [];
  for (const list of [data?.en?.recall_cues, data?.en?.weight_cues]) {
    if (!Array.isArray(list)) continue;
    for (const cue of list) {
      const c = typeof cue === 'string' ? normalise(cue).trim() : '';
      if (c && !out.includes(c)) out.push(c);
    }
  }
  return out;
}

function normalise(text: string): string {
  return text.replace(/[‘’]/g, "'").toLowerCase();
}

/** The current cue list (cached; re-read when the file changes). */
export function emotionalCues(now: number = Date.now()): readonly string[] {
  if (cache && now - cache.checkedAt < RECHECK_MS) return cache.cues;
  const path = cuesPath();
  if (!path) {
    warnOnce('no emotional_cues.json found');
    cache = { path: '', mtimeMs: 0, checkedAt: now, cues: [] };
    return cache.cues;
  }
  try {
    const mtimeMs = statSync(path).mtimeMs;
    if (cache && cache.path === path && cache.mtimeMs === mtimeMs) {
      cache.checkedAt = now;
      return cache.cues;
    }
    cache = { path, mtimeMs, checkedAt: now, cues: parseCues(readFileSync(path, 'utf8')) };
  } catch (err) {
    warnOnce(err instanceof Error ? err.message : String(err));
    cache = { path, mtimeMs: 0, checkedAt: now, cues: cache?.cues ?? [] };
  }
  return cache.cues;
}

/** Does `text` carry emotional weight by zoe-data's cue data? */
export function hasEmotionalWeight(text: string): boolean {
  if (!text) return false;
  const low = normalise(text);
  return emotionalCues().some((cue) => low.includes(cue));
}

/** Tests: forget the cached file. */
export function resetEmotionalCuesCache(): void {
  cache = undefined;
  warned = false;
}
