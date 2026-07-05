/**
 * Per-message identity + dispatch decision for the Telegram channel, extracted
 * as a pure function so it is unit-testable without grammY or a bot token.
 *
 * Identity IS the gate (no static allow-list):
 *   resolve the verified sender's telegram_id → their real Zoe user_id.
 *     linked   → ask Zoe's brain AS that user and relay the reply.
 *     unlinked → guide them to link (self-service); NEVER run the brain for an
 *                unlinked sender, so an unknown/stranger id can reach no data.
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

/** Onboarding reply for an unlinked sender (id interpolated for manual linking). */
export function unlinkedMessage(telegramId: number): string {
  return (
    "You're not linked to a Zoe account yet. Open Zoe → Settings → Telegram and " +
    'scan the QR (or tap Connect), or send /start to begin. Prefer to type it? Your ' +
    `Telegram ID is ${telegramId}.`
  );
}

/**
 * Handle one text message. `telegramId` is the Telegram-verified sender id;
 * `chatId` the chat to reply into; `text` the message body.
 *
 * Identity is the gate: a linked sender runs the brain as their real Zoe user;
 * an unlinked sender is guided to link and NEVER reaches the brain (so an unknown
 * id can access no data). Linking requires a signed token minted in an
 * authenticated Zoe session, so "linked ⇒ allowed" needs no separate allow-list.
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
