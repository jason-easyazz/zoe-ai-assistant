/**
 * lists / reminders / timers.
 * Intents (verified in intent_router):
 *   lists     -> list_add {item,list_type} | list_remove {item,list_type} | list_show {list_type}
 *   reminders -> reminder_create {title,date?,time?} | reminder_list {}
 *   timers    -> timer_create {minutes,label}
 */
import { Type } from "@sinclair/typebox";
import type { AbilityContext, CapabilityEntry } from "./types";
import { dispatchIntent } from "./_dispatch";

const LIST_ALIASES: Record<string, string> = {
  grocery: "shopping", groceries: "shopping",
  "to do": "tasks", "to-do": "tasks", todo: "tasks", todos: "tasks", task: "tasks",
};
const CANONICAL_LISTS = ["shopping", "personal", "work", "bucket", "tasks"];
function normalizeListType(raw: unknown): string {
  const s = String(raw ?? "").trim().toLowerCase();
  if (LIST_ALIASES[s]) return LIST_ALIASES[s];
  return CANONICAL_LISTS.includes(s) ? s : "shopping";
}

const lists: CapabilityEntry = {
  id: "lists",
  name: "lists",
  domain: "lists",
  description:
    "Manage the user's lists (shopping, tasks/to-do, personal, work, bucket): add an item, remove an " +
    "item, or show a list. For groceries/shopping/to-do items — NOT time-based reminders or countdown timers.",
  parameters: Type.Object({
    action: Type.String({ description: "one of: add | remove | show" }),
    item: Type.Optional(Type.String({ description: "item text (required for add/remove)" })),
    list_type: Type.Optional(Type.String({ description: "shopping | tasks | personal | work | bucket (default shopping)" })),
  }),
  examples: ["add milk to the shopping list", "remove bread from the shopping list", "what's on my shopping list", "show me the to-do list"],
  negativeExamples: ["remind me to call mum at 5pm", "set a 10 minute timer", "what's on my calendar"],
  permissions: ["user-data:read", "user-data:write"],
  tier: "on-demand",
  triggers: [
    /\badd\b.+\bto\b.+\blist\b/i, /\bput\b.+\bon\b.+\blist\b/i,
    /\b(?:remove|delete|take off|cross off)\b.+\bfrom\b.+\blist\b/i,
    /\bwhat(?:'s| is)\s+on\b.+\blist\b/i, /\b(?:show|read|check)\b.+\blist\b/i,
    /\b(?:shopping|grocery|groceries|to-?do|todo|tasks?)\s+list\b/i,
  ],
  async execute(params, ctx: AbilityContext): Promise<string> {
    const action = String(params.action ?? "").toLowerCase();
    const list_type = normalizeListType(params.list_type);
    if (action === "show") return dispatchIntent(ctx, "list_show", { list_type });
    const item = String(params.item ?? "").trim();
    if (!item) return `I need to know which item to ${action} ${action === "remove" ? "from" : "to"} the ${list_type} list.`;
    if (action === "add") return dispatchIntent(ctx, "list_add", { item, list_type });
    if (action === "remove") return dispatchIntent(ctx, "list_remove", { item, list_type });
    return `Unknown list action: ${action}`;
  },
};

const reminders: CapabilityEntry = {
  id: "reminders",
  name: "reminders",
  domain: "reminders",
  description:
    "Create and review reminders — a prompt tied to a wall-clock time/date ('remind me to take the bins " +
    "out at 7pm'). action=create sets one; action=list reads them. For a plain countdown use timers.",
  parameters: Type.Object({
    action: Type.String({ description: "one of: create | list" }),
    title: Type.Optional(Type.String({ description: "what to be reminded about (required for create)" })),
    date: Type.Optional(Type.String({ description: "optional due date ('tomorrow', 'Monday', '2026-06-20')" })),
    time: Type.Optional(Type.String({ description: "optional due time ('7pm', '19:00')" })),
  }),
  examples: ["remind me to call mum at 5pm", "set a reminder to water the plants tomorrow", "show my reminders", "what are my reminders"],
  negativeExamples: ["add milk to the shopping list", "set a 10 minute timer", "what's on my calendar tomorrow"],
  permissions: ["user-data:read", "user-data:write"],
  tier: "on-demand",
  triggers: [/\bremind me\b/i, /\bset (?:a |an )?reminder\b/i, /\b(?:add|create|make) (?:a |an )?reminder\b/i, /\b(?:show|list|what are)\s+(?:my )?reminders\b/i],
  async execute(params, ctx: AbilityContext): Promise<string> {
    const action = String(params.action ?? "").toLowerCase();
    if (action === "list") return dispatchIntent(ctx, "reminder_list", {});
    if (action === "create") {
      const title = String(params.title ?? "").trim();
      if (!title) return "I need to know what to remind you about.";
      const slots: Record<string, unknown> = { title };
      const date = String(params.date ?? "").trim();
      const time = String(params.time ?? "").trim();
      if (date) slots.date = date;
      if (time) slots.time = time;
      return dispatchIntent(ctx, "reminder_create", slots);
    }
    return `Unknown reminder action: ${action}`;
  },
};

const timers: CapabilityEntry = {
  id: "timers",
  name: "timers",
  domain: "timers",
  description: "Start a countdown timer for N minutes, optionally named ('set a 10 minute timer', '3 minute timer for the eggs'). For short durations from now — not clock-time reminders.",
  parameters: Type.Object({
    minutes: Type.Number({ description: "duration in minutes (>=1)", minimum: 1 }),
    label: Type.Optional(Type.String({ description: "optional timer name (default 'Timer')" })),
  }),
  examples: ["set a 10 minute timer", "start a 5 minute timer", "set a timer for 3 minutes called eggs"],
  negativeExamples: ["remind me to call the dentist at 9am", "add eggs to the shopping list", "what time is it"],
  permissions: ["user-data:write"],
  tier: "on-demand",
  triggers: [/\b(?:set|start|create)\s+(?:a |an )?\d+[\s-]?(?:minute|min)s?\s+timer\b/i, /\b\d+\s*min(?:ute)?s?\s+timer\b/i, /\b(?:set|start)\s+(?:a |an )?timer\b/i],
  async execute(params, ctx: AbilityContext): Promise<string> {
    const raw = Number(params.minutes);
    const minutes = Number.isFinite(raw) && raw >= 1 ? Math.round(raw) : 5;
    const label = String(params.label ?? "").trim() || "Timer";
    return dispatchIntent(ctx, "timer_create", { minutes, label });
  },
};

export default [lists, reminders, timers];
