/**
 * Shared dispatch helper for domain tools.
 *
 * Runs an allowlisted zoe-data intent for the current user via the internal
 * endpoint POST /api/system/intent-dispatch (X-Internal-Token), reusing the
 * exact fulfillment path the live chat uses (intent_router.execute_intent).
 * Returns the rendered, user-facing result string.
 */
import type { AbilityContext } from "./types";

const TIMEOUT_MS = Number(process.env.ZOE_CORE_DISPATCH_TIMEOUT_MS ?? 8000);

export async function dispatchIntent(
  ctx: AbilityContext,
  intent: string,
  slots: Record<string, unknown>,
): Promise<string> {
  const url = new URL("/api/system/intent-dispatch", ctx.zoeDataUrl);
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  if (ctx.internalToken) headers["X-Internal-Token"] = ctx.internalToken;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ user_id: ctx.userId, intent, slots }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!res.ok) {
      return `I couldn't do that right now (the ${intent.split("_")[0]} service returned ${res.status}).`;
    }
    const data = (await res.json()) as { result?: string; ok?: boolean };
    const result = (data.result ?? "").trim();
    if (data.ok === false) {
      // Endpoint fulfilled the HTTP request (200) but execute_intent itself
      // failed (returned None) -- do not report a false success. Per
      // services/zoe-data/routers/system.py's intent-dispatch, `ok` is
      // `result is not None`, so ok:false means the fulfillment path really
      // errored -- it is not "no result text" for a legitimate empty-but-
      // successful outcome (those are fixed upstream to return real text,
      // e.g. "No reminders set." -- see commit 467f9fdd).
      return `I couldn't do that (the ${intent.split("_")[0]} service didn't complete the request).`;
    }
    return result || "Done.";
  } catch (err) {
    // Don't swallow silently: surface the failure for observability while still
    // returning a calm, user-facing message (mirrors the abilities.ts pattern).
    console.warn(`[zoe-core] intent-dispatch failed for "${intent}":`, err);
    return "I couldn't reach that service right now.";
  }
}
