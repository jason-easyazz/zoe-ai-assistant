/**
 * A mock Telegram Bot API, good enough for the real grammY client.
 *
 * WHY IT EXISTS. The channel's whole job is the takeover-and-long-poll sequence
 * (deleteWebhook → getMe → getUpdates → sendMessage). Stubbing `handleIncoming`'s
 * deps proves the DECISIONS; only a real transport proves the WIRING — that the
 * commands are registered before `message:text`, that `onStart` flips /health to
 * 200, that a 409 flips it back to 503, and that a reply actually leaves through
 * `bot.api`. So the suite speaks the Bot API to grammY over loopback instead.
 *
 * NO REAL TELEGRAM TRAFFIC EVER. `src/telegram.ts` builds its `Bot` with
 * `client.apiRoot = TELEGRAM_API_ROOT`; the tests set that to this server's URL
 * BEFORE the dynamic import. If that env were ever dropped the requests would go
 * to api.telegram.org with a fake token and fail 401 — noisy, not silent, and
 * test/offline_transport.test.ts asserts the mock actually received the calls,
 * so a leak fails the suite rather than passing quietly.
 *
 * Also runnable directly (`node --experimental-strip-types mock-telegram.ts <port>`)
 * — smoke-built.sh uses that to give the BUILT server something to poll.
 */
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export interface SentMessage {
  chat_id: number | string;
  text: string;
}

export interface MockTelegram {
  /** Base URL to hand to grammY as `apiRoot` (no trailing slash). */
  url: string;
  /** Every sendMessage the bot made, in order. */
  sent: SentMessage[];
  /** Every Bot API method called, in order. */
  methods: string[];
  /** Queue an update for the next getUpdates poll. */
  push(update: unknown): void;
  /** Make the NEXT getUpdates answer 409 Conflict (another consumer holds the token). */
  failNextPollWith409(): void;
  close(): Promise<void>;
}

let nextUpdateId = 1000;
let nextMessageId = 1;

/** Build a minimal `message:text` update from a verified sender. */
export function textUpdate(telegramId: number, chatId: number, text: string, username?: string) {
  return {
    update_id: nextUpdateId++,
    message: {
      message_id: nextMessageId++,
      date: Math.floor(Date.now() / 1000),
      chat: { id: chatId, type: 'private' },
      from: { id: telegramId, is_bot: false, first_name: 'Test', username },
      text,
      // grammY's `bot.command()` filter needs the bot_command entity to fire.
      ...(text.startsWith('/')
        ? { entities: [{ type: 'bot_command', offset: 0, length: text.split(' ')[0].length }] }
        : {}),
    },
  };
}

export async function startMockTelegram(port = 0): Promise<MockTelegram> {
  const sent: SentMessage[] = [];
  const methods: string[] = [];
  const pending: unknown[] = [];
  let conflictOnce = false;
  // Long-poll requests parked until an update arrives or the server closes.
  const waiters = new Set<(value: void) => void>();

  const server: Server = createServer((req, res) => {
    const method = (req.url ?? '').split('/').pop() ?? '';
    methods.push(method);

    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
    });
    req.on('end', async () => {
      const json = (payload: unknown, status = 200) => {
        res.writeHead(status, { 'content-type': 'application/json' });
        res.end(JSON.stringify(payload));
      };
      const parsed: Record<string, unknown> = body ? JSON.parse(body) : {};

      switch (method) {
        case 'getMe':
          return json({
            ok: true,
            result: { id: 42, is_bot: true, first_name: 'Zoe', username: 'zoe_test_bot' },
          });
        case 'deleteWebhook':
          return json({ ok: true, result: true });
        case 'getUpdates': {
          if (conflictOnce) {
            conflictOnce = false;
            return json(
              {
                ok: false,
                error_code: 409,
                description:
                  'Conflict: terminated by other getUpdates request; make sure that only one bot instance is running',
              },
              409,
            );
          }
          if (pending.length === 0) {
            // Park like the real long poll, so the bot does not hot-loop.
            await new Promise<void>((resolve) => {
              waiters.add(resolve);
              // Bound it so a test that queues nothing still terminates.
              setTimeout(() => {
                waiters.delete(resolve);
                resolve();
              }, 150).unref();
            });
          }
          return json({ ok: true, result: pending.splice(0, pending.length) });
        }
        case 'sendMessage': {
          sent.push({
            chat_id: parsed.chat_id as number,
            text: String(parsed.text ?? ''),
          });
          return json({
            ok: true,
            result: {
              message_id: nextMessageId++,
              date: Math.floor(Date.now() / 1000),
              chat: { id: parsed.chat_id, type: 'private' },
              text: parsed.text,
            },
          });
        }
        default:
          return json({ ok: true, result: true });
      }
    });
  });

  await new Promise<void>((resolve) => server.listen(port, '127.0.0.1', resolve));
  const address = server.address() as AddressInfo;

  return {
    url: `http://127.0.0.1:${address.port}`,
    sent,
    methods,
    push(update) {
      pending.push(update);
      for (const wake of waiters) wake();
      waiters.clear();
    },
    failNextPollWith409() {
      conflictOnce = true;
    },
    close: () =>
      new Promise<void>((resolve) => {
        for (const wake of waiters) wake();
        waiters.clear();
        server.close(() => resolve());
        server.closeAllConnections?.();
      }),
  };
}

// Direct-run mode for smoke-built.sh: `node --experimental-strip-types \
// test/helpers/mock-telegram.ts 39001` and leave it running.
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.argv[2] ?? 39001);
  void startMockTelegram(port).then((mock) => {
    console.log(`mock telegram listening at ${mock.url}`);
  });
}
