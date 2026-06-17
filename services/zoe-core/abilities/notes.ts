/**
 * notes / journal.
 * Intents (verified in intent_router): note_create {title,content} | note_search {query};
 * journal_create {content} | journal_prompt {} | journal_streak {}.
 */
import { Type } from "typebox";
import type { AbilityContext, CapabilityEntry } from "./types";
import { dispatchIntent } from "./_dispatch";

const notes: CapabilityEntry = {
  id: "notes",
  name: "notes",
  domain: "notes",
  description:
    "Create and search the user's free-form notes. action=create saves a note; action=search finds one. " +
    "For notes/ideas/text to remember — NOT journal/diary entries (use journal), reminders, calendar, or lists.",
  parameters: Type.Object({
    action: Type.String({ description: "one of: create | search" }),
    content: Type.Optional(Type.String({ description: "note body (required for create)" })),
    title: Type.Optional(Type.String({ description: "optional note title (derived from content if omitted)" })),
    query: Type.Optional(Type.String({ description: "search keyword/topic (required for search)" })),
  }),
  examples: ["make a note: pick up dry cleaning Friday", "write a note titled groceries", "search my notes for the kickoff plan", "find my note about the wifi password"],
  negativeExamples: ["remind me to pick up dry cleaning Friday", "add milk to my shopping list", "write in my journal about today"],
  permissions: ["user-data:read", "user-data:write"],
  tier: "on-demand",
  triggers: [/\b(make|create|write|save|add|take)\s+(a\s+)?note\b/i, /\b(search|find|look\s*up)\s+(my\s+)?notes?\b/i],
  async execute(params, ctx: AbilityContext): Promise<string> {
    const action = String(params.action ?? "").toLowerCase();
    if (action === "create") {
      const content = typeof params.content === "string" ? params.content.trim() : "";
      if (!content) return "I need the text of the note before I can save it.";
      const title = typeof params.title === "string" && params.title.trim() ? params.title.trim() : content.slice(0, 60);
      return dispatchIntent(ctx, "note_create", { title, content });
    }
    if (action === "search") {
      const query = typeof params.query === "string" ? params.query.trim() : "";
      if (!query) return "What would you like me to search your notes for?";
      return dispatchIntent(ctx, "note_search", { query });
    }
    return `Unsupported notes action: ${action || "(none)"}.`;
  },
};

const journal: CapabilityEntry = {
  id: "journal",
  name: "journal",
  domain: "journal",
  description:
    "Manage the user's journal/diary. action=entry writes a journal entry; action=prompt gives journaling " +
    "prompts; action=streak reports their journaling streak/stats. Reflective diary entries — NOT free-form notes (use notes).",
  parameters: Type.Object({
    action: Type.String({ description: "one of: entry | prompt | streak" }),
    content: Type.Optional(Type.String({ description: "journal entry text (required for entry)" })),
    mood: Type.Optional(Type.String({ description: "optional mood for the entry" })),
  }),
  examples: ["write in my journal: today was a great day", "give me a journal prompt", "how's my journaling streak?", "what's my journal streak"],
  negativeExamples: ["make a note about the park trip", "remind me to write in my journal tonight"],
  permissions: ["user-data:read", "user-data:write"],
  tier: "on-demand",
  triggers: [/\b(write|create|make|start|new|log|add)\b.*\b(journal|diary)\b/i, /\bjournal(?:ing)?\s+prompt/i, /\b(journal|journaling)\s+(streak|stats)\b/i],
  async execute(params, ctx: AbilityContext): Promise<string> {
    const action = String(params.action ?? "").toLowerCase();
    if (action === "entry") {
      const content = typeof params.content === "string" ? params.content.trim() : "";
      if (!content) return dispatchIntent(ctx, "journal_prompt", {});
      const slots: Record<string, unknown> = { content };
      if (typeof params.mood === "string" && params.mood.trim()) slots.mood = params.mood.trim();
      return dispatchIntent(ctx, "journal_create", slots);
    }
    if (action === "prompt") return dispatchIntent(ctx, "journal_prompt", {});
    if (action === "streak") return dispatchIntent(ctx, "journal_streak", {});
    return `Unsupported journal action: ${action || "(none)"}.`;
  },
};

export default [notes, journal];
