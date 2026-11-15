/**
 * Per-message identity + dispatch decisions for the Telegram channel, extracted
 * as pure functions so they are unit-testable without grammY or a bot token.
 *
 * Identity IS the gate (no static allow-list):
 *   resolve the verified sender's telegram_id → their real Zoe user_id.
 *     linked   → ask Zoe's brain AS that user and relay the reply.
 *     unlinked → guide them to link (self-service); NEVER run the brain for an
 *                unlinked sender, so an unknown/stranger id can reach no data.
 *
 * PORT NOTE: on 1.x only `handleIncoming` lived here — the `/start` and `/new`
 * command bodies were written inline in `app.ts` inside grammY closures, which
 * made the `/new` identity gate (an unlinked stranger must not be able to grow
 * the epoch file or drive session churn) untestable. The port pulls both out as
 * `handleStart` / `handleNew` with the same injected-deps shape, so every
 * identity gate on the channel now has a negative control. `app.ts` is reduced
 * to grammY wiring.
 *
 * PRODUCTION since the 2026-08-09 cutover (auto-deployed).
 */
import { describeError } from './redact.ts';
import { framedForward } from './untrusted.ts';

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

/** Reply for a sender whose account is not linked: how to link (the QR / link flow in Settings; nothing to type). */
export function unlinkedMessage(): string {
  return (
    "This Telegram isn't connected to a Zoe account yet, so I can't act for you here. " +
    'To connect it, open Zoe → Settings → Telegram, tap Connect Telegram and scan the code ' +
    '(or tap the link), then send the message it opens.'
  );
}

/** Thrown by the redeemer when a link was refused (HTTP 409): this account is linked to another member, or the member is
 * already linked to a different account (a redemption never moves a link). Nothing was changed. */
export class LinkHeldError extends Error {
  constructor() {
    super('telegram account already linked to another member');
    this.name = 'LinkHeldError';
  }
}

/** Reply when a link was refused (see LinkHeldError): one message covers both causes. */
export const LINK_HELD_REPLY =
  'This Telegram is already connected to another Zoe account, or your Zoe account already has a different Telegram, ' +
  'so I did not change anything. Disconnect the old link in Zoe → Settings → Telegram first (an admin can too).';

/**
 * Handle one text message. `telegramId` is the Telegram-verified sender id;
 * `chatId` the chat to reply into; `text` the message body.
 *
 * Identity is the gate: a linked sender runs the brain as their real Zoe user;
 * an unlinked sender is guided to link and NEVER reaches the brain (so an unknown
 * id can access no data). A link exists only after a signed token minted in an
 * authenticated Zoe session was redeemed from this account, so "linked ⇒ allowed"
 * needs no separate allow-list.
 */
export async function handleIncoming(
  telegramId: number,
  chatId: number,
  text: string,
  deps: IncomingDeps,
  opts: { forwarded?: boolean } = {},
): Promise<void> {
  const userId = await deps.resolve(telegramId);
  if (!userId) {
    await deps.reply(unlinkedMessage());
    return;
  }
  // A FORWARDED message is someone else's words: fenced as untrusted data, never the member's command (src/untrusted.ts).
  const asked = opts.forwarded ? framedForward(text) : text;
  const answer = await deps.ask(asked, deps.session(chatId), userId);
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

export interface StartDeps {
  /** Redeem a signed link token for the VERIFIED sender id; throws LinkHeldError when the link is refused (409). */
  consume: (token: string, telegramId: number, username?: string) => Promise<string | null>;
  reply: (text: string) => Promise<unknown>;
}

/**
 * Handle `/start [token]`.
 *
 * Deliberately NOT identity-gated — a brand-new family member must be able to
 * onboard here. It is safe because redemption requires a valid signed token
 * minted in an authenticated Zoe session; an absent or bad token gets only a
 * generic reply and links nothing. An error is caught and answered generically
 * so a zoe-data hiccup never leaks a stack trace into a chat.
 */
export async function handleStart(
  telegramId: number,
  token: string,
  username: string | undefined,
  deps: StartDeps,
): Promise<void> {
  const trimmed = token.trim();
  try {
    const linkedUser = trimmed ? await deps.consume(trimmed, telegramId, username) : null;
    await deps.reply(startReply(linkedUser, Boolean(trimmed)));
  } catch (err) {
    if (err instanceof LinkHeldError) {
      await deps.reply(LINK_HELD_REPLY);
      return;
    }
    console.error(`Telegram link error: ${describeError(err)}`);
    await deps.reply('Something went wrong linking your account. Please try again in a moment.');
  }
}

export interface NewSessionDeps {
  resolve: (telegramId: number) => Promise<string | null>;
  /** Rotate this chat's session epoch (memories untouched). */
  bump: (chatId: number) => void;
  reply: (text: string) => Promise<unknown>;
}

/**
 * Handle `/new` (and `/reset`): rotate the chat's session epoch so the next turn
 * gets a fresh zoe-data context, leaving memories and old chat rows alone.
 *
 * LINKED SENDERS ONLY — the same identity gate as a normal message. An unlinked
 * stranger must not be able to grow the epoch file or drive session churn, so
 * `bump` must not be reached before `resolve` returns a user. That is the
 * assertion test/handler.test.ts negative-controls.
 */
export async function handleNew(
  telegramId: number,
  chatId: number,
  deps: NewSessionDeps,
): Promise<void> {
  try {
    const zoeUser = await deps.resolve(telegramId);
    if (!zoeUser) {
      await deps.reply('Please link your Zoe account first (Zoe settings → Telegram).');
      return;
    }
    deps.bump(chatId);
    await deps.reply('Okay — fresh conversation. Your memories are untouched; ask me anything.');
  } catch (err) {
    console.error(`Telegram /new error: ${describeError(err)}`);
    await deps.reply('Something went wrong starting a fresh conversation. Please try again.');
  }
}
