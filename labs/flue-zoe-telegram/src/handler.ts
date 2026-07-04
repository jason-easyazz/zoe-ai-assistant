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
