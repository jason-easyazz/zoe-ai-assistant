/**
 * people — the relationship/contacts memory.
 * Intents (verified in intent_router): people_create {name,relationship,context,circle,notes?}
 *   | people_search {query}.
 * (Third-party person-to-person relationships are captured from natural language
 * by zoe-data's person_extractor on every turn — no dedicated relate action.)
 */
import { Type } from "@sinclair/typebox";
import type { AbilityContext, CapabilityEntry } from "./types";
import { dispatchIntent } from "./_dispatch";

const people: CapabilityEntry = {
  id: "people",
  name: "people",
  domain: "people",
  description:
    "The user's relationship/contacts memory. action=create saves a new person (name + how they're known); " +
    "action=search looks someone up ('who is Sarah?'). For storing/recalling who people are — NOT messaging, calendar, or reminders.",
  parameters: Type.Object({
    action: Type.String({ description: "one of: create | search" }),
    name: Type.Optional(Type.String({ description: "person's name (create)" })),
    relationship: Type.Optional(Type.String({ description: "create: label like 'friend'/'colleague'" })),
    query: Type.Optional(Type.String({ description: "search only: name/text to look up" })),
    context: Type.Optional(Type.String({ description: "create only: 'personal' (default) or 'work'" })),
    circle: Type.Optional(Type.String({ description: "create only: 'inner' | 'circle' (default) | 'public'" })),
    notes: Type.Optional(Type.String({ description: "create only: optional free-text notes" })),
  }),
  examples: ["add a contact for Sarah, she's my colleague", "remember James — a friend from college", "who is Sarah?", "find my contact for Dr. Patel"],
  negativeExamples: ["remind me to call Sarah tomorrow", "schedule lunch with Bob Friday", "text Mom that I'll be late"],
  permissions: ["user-data:read", "user-data:write"],
  tier: "on-demand",
  triggers: [/\b(add|save|create|remember)\b.*\b(contact|person|people)\b/i, /\bwho\s+is\b/i, /\b(find|look\s*up|search)\b.*\b(contact|person|people)\b/i],
  async execute(params, ctx: AbilityContext): Promise<string> {
    const action = String(params.action ?? "").toLowerCase();
    if (action === "create") {
      const name = String(params.name ?? "").trim();
      if (!name) return "I need a name to save a new contact.";
      const slots: Record<string, unknown> = {
        name,
        relationship: String(params.relationship ?? "").trim() || "friend",
        context: String(params.context ?? "").trim() || "personal",
        circle: String(params.circle ?? "").trim() || "circle",
      };
      if (typeof params.notes === "string" && params.notes.trim()) slots.notes = params.notes.trim();
      return dispatchIntent(ctx, "people_create", slots);
    }
    if (action === "search") {
      const query = String(params.query ?? "").trim() || String(params.name ?? "").trim();
      if (!query) return "Who would you like me to look up?";
      return dispatchIntent(ctx, "people_search", { query });
    }
    return `Unknown people action: ${action || "(none)"}. Use create or search.`;
  },
};

export default [people];
