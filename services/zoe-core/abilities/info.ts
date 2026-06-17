/**
 * Reference domain tool: `info` — weather / time / date.
 *
 * Demonstrates the CapabilityEntry contract every domain tool follows:
 * time/date answer locally; weather calls zoe-data. Copy this shape for
 * calendar, lists, notes, people, media, home, etc.
 */
import { Type } from "typebox";
import type { CapabilityEntry } from "./types";

const info: CapabilityEntry = {
  id: "info.query",
  name: "info",
  domain: "info",
  description:
    "Answer weather, current time, and date questions. Use when the user asks about weather/forecast/temperature, what time it is, or what today's date/day is.",
  parameters: Type.Object({
    kind: Type.String({ description: "one of: weather | time | date" }),
  }),
  examples: ["what's the weather", "is it going to rain", "what time is it", "what's the date today"],
  negativeExamples: ["set a timer for ten minutes", "add milk to my shopping list"],
  permissions: ["read-only", "network"],
  tier: "on-demand",
  triggers: [
    /\b(weather|rain|forecast|temperature|temp|umbrella|jacket|hot|cold|sunny|wind)\b/i,
    /\b(time|clock|o'?clock|date|day|today)\b/i,
  ],
  async execute(params, ctx) {
    const kind = String(params.kind ?? "").toLowerCase();
    if (kind === "time") {
      return `It's ${new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}.`;
    }
    if (kind === "date") {
      return `Today is ${new Date().toLocaleDateString(undefined, {
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
      })}.`;
    }
    // weather → zoe-data
    try {
      const url = new URL("/api/weather/current", ctx.zoeDataUrl);
      const headers: Record<string, string> = { Accept: "application/json" };
      if (ctx.internalToken) headers["X-Internal-Token"] = ctx.internalToken;
      const res = await fetch(url, { headers, signal: AbortSignal.timeout(4000) });
      if (!res.ok) return "I couldn't reach the weather service right now.";
      const d = (await res.json()) as Record<string, any>;
      const cur = d.current ?? d;
      const temp = cur.temp ?? cur.temperature;
      const desc = cur.description ?? cur.summary ?? "";
      const city = cur.city ?? "your area";
      return temp != null
        ? `It's ${temp}°${desc ? ` and ${desc}` : ""} in ${city}.`
        : "I don't have the current weather right now.";
    } catch {
      return "I couldn't reach the weather service right now.";
    }
  },
};

export default [info];
