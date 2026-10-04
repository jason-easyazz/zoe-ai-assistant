/**
 * A mock zoe-data, implementing exactly the endpoints this channel calls.
 *
 * The four text-lane contracts ARE the port's real contract surface — they are
 * runtime-independent HTTP, so they must come through the 1.x→2.x move
 * byte-identical. The mock records what it received (URL, headers, body) so the
 * tests assert on the WIRE, not on the client's intentions:
 *
 *   GET  /api/system/resolve-telegram/<id>          → { user_id | null }
 *   POST /api/system/telegram/consume-link-token    → { user_id } | 400
 *   POST /api/system/telegram/register-bot          → { ok }
 *   POST /api/chat/?stream=false                    → { response }
 *
 * Voice notes (flag-dark, zoe-data's `telegram_media` router) add two more:
 *
 *   POST /api/system/telegram/transcribe   raw audio → { ok, text }
 *   POST /api/system/telegram/synthesize   { text }  → audio/ogg bytes | 503
 *
 * Nothing here ever reaches the live zoe-data on :8000: the tests set
 * ZOE_DATA_URL to this server's ephemeral loopback URL before importing the
 * modules that read it. (The brain sidecar port recorded the mirror-image
 * near-miss — a module-load `ZOE_DATA_URL` pin briefly aimed test tools at live
 * zoe-data — so the ordering here is deliberate, not incidental.)
 */
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

export interface RecordedRequest {
  method: string;
  url: string;
  headers: Record<string, string | string[] | undefined>;
  body: unknown;
  /** Raw body bytes — what a binary (audio) upload actually carried. */
  raw: Uint8Array;
}

export interface MockZoeData {
  url: string;
  requests: RecordedRequest[];
  /** telegram_id (as string) → Zoe user_id. Anything absent resolves unlinked. */
  links: Map<string, string>;
  /** Valid link tokens: token → user_id. Anything else is a 400. */
  tokens: Map<string, string>;
  /** Canned /api/chat reply. */
  reply: string;
  /** Canned transcript for telegram/transcribe ('' = no speech). */
  transcript: string;
  /** When false, telegram/synthesize answers 503 (Kokoro down). */
  synthOk: boolean;
  /** The OGG bytes telegram/synthesize serves. */
  ogg: Uint8Array;
  close(): Promise<void>;
}

export async function startMockZoeData(): Promise<MockZoeData> {
  const requests: RecordedRequest[] = [];
  const links = new Map<string, string>();
  const tokens = new Map<string, string>();

  const state = {
    reply: 'Hi — this is Zoe.',
    transcript: 'what time is it',
    synthOk: true,
    ogg: new Uint8Array([0x4f, 0x67, 0x67, 0x53, 0x00, 0x02, 0xaa, 0xbb]), // "OggS" + filler
  };

  const server: Server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (chunk: Buffer) => {
      chunks.push(chunk);
    });
    req.on('end', () => {
      const url = req.url ?? '';
      const raw = Buffer.concat(chunks);
      let body: unknown = null;
      const text = raw.toString('utf8');
      try {
        body = raw.length ? JSON.parse(text) : null;
      } catch {
        body = text;
      }
      requests.push({ method: req.method ?? '', url, headers: req.headers, body, raw: new Uint8Array(raw) });

      const json = (payload: unknown, status = 200) => {
        res.writeHead(status, { 'content-type': 'application/json' });
        res.end(JSON.stringify(payload));
      };

      if (url.startsWith('/api/system/resolve-telegram/')) {
        const id = url.slice('/api/system/resolve-telegram/'.length);
        return json({ user_id: links.get(id) ?? null });
      }
      if (url.startsWith('/api/system/telegram/consume-link-token')) {
        const payload = body as { token?: string; telegram_id?: string } | null;
        const userId = payload?.token ? tokens.get(payload.token) : undefined;
        if (!userId) return json({ detail: 'invalid or expired link token' }, 400);
        if (payload?.telegram_id) links.set(payload.telegram_id, userId);
        return json({ ok: true, user_id: userId, telegram_id: payload?.telegram_id });
      }
      if (url.startsWith('/api/system/telegram/register-bot')) {
        return json({ ok: true });
      }
      if (url.startsWith('/api/system/telegram/transcribe')) {
        if (!raw.length) return json({ ok: false, error: 'empty audio' }, 400);
        return json({ ok: true, text: state.transcript, duration_s: 4.0 });
      }
      if (url.startsWith('/api/system/telegram/synthesize')) {
        if (!state.synthOk) return json({ ok: false, error: 'tts unavailable' }, 503);
        res.writeHead(200, { 'content-type': 'audio/ogg' });
        return res.end(Buffer.from(state.ogg));
      }
      if (url.startsWith('/api/chat/')) {
        return json({ response: state.reply });
      }
      return json({ detail: 'not found' }, 404);
    });
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address() as AddressInfo;

  return {
    url: `http://127.0.0.1:${address.port}`,
    requests,
    links,
    tokens,
    get reply() {
      return state.reply;
    },
    set reply(value: string) {
      state.reply = value;
    },
    get transcript() {
      return state.transcript;
    },
    set transcript(value: string) {
      state.transcript = value;
    },
    get synthOk() {
      return state.synthOk;
    },
    set synthOk(value: boolean) {
      state.synthOk = value;
    },
    get ogg() {
      return state.ogg;
    },
    set ogg(value: Uint8Array) {
      state.ogg = value;
    },
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => resolve());
        server.closeAllConnections?.();
      }),
  };
}

/** Poll until `predicate` holds or the deadline passes. Returns whether it held. */
export async function waitFor(
  predicate: () => boolean,
  timeoutMs = 10_000,
  stepMs = 20,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((r) => setTimeout(r, stepMs));
  }
  return predicate();
}
