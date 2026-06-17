/**
 * people — the relationship/contacts memory.
 * Intents (verified in intent_router): people_create {name,relationship,context,circle,notes?}
 *   | people_relate {name_a,name_b,role} | people_search {query}.
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
    "action=relate links two known people by a role ('Alice and Bob are siblings'); action=search looks " +
    "someone up ('who is Sarah?'). For storing/recalling who people are and how they relate — NOT messaging, calendar, or reminders.",
  parameters: Type.Object({
    action: Type.String({ description: "one of: create | relate | search" }),
    name: Type.Optional(Type.String({ description: "person's name (create) or first person (relate)" })),
    relationship: Type.Optional(Type.String({ description: "create: label like 'friend'/'colleague'; relate: role linking the pair (e.g. 'sibling','partner')" })),
    related_to: Type.Optional(Type.String({ description: "relate only: the second person to link 'name' to" })),
    query: Type.Optional(Type.String({ description: "search only: name/text to look up" })),
    context: Type.Optional(Type.String({ description: "create only: 'personal' (default) or 'work'" })),
    circle: Type.Optional(Type.String({ description: "create only: 'inner' | 'circle' (default) | 'public'" })),
    notes: Type.Optional(Type.String({ description: "create only: optional free-text notes" })),
  }),
  examples: ["add a contact for Sarah, she's my colleague", "remember James — a friend from college", "link Alice and Bob as siblings", "who is Sarah?", "find my contact for Dr. Patel"],
  negativeExamples: ["remind me to call Sarah tomorrow", "schedule lunch with Bob Friday", "text Mom that I'll be late"],
  permissions: ["user-data:read", "user-data:write"],
  tier: "on-demand",
  triggers: [/\b(add|save|create|remember)\b.*\b(contact|person|people)\b/i, /\b(link|connect|relate)\b.*\b(and|with|to)\b/i, /\bwho\s+is\b/i, /\b(find|look\s*up|search)\b.*\b(contact|person|people)\b/i],
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
    if (action === "relate") {
      const nameA = String(params.name ?? "").trim();
      const nameB = String(params.related_to ?? "").trim();
      if (!nameA || !nameB) return "I need two people to link them — give me both names.";
      const role = String(params.relationship ?? "").trim() || "friend";
      return dispatchIntent(ctx, "people_relate", { name_a: nameA, name_b: nameB, role });
    }
    if (action === "search") {
      const query = String(params.query ?? "").trim() || String(params.name ?? "").trim();
      if (!query) return "Who would you like me to look up?";
      return dispatchIntent(ctx, "people_search", { query });
    }
    return `Unknown people action: ${action || "(none)"}. Use create, relate, or search.`;
  },
};

export default [people];
