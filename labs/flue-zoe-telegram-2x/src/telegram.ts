/**
 * Telegram transport for the Zoe channel — project-owned grammY bot + helpers.
 *
 * Long-polling (not webhook): the bot reaches OUT to Telegram, so nothing is
 * exposed on the Jetson — no public ingress, no Cloudflare route. This mirrors
 * how Hermes ran its bot, and is the private/NAT-friendly choice for a home box.
 *
 * The bot's `api` doubles as the outbound client used by both the reply tool and
 * any proactive ("home channel") push.
 *
 * FLUE 2.x DELTA: `defineTool` survives the upgrade, but its runtime contract
 * changed in two places (@flue/runtime docs/guide/migration.md, "Tools"):
 * `run({ input })` → `run({ data })`, and `run()` must now return a RESULT
 * ENVELOPE `{ output?, terminate? }` — a bare object throws at runtime. Both are
 * applied below and pinned by test/tool_shape.test.ts.
 *
 * LAB ONLY. Nothing here touches the live voice path.
 */
import { defineTool } from '@flue/runtime';
import { Bot } from 'grammy';
import * as v from 'valibot';

export function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required (see .env.example).`);
  return value;
}

/**
 * Telegram Bot API root. Defaults to the real service; overridable so the
 * OFFLINE test suite can point the whole transport at a local mock Bot API and
 * exercise takeover / polling / send for real without ever reaching
 * api.telegram.org (see test/helpers/mock-telegram.ts).
 *
 * This is the ONLY new environment variable the 2.x port introduces, it is
 * additive, and the default is the 1.x behaviour byte for byte. Never set it on
 * the deployed unit.
 */
export const TELEGRAM_API_ROOT = (process.env.TELEGRAM_API_ROOT ?? 'https://api.telegram.org').replace(
  /\/$/,
  '',
);

/** The bot owns both long-poll ingress (bot.start) and outbound (bot.api). */
export const bot = new Bot(requiredEnv('TELEGRAM_BOT_TOKEN'), {
  client: { apiRoot: TELEGRAM_API_ROOT },
});

// No static allow-list: identity is the gate. A sender only reaches Zoe's brain
// if their telegram_id resolves to a linked Zoe user (see src/handler.ts), and
// linking requires a signed token minted in an authenticated Zoe session. An
// unlinked/unknown sender is only ever guided to link — it can access no data.

// The agent instance id encodes the chat, so the same chat reopens the same
// durable session. Keep it a simple, reversible string.
export function conversationKey(chatId: number): string {
  return `telegram:chat:${chatId}`;
}

export function chatIdFromKey(id: string): number {
  const raw = id.split(':').pop();
  const n = Number(raw);
  if (!Number.isFinite(n)) throw new Error(`Unparseable conversation key: ${id}`);
  return n;
}

/**
 * Reply tool bound to one chat. Because dispatch() is async (no synchronous
 * response to return), an agent MUST call this to actually answer the user.
 *
 * Dormant on this channel by construction — src/agents/zoe.ts is a placeholder
 * that is never dispatched — but it is the one place the 2.x tool contract is
 * expressed, so it is ported properly and tested rather than left to rot.
 */
export function postMessage(chatId: number) {
  return defineTool({
    name: 'post_telegram_message',
    description: 'Send your reply to the Telegram chat you are talking to. Always use this to answer.',
    input: v.object({ text: v.pipe(v.string(), v.minLength(1)) }),
    // 2.x: an explicit output schema, so the `{ output }` envelope below is
    // validated rather than merely conventional.
    output: v.object({ messageId: v.number() }),
    async run({ data }) {
      const message = await bot.api.sendMessage(chatId, data.text);
      return { output: { messageId: message.message_id } };
    },
  });
}
