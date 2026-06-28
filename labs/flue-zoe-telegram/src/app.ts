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
import { askZoe, sessionFor } from './brain.ts';
import { bot, isAllowed } from './telegram.ts';

// --- Telegram long-poll ingress -------------------------------------------
// Only text messages, only from allow-listed users. Ask Zoe's real brain
// (/api/chat) with a stable per-chat session id and relay her reply.
bot.on('message:text', async (ctx) => {
  if (!isAllowed(ctx.from?.id)) return; // fail closed; strangers get nothing
  try {
    const reply = await askZoe(ctx.message.text, sessionFor(ctx.chat.id));
    await ctx.reply(reply);
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
  .deleteWebhook({ drop_pending_updates: true })
  .then(() =>
    bot.start({
      allowed_updates: ['message'],
      onStart: (me) => {
        polling = true;
        console.log(`Zoe Telegram bot @${me.username} polling (took the bot over)`);
      },
    }),
  )
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
