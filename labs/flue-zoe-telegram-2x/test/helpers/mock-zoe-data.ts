/**
 * A mock zoe-data, implementing exactly the four endpoints this channel calls.
 *
 * These four ARE the port's real contract surface — they are runtime-independent
 * HTTP, so they must come through the 1.x→2.x move byte-identical. The mock
 * records what it received (URL, headers, body) so the tests assert on the WIRE,
 * not on the client's intentions:
 *
 *   GET  /api/system/resolve-telegram/<id>          → { user_id | null }
 *   POST /api/system/telegram/consume-link-token    → { user_id } | 400
 *   POST /api/system/telegram/register-bot          → { ok }
 *   POST /api/chat/?stream=false                    → { response }
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
  close(): Promise<void>;
}

export async function startMockZoeData(): Promise<MockZoeData> {
  const requests: RecordedRequest[] = [];
  const links = new Map<string, string>();
  const tokens = new Map<string, string>();

  const state = { reply: 'Hi — this is Zoe.' };

  const server: Server = createServer((req, res) => {
    let raw = '';
    req.on('data', (chunk) => {
      raw += chunk;
    });
    req.on('end', () => {
      const url = req.url ?? '';
      let body: unknown = null;
      try {
        body = raw ? JSON.parse(raw) : null;
      } catch {
        body = raw;
      }
      requests.push({ method: req.method ?? '', url, headers: req.headers, body });

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
