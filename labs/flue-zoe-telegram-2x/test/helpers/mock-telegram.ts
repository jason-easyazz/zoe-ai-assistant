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
 * Voice notes (flag-dark) add three more surfaces: `getFile`, the
 * `/file/bot<token>/<path>` download route, and a multipart `sendVoice` sink —
 * so the offline suite can prove a note is fetched, the reply leaves as an
 * uploaded OGG, and (the controls) that nothing is fetched for an unlinked,
 * forwarded, over-long or flag-off note.
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
  /** Present when the bot attached a keyboard (e.g. the /talk URL button). */
  reply_markup?: unknown;
}

export interface SentVoice {
  chat_id: number | string;
  caption?: string;
  /** The uploaded OGG bytes (multipart `voice` part). */
  bytes: Uint8Array;
  filename?: string;
}

export interface MockTelegram {
  /** Base URL to hand to grammY as `apiRoot` (no trailing slash). */
  url: string;
  /** Every sendMessage the bot made, in order. */
  sent: SentMessage[];
  /** Every sendVoice the bot made, in order. */
  voices: SentVoice[];
  /** Every Bot API method called, in order. */
  methods: string[];
  /** `/file/bot…/<path>` downloads the bot made (paths only — never a token). */
  downloads: string[];
  /** Bytes served for a file_id's download (registered with `addFile`). */
  addFile(fileId: string, bytes: Uint8Array): void;
  /** Queue an update for the next getUpdates poll. */
  push(update: unknown): void;
  /** Make the NEXT getUpdates answer 409 Conflict (another consumer holds the token). */
  failNextPollWith409(): void;
  close(): Promise<void>;
}

let nextUpdateId = 1000;
let nextMessageId = 1;

function baseMessage(telegramId: number, chatId: number, username?: string) {
  return {
    message_id: nextMessageId++,
    date: Math.floor(Date.now() / 1000),
    chat: { id: chatId, type: 'private' },
    from: { id: telegramId, is_bot: false, first_name: 'Test', username },
  };
}

/** Build a minimal `message:text` update from a verified sender. */
export function textUpdate(telegramId: number, chatId: number, text: string, username?: string) {
  return {
    update_id: nextUpdateId++,
    message: {
      ...baseMessage(telegramId, chatId, username),
      text,
      // grammY's `bot.command()` filter needs the bot_command entity to fire.
      ...(text.startsWith('/')
        ? { entities: [{ type: 'bot_command', offset: 0, length: text.split(' ')[0].length }] }
        : {}),
    },
  };
}

export interface VoiceUpdateOptions {
  duration?: number;
  file_size?: number;
  /** Mark the note as forwarded from someone else. */
  forwarded?: boolean;
  /** Send as an `audio` file instead of a `voice` note. */
  audio?: boolean;
}

/** Build a `message:voice` (or `message:audio`) update from a verified sender. */
export function voiceUpdate(telegramId: number, chatId: number, fileId: string, opts: VoiceUpdateOptions = {}) {
  const media = {
    file_id: fileId,
    file_unique_id: `u-${fileId}`,
    duration: opts.duration ?? 4,
    mime_type: opts.audio ? 'audio/mpeg' : 'audio/ogg',
    file_size: opts.file_size ?? 60_000,
  };
  return {
    update_id: nextUpdateId++,
    message: {
      ...baseMessage(telegramId, chatId),
      ...(opts.audio ? { audio: media } : { voice: media }),
      ...(opts.forwarded
        ? { forward_origin: { type: 'hidden_user', sender_user_name: 'Someone', date: 1 } }
        : {}),
    },
  };
}

/** Minimal multipart/form-data parser: name → {value|bytes, filename}. */
function parseMultipart(body: Buffer, contentType: string): Map<string, { bytes: Buffer; filename?: string }> {
  const out = new Map<string, { bytes: Buffer; filename?: string }>();
  const m = /boundary=("?)([^";]+)\1/.exec(contentType);
  if (!m) return out;
  const boundary = Buffer.from(`--${m[2]}`);
  let pos = body.indexOf(boundary);
  while (pos !== -1) {
    const next = body.indexOf(boundary, pos + boundary.length);
    if (next === -1) break;
    const part = body.subarray(pos + boundary.length, next);
    const headerEnd = part.indexOf('\r\n\r\n');
    if (headerEnd !== -1) {
      const headers = part.subarray(0, headerEnd).toString('utf8');
      const name = /name="([^"]+)"/.exec(headers)?.[1];
      // grammY writes `filename=zoe.ogg` unquoted; accept both spellings.
      const filename = /filename=("?)([^";\r\n]*)\1/.exec(headers)?.[2];
      // Body runs to the CRLF that precedes the next boundary.
      const bytes = part.subarray(headerEnd + 4, part.length - 2);
      if (name) out.set(name, { bytes: Buffer.from(bytes), filename });
    }
    pos = next;
  }
  return out;
}

export async function startMockTelegram(port = 0): Promise<MockTelegram> {
  const sent: SentMessage[] = [];
  const voices: SentVoice[] = [];
  const methods: string[] = [];
  const downloads: string[] = [];
  const files = new Map<string, Uint8Array>();
  const pending: unknown[] = [];
  let conflictOnce = false;
  // Long-poll requests parked until an update arrives or the server closes.
  const waiters = new Set<(value: void) => void>();

  const server: Server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => {
      chunks.push(chunk);
    });
    req.on('end', async () => {
      const json = (payload: unknown, status = 200) => {
        res.writeHead(status, { 'content-type': 'application/json' });
        res.end(JSON.stringify(payload));
      };
      const url = req.url ?? '';

      // File download: /file/bot<token>/<file_path>. Record the PATH only.
      if (url.startsWith('/file/bot')) {
        const path = url.replace(/^\/file\/bot[^/]+\//, '');
        downloads.push(path);
        const fileId = path.replace(/^voice\//, '').replace(/\.oga$/, '');
        const bytes = files.get(fileId);
        if (!bytes) {
          res.writeHead(404, { 'content-type': 'application/json' });
          return res.end(JSON.stringify({ ok: false, error_code: 404, description: 'Not Found' }));
        }
        res.writeHead(200, { 'content-type': 'audio/ogg', 'content-length': String(bytes.byteLength) });
        return res.end(Buffer.from(bytes));
      }

      const method = url.split('/').pop() ?? '';
      methods.push(method);
      const raw = Buffer.concat(chunks);
      const contentType = String(req.headers['content-type'] ?? '');
      let parsed: Record<string, unknown> = {};
      let parts: Map<string, { bytes: Buffer; filename?: string }> | null = null;
      if (contentType.startsWith('multipart/form-data')) {
        parts = parseMultipart(raw, contentType);
        for (const [name, part] of parts) if (!part.filename) parsed[name] = part.bytes.toString('utf8');
      } else if (raw.length) {
        parsed = JSON.parse(raw.toString('utf8'));
      }

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
        case 'getFile': {
          const fileId = String(parsed.file_id ?? '');
          if (!files.has(fileId)) {
            return json({ ok: false, error_code: 400, description: 'Bad Request: file not found' }, 400);
          }
          return json({
            ok: true,
            result: {
              file_id: fileId,
              file_unique_id: `u-${fileId}`,
              file_size: files.get(fileId)!.byteLength,
              file_path: `voice/${fileId}.oga`,
            },
          });
        }
        case 'sendMessage': {
          const markup = parsed.reply_markup;
          sent.push({
            chat_id: parsed.chat_id as number,
            text: String(parsed.text ?? ''),
            ...(markup === undefined
              ? {}
              : { reply_markup: typeof markup === 'string' ? JSON.parse(markup) : markup }),
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
        case 'sendVoice': {
          // grammY uploads an InputFile under a generated part name and sets the
          // `voice` field to `attach://<name>`; a string file_id would be plain.
          const ref = String(parsed.voice ?? '');
          const voice = ref.startsWith('attach://') ? parts?.get(ref.slice('attach://'.length)) : parts?.get('voice');
          voices.push({
            chat_id: Number(parsed.chat_id),
            caption: parsed.caption === undefined ? undefined : String(parsed.caption),
            bytes: voice ? new Uint8Array(voice.bytes) : new Uint8Array(),
            filename: voice?.filename,
          });
          return json({
            ok: true,
            result: {
              message_id: nextMessageId++,
              date: Math.floor(Date.now() / 1000),
              chat: { id: Number(parsed.chat_id), type: 'private' },
              voice: { file_id: 'sent-voice', file_unique_id: 'u-sent-voice', duration: 1 },
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
    voices,
    methods,
    downloads,
    addFile(fileId, bytes) {
      files.set(fileId, bytes);
    },
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
