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
 * Wave 1 — "daily-driver" batch per the signed-off cut-list decision record
 * (docs/knowledge/flue-cutover-tool-cut-list.md §3). 100% thin HTTP wrappers
 * over EXISTING _DISPATCHABLE_INTENTS — zero new zoe-data surface:
 *
 *   reads:
 *     - note_search → note_search {query}
 *   writes (gated behind ZOE_BRAIN_ALLOW_WRITES, dry-run by default):
 *     - add_to_list → list_add    {item,list_type} (generalises shopping_list_add)
 *     - list_remove → list_remove {item,list_type}
 *   grouped action-dispatch (mirrors prod abilities/notes.ts + people.ts):
 *     - journal action=create|prompt|streak → journal_create {content,mood?} /
 *       journal_prompt {} / journal_streak {} (create is write-gated)
 *     - people  action=create|relate|search → people_create {name,relationship,notes?} /
 *       people_relate {name_a,name_b,role} / people_search {query}
 *       (create/relate are write-gated)
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
// .ts extension so the offline strip-types tests (node --experimental-strip-types)
// can resolve it too; tsconfig has allowImportingTsExtensions and the flue build
// bundles .ts specifiers fine.
import { ACTIVATOR_TOOL_NAME, GROUP_NAMES, GROUP_SUMMARY, TOOL_GROUPS } from './tool-groups.ts';

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

// Combine the turn's abort signal (if any) with a per-tool timeout. The old code
// (`signal ?? AbortSignal.timeout(...)`) made the timeout dead code: the turn
// signal is essentially always present, so a blocked zoe-data endpoint hung to the
// turn deadline (~120s) instead of the intended 8s. AbortSignal.any aborts on the
// FIRST of {turn aborted, timeout}, so a stuck endpoint is bounded at 8s while the
// turn can still cancel earlier. (AbortSignal.any is available on Node >= 20.3;
// this lab targets Node >= 22.)
function fetchSignal(signal: AbortSignal | undefined): AbortSignal {
  const timeout = AbortSignal.timeout(HTTP_TIMEOUT_MS);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

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
      signal: fetchSignal(signal),
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
        signal: fetchSignal(signal),
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
 *
 * FAILS CLOSED — never claims a timer started off this path. zoe-data's
 * timer_create intent (intent_router.py) returns a CANNED "Starting a N minute
 * timer" line WHETHER OR NOT a countdown was actually scheduled: on the touch
 * panel Skybridge owns the real timer/alarm and is consulted BEFORE that
 * fast-path, so the /api/system/intent-dispatch route this tool uses NEVER
 * schedules a countdown or fires an alarm. The dispatch response
 * ({intent, ok, result}) therefore carries no proof a real timer exists — ok:true
 * only means "result is not None" (i.e. the canned line). There is no response
 * this path can return that genuinely confirms a scheduled timer, so reporting
 * success would tell the user a timer started when none will fire. We require an
 * explicit positive confirmation that is NOT the canned line (same fail-closed
 * discipline the shopping-list / dispatch path uses); since the backend can only
 * ever return that canned line, set_timer stays effectively always-uncertain.
 */
// The canned, NON-confirming line zoe-data's timer_create always returns
// ("Starting a 10 minute timer", "Starting a 3 minute timer for the eggs").
// Seeing it is proof of NOTHING — explicitly treated as a non-confirmation.
const CANNED_TIMER_RE = /^\s*starting a\b.*\btimer\b/i;

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
    if (!ALLOW_WRITES) {
      return `WRITE DISABLED — a ${minutes} minute timer${named} was NOT started (this is a ` +
        `lab build; set ZOE_BRAIN_ALLOW_WRITES=true to enable writes). Tell the user you ` +
        `can't do that yet — do NOT claim it was done.`;
    }
    const out = await dispatchIntent('timer_create', { minutes, label }, 'timer', signal);
    // A genuine confirmation requires ok===true AND a non-empty result that is NOT
    // the canned "Starting a … timer" line. The backend cannot currently produce
    // such a result, so this is effectively always-uncertain — by design, never a
    // fabricated "timer started".
    const confirmed = out.ok && out.text.length > 0 && !CANNED_TIMER_RE.test(out.text);
    if (!confirmed) {
      return "I can't reliably start a real timer right now, so I won't say I did. " +
        'Set it on the kitchen panel, or ask me for a reminder instead.';
    }
    return out.text;
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

// ─── Wave 1: daily-driver batch (cut-list record §3, Wave 1) ─────────────────

/**
 * add_to_list — add an item to ANY of the user's lists. WRITE → list_add
 * {item,list_type}. Generalises shopping_list_add with a list_type arg (the
 * cut-list record's Wave-1 `add_to_list`). Gated behind ZOE_BRAIN_ALLOW_WRITES.
 */
const addToList = defineTool({
  name: 'add_to_list',
  description:
    "Add an item to one of the user's lists: shopping, tasks/to-do, personal, work, " +
    'or bucket. Use when they ask to add or put something on a specific list ' +
    '("add gym to my tasks list", "put Japan on my bucket list").',
  input: v.object({
    item: v.pipe(v.string(), v.trim(), v.minLength(1)),
    list_type: v.optional(v.string()),
  }),
  run: async ({ input, signal }) => {
    const item = String(input.item ?? '').trim();
    if (!item) return 'I need to know which item to add.';
    const listType = normalizeListType(input?.list_type);
    return runWrite(
      'list_add',
      { item, list_type: listType },
      'list',
      `"${item}"`,
      `Added "${item}" to your ${listType} list.`,
      signal,
    );
  },
});

/**
 * list_remove — remove an item from one of the user's lists. WRITE →
 * list_remove {item,list_type}. Gated behind ZOE_BRAIN_ALLOW_WRITES.
 */
const listRemove = defineTool({
  name: 'list_remove',
  description:
    "Remove an item from one of the user's lists (shopping, tasks/to-do, personal, " +
    'work, bucket). Use when they ask to remove, delete, or cross something off a list.',
  input: v.object({
    item: v.pipe(v.string(), v.trim(), v.minLength(1)),
    list_type: v.optional(v.string()),
  }),
  run: async ({ input, signal }) => {
    const item = String(input.item ?? '').trim();
    if (!item) return 'I need to know which item to remove.';
    const listType = normalizeListType(input?.list_type);
    return runWrite(
      'list_remove',
      { item, list_type: listType },
      'list',
      `removing "${item}"`,
      `Removed "${item}" from your ${listType} list.`,
      signal,
    );
  },
});

/**
 * note_search — search the user's saved notes. READ → note_search {query}.
 */
const noteSearch = defineTool({
  name: 'note_search',
  description:
    "Search the user's saved notes by keyword or topic (\"find my note about the " +
    'wifi password"). For saving a new note use create_note.',
  input: v.object({
    query: v.pipe(v.string(), v.trim(), v.minLength(1)),
  }),
  run: async ({ input, signal }) => {
    const query = String(input.query ?? '').trim();
    if (!query) return 'What would you like me to search your notes for?';
    const out = await dispatchIntent('note_search', { query }, 'notes', signal);
    if (!out.ok) return out.text;
    return out.text || `I couldn't find any notes about ${query}.`;
  },
});

/**
 * journal — the user's journal/diary, grouped action-dispatch (mirrors prod's
 * abilities/notes.ts journal entry). action=create writes an entry (WRITE →
 * journal_create {content,mood?}, gated); action=prompt / action=streak are
 * reads (journal_prompt {} / journal_streak {}).
 */
const journal = defineTool({
  name: 'journal',
  description:
    "The user's journal/diary. action=create writes a journal entry; action=prompt " +
    'suggests journaling prompts; action=streak reports their journaling streak. ' +
    'Reflective diary entries — NOT free-form notes (use create_note for those).',
  input: v.object({
    action: v.picklist(['create', 'prompt', 'streak']),
    content: v.optional(v.string()),
    mood: v.optional(v.string()),
  }),
  run: async ({ input, signal }) => {
    if (input.action === 'prompt' || input.action === 'streak') {
      const intent = input.action === 'prompt' ? 'journal_prompt' : 'journal_streak';
      const out = await dispatchIntent(intent, {}, 'journal', signal);
      if (!out.ok) return out.text;
      return out.text ||
        (input.action === 'prompt'
          ? "I don't have a journal prompt for you right now."
          : "I can't see your journaling streak right now.");
    }
    const content = String(input?.content ?? '').trim();
    if (!content) return 'What would you like your journal entry to say?';
    const slots: Record<string, unknown> = { content };
    const mood = String(input?.mood ?? '').trim();
    if (mood) slots.mood = mood;
    return runWrite('journal_create', slots, 'journal', 'that journal entry',
      'Saved your journal entry.', signal);
  },
});

/**
 * people — the user's people/contacts memory, grouped action-dispatch (mirrors
 * prod's abilities/people.ts). action=create saves a person (WRITE →
 * people_create, gated); action=relate links two known people (WRITE →
 * people_relate, gated); action=search looks someone up (READ → people_search).
 * The relationship/context/circle defaults match the prod ability.
 */
const people = defineTool({
  name: 'people',
  description:
    "The user's people/contacts memory. action=create saves a new person (name + " +
    "relationship); action=relate links two known people ('Alice and Bob are " +
    "siblings' — name + related_to + relationship); action=search looks someone up " +
    "('who is Sarah?'). NOT for messaging, calendar, or reminders.",
  input: v.object({
    action: v.picklist(['create', 'relate', 'search']),
    name: v.optional(v.string()),
    relationship: v.optional(v.string()),
    related_to: v.optional(v.string()),
    query: v.optional(v.string()),
    notes: v.optional(v.string()),
  }),
  run: async ({ input, signal }) => {
    if (input.action === 'search') {
      const query = String(input?.query ?? '').trim() || String(input?.name ?? '').trim();
      if (!query) return 'Who would you like me to look up?';
      const out = await dispatchIntent('people_search', { query }, 'contacts', signal);
      if (!out.ok) return out.text;
      return out.text || `I couldn't find anyone matching "${query}".`;
    }
    if (input.action === 'relate') {
      const nameA = String(input?.name ?? '').trim();
      const nameB = String(input?.related_to ?? '').trim();
      if (!nameA || !nameB) return 'I need two people to link them — give me both names.';
      const role = String(input?.relationship ?? '').trim() || 'friend';
      return runWrite('people_relate', { name_a: nameA, name_b: nameB, role }, 'contacts',
        `the link between "${nameA}" and "${nameB}"`,
        `Linked ${nameA} and ${nameB} as ${role}.`, signal);
    }
    const name = String(input?.name ?? '').trim();
    if (!name) return 'I need a name to save a new contact.';
    const slots: Record<string, unknown> = {
      name,
      relationship: String(input?.relationship ?? '').trim() || 'friend',
      context: 'personal',
      circle: 'circle',
    };
    const notes = String(input?.notes ?? '').trim();
    if (notes) slots.notes = notes;
    return runWrite('people_create', slots, 'contacts', `contact "${name}"`,
      `Added ${name} to your contacts.`, signal);
  },
});

// ─── Progressive disclosure activator ────────────────────────────────────────

/**
 * activate_abilities — the model-facing side of progressive tool disclosure
 * (see src/tools/tool-groups.ts for the mechanism and rationale).
 *
 * Only the always-on core tool schemas are sent to the model each turn; this
 * tool is how the model reaches everything else. It is deliberately STATELESS:
 * the activation is the tool CALL itself, which lands in the session
 * transcript — the wire-level disclosure filter derives the active set from
 * the transcript, so the requested group's schemas appear on the very next
 * model request within the same turn. The run body just confirms and steers.
 *
 * Not a security boundary: every tool stays registered on the agent, with the
 * same identity fail-closed semantics and ZOE_BRAIN_ALLOW_WRITES gate as
 * before. Disclosure only shrinks what the model SEES per call.
 */
const activateAbilities = defineTool({
  name: ACTIVATOR_TOOL_NAME,
  description:
    'Unlock a group of additional tools when the user asks for something none of ' +
    `your currently available tools can do. Groups: ${GROUP_SUMMARY}. ` +
    'After it returns, call the unlocked tool you need.',
  // DEAD-SIMPLE wire schema, kept that way on purpose for the 4B brain: one
  // required string property whose JSON schema is a bare enum of group names
  // (Flue requires a top-level OBJECT schema — tool.ts assertToolDefinition —
  // so a root-level enum is not an option). GROUP_SUMMARY (used in the
  // description above) is derived from the same canonical map as the picklist,
  // so the catalogue and the enum cannot drift apart. The exact wire shape is
  // pinned by a unit test (test/activator_fallback.test.ts).
  input: v.object({
    group: v.pipe(
      v.picklist(GROUP_NAMES),
      v.description(`The ability group to unlock — one of: ${GROUP_NAMES.join(', ')}.`),
    ),
  }),
  run: async ({ input }) => {
    const tools = TOOL_GROUPS[input.group];
    return (
      `Activated the ${input.group} tools: ${tools.join(', ')}. ` +
      'They are available now — call the one you need next.'
    );
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
  // Wave 1 — daily-driver batch (cut-list record §3; writes gated as above)
  addToList,
  listRemove,
  noteSearch,
  journal,
  people,
  // Progressive disclosure — always-on activator for the grouped tools above
  activateAbilities,
];
