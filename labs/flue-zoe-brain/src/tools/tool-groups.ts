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
 * schemas instead of 11.
 *
 * Unknown tool names (registered on the agent but absent from the grouping
 * map) are ALWAYS disclosed — adding a 12th tool without grouping it here
 * must not make it silently invisible.
 *
 * Kill switch: ZOE_BRAIN_PROGRESSIVE_TOOLS=false restores the old
 * all-schemas-every-call behaviour (used for A/B latency comparison).
 *
 * LAB ONLY.
 */
import type { Context, Message } from '@earendil-works/pi-ai';

/** The always-disclosed activator tool (defined in zoe-tools.ts). */
export const ACTIVATOR_TOOL_NAME = 'activate_abilities';

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
  lists: ['shopping_list_add', 'show_list'],
  timers: ['set_timer'],
  reminders: ['list_reminders', 'add_reminder'],
  calendar: ['show_calendar', 'add_calendar_event'],
  notes: ['create_note'],
} as const satisfies Record<string, readonly string[]>;

export type AbilityGroup = keyof typeof TOOL_GROUPS;

export const GROUP_NAMES = Object.keys(TOOL_GROUPS) as readonly AbilityGroup[];

/**
 * Deterministic relevance triggers per group, matched (case-insensitively)
 * against the last user message — prod's keyword/example matching analogue.
 * Misses are fine: the activator tool is the model-driven fallback.
 */
const GROUP_TRIGGERS: Record<AbilityGroup, RegExp> = {
  weather:
    /\b(weather|temperature|forecast|rain|raining|rainy|snow|sunny|wind|windy|umbrella|jacket|degrees|hot|cold)\b/i,
  lists: /\b(lists?|shopping|grocer(?:y|ies)|to-?dos?|tasks?)\b/i,
  timers: /\b(timers?|countdown)\b/i,
  reminders: /\bremind(?:er|ers)?\b/i,
  calendar: /\b(calendar|schedule|appointments?|meetings?|events?|agenda)\b/i,
  notes: /\bnotes?\b|\bjot\b|\bwrite (?:this|that|it) down\b/i,
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
  // transcript's assistant tool calls.
  for (const msg of messages) {
    if (msg.role !== 'assistant') continue;
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
 * never mutated). Unknown tool names always survive the filter.
 */
export function discloseTools(context: Context): Context {
  const tools = context.tools;
  if (!tools || tools.length === 0) return context;
  const active = activeToolNames(context.messages);
  const disclosed = tools.filter(
    (tool) => active.has(tool.name) || !KNOWN_TOOL_NAMES.has(tool.name),
  );
  if (disclosed.length === tools.length) return context;
  return { ...context, tools: disclosed };
}
