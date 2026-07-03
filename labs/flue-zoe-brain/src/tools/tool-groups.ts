/**
 * Progressive tool disclosure for the Flue Zoe brain — the port of prod's
 * pattern (services/zoe-core/extensions/abilities.ts: always-on core +
 * relevance-matched tools per turn via `pi.setActiveTools`) onto the Flue
 * sidecar.
 *
 * WHY: all 11 tool schemas were sent on EVERY model call. On the 4B Gemma
 * brain that bloats the prompt (slower prefill in the 8k context) and hurts
 * tool-choice reliability. Prod solves it with progressive disclosure; this
 * module is the sidecar's equivalent.
 *
 * THE MECHANISM (and why it lives at the wire, not the agent): Flue
 * 1.0.0-beta.6 exposes NO per-turn tool switching — `AgentRuntimeConfig.tools`
 * is a static `ToolDefinition[]` and `ToolContext` is only `{input, signal}`
 * (verified in @flue/runtime dist types). pi-agent-core's
 * `AgentHarness.setActiveTools()` exists (pi 0.80.2
 * packages/agent/src/harness/agent-harness.ts) but Flue does not surface it.
 * The sidecar DOES own the wire: every model call goes through the registered
 * `zoe-capped-completions` api handler (src/providers/capped-completions.ts),
 * which sees the full pi-ai `Context {systemPrompt, messages, tools}`. So
 * disclosure filters `context.tools` there — the model only ever SEES the
 * active schemas, while ALL tools stay registered on the agent, so any tool
 * the model does call still executes normally (execution looks tools up in
 * the agent's own registry, not in the wire copy — same seam the iteration
 * cap already relies on).
 *
 * ACTIVE SET, derived STATELESSLY from the request itself (no shared mutable
 * state, so concurrent sessions can't leak into each other and a process
 * restart loses nothing):
 *   1. the always-on core (get_time, recall_memory, activate_abilities);
 *   2. groups keyword-matched against the LAST user message (prod's
 *      `isRelevant` analogue — deterministic, no embedder);
 *   3. groups the model explicitly unlocked via the `activate_abilities` tool
 *      anywhere in this session's transcript (the call lands in `messages`,
 *      so the very NEXT model request in the same turn already discloses the
 *      group — no state handoff needed);
 *   4. groups whose tools were already called in this session (sticky: a
 *      follow-up turn like "and tomorrow?" keeps get_weather visible without
 *      re-activation).
 * Trade-off (documented in README): the per-session set grows monotonically —
 * a long session that touches every domain converges back to all schemas.
 * Sessions are per-conversation, so in practice a typical turn carries 3
 * schemas instead of all 19.
 *
 * Unknown Zoe tool names (registered on the agent but absent from the grouping
 * map) are ALWAYS disclosed — adding a 12th Zoe tool without grouping it here
 * must not make it silently invisible. The ONE exception is the pi/Flue coding
 * built-ins (read/write/edit/bash/grep/glob/task), which the Flue harness
 * injects on every turn regardless of the sidecar's tool list: those are
 * unconditionally STRIPPED (see CODING_BUILTIN_TOOL_NAMES) — a family voice
 * assistant must never be handed bash/write/edit/task, and they bloat the 4B
 * context.
 *
 * Kill switch: ZOE_BRAIN_PROGRESSIVE_TOOLS=false restores the old
 * all-schemas-every-call behaviour (used for A/B latency comparison).
 *
 * LAB ONLY.
 */
import type { Context, Message } from '@earendil-works/pi-ai';

/** The always-disclosed activator tool (defined in zoe-tools.ts). */
export const ACTIVATOR_TOOL_NAME = 'activate_abilities';

/**
 * pi/Flue coding-agent built-ins that the harness injects into EVERY model
 * turn regardless of the sidecar's own tool list.
 *
 * WHY THIS EXISTS: Flue's harness always assembles the framework's built-in
 * coding toolset alongside the agent's declared tools — verified in the
 * runtime dist, `createBuiltinToolGroups` → `createTools(env, …)` in
 * @flue/runtime (skill-package chunk: `createReadTool`/`createWriteTool`/
 * `createEditTool`/`createBashTool`/`createGrepTool`/`createGlobTool`, plus the
 * optional `createTaskTool`). `defineAgent` exposes NO option to suppress them,
 * so the sidecar cannot avoid registering them; they arrive on `context.tools`
 * on every wire call. That is a correctness AND safety defect for a family
 * VOICE assistant: it must never be handed `bash`/`write`/`edit`/`task`, and
 * the extra schemas bloat the ~8k context on the 4B brain. Production's brain
 * never sends these.
 *
 * These names are NOT in any Zoe ability group, so before this denylist the
 * "unknown tool names always survive" rule below waved them straight through.
 * We strip them unconditionally at the disclosure chokepoint (the one place
 * that already rewrites `context.tools` every call). The names are the exact
 * ones the framework registers (confirmed from the @flue/runtime source).
 *
 * NOTE: this denylist is intentionally the framework CODING built-ins only. It
 * must NOT list `activate_abilities` or any real Zoe tool — those are handled
 * by the disclosure logic and must keep flowing.
 */
export const CODING_BUILTIN_TOOL_NAMES: ReadonlySet<string> = new Set([
  'read',
  'write',
  'edit',
  'bash',
  'grep',
  'glob',
  'task',
]);

/** Always-on core: cheap, every-turn-relevant, plus the activator itself. */
export const CORE_TOOL_NAMES: readonly string[] = [
  'get_time',
  'recall_memory',
  ACTIVATOR_TOOL_NAME,
];

/**
 * Ability groups → the tool names they disclose. Mirrors the domain split of
 * the prod abilities (weather / lists / timers / reminders / calendar / notes).
 */
export const TOOL_GROUPS = {
  weather: ['get_weather'],
  lists: ['shopping_list_add', 'add_to_list', 'list_remove', 'show_list'],
  timers: ['set_timer'],
  reminders: ['list_reminders', 'add_reminder'],
  calendar: ['show_calendar', 'add_calendar_event'],
  notes: ['create_note', 'note_search'],
  journal: ['journal'],
  people: ['people'],
  media: ['media'],
  home: ['home'],
  memory: ['remember_fact'],
} as const satisfies Record<string, readonly string[]>;

export type AbilityGroup = keyof typeof TOOL_GROUPS;

export const GROUP_NAMES = Object.keys(TOOL_GROUPS) as readonly AbilityGroup[];

/**
 * One-line human purpose per group, used to build the `activate_abilities`
 * tool description. Kept HERE, next to the canonical map, and typed as a
 * total Record so adding a group without a purpose line is a compile error —
 * the activator's description can never silently drift out of sync with the
 * groups its picklist accepts.
 */
export const GROUP_PURPOSES: Record<AbilityGroup, string> = {
  weather: 'current weather / forecast',
  lists: 'add to, remove from, or show shopping/task lists',
  timers: 'countdown timers',
  reminders: 'list or create reminders',
  calendar: 'show or add events',
  notes: 'save or search notes',
  journal: 'diary entries, journaling prompts, streak',
  people: 'save or look up people/contacts',
  media: 'play/control music, set music or speaking volume',
  home: 'turn on/off, dim, or brighten the lights',
  memory: 'store a fact the user asks you to remember',
};

/**
 * The model-facing group catalogue — every group with its purpose line, derived
 * from the canonical map (never hand-maintained). Used in BOTH places the model
 * can learn its groups from: the `activate_abilities` tool description
 * (zoe-tools.ts) and the agent's system instructions (agents/zoe.ts). The E2E
 * finding behind the latter: a catalogue that lives only in a tool description
 * is invisible while the model is deciding whether to call any tool at all.
 */
export const GROUP_SUMMARY = GROUP_NAMES.map(
  (group) => `${group} (${GROUP_PURPOSES[group]})`,
).join(', ');

/**
 * Deterministic relevance triggers per group, matched (case-insensitively)
 * against the last user message — prod's keyword/example matching analogue.
 * Misses are fine: the activator tool is the model-driven fallback. Widened
 * for high-value INDIRECT phrasings (E2E: keyword-free prompts never reached
 * the activator, so pre-disclosure is the defence-in-depth layer): weather
 * gains washing/laundry/outside ("can I hang the washing out?"), calendar
 * gains "anything on <day>"-style asks and "am I free". A false positive
 * only discloses one extra schema; a false negative loses the whole ability.
 */
const GROUP_TRIGGERS: Record<AbilityGroup, RegExp> = {
  weather:
    /\b(weather|temperature|forecast|rain|raining|rainy|snow|sunny|wind|windy|umbrella|jacket|degrees|hot|cold|washing|laundry|outside)\b/i,
  lists: /\b(lists?|shopping|grocer(?:y|ies)|to-?dos?|tasks?)\b/i,
  timers: /\b(timers?|countdown)\b/i,
  reminders: /\bremind(?:er|ers)?\b/i,
  calendar:
    // Direct nouns; "anything/what's on|for|planned|happening … <day-word>";
    // "am I free". The day-word requirement keeps "what's on my shopping
    // list?" from tripping calendar.
    /\b(calendar|schedule|appointments?|meetings?|events?|agenda)\b|\b(?:anything|something|nothing|plans|what(?:'s| is)?|what do (?:i|we) have)\s+(?:on|for|planned|happening|going on)\b[^.?!]*\b(?:today|tonight|tomorrow|weekend|week|(?:mon|tues?|wednes|thurs?|fri|satur|sun)day)\b|\bam i free\b/i,
  notes: /\bnotes?\b|\bjot\b|\bwrite (?:this|that|it) down\b/i,
  journal: /\b(?:journal(?:ing)?|diary)\b/i,
  people:
    // Direct contact-store phrasings and "who is <name>" lookups. Deliberately
    // NOT a bare \bpeople\b — "how many people live in Perth" is not a
    // contacts ask; a false positive only discloses one extra schema, but the
    // bare noun would trip on most small talk.
    /\bcontacts?\b|\bwho\s+is\b|\b(?:add|save|remember|link|relate)\b[^.?!]*\b(?:person|people|friend|colleague|contact)\b/i,
  // Playback verbs, transport controls, and volume phrasings (incl. "turn it
  // up/down", "louder/quieter"). "volume" alone trips media — set_music_volume
  // vs system_volume is the model's action choice, not disclosure's.
  media:
    /\b(play|put on)\b|\b(pause|resume|unpause|stop|skip|next|previous|shuffle|mute|unmute)\b|\bwhat(?:'s| is)(?: currently)? playing\b|\bvolume\b|\b(louder|quieter|turn it (?:up|down))\b|\b(?:spotify|music)\b/i,
  // Light control only — the day-word-free "lights" noun with an on/off/dim/
  // brighten verb, or the "<verb> the lights" shape.
  home:
    /\blights?\b|\b(?:turn|switch|flip)\s+(?:on|off)\b|\b(?:dim|brighten)\b/i,
  // Explicit "remember (that) ...", "don't forget ...", "keep in mind ...",
  // "make a note that ..." style WRITE requests. Recall is handled by the
  // always-on core recall_memory, so this only needs the store phrasings.
  memory:
    /\bremember (?:that|this|i|my|to keep)\b|\bdon'?t forget\b|\bkeep in mind\b|\bmake a mental note\b/i,
};

/** Reverse map: tool name → its group. */
const TOOL_TO_GROUP: ReadonlyMap<string, AbilityGroup> = new Map(
  (Object.entries(TOOL_GROUPS) as [AbilityGroup, readonly string[]][]).flatMap(
    ([group, names]) => names.map((name): [string, AbilityGroup] => [name, group]),
  ),
);

/** Every tool name the grouping map knows about (core + grouped). */
const KNOWN_TOOL_NAMES: ReadonlySet<string> = new Set([
  ...CORE_TOOL_NAMES,
  ...TOOL_TO_GROUP.keys(),
]);

/**
 * Progressive disclosure on/off (default ON). Read fresh each call — no
 * module-load pinning, same discipline as the iteration cap.
 */
export function progressiveToolsEnabled(): boolean {
  const raw = (process.env.ZOE_BRAIN_PROGRESSIVE_TOOLS ?? 'true').toLowerCase();
  return raw !== 'false' && raw !== '0';
}

/** Text of the last user message (user content may be a string or parts). */
function lastUserText(messages: Message[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role !== 'user') continue;
    if (typeof msg.content === 'string') return msg.content;
    return msg.content
      .filter((c): c is Extract<typeof c, { type: 'text' }> => c.type === 'text')
      .map((c) => c.text)
      .join('\n');
  }
  return '';
}

/**
 * Groups considered active for this request. See the module header for the
 * four sources. Pure function of the message window — no shared state.
 */
export function activeGroups(messages: Message[]): Set<AbilityGroup> {
  const active = new Set<AbilityGroup>();

  // 2. Keyword relevance on the last user message.
  const userText = lastUserText(messages);
  if (userText) {
    for (const group of GROUP_NAMES) {
      if (GROUP_TRIGGERS[group].test(userText)) active.add(group);
    }
  }

  // 3 + 4. Explicit activations and sticky already-used groups, from the
  // transcript's assistant tool calls. Guard string content the same way
  // lastUserText does, so a plain-text assistant message can never be
  // iterated character-by-character if the pi-ai message type evolves.
  for (const msg of messages) {
    if (msg.role !== 'assistant') continue;
    if (typeof msg.content === 'string') continue;
    for (const part of msg.content) {
      if (part.type !== 'toolCall') continue;
      if (part.name === ACTIVATOR_TOOL_NAME) {
        const requested = String(
          (part.arguments as Record<string, unknown> | undefined)?.group ?? '',
        ).toLowerCase();
        if ((GROUP_NAMES as readonly string[]).includes(requested)) {
          active.add(requested as AbilityGroup);
        }
        continue;
      }
      const group = TOOL_TO_GROUP.get(part.name);
      if (group) active.add(group);
    }
  }

  return active;
}

/** Tool names disclosed to the model for this request. */
export function activeToolNames(messages: Message[]): Set<string> {
  const names = new Set<string>(CORE_TOOL_NAMES);
  for (const group of activeGroups(messages)) {
    for (const name of TOOL_GROUPS[group]) names.add(name);
  }
  return names;
}

/**
 * Filter `context.tools` down to the disclosed set (returns a shallow copy;
 * the original context — which the agent loop uses for tool EXECUTION — is
 * never mutated). Rules, in order:
 *   - pi/Flue coding built-ins (`CODING_BUILTIN_TOOL_NAMES`) are ALWAYS
 *     stripped — a family voice assistant must never be handed
 *     bash/write/edit/task, and they bloat the 4B context (see the denylist
 *     header for why the harness injects them unavoidably);
 *   - a disclosed grouped/core Zoe tool passes;
 *   - an UNKNOWN tool name (a real Zoe tool added without grouping it here)
 *     still survives — so adding a 12th Zoe tool without grouping it can't make
 *     it silently invisible. The coding-built-in strip takes precedence over
 *     this survive rule, so the two never conflict.
 */
export function discloseTools(context: Context): Context {
  const tools = context.tools;
  if (!tools || tools.length === 0) return context;
  const active = activeToolNames(context.messages);
  const disclosed = tools.filter(
    (tool) =>
      !CODING_BUILTIN_TOOL_NAMES.has(tool.name) &&
      (active.has(tool.name) || !KNOWN_TOOL_NAMES.has(tool.name)),
  );
  if (disclosed.length === tools.length) return context;
  return { ...context, tools: disclosed };
}

/**
 * Strip ONLY the pi/Flue coding built-ins (`CODING_BUILTIN_TOOL_NAMES`) from
 * `context.tools`, leaving every Zoe tool untouched. This is the SAFETY floor:
 * it runs unconditionally, independent of the progressive-disclosure kill
 * switch, so `ZOE_BRAIN_PROGRESSIVE_TOOLS=false` can never re-expose
 * bash/write/edit/task to the voice brain. `discloseTools` already removes
 * these when disclosure is ON; this covers the disclosure-OFF path. Returns a
 * shallow copy only when something was actually removed (never mutates).
 */
export function stripCodingBuiltins(context: Context): Context {
  const tools = context.tools;
  if (!tools || tools.length === 0) return context;
  const kept = tools.filter((tool) => !CODING_BUILTIN_TOOL_NAMES.has(tool.name));
  if (kept.length === tools.length) return context;
  return { ...context, tools: kept };
}
