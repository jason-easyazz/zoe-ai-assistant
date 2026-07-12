/**
 * Flue app entry for the Zoe Telegram channel.
 *
 * Three jobs:
 *   1. Long-poll Telegram and, for each allow-listed user's message, ask Zoe's
 *      REAL brain (zoe-data's /api/chat via src/brain.ts) and relay the reply.
 *      The intelligence lives in zoe-data — this channel just calls it. The Flue
 *      LLM-agent path (src/agents/zoe.ts) is a build-time placeholder only and is
 *      never dispatched, so NO model provider is registered here. In particular
 *      the local voice Gemma on :11434 is NOT wired up (labs/AGENTS.md Forbidden).
 *   2. Take the bot over cleanly from any prior consumer (e.g. Hermes).
 *   3. Mount Flue's HTTP API + a /health route (so the process can be supervised);
 *      /health reports non-200 when long-polling is down.
 *
 * LAB ONLY. Not wired into any production Zoe service; voice path untouched.
 */
import { GrammyError } from 'grammy';
import { Hono } from 'hono';
import { flue } from '@flue/runtime/routing';
import { askZoeAs, bumpSession, consumeLinkToken, registerBotUsername, resolveTelegramUser, sessionFor } from './brain.ts';
import { handleIncoming, startReply } from './handler.ts';
import { bot } from './telegram.ts';

// --- /start deep-link linking ---------------------------------------------
// Self-service account linking: the user taps/scans a deep link from Zoe
// settings, which sends us `/start <token>`. We redeem the signed token via
// zoe-data, binding THIS verified Telegram id to the Zoe user it was minted for.
// NOT allow-list gated — a brand-new family member must be able to onboard here;
// linking is safe because it requires a valid signed token (an authenticated Zoe
// session), and invalid tokens get only a generic reply. Registered before the
// message:text handler so `/start` never falls through to the brain.
bot.command('start', async (ctx) => {
  const telegramId = ctx.from?.id;
  if (telegramId === undefined) return;
  const token = (ctx.match ?? '').trim();
  try {
    const linkedUser = token
      ? await consumeLinkToken(token, telegramId, ctx.from?.username)
      : null;
    await ctx.reply(startReply(linkedUser, Boolean(token)));
  } catch (err) {
    console.error('Telegram link error:', err);
    await ctx.reply('Something went wrong linking your account. Please try again in a moment.');
  }
});

// --- /new: fresh conversation session --------------------------------------
// A long-lived per-chat session can get poisoned (Zoe's own wrong denial echoes
// on every retry, outvoting the memory packet). /new rotates the session epoch:
// fresh zoe-data + sidecar context, memories and old chat rows untouched.
// Registered before message:text so it never falls through to the brain.
bot.command(['new', 'reset'], async (ctx) => {
  try {
    bumpSession(ctx.chat.id);
    await ctx.reply("Okay — fresh conversation. Your memories are untouched; ask me anything.");
  } catch (err) {
    console.error('Telegram /new error:', err);
    await ctx.reply('Something went wrong starting a fresh conversation. Please try again.');
  }
});

// --- Telegram long-poll ingress -------------------------------------------
// Identity IS the gate (no static allow-list): handleIncoming (src/handler.ts,
// unit-tested without grammY) resolves the sender → their Zoe user and runs the
// brain AS them; an unlinked sender is guided to link and never reaches the
// brain. Linking requires a signed token from an authenticated Zoe session, so
// "linked ⇒ allowed" is a sufficient gate for real-user access.
bot.on('message:text', async (ctx) => {
  const telegramId = ctx.from?.id;
  if (telegramId === undefined) return; // no verified sender id → nothing to resolve

  try {
    await handleIncoming(telegramId, ctx.chat.id, ctx.message.text, {
      resolve: resolveTelegramUser,
      ask: askZoeAs,
      session: sessionFor,
      reply: (text) => ctx.reply(text),
    });
  } catch (err) {
    console.error('Zoe brain/reply error:', err);
  }
});

// --- Poll health ----------------------------------------------------------
// If deleteWebhook()/bot.start() never succeeds (or later dies), Telegram is
// effectively down. Track that so /health can fail and a supervisor notices,
// instead of the process looking healthy while it serves nobody.
let polling = false;

// Take the bot over cleanly from any prior consumer (e.g. Hermes) and start
// polling. bot.start() runs the long-poll loop until the process stops, so it is
// fire-and-forget here; failures flip `polling` to false and fail /health.
void bot.api
  // Do NOT drop pending updates: during a long-poll takeover we haven't proven we
  // own the bot yet. If Hermes is still polling this token, dropping here would
  // discard queued user messages and THEN bot.start() would 409 — losing messages
  // neither poller can recover. Just clear any webhook; queued updates survive.
  .deleteWebhook()
  .then(() =>
    bot.start({
      allowed_updates: ['message'],
      onStart: (me) => {
        polling = true;
        console.log(`Zoe Telegram bot @${me.username} polling (took the bot over)`);
        // Tell zoe-data our @username so the settings UI can build deep links.
        void registerBotUsername(me.username);
      },
    }),
  )
  .then(() => {
    // bot.start() resolves when the long-poll loop STOPS (process shutdown or the
    // loop dying after onStart ran). Clear `polling` so /health flips to 503 and a
    // supervisor restarts us, instead of looking healthy while serving nobody.
    polling = false;
    console.warn('Telegram long-poll loop stopped; marking unhealthy.');
  })
  .catch((err) => {
    polling = false;
    if (err instanceof GrammyError && err.error_code === 409) {
      // 409 Conflict = another getUpdates consumer (almost certainly Hermes's
      // poller) is still polling this same token. Two consumers fight over
      // updates; stop the other one first. We do NOT retry — a retry storm just
      // hammers Telegram. Operator must stop the other consumer and restart.
      console.error(
        'Telegram 409 Conflict: another consumer is still polling this bot ' +
          'token (likely Hermes). Stop it first, then restart this app. ' +
          'Not retrying.',
      );
    } else {
      console.error('Telegram poll error:', err);
    }
  });

const app = new Hono();
app.get('/health', (c) =>
  polling
    ? c.json({ ok: true, service: 'flue-zoe-telegram', polling: true })
    : c.json({ ok: false, service: 'flue-zoe-telegram', polling: false }, 503),
);
app.route('/', flue());

export default app;
