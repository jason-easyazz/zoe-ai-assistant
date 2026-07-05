/**
 * Bridge to Zoe's REAL brain — zoe-data's /api/chat. This is the same pipeline
 * voice + touch use: intent/semantic routing, her experts/tools (time, weather,
 * lists, calendar, timers), memory, and her real persona. We send the user's
 * text + a stable per-chat session id and relay the reply.
 *
 * This is why Telegram-Zoe is the REAL Zoe and not a generic Gemma chatbot: the
 * intelligence lives in zoe-data, and this channel just calls it.
 *
 * ACCOUNT LINKING: a Telegram sender is mapped to their real Zoe user via
 * /api/system/resolve-telegram/<telegram_id> (they store their numeric id in
 * their Zoe profile). Once resolved, we forward that user_id to /api/chat over
 * zoe-data's TRUSTED internal path (X-Zoe-User-Id + X-Internal-Token), so the
 * turn runs as the real user (with their memory) instead of as guest.
 *
 * LAB ONLY.
 */

// zoe-data base URL. ZOE_DATA_URL is the documented name; ZOE_BRAIN_URL is kept
// as a back-compat alias for the earlier config. Default is correct on the Jetson.
const DATA_URL = (process.env.ZOE_DATA_URL ?? process.env.ZOE_BRAIN_URL ?? 'http://127.0.0.1:8000').replace(
  /\/$/,
  '',
);

// Shared secret for zoe-data's internal endpoints (resolver + trusted /api/chat
// user override). On loopback zoe-data also accepts no token, but sending it is
// harmless and lets the bot run off-box if ever needed.
const INTERNAL_TOKEN = process.env.ZOE_INTERNAL_TOKEN ?? '';

function internalHeaders(): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json' };
  if (INTERNAL_TOKEN) h['X-Internal-Token'] = INTERNAL_TOKEN;
  return h;
}

/** Stable zoe-data session id per Telegram chat, so memory/context carries over. */
export function sessionFor(chatId: number): string {
  return `telegram-${chatId}`;
}

/**
 * Resolve a verified Telegram user id to a Zoe user_id via zoe-data's internal
 * resolver. Returns the user_id string if the sender has linked their account,
 * or null if not linked (they must set their telegram_id in Zoe settings).
 */
export async function resolveTelegramUser(telegramId: number): Promise<string | null> {
  const res = await fetch(`${DATA_URL}/api/system/resolve-telegram/${telegramId}`, {
    method: 'GET',
    headers: internalHeaders(),
  });
  if (!res.ok) {
    throw new Error(`resolve-telegram ${res.status} ${await res.text().catch(() => '')}`);
  }
  const data = (await res.json()) as { user_id?: string | null };
  return data.user_id ?? null;
}

/**
 * Redeem a self-service link token: the user tapped/scanned a deep link from Zoe
 * settings and sent us `/start <token>`. We forward the token + the VERIFIED
 * sender id to zoe-data, which links that Telegram account to the Zoe user the
 * token was minted for. Returns the linked Zoe user_id, or null if the token was
 * invalid/expired.
 */
export async function consumeLinkToken(
  token: string,
  telegramId: number,
  telegramUsername?: string,
): Promise<string | null> {
  const res = await fetch(`${DATA_URL}/api/system/telegram/consume-link-token`, {
    method: 'POST',
    headers: internalHeaders(),
    body: JSON.stringify({
      token,
      telegram_id: String(telegramId),
      telegram_username: telegramUsername ?? null,
    }),
  });
  if (res.status === 400) return null; // invalid / expired token
  if (!res.ok) throw new Error(`consume-link-token ${res.status} ${await res.text().catch(() => '')}`);
  const data = (await res.json()) as { user_id?: string | null };
  return data.user_id ?? null;
}

/**
 * Tell zoe-data our @username at startup so the settings UI can build
 * `https://t.me/<bot>?start=<token>` deep links. Best-effort — logs and swallows
 * failures so a transient zoe-data hiccup never blocks the bot from polling.
 */
export async function registerBotUsername(username: string): Promise<void> {
  try {
    const res = await fetch(`${DATA_URL}/api/system/telegram/register-bot`, {
      method: 'POST',
      headers: internalHeaders(),
      body: JSON.stringify({ username }),
    });
    if (!res.ok) console.warn(`register-bot ${res.status} ${await res.text().catch(() => '')}`);
  } catch (e) {
    console.warn('register-bot failed (non-fatal):', e);
  }
}

/**
 * Ask Zoe's brain AS a resolved Zoe user and return her reply text.
 *
 * The user_id is forwarded via zoe-data's trusted internal path (X-Zoe-User-Id +
 * X-Internal-Token). zoe-data only honours the override for internal callers
 * (loopback / valid token); a public request that sets the header is ignored, so
 * this cannot be used to impersonate a user from outside.
 */
export async function askZoeAs(text: string, sessionId: string, userId: string): Promise<string> {
  const headers = internalHeaders();
  // TRUSTED forwarded identity: run the turn as this real Zoe user.
  headers['X-Zoe-User-Id'] = userId;

  const res = await fetch(`${DATA_URL}/api/chat/?stream=false`, {
    method: 'POST',
    headers,
    // channel:'telegram' tags the turn so fast_tiers can apply the telegram profile.
    body: JSON.stringify({ message: text, session_id: sessionId, channel: 'telegram' }),
  });
  if (!res.ok) throw new Error(`brain ${res.status} ${await res.text().catch(() => '')}`);
  const data = (await res.json()) as { response?: string; error?: string };
  return data.response ?? data.error ?? "Sorry, I didn't get a reply.";
}
