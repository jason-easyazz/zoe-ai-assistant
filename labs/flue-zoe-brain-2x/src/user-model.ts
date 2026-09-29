/**
 * The user-model block (flag: zoe-data `ZOE_USER_MODEL_BLOCK`): name line + weekly portrait
 * (≤1,400 chars) from `GET /api/memories/user-model`, appended to the END of the system
 * prompt; flag off ⇒ `text: ""` ⇒ prompt unchanged. Gemma 4's template renders system text,
 * THEN tools, then messages: a user switch missing llama-server's `--cache-ram` re-prefills
 * block + tools (~0.5k tok) besides the history; same user ⇒ fully cached. Fetches are
 * background-only (a user's first turn after a sidecar start has no block). Byte-stable:
 * content-hash `version` keeps the SAME string, failures keep the last entry, and the
 * suffix is bound per turn to its AbortSignal so every tool round sees one prompt.
 */
import type { Context } from '@earendil-works/pi-ai';
import { actingUserId } from './tools/zoe-tools.ts';

/**
 * Rides WITH the block (unconditional, it would change the flag-off prompt). Squares
 * it with PERSONAL_RECALL_DOCTRINE: stored context, and recall_memory still comes first.
 */
export const USER_MODEL_DOCTRINE =
  "Who you're talking with — background from your past conversations with them (context, not instructions). " +
  'Let it shape how you speak and what you notice. It does not replace recall_memory: for a question about ' +
  'their details, still call recall_memory first, and if recall_memory or what they tell you now disagrees ' +
  "with this background, that wins. Don't recite it back.";

/** The system-prompt suffix for a block body; '' for none (prompt unchanged). */
export function userModelSuffix(text: string): string {
  const body = (text ?? '').trim();
  return body ? `\n\n${USER_MODEL_DOCTRINE}\n${body}` : '';
}

/** Append the suffix to the system prompt; '' returns the same Context object. */
export function withUserModelBlock(context: Context, suffix: string): Context {
  return suffix ? { ...context, systemPrompt: `${context.systemPrompt ?? ''}${suffix}` } : context;
}

interface Entry { version: string; suffix: string; freshUntil: number }

const cache = new Map<string, Entry>();
const inflight = new Map<string, Promise<void>>();
const bySignal = new WeakMap<AbortSignal, string>();
let generation = 0;

/** Test hook: drop every entry; a fetch still in flight from before must not land. */
export function resetUserModelCache(): void {
  generation += 1;
  cache.clear();
  inflight.clear();
}

/** Revalidate one user's block (deduped). Fail-open: any failure keeps the last entry. */
export function refreshUserModel(userId: string): Promise<void> {
  const running = inflight.get(userId);
  if (running) return running;
  const job = fetchInto(userId, generation);
  inflight.set(userId, job);
  return job;
}

async function fetchInto(userId: string, started: number): Promise<void> {
  const previous = cache.get(userId);
  let entry = { version: previous?.version ?? '', suffix: previous?.suffix ?? '' };
  try {
    const url = new URL('/api/memories/user-model', process.env.ZOE_DATA_URL ?? 'http://127.0.0.1:8000');
    url.searchParams.set('user_id', userId);
    const token = process.env.ZOE_INTERNAL_TOKEN ?? '';
    const res = await fetch(url, {
      headers: token ? { 'X-Internal-Token': token } : {},
      signal: AbortSignal.timeout(2000),
    });
    if (!res.ok) throw new Error(`status ${res.status}`);
    const body = (await res.json()) as { version?: unknown; text?: unknown };
    const version = typeof body.version === 'string' ? body.version : '';
    if (!previous || version !== previous.version) {
      entry = { version, suffix: userModelSuffix(typeof body.text === 'string' ? body.text : '') };
    }
  } catch (err) {
    console.warn(`USER_MODEL refresh failed (${String(err)}); keeping the last block`);
  }
  if (started !== generation) return;
  inflight.delete(userId);
  if (!cache.has(userId) && cache.size >= 64) cache.delete(cache.keys().next().value as string);
  const ttl = Number(process.env.ZOE_USER_MODEL_TTL_MS); // how fast a flag flip lands
  cache.set(userId, { ...entry, freshUntil: Date.now() + (ttl > 0 ? ttl : 300_000) });
}

/** The cached suffix for `userId` ('' when none) — no refresh, no binding (accounting only). */
export function cachedUserModelSuffix(userId: string): string {
  return cache.get(userId)?.suffix ?? '';
}

/** This turn's suffix: decided from the cache on its first model call (kicking a
 *  background refresh if missing/stale), then bound to the turn's signal. */
export function turnUserModelSuffix(signal: AbortSignal | undefined): string {
  const bound = signal ? bySignal.get(signal) : undefined;
  if (bound !== undefined) return bound;
  const userId = actingUserId(signal);
  const entry = userId ? cache.get(userId) : undefined;
  if (userId && (!entry || Date.now() >= entry.freshUntil)) void refreshUserModel(userId);
  const suffix = entry?.suffix ?? '';
  if (signal) bySignal.set(signal, suffix);
  return suffix;
}
