/**
 * Bridge to Zoe's REAL brain — zoe-data's /api/chat. This is the same pipeline
 * voice + touch use: intent/semantic routing, her experts/tools (time, weather,
 * lists, calendar, timers), memory, and her real persona. We send the user's
 * text + a stable per-chat session id and relay the reply.
 *
 * This is why Telegram-Zoe is the REAL Zoe and not a generic Gemma chatbot: the
 * intelligence lives in zoe-data, and this channel just calls it.
 *
 * LAB ONLY.
 */
const BRAIN_URL = (process.env.ZOE_BRAIN_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '');

/** Stable zoe-data session id per Telegram chat, so memory/context carries over. */
export function sessionFor(chatId: number): string {
  return `telegram-${chatId}`;
}

/** Ask Zoe's brain and return her reply text. */
export async function askZoe(text: string, sessionId: string): Promise<string> {
  // Optional auth: an X-Session-ID (zoe-auth session) makes Zoe answer as the
  // logged-in user (with their memory) instead of guest. Unset = guest identity.
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (process.env.ZOE_BRAIN_SESSION) headers['X-Session-ID'] = process.env.ZOE_BRAIN_SESSION;
  if (process.env.ZOE_BRAIN_DEVICE_TOKEN) headers['X-Device-Token'] = process.env.ZOE_BRAIN_DEVICE_TOKEN;

  // Identify as the Telegram channel so fast_tiers applies the telegram profile
  // (Phase 1 / Seam C). Takes effect once the zoe-data side (PR #883) is deployed;
  // harmlessly ignored before that. NOTE: real per-user identity still requires
  // ZOE_BRAIN_SESSION / ZOE_BRAIN_DEVICE_TOKEN above — without one the brain runs
  // as guest. The allow-list gates WHO can reach the bridge; it does not by itself
  // map the verified sender to a Zoe user (that mapping is the operator-set token).
  const res = await fetch(`${BRAIN_URL}/api/chat/?stream=false`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message: text, session_id: sessionId, channel: 'telegram' }),
  });
  if (!res.ok) throw new Error(`brain ${res.status} ${await res.text().catch(() => '')}`);
  const data = (await res.json()) as { response?: string; error?: string };
  return data.response ?? data.error ?? "Sorry, I didn't get a reply.";
}
