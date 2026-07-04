/**
 * Per-message identity + dispatch decision for the Telegram channel, extracted
 * as a pure function so it is unit-testable without grammY or a bot token.
 *
 * Flow (defence in depth):
 *   1. allow-list gate (done by the caller BEFORE us) — strangers never get here.
 *   2. resolve the verified sender's telegram_id → their real Zoe user_id.
 *        linked   → ask Zoe's brain AS that user and relay the reply.
 *        unlinked → tell them their numeric id + how to link; NEVER run the brain
 *                   as a real user for an unlinked sender.
 *
 * LAB ONLY.
 */

export interface IncomingDeps {
  /** Resolve a verified telegram id → Zoe user_id, or null if not linked. */
  resolve: (telegramId: number) => Promise<string | null>;
  /** Ask Zoe's brain AS the resolved user; returns the reply text. */
  ask: (text: string, sessionId: string, userId: string) => Promise<string>;
  /** Stable zoe-data session id for this chat. */
  session: (chatId: number) => string;
  /** Send a reply back to the chat. */
  reply: (text: string) => Promise<unknown>;
}

/** The exact refuse message for an unlinked sender (id interpolated). */
export function unlinkedMessage(telegramId: number): string {
  return (
    `You're not linked to a Zoe account yet — set your Telegram ID (${telegramId}) ` +
    'in your Zoe settings, then message me again.'
  );
}

/**
 * Handle one already-allow-listed text message. `telegramId` is the verified
 * sender id; `chatId` the chat to reply into; `text` the message body.
 */
export async function handleIncoming(
  telegramId: number,
  chatId: number,
  text: string,
  deps: IncomingDeps,
): Promise<void> {
  const userId = await deps.resolve(telegramId);
  if (!userId) {
    await deps.reply(unlinkedMessage(telegramId));
    return;
  }
  const answer = await deps.ask(text, deps.session(chatId), userId);
  await deps.reply(answer);
}

/**
 * Gate + dispatch a text message under the "linked OR allow-listed" policy:
 *   - linked (resolves to a Zoe user)      → always allowed; ask the brain as them.
 *     A linked account is a real Zoe user (linking needs a signed token minted in
 *     an authenticated session), so it is allowed even if not in the static list.
 *   - allow-listed but NOT linked          → onboard: tell them to link (they may
 *     be a known device that hasn't finished the QR/settings step).
 *   - neither                              → stranger; ignore silently (fail closed).
 */
export async function handleTextMessage(
  telegramId: number,
  chatId: number,
  text: string,
  isAllowed: (id: number) => boolean,
  deps: IncomingDeps,
): Promise<void> {
  const userId = await deps.resolve(telegramId);
  if (userId) {
    const answer = await deps.ask(text, deps.session(chatId), userId);
    await deps.reply(answer);
    return;
  }
  if (isAllowed(telegramId)) {
    await deps.reply(unlinkedMessage(telegramId));
  }
  // else: not linked and not allow-listed → stranger; say nothing.
}

/** Reply text for a `/start` with no/invalid/expired payload vs a successful link. */
export function startReply(linkedUserId: string | null, hadToken: boolean): string {
  if (linkedUserId) {
    return (
      `✅ Linked! This Telegram is now connected to your Zoe account. ` +
      `Say hi and I'll know it's you — your reminders, lists and memory come with you here.`
    );
  }
  if (hadToken) {
    return (
      'That link expired or was invalid. Open Zoe → Settings → Telegram, ' +
      'generate a fresh QR/link, and scan it again.'
    );
  }
  return (
    "Hi! I'm Zoe. To connect this Telegram to your Zoe account, open Zoe → " +
    'Settings → Telegram and scan the QR (or tap Connect Telegram).'
  );
}
