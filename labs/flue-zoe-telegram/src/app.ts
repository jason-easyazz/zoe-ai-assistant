/**
 * Flue app entry for the Zoe Telegram channel.
 *
 * Three jobs:
 *   1. Register Zoe's LOCAL Gemma brain as the `zoe` model provider. This is a
 *      SEPARATE provider/client from the voice path's own connection to
 *      llama-server — the voice fast-path is never routed through Flue. (They
 *      share the GPU, which is the one measured/accepted overlap; validate with
 *      the #735 latency probe before trusting it live.)
 *   2. Long-poll Telegram and hand each allowed user's message to the durable
 *      Zoe agent for that chat.
 *   3. Mount Flue's HTTP API + a /health route (so the process can be supervised).
 *
 * LAB ONLY. Not wired into any production Zoe service; voice path untouched.
 */
import { dispatch, registerProvider } from '@flue/runtime';
import { flue } from '@flue/runtime/routing';
import { Hono } from 'hono';
import zoe from './agents/zoe.ts';
import { bot, conversationKey, isAllowed } from './telegram.ts';

// Zoe's brain: the local OpenAI-compatible llama-server (Gemma E4B). A distinct
// provider id keeps this wholly separate from the live voice path's own client.
registerProvider('zoe', {
  api: 'openai-completions',
  baseUrl: process.env.ZOE_BRAIN_BASE_URL ?? 'http://127.0.0.1:11434/v1',
  apiKey: process.env.ZOE_BRAIN_API_KEY ?? 'not-needed',
});

// --- Telegram long-poll ingress -------------------------------------------
// Only text messages, only from allow-listed users, then dispatch to the durable
// Zoe agent for that chat (same chat => same session => context carries over).
bot.on('message:text', async (ctx) => {
  if (!isAllowed(ctx.from?.id)) return; // fail closed; strangers get nothing
  await dispatch(zoe, {
    id: conversationKey(ctx.chat.id),
    input: {
      type: 'telegram.message',
      text: ctx.message.text,
      from: ctx.from?.first_name ?? 'user',
    },
  });
});

// Take the bot over cleanly from any prior consumer (e.g. Hermes) and start
// polling. bot.start() runs the long-poll loop until the process stops, so it is
// fire-and-forget here; failures are logged, not fatal to the HTTP server.
void bot.api
  .deleteWebhook({ drop_pending_updates: true })
  .then(() =>
    bot.start({
      allowed_updates: ['message'],
      onStart: (me) => console.log(`Zoe Telegram bot @${me.username} polling`),
    }),
  )
  .catch((err) => console.error('Telegram poll error:', err));

const app = new Hono();
app.get('/health', (c) => c.json({ ok: true, service: 'flue-zoe-telegram' }));
app.route('/', flue());

export default app;
