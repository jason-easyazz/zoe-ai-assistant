/**
 * calendar — create and view calendar events.
 * Intents (verified in intent_router.execute_intent):
 *   create -> calendar_create { title, date?, time?, category? }
 *   show   -> calendar_show   { qualifier }   (today|tomorrow|this week|this month|"")
 */
import { Type } from "@sinclair/typebox";
import type { AbilityContext, CapabilityEntry } from "./types";
import { dispatchIntent } from "./_dispatch";

const calendar: CapabilityEntry = {
  id: "calendar",
  name: "calendar",
  domain: "calendar",
  description:
    "Create and view the user's calendar events. Use WHEN the user wants to schedule/book/add an " +
    "appointment, meeting, or event, OR to see what's on their calendar (today/tomorrow/this week/" +
    "this month/upcoming). NOT for reminders, lists, notes, or journal entries.",
  parameters: Type.Object({
    action: Type.String({ description: "one of: create | show" }),
    title: Type.Optional(Type.String({ description: "event title (required for create)" })),
    date: Type.Optional(Type.String({ description: "event date for create — free-form ok ('tomorrow', '2026-06-20')" })),
    time: Type.Optional(Type.String({ description: "event time for create — free-form ok ('3pm', '15:00')" })),
    category: Type.Optional(Type.String({ description: "optional category (defaults 'general')" })),
    qualifier: Type.Optional(Type.String({ description: "for show: today | tomorrow | this week | this month (omit for upcoming)" })),
  }),
  examples: [
    "Add dentist to my calendar tomorrow at 3pm",
    "Schedule a meeting with Sam on Friday at 10am",
    "What's on my calendar today?",
    "Show me my schedule this week",
  ],
  negativeExamples: ["Remind me to call mum at 5pm", "Add milk to my shopping list", "What's the weather tomorrow?"],
  permissions: ["user-data:read", "user-data:write"],
  tier: "on-demand",
  triggers: [
    /\b(?:add|put|create|schedule|set\s*up|book)\b[\s\S]*\b(?:calendar|appointment|meeting|event)\b/i,
    /\b(?:calendar|appointment|meeting|event)\b[\s\S]*\b(?:add|put|create|schedule|book)\b/i,
    /\bon my (?:calendar|schedule)\b/i,
    /\b(?:show|check|view)\b[\s\S]*\b(?:calendar|schedule|events?)\b/i,
    /\bwhat(?:'s| is)?\s+(?:on|happening)\b[\s\S]*\b(?:today|tomorrow|this week|this month|calendar|schedule)\b/i,
  ],
  async execute(params, ctx: AbilityContext): Promise<string> {
    const action = String(params.action ?? "").trim().toLowerCase();
    if (action === "create") {
      const title = typeof params.title === "string" ? params.title.trim() : "";
      if (!title) return "I need a title for the event. What should I call it?";
      const slots: Record<string, unknown> = { title };
      if (typeof params.date === "string" && params.date.trim()) slots.date = params.date.trim();
      if (typeof params.time === "string" && params.time.trim()) slots.time = params.time.trim();
      if (typeof params.category === "string" && params.category.trim()) slots.category = params.category.trim();
      return dispatchIntent(ctx, "calendar_create", slots);
    }
    if (action === "show" || action === "list") {
      const qualifier = typeof params.qualifier === "string" ? params.qualifier.trim() : "";
      return dispatchIntent(ctx, "calendar_show", { qualifier });
    }
    return `Unknown calendar action "${action}". Use "create" or "show".`;
  },
};

export default [calendar];
