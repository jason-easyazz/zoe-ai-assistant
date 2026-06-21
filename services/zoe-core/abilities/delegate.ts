/**
 * delegate — hand work off to a specialist peer agent (Hermes) for things
 * zoe-core can't do natively yet: live web research, current events, prices,
 * news, and complex multi-step tasks. Hermes owns web browsing / Telegram / the
 * harness; this is the synchronous parity of the legacy __ESCALATE_HERMES__ path.
 *
 * Calls POST /api/system/delegate-sync (internal token), which runs a
 * synchronous Hermes completion and returns the answer to fold into the turn.
 */
import { Type } from "@sinclair/typebox";
import type { AbilityContext, CapabilityEntry } from "./types";

const TIMEOUT_MS = Number(process.env.ZOE_CORE_DELEGATE_TIMEOUT_MS ?? 120000);

async function delegateSync(ctx: AbilityContext, task: string, target = "hermes"): Promise<string> {
  const url = new URL("/api/system/delegate-sync", ctx.zoeDataUrl);
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (ctx.internalToken) headers["X-Internal-Token"] = ctx.internalToken;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ user_id: ctx.userId, task, target }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) {
      return `I couldn't reach ${target} just now (status ${res.status}).`;
    }
    const data = (await res.json()) as { result?: string; ok?: boolean };
    return (data.result ?? "").trim() || "I asked but didn't get a clear answer back.";
  } catch (err) {
    console.warn(`[zoe-core] delegate-sync to ${target} failed:`, err);
    return `I couldn't reach ${target} right now.`;
  }
}

const research: CapabilityEntry = {
  id: "research",
  name: "research",
  domain: "delegate",
  description:
    "Hand off to Hermes for anything you can't answer yourself: live web search, current events, " +
    "prices/availability, news, sports scores, or complex multi-step research. Pass the user's full " +
    "question as `task`. Use this ONLY when the answer genuinely needs the live web or specialist " +
    "tools — never for chit-chat, things you remember, or the calendar/lists/notes/reminders/media/" +
    "home tools you already have.",
  parameters: Type.Object({
    task: Type.String({ description: "the full question or task to research, in plain language" }),
  }),
  examples: [
    "what's the weather forecast for the weekend",
    "find the cheapest flights to Sydney next month",
    "what's the latest news on the election",
    "look up the opening hours for the local pharmacy",
    "search the web for reviews of this restaurant",
  ],
  negativeExamples: [
    "what's on my shopping list",
    "add milk to the list",
    "what's my dog's name",
    "remind me to call mum at 5",
    "hello",
  ],
  // network + user-data:read: both freely allowed (no approval prompt) and
  // user-scoped, so the tool fails closed when the acting user is unknown.
  permissions: ["network", "user-data:read"],
  tier: "on-demand",
  triggers: [
    /\b(search|look up|google|find out|research)\b/i,
    /\b(latest|current|today'?s|recent)\b/i,
    /\bwhat'?s on\b/i,
    /\b(price|prices|cheapest|how much)\b/i,
    /\b(news|weather|forecast|score|results?)\b/i,
  ],
  async execute(params, ctx: AbilityContext): Promise<string> {
    const task = String(params.task ?? "").trim();
    if (!task) return "What would you like me to look into?";
    return delegateSync(ctx, task, "hermes");
  },
};

export default [research];
