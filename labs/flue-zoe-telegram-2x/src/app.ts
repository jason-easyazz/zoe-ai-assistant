/**
 * Flue app entry for the Zoe Telegram channel (Flue 2.x).
 *
 * Three jobs, unchanged from the beta:
 *   1. Long-poll Telegram and, for each verified sender's message, ask Zoe's
 *      REAL brain (zoe-data's /api/chat via src/brain.ts) and relay the reply.
 *      The intelligence lives in zoe-data — this channel just calls it. The Flue
 *      LLM-agent path (src/agents/zoe.ts) is a placeholder only and is never
 *      dispatched, so NO model provider is registered here. In particular the
 *      local voice Gemma on :11434 is NOT wired up (labs/AGENTS.md Forbidden).
 *   2. Take the bot over cleanly from any prior consumer (e.g. Hermes).
 *   3. Mount the HTTP surface + a /health route (so the process can be
 *      supervised); /health reports non-200 when long-polling is down. Plus
 *      `POST /internal/notify`: zoe-data asks us to message a linked member
 *      with this bot's token ("send to my phone"; src/notify.ts).
 *
 * WHAT CHANGED FROM THE BETA:
 *
 *   - `app.route('/', flue())` → `app.route('/agents/zoe', createAgentRouter(...))`.
 *     The auto-mounted router and discovery-by-directory are deleted in 2.x;
 *     every route is mounted by hand. The URL SHAPE IS DELIBERATELY UNCHANGED.
 *   - The grammY handler bodies moved out of this file into pure functions in
 *     src/handler.ts, so the `/new` identity gate is unit-testable.
 *   - The route map is now a `createApp()` FACTORY and the poll bootstrap a
 *     `startTelegramPolling()` function. Both are still invoked at module load,
 *     so deployed behaviour is identical; splitting them is what lets the
 *     offline suite drive the REAL route map and the REAL takeover sequence
 *     against a mock Bot API instead of a hand-rolled copy that could drift.
 *
 * MOUNTING IS NOT REGISTRATION. `createAgentRouter(ZoeTelegram)` serves the
 * routes; what makes it an agent at all is the `'use agent'` directive scan run
 * by the `@flue/vite` plugin at build time. Both are required.
 *
 * PRODUCTION since the 2026-08-09 cutover: flue-zoe-telegram.service runs this
 * build on :3582 and deploy.yml auto-deploys diffs here. Voice path untouched.
 */
import { GrammyError, InlineKeyboard, InputFile } from 'grammy';
import { Hono } from 'hono';
import { createAgentRouter } from '@flue/runtime/routing';
import { ZoeTelegram } from './agents/zoe.ts';
import {
  askZoeAs,
  bumpSession,
  consumeLinkToken,
  registerBotUsername,
  resolveTelegramUser,
  sessionFor,
  synthesizeTelegramVoice,
  transcribeTelegramAudio,
} from './brain.ts';
import { handleIncoming, handleNew, handleStart } from './handler.ts';
import { handleNotify, isLoopbackAddress } from './notify.ts';
import { bot, downloadTelegramFile } from './telegram.ts';
import { describeError } from './redact.ts';
import {
  MAX_NOTE_BYTES,
  handleTalk,
  handleVoiceNote,
  optionsFromEnv,
  talkPageUrl,
  voiceNotesEnabled,
} from './voice.ts';

/**
 * Poll health. If deleteWebhook()/bot.start() never succeeds (or later dies),
 * Telegram is effectively down. Track that so /health can fail and the watchdog
 * timer notices, instead of the process looking healthy while it serves nobody.
 */
export interface PollHealth {
  polling: boolean;
}

export const pollHealth: PollHealth = { polling: false };

/** Register the grammY handlers. Ordering is load-bearing: the command handlers
 *  must be registered BEFORE `message:text` so `/start` and `/new` never fall
 *  through to the brain. */
export function registerTelegramHandlers(): void {
  // --- /start deep-link linking ---------------------------------------------
  // Self-service account linking: the user taps/scans a deep link from Zoe
  // settings, which sends us `/start <token>`. Not identity-gated — a brand-new
  // family member must be able to onboard — and safe because redemption needs a
  // valid signed token from an authenticated Zoe session (see handler.ts).
  bot.command('start', async (ctx) => {
    const telegramId = ctx.from?.id;
    if (telegramId === undefined) return;
    await handleStart(telegramId, ctx.match ?? '', ctx.from?.username, {
      consume: consumeLinkToken,
      reply: (text) => ctx.reply(text),
    });
  });

  // --- /new: fresh conversation session --------------------------------------
  // A long-lived per-chat session can get poisoned (Zoe's own wrong denial echoes
  // on every retry, outvoting the memory packet). /new rotates the session epoch:
  // fresh zoe-data context, memories and old chat rows untouched. LINKED senders
  // only — same identity gate as the message handler.
  bot.command(['new', 'reset'], async (ctx) => {
    const telegramId = ctx.from?.id;
    if (telegramId === undefined) return;
    await handleNew(telegramId, ctx.chat.id, {
      resolve: resolveTelegramUser,
      bump: bumpSession,
      reply: (text) => ctx.reply(text),
    });
  });

  // --- /talk: hand the phone the existing push-to-talk page ------------------
  // Registered only when ZOE_BASE_URL names the public base URL; otherwise
  // `/talk` keeps falling through to the brain as text (today's behaviour).
  // BEFORE `message:text` like every command, or the text handler eats it.
  const talkUrl = talkPageUrl();
  if (talkUrl) {
    bot.command('talk', async (ctx) => {
      const telegramId = ctx.from?.id;
      if (telegramId === undefined) return;
      await handleTalk(telegramId, talkUrl, {
        resolve: resolveTelegramUser,
        reply: (text) => ctx.reply(text),
        replyWithLink: (text, label, url) =>
          ctx.reply(text, { reply_markup: new InlineKeyboard().url(label, url) }),
      });
    });
  }

  // --- Telegram long-poll ingress -------------------------------------------
  // Identity IS the gate (no static allow-list): handleIncoming resolves the
  // sender → their Zoe user and runs the brain AS them; an unlinked sender is
  // guided to link and never reaches the brain. Linking requires a signed token
  // from an authenticated Zoe session, so "linked ⇒ allowed" is sufficient.
  bot.on('message:text', async (ctx) => {
    const telegramId = ctx.from?.id;
    if (telegramId === undefined) return; // no verified sender id → nothing to resolve

    try {
      await handleIncoming(
        telegramId,
        ctx.chat.id,
        ctx.message.text,
        {
          resolve: resolveTelegramUser,
          ask: askZoeAs,
          session: sessionFor,
          reply: (text) => ctx.reply(text),
        },
        { forwarded: ctx.message.forward_origin !== undefined },
      );
    } catch (err) {
      console.error(`Zoe brain/reply error: ${describeError(err)}`);
    }
  });

  // --- Voice notes in and out (flag-dark) ------------------------------------
  // ZOE_TELEGRAM_VOICE_NOTES unset → NO handler: a voice note is received by
  // the poll loop and ignored, exactly as today. On → the same identity gate
  // and the same /api/chat turn as text, replying in kind (src/voice.ts).
  if (voiceNotesEnabled()) {
    const opts = optionsFromEnv();
    bot.on(['message:voice', 'message:audio'], async (ctx) => {
      const telegramId = ctx.from?.id;
      if (telegramId === undefined) return;
      const media = ctx.message.voice ?? ctx.message.audio;
      if (!media) return;
      try {
        await handleVoiceNote(
          telegramId,
          ctx.chat.id,
          {
            kind: ctx.message.voice ? 'voice' : 'audio',
            fileId: media.file_id,
            fileUniqueId: media.file_unique_id,
            duration: media.duration,
            fileSize: media.file_size,
            forwarded: ctx.message.forward_origin !== undefined,
          },
          {
            resolve: resolveTelegramUser,
            session: sessionFor,
            ask: askZoeAs,
            download: (fileId) => downloadTelegramFile(fileId, MAX_NOTE_BYTES),
            transcribe: transcribeTelegramAudio,
            synthesize: synthesizeTelegramVoice,
            reply: (text) => ctx.reply(text),
            replyVoice: (ogg) => ctx.replyWithVoice(new InputFile(ogg, 'zoe.ogg')),
          },
          opts,
        );
      } catch (err) {
        console.error(`Zoe voice-note error: ${describeError(err)}`);
      }
    });
  }
}

/**
 * Take the bot over cleanly from any prior consumer (e.g. Hermes) and start
 * long-polling. `bot.start()` runs the loop until the process stops, so this is
 * fire-and-forget; failures flip `health.polling` to false and fail /health.
 */
export function startTelegramPolling(health: PollHealth = pollHealth): Promise<void> {
  return (
    bot.api
      // Do NOT drop pending updates: during a long-poll takeover we haven't proven
      // we own the bot yet. If another consumer is still polling this token,
      // dropping here would discard queued user messages and THEN bot.start()
      // would 409 — losing messages neither poller can recover. Just clear any
      // webhook; queued updates survive.
      .deleteWebhook()
      .then(() =>
        bot.start({
          allowed_updates: ['message'],
          onStart: (me) => {
            health.polling = true;
            console.log(`Zoe Telegram bot @${me.username} polling (took the bot over)`);
            // Tell zoe-data our @username so the settings UI can build deep links.
            void registerBotUsername(me.username);
          },
        }),
      )
      .then(() => {
        // bot.start() resolves when the long-poll loop STOPS (process shutdown or
        // the loop dying after onStart ran). Clear `polling` so /health flips to
        // 503 and the watchdog restarts us, instead of looking healthy while
        // serving nobody.
        health.polling = false;
        console.warn('Telegram long-poll loop stopped; marking unhealthy.');
      })
      .catch((err) => {
        health.polling = false;
        if (err instanceof GrammyError && err.error_code === 409) {
          // 409 Conflict = another getUpdates consumer (almost certainly a stray
          // poller) is still polling this same token. Two consumers fight over
          // updates; stop the other one first. We do NOT retry — a retry storm
          // just hammers Telegram. Operator must stop the other consumer and
          // restart.
          console.error(
            'Telegram 409 Conflict: another consumer is still polling this bot ' +
              'token (likely Hermes). Stop it first, then restart this app. ' +
              'Not retrying.',
          );
        } else {
          console.error(`Telegram poll error: ${describeError(err)}`);
        }
      })
  );
}

/** Send one notify message: a URL button when given; plain text with the URL if
 *  Telegram refuses the button (it rejects some LAN hostnames as button URLs). */
async function sendNotify(chatId: number, text: string, link?: { label: string; url: string }): Promise<void> {
  if (!link) {
    await bot.api.sendMessage(chatId, text, { link_preview_options: { is_disabled: true } });
    return;
  }
  try {
    await bot.api.sendMessage(chatId, text, {
      reply_markup: new InlineKeyboard().url(link.label, link.url),
      link_preview_options: { is_disabled: true },
    });
  } catch (err) {
    if (!(err instanceof GrammyError && err.error_code === 400)) throw err;
    await bot.api.sendMessage(chatId, `${text}\n${link.url}`, { link_preview_options: { is_disabled: true } });
  }
}

/**
 * Build the channel's HTTP route map.
 *
 * A FACTORY, not a module-level block, so the tests exercise THIS wiring — the
 * real mount path, the real health semantics — rather than a test-only copy.
 */
export function createApp(health: PollHealth = pollHealth): Hono {
  const app = new Hono();

  // Liveness probe. 200 only while long-polling is up; 503 otherwise, which is
  // exactly what flue-zoe-telegram-watchdog.timer polls for once a minute (a
  // dead poll loop inside a live process is invisible to systemd's
  // Restart=always). Deploy/rollback both depend on this contract — keep the
  // body shape stable.
  app.get('/health', (c) =>
    health.polling
      ? c.json({ ok: true, service: 'flue-zoe-telegram', polling: true })
      : c.json({ ok: false, service: 'flue-zoe-telegram', polling: false }, 503),
  );

  // The placeholder agent's HTTP surface, mounted at the same path the beta's
  // auto-router served it from. Never used by this channel; see agents/zoe.ts.
  app.route('/agents/zoe', createAgentRouter(ZoeTelegram));

  // zoe-data → a linked member's chat ("send to my phone"), sent with THIS bot's
  // token. Shared-secret + linked-member gated; see src/notify.ts.
  app.post('/internal/notify', async (c) => {
    // We listen on every interface: a loopback socket is required (unknown = refused).
    const remote = (c.env as { incoming?: { socket?: { remoteAddress?: string } } } | undefined)?.incoming?.socket
      ?.remoteAddress;
    if (!remote || !isLoopbackAddress(remote)) return c.json({ ok: false, error: 'loopback only' }, 403);
    const body = await c.req.json().catch(() => null);
    const result = await handleNotify(c.req.header('authorization'), body, {
      token: process.env.ZOE_INTERNAL_TOKEN,
      resolve: resolveTelegramUser,
      send: sendNotify,
    });
    return c.json(result.body, result.status);
  });

  return app;
}

registerTelegramHandlers();
void startTelegramPolling(pollHealth);

export default createApp(pollHealth);
