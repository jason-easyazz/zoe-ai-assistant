/**
 * Phase 3, increment 1 — give the Flue Zoe brain a few REAL tools.
 *
 * These are Flue `defineTool` tools that call zoe-data's EXISTING internal
 * capability endpoints over HTTP — the same seam the production Pi brain uses
 * (services/zoe-core/extensions/memory.ts + abilities/_dispatch.ts):
 *
 *   - get_time          → answered locally (mirrors abilities/info.ts time/date)
 *   - recall_memory     → GET  /api/memories/for-prompt   (memory.ts)
 *   - shopping_list_add → POST /api/system/intent-dispatch (list_add intent)
 *
 * SECURITY — identity is bound in TRUSTED CODE, never from model args. Tool
 * arguments are model-chosen and are NOT an auth boundary, so the acting
 * `user_id` is read from the env (ZOE_BRAIN_USER_ID), exactly as prod resolves
 * the acting user per session (abilities.ts reads ZOE_CORE_USER_ID). A tool that
 * needs a user FAILS CLOSED when no user is configured rather than acting as a
 * default identity. The model may only choose the *content* (the item text, the
 * recall query) — never *whose* data it touches.
 *
 * LAB ONLY — read-ish by default. shopping_list_add is the one writer; it is
 * gated behind ZOE_BRAIN_ALLOW_WRITES (default OFF → dry-run, so a parity run
 * doesn't mutate Jason's real list). See parity/RESULTS.md.
 */
import { defineTool } from '@flue/runtime';
import * as v from 'valibot';

// zoe-data base URL — the live capability backend. Overridable for the lab.
// Defaults to the live local endpoint (same default as prod's ZOE_DATA_URL).
const ZOE_DATA_URL = process.env.ZOE_DATA_URL ?? 'http://127.0.0.1:8000';
const INTERNAL_TOKEN = process.env.ZOE_INTERNAL_TOKEN ?? '';
const HTTP_TIMEOUT_MS = Number(process.env.ZOE_BRAIN_TOOL_TIMEOUT_MS ?? 8000);

// Writes are off by default in the lab: shopping_list_add becomes a DRY-RUN
// unless explicitly enabled, so the parity harness can't mutate the real list.
const ALLOW_WRITES =
  (process.env.ZOE_BRAIN_ALLOW_WRITES ?? 'false').toLowerCase() === 'true';

// The acting user, bound in trusted code (env), NOT from model args. Read fresh
// each call so a single process is never pinned to one identity at module load.
function actingUserId(): string {
  return (process.env.ZOE_BRAIN_USER_ID ?? '').trim();
}

function internalHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { Accept: 'application/json', ...extra };
  if (INTERNAL_TOKEN) headers['X-Internal-Token'] = INTERNAL_TOKEN;
  return headers;
}

/**
 * get_time — current local time and date.
 *
 * Mirrors prod's abilities/info.ts (time/date answer locally, no network). The
 * Flue server runs on the same host as the brain, so the host clock is correct.
 */
const getTime = defineTool({
  name: 'get_time',
  description:
    "Get the current time and date. Use when the user asks what time it is, " +
    "what today's date or day is, or anything that needs the current wall clock.",
  // No input: the time is not user-parameterised.
  run: async () => {
    const now = new Date();
    const time = now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    const date = now.toLocaleDateString(undefined, {
      weekday: 'long',
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
    return `It's ${time} on ${date}.`;
  },
});

/**
 * recall_memory — pull Zoe's compact, cited memory packet for the user.
 *
 * Calls zoe-data's internal GET /api/memories/for-prompt (same endpoint the prod
 * memory.ts extension injects every turn). Returns the packet text the model can
 * use to answer "what do you remember about me" style questions. Fails OPEN to a
 * calm message — memory is best-effort and must never break a turn.
 */
const recallMemory = defineTool({
  name: 'recall_memory',
  description:
    "Recall what Zoe knows about the user — their stored facts, preferences, and " +
    "context. Use when the user asks what you remember about them, or when " +
    "personal context would help you answer well.",
  input: v.object({
    query: v.optional(
      v.string(),
      // Optional relevance hint — the topic to rank memories against.
    ),
  }),
  run: async ({ input, signal }) => {
    const userId = actingUserId();
    if (!userId) {
      // Fail closed: no known user → don't leak a default user's memories.
      return "I'm not sure whose memories I'd be recalling, so I can't do that safely right now.";
    }
    try {
      const url = new URL('/api/memories/for-prompt', ZOE_DATA_URL);
      url.searchParams.set('user_id', userId);
      const query = String(input?.query ?? '').trim();
      if (query) url.searchParams.set('message', query.slice(0, 500));
      const res = await fetch(url, {
        headers: internalHeaders(),
        signal: signal ?? AbortSignal.timeout(HTTP_TIMEOUT_MS),
      });
      if (!res.ok) return "I couldn't reach my memory right now.";
      const data = (await res.json()) as { packet?: string };
      const packet = (data.packet ?? '').trim();
      return packet || "I don't have anything stored about that yet.";
    } catch {
      return "I couldn't reach my memory right now.";
    }
  },
});

/**
 * shopping_list_add — add an item to the user's shopping list.
 *
 * Calls zoe-data's internal POST /api/system/intent-dispatch with the allowlisted
 * `list_add` intent — the exact fulfilment path prod's abilities/lists.ts uses
 * via _dispatch.ts. The user_id is bound from env (trusted), only the item text
 * comes from the model.
 *
 * WRITE: gated behind ZOE_BRAIN_ALLOW_WRITES. Default OFF → returns a dry-run
 * acknowledgement WITHOUT calling zoe-data, so a parity run never mutates the
 * real list. Set ZOE_BRAIN_ALLOW_WRITES=true to actually write.
 */
const shoppingListAdd = defineTool({
  name: 'shopping_list_add',
  description:
    "Add an item to the user's shopping list. Use when the user asks to add, put, " +
    "or note something on their shopping/grocery list.",
  input: v.object({
    item: v.pipe(v.string(), v.trim(), v.minLength(1)),
  }),
  run: async ({ input, signal }) => {
    const userId = actingUserId();
    if (!userId) {
      return "I'm not sure whose list I'd be adding to, so I can't do that safely right now.";
    }
    const item = String(input.item ?? '').trim();
    if (!item) return 'I need to know which item to add to the shopping list.';

    if (!ALLOW_WRITES) {
      // Lab default: DRY-RUN. Acknowledge without mutating the real list.
      return `(dry-run) I'd add "${item}" to your shopping list. ` +
        `Writes are disabled in the lab (set ZOE_BRAIN_ALLOW_WRITES=true to enable).`;
    }

    try {
      const url = new URL('/api/system/intent-dispatch', ZOE_DATA_URL);
      const res = await fetch(url, {
        method: 'POST',
        headers: internalHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          user_id: userId,
          intent: 'list_add',
          slots: { item, list_type: 'shopping' },
        }),
        signal: signal ?? AbortSignal.timeout(HTTP_TIMEOUT_MS),
      });
      if (!res.ok) {
        return `I couldn't add that to your list right now (the list service returned ${res.status}).`;
      }
      const data = (await res.json()) as { result?: string };
      return (data.result ?? '').trim() || `Added "${item}" to your shopping list.`;
    } catch {
      return "I couldn't reach the list service right now.";
    }
  },
});

/** All Zoe-brain tools, wired onto the agent in src/agents/zoe.ts. */
export const zoeTools = [getTime, recallMemory, shoppingListAdd];
