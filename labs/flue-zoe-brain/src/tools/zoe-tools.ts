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
 * Phase 3, increment 2 — next batch of high-value abilities, each mapping to an
 * intent in zoe-data's _DISPATCHABLE_INTENTS allowlist (routers/system.py). Slot
 * shapes mirror the prod abilities (services/zoe-core/abilities/*.ts):
 *
 *   reads  (no write gate):
 *     - get_weather    → weather      {forecast}
 *     - list_reminders → reminder_list {}
 *     - show_calendar  → calendar_show {qualifier}
 *     - show_list      → list_show     {list_type}
 *   writes (gated behind ZOE_BRAIN_ALLOW_WRITES, dry-run by default):
 *     - set_timer          → timer_create    {minutes,label}
 *     - add_reminder       → reminder_create {title,date?,time?}
 *     - add_calendar_event → calendar_create {title,date?,time?,category?}
 *     - create_note        → note_create     {title,content}
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
// Validate the timeout: Number('') is 0 and Number('abc') is NaN, either of which
// would make AbortSignal.timeout() abort every call immediately. Fall back to 8s
// for any non-positive / non-finite value.
const HTTP_TIMEOUT_MS = (() => {
  const n = Number(process.env.ZOE_BRAIN_TOOL_TIMEOUT_MS);
  return Number.isFinite(n) && n > 0 ? n : 8000;
})();

// Identities that zoe-data accepts but that mean "not a real user" — treat as no
// user so a tool fails closed instead of silently returning an empty packet.
const GUEST_IDENTITIES = new Set(['guest', 'anonymous', 'anon', 'unknown', 'none']);

// Writes are off by default in the lab: shopping_list_add becomes a DRY-RUN
// unless explicitly enabled, so the parity harness can't mutate the real list.
const ALLOW_WRITES =
  (process.env.ZOE_BRAIN_ALLOW_WRITES ?? 'false').toLowerCase() === 'true';

// The acting user, bound in trusted code (env), NOT from model args. Read fresh
// each call so a single process is never pinned to one identity at module load.
function actingUserId(): string {
  const id = (process.env.ZOE_BRAIN_USER_ID ?? '').trim();
  // Fail closed on guest-style identities: zoe-data returns a *successful* empty
  // packet for them, which would otherwise look like "nothing stored" and hide an
  // invalid acting identity.
  if (!id || GUEST_IDENTITIES.has(id.toLowerCase())) return '';
  return id;
}

function internalHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { Accept: 'application/json', ...extra };
  if (INTERNAL_TOKEN) headers['X-Internal-Token'] = INTERNAL_TOKEN;
  return headers;
}

// Shape of the intent-dispatch response (routers/system.py: ok = result is not None).
type DispatchResponse = { intent?: string; ok?: boolean; result?: string };

/**
 * Run one allowlisted intent via POST /api/system/intent-dispatch — the shared
 * mechanic behind every dispatch tool below (mirrors abilities/_dispatch.ts).
 *
 * The acting user_id is bound from env in trusted code (never model args). The
 * caller supplies only the intent name + content slots. Returns a discriminated
 * result so each tool can phrase success/failure in its own voice and NEVER
 * claim success on a non-confirming response (anything but an explicit ok === true,
 * or a transport error).
 *
 * `service` is a short noun for user-facing error text ("reminder", "calendar").
 */
async function dispatchIntent(
  intent: string,
  slots: Record<string, unknown>,
  service: string,
  signal?: AbortSignal,
): Promise<{ ok: true; text: string } | { ok: false; text: string }> {
  const userId = actingUserId();
  if (!userId) {
    return { ok: false, text: "I'm not sure whose data this would touch, so I can't do that safely right now." };
  }
  try {
    const url = new URL('/api/system/intent-dispatch', ZOE_DATA_URL);
    const res = await fetch(url, {
      method: 'POST',
      headers: internalHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ user_id: userId, intent, slots }),
      signal: signal ?? AbortSignal.timeout(HTTP_TIMEOUT_MS),
    });
    if (!res.ok) {
      return { ok: false, text: `I couldn't reach the ${service} service right now (it returned ${res.status}).` };
    }
    const data = (await res.json()) as DispatchResponse;
    // Require an EXPLICIT positive confirmation. zoe-data's /api/system/intent-dispatch
    // returns { intent, ok: (result is not None), result } (routers/system.py), so a
    // genuine success is ALWAYS ok === true. Anything else on a 200 — ok:false, ok
    // missing/undefined, a non-boolean ok, an empty/garbled body — is NOT a
    // confirmation. Fail closed with a non-confirming line rather than fabricate a
    // "done" reply (e.g. successFallback) the backend never actually confirmed.
    if (data.ok !== true) {
      return { ok: false, text: `I couldn't confirm that went through with the ${service} service.` };
    }
    return { ok: true, text: (data.result ?? '').trim() };
  } catch {
    return { ok: false, text: `I couldn't reach the ${service} service right now.` };
  }
}

// A write tool's run() body: returns a dry-run notice when writes are disabled,
// otherwise dispatches the intent. `dryRunItem` is the human label shown in the
// dry-run line; `slots` is built by the caller (content only, never identity).
async function runWrite(
  intent: string,
  slots: Record<string, unknown>,
  service: string,
  dryRunItem: string,
  successFallback: string,
  signal?: AbortSignal,
): Promise<string> {
  if (!ALLOW_WRITES) {
    return `WRITE DISABLED — ${dryRunItem} was NOT saved (this is a lab build; set ` +
      `ZOE_BRAIN_ALLOW_WRITES=true to enable writes). Tell the user you can't do that yet — ` +
      `do NOT claim it was done.`;
  }
  const out = await dispatchIntent(intent, slots, service, signal);
  if (!out.ok) return out.text;
  return out.text || successFallback;
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
    "Recall what Zoe knows about the user — their stored name, facts, preferences, " +
    "relationships, and context. This is your ONLY source of what's actually stored " +
    "about the user; you do NOT know it on your own. You MUST call this tool BEFORE " +
    "you ever say you remember, know, or DON'T remember/know anything about the user. " +
    "Never answer \"I don't remember\", \"I don't have anything stored\", or \"I don't " +
    "know that about you\" without calling recall_memory first — calling it is the only " +
    "way to find out. Use it whenever the user asks what you know/remember about them, " +
    "their name, their preferences, or whenever personal context would help.",
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
    const item = String(input.item ?? '').trim();
    if (!item) return 'I need to know which item to add to the shopping list.';
    return runWrite(
      'list_add',
      { item, list_type: 'shopping' },
      'list',
      `"${item}"`,
      `Added "${item}" to your shopping list.`,
      signal,
    );
  },
});

// ─── Phase 3 increment 2: next batch ──────────────────────────────────────────

// Canonical lists the prod abilities accept (services/zoe-core/abilities/lists.ts).
const LIST_ALIASES: Record<string, string> = {
  grocery: 'shopping', groceries: 'shopping',
  'to do': 'tasks', 'to-do': 'tasks', todo: 'tasks', todos: 'tasks', task: 'tasks',
};
const CANONICAL_LISTS = new Set(['shopping', 'personal', 'work', 'bucket', 'tasks']);
function normalizeListType(raw: unknown): string {
  const s = String(raw ?? '').trim().toLowerCase();
  if (LIST_ALIASES[s]) return LIST_ALIASES[s];
  return CANONICAL_LISTS.has(s) ? s : 'shopping';
}

/**
 * get_weather — current conditions (or short forecast) for the user's area.
 * READ → weather intent {forecast}. Fails OPEN to a calm line.
 */
const getWeather = defineTool({
  name: 'get_weather',
  description:
    "Get the current weather or a short forecast for the user's area. Use when they " +
    'ask about weather, temperature, rain, or whether to bring a jacket/umbrella.',
  input: v.object({
    forecast: v.optional(v.boolean()),
  }),
  run: async ({ input, signal }) => {
    const out = await dispatchIntent('weather', { forecast: Boolean(input?.forecast) }, 'weather', signal);
    if (!out.ok) return out.text;
    return out.text || "I don't have the current weather right now.";
  },
});

/**
 * list_reminders — read the user's reminders. READ → reminder_list {}.
 */
const listReminders = defineTool({
  name: 'list_reminders',
  description:
    'List the user\'s reminders. Use when they ask what their reminders are or what ' +
    'they\'ve asked to be reminded about.',
  run: async ({ signal }) => {
    const out = await dispatchIntent('reminder_list', {}, 'reminder', signal);
    if (!out.ok) return out.text;
    return out.text || "You don't have any reminders set.";
  },
});

/**
 * show_calendar — read upcoming calendar events. READ → calendar_show {qualifier}.
 * qualifier: today | tomorrow | this week | this month | "" (upcoming).
 */
const showCalendar = defineTool({
  name: 'show_calendar',
  description:
    "Show what's on the user's calendar. Use when they ask about their schedule or " +
    'events for today, tomorrow, this week, this month, or upcoming.',
  input: v.object({
    qualifier: v.optional(v.string()),
  }),
  run: async ({ input, signal }) => {
    const qualifier = String(input?.qualifier ?? '').trim();
    const out = await dispatchIntent('calendar_show', { qualifier }, 'calendar', signal);
    if (!out.ok) return out.text;
    return out.text || "You don't have anything on your calendar then.";
  },
});

/**
 * show_list — read one of the user's lists. READ → list_show {list_type}.
 */
const showList = defineTool({
  name: 'show_list',
  description:
    "Show what's on one of the user's lists (shopping, tasks/to-do, personal, work, " +
    'bucket). Use when they ask what is on a list.',
  input: v.object({
    list_type: v.optional(v.string()),
  }),
  run: async ({ input, signal }) => {
    const listType = normalizeListType(input?.list_type);
    const out = await dispatchIntent('list_show', { list_type: listType }, 'list', signal);
    if (!out.ok) return out.text;
    return out.text || `Your ${listType} list is empty.`;
  },
});

/**
 * set_timer — start a countdown timer for N minutes. WRITE → timer_create
 * {minutes,label}. Gated behind ZOE_BRAIN_ALLOW_WRITES (dry-run by default).
 */
const setTimer = defineTool({
  name: 'set_timer',
  description:
    'Start a countdown timer for a number of minutes, optionally named ("set a 10 ' +
    'minute timer", "3 minute timer for the eggs"). For short durations from now — ' +
    'not clock-time reminders (use add_reminder for those).',
  input: v.object({
    minutes: v.pipe(v.number(), v.minValue(1)),
    label: v.optional(v.string()),
  }),
  run: async ({ input, signal }) => {
    const minutes = Math.round(input.minutes);
    const label = String(input?.label ?? '').trim() || 'Timer';
    const named = label.toLowerCase() === 'timer' ? '' : ` for ${label}`;
    return runWrite(
      'timer_create',
      { minutes, label },
      'timer',
      `a ${minutes} minute timer${named}`,
      `Starting a ${minutes} minute timer${named}.`,
      signal,
    );
  },
});

/**
 * add_reminder — create a reminder tied to a wall-clock time/date. WRITE →
 * reminder_create {title,date?,time?}. Gated behind ZOE_BRAIN_ALLOW_WRITES.
 */
const addReminder = defineTool({
  name: 'add_reminder',
  description:
    'Create a reminder tied to a time or date ("remind me to take the bins out at 7pm"). ' +
    'For a plain countdown use set_timer instead.',
  input: v.object({
    title: v.pipe(v.string(), v.trim(), v.minLength(1)),
    date: v.optional(v.string()),
    time: v.optional(v.string()),
  }),
  run: async ({ input, signal }) => {
    const title = String(input.title ?? '').trim();
    if (!title) return 'I need to know what to remind you about.';
    const slots: Record<string, unknown> = { title };
    const date = String(input?.date ?? '').trim();
    const time = String(input?.time ?? '').trim();
    if (date) slots.date = date;
    if (time) slots.time = time;
    return runWrite('reminder_create', slots, 'reminder', `a reminder to "${title}"`,
      `Okay, I'll remind you to ${title}.`, signal);
  },
});

/**
 * add_calendar_event — create a calendar event. WRITE → calendar_create
 * {title,date?,time?,category?}. Gated behind ZOE_BRAIN_ALLOW_WRITES.
 */
const addCalendarEvent = defineTool({
  name: 'add_calendar_event',
  description:
    "Add an event/appointment/meeting to the user's calendar (\"add dentist tomorrow " +
    'at 3pm", "schedule a meeting Friday at 10am"). For time-based reminders use ' +
    'add_reminder instead.',
  input: v.object({
    title: v.pipe(v.string(), v.trim(), v.minLength(1)),
    date: v.optional(v.string()),
    time: v.optional(v.string()),
    category: v.optional(v.string()),
  }),
  run: async ({ input, signal }) => {
    const title = String(input.title ?? '').trim();
    if (!title) return 'I need a title for the event. What should I call it?';
    const slots: Record<string, unknown> = { title };
    const date = String(input?.date ?? '').trim();
    const time = String(input?.time ?? '').trim();
    const category = String(input?.category ?? '').trim();
    if (date) slots.date = date;
    if (time) slots.time = time;
    if (category) slots.category = category;
    return runWrite('calendar_create', slots, 'calendar', `"${title}"`,
      `Added "${title}" to your calendar.`, signal);
  },
});

/**
 * create_note — save a free-form note. WRITE → note_create {title,content}.
 * Gated behind ZOE_BRAIN_ALLOW_WRITES.
 */
const createNote = defineTool({
  name: 'create_note',
  description:
    'Save a free-form note ("make a note: pick up dry cleaning Friday"). For diary/ ' +
    'reflective entries use the journal; for time-based prompts use add_reminder.',
  input: v.object({
    content: v.pipe(v.string(), v.trim(), v.minLength(1)),
    title: v.optional(v.string()),
  }),
  run: async ({ input, signal }) => {
    const content = String(input.content ?? '').trim();
    if (!content) return 'I need the text of the note before I can save it.';
    const title = String(input?.title ?? '').trim() || content.slice(0, 60);
    return runWrite('note_create', { title, content }, 'notes', 'that note',
      'Saved your note.', signal);
  },
});

/** All Zoe-brain tools, wired onto the agent in src/agents/zoe.ts. */
export const zoeTools = [
  // Phase 3 increment 1
  getTime,
  recallMemory,
  shoppingListAdd,
  // Phase 3 increment 2 — reads
  getWeather,
  listReminders,
  showCalendar,
  showList,
  // Phase 3 increment 2 — writes (gated behind ZOE_BRAIN_ALLOW_WRITES)
  setTimer,
  addReminder,
  addCalendarEvent,
  createNote,
];
