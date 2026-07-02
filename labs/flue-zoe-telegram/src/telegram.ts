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
 * LAB ONLY. Nothing here touches the live voice path.
 */
import { defineTool } from '@flue/runtime';
import { Bot } from 'grammy';
import * as v from 'valibot';

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required (see .env.example).`);
  return value;
}

/** The bot owns both long-poll ingress (bot.start) and outbound (bot.api). */
export const bot = new Bot(requiredEnv('TELEGRAM_BOT_TOKEN'));

/**
 * Allow-list of Telegram user IDs (comma-separated `TELEGRAM_ALLOWED_USERS`).
 * Fail CLOSED: an empty list rejects everyone, so a misconfigured deploy can't
 * accidentally expose Zoe to strangers who message the bot.
 */
const allowed = new Set(
  (process.env.TELEGRAM_ALLOWED_USERS ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
);

export function isAllowed(userId: number | undefined): boolean {
  return userId !== undefined && allowed.has(String(userId));
}

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
 * response to return), the agent MUST call this to actually answer the user.
 */
export function postMessage(chatId: number) {
  return defineTool({
    name: 'post_telegram_message',
    description: 'Send your reply to the Telegram chat you are talking to. Always use this to answer.',
    input: v.object({ text: v.pipe(v.string(), v.minLength(1)) }),
    async run({ input }) {
      const message = await bot.api.sendMessage(chatId, input.text);
      return { messageId: message.message_id };
    },
  });
}
