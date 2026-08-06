/**
 * An in-process stand-in for zoe-data's internal capability endpoints.
 *
 * This exists for ONE assertion that nothing else can make honestly: which
 * `user_id` a tool actually acted as. The acting identity is resolved inside the
 * tool (`actingUserId(signal)` in src/tools/zoe-tools.ts) and then leaves the
 * process as a query parameter or a JSON field. Capturing it HERE — at the point
 * it crosses the wire toward the real backend — proves the whole chain: envelope
 * on the turn message → provider binds it to the turn's AbortSignal → tool reads
 * it back by its own signal → tool sends it. Asserting on any earlier link would
 * be asserting on the implementation instead of the outcome.
 *
 * `stallMs` holds a request open so two turns' tool executions genuinely overlap
 * in wall-clock time, which is what makes the concurrency test adversarial rather
 * than decorative.
 */
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

export interface CapturedCall {
  path: string;
  /** The acting user_id, from the query string (reads) or body (dispatches). */
  userId: string;
  /** Intent name for POST /api/system/intent-dispatch, else ''. */
  intent: string;
  at: number;
}

export interface MockZoeData {
  url: string;
  calls: CapturedCall[];
  close(): Promise<void>;
}

export interface MockZoeDataOptions {
  /** Hold every request open this long before replying. */
  stallMs?: number;
  /** Packet text returned by /api/memories/for-prompt. */
  packet?: string;
}

export async function startMockZoeData(options: MockZoeDataOptions = {}): Promise<MockZoeData> {
  const calls: CapturedCall[] = [];

  const server: Server = createServer((req, res) => {
    void (async () => {
      let raw = '';
      for await (const part of req) raw += part;
      const url = new URL(req.url ?? '/', 'http://mock.zoe-data');

      let userId = url.searchParams.get('user_id') ?? '';
      let intent = '';
      if (raw) {
        try {
          const body = JSON.parse(raw) as { user_id?: string; intent?: string };
          userId = body.user_id ?? userId;
          intent = body.intent ?? '';
        } catch {
          /* not JSON — leave the query-string reading in place */
        }
      }
      calls.push({ path: url.pathname, userId, intent, at: Date.now() });

      if (options.stallMs) await new Promise((r) => setTimeout(r, options.stallMs));

      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(
        JSON.stringify({
          packet: options.packet ?? 'Nothing stored yet.',
          intent,
          ok: true,
          result: 'ok',
        }),
      );
    })().catch(() => {
      try {
        res.writeHead(500).end();
      } catch {
        /* already sent */
      }
    });
  });

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;

  return {
    url: `http://127.0.0.1:${port}`,
    calls,
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}
