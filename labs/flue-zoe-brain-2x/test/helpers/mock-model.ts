/**
 * An in-process, OpenAI-compatible mock model server for the sidecar's tests.
 *
 * WHY A REAL HTTP SERVER RATHER THAN A FAKE PROVIDER: the seam under test IS the
 * wire. `applyPolicies` (windowing → strip-builtins → disclosure → cap) only
 * takes effect in what actually reaches the model, and the whole point of the
 * tool-iteration cap is that the model "physically cannot request another tool"
 * because the tool list was stripped. A stubbed `{ stream, streamSimple }` pair
 * would let us assert on the Context object we handed pi-ai and prove nothing
 * about the request pi-ai then built. Driving pi-ai's genuine openai-completions
 * handler against a socket means every assertion below is made on the BYTES the
 * brain would have received.
 *
 * It also keeps the tests off the live llama-server on :11434 — this box runs the
 * production voice brain and a second model process does not fit in RAM.
 *
 * Binds 127.0.0.1 on an EPHEMERAL port (`listen(0)`), so parallel test files can
 * never collide, and holds no state beyond the captured requests.
 */
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';

/** One scripted assistant turn. Tool calls and text may be combined. */
export interface MockTurn {
  /** Tool calls the model should emit this turn. */
  toolCalls?: { name: string; args?: unknown; id?: string }[];
  /** Assistant text to stream, split into deltas at whitespace. */
  text?: string;
  /** Stall before responding — used to force concurrent turns to interleave. */
  delayMs?: number;
}

/** What the mock saw, as parsed from the request body. */
export interface CapturedRequest {
  /** Tool NAMES offered on this call, in order. `[]` when none were offered. */
  toolNames: string[];
  /** The system message text, or '' when none was sent. */
  systemPrompt: string;
  /** Every message, reduced to `{ role, text }` for assertion convenience. */
  messages: { role: string; text: string }[];
  temperature?: number;
  /** The raw parsed body, for anything the reductions above drop. */
  raw: Record<string, unknown>;
}

export interface MockModelServer {
  /** Base URL to hand the provider, e.g. `http://127.0.0.1:53219/v1`. */
  baseUrl: string;
  /** Every request the model received, oldest first. */
  requests: CapturedRequest[];
  /** Requests seen so far — a stable count for polling assertions. */
  readonly callCount: number;
  close(): Promise<void>;
}

/**
 * A turn script. Either a fixed list consumed in order (running off the end
 * yields a plain "done." text turn, so a test can never hang on an
 * under-specified script) or a function computing the turn from the call index
 * — which is how the cap's negative control scripts an INFINITE tool loop.
 */
export type MockScript = MockTurn[] | ((call: number, req: CapturedRequest) => MockTurn);

function textOf(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        part && typeof part === 'object' && (part as { text?: unknown }).text
          ? String((part as { text: unknown }).text)
          : '',
      )
      .join('');
  }
  return '';
}

function capture(body: Record<string, unknown>): CapturedRequest {
  const rawMessages = Array.isArray(body.messages)
    ? (body.messages as Record<string, unknown>[])
    : [];
  const tools = Array.isArray(body.tools) ? (body.tools as Record<string, unknown>[]) : [];
  return {
    toolNames: tools.map((t) => {
      const fn = t.function as { name?: unknown } | undefined;
      return String(fn?.name ?? t.name ?? '');
    }),
    systemPrompt: rawMessages
      .filter((m) => m.role === 'system')
      .map((m) => textOf(m.content))
      .join('\n'),
    messages: rawMessages.map((m) => ({ role: String(m.role), text: textOf(m.content) })),
    temperature: typeof body.temperature === 'number' ? body.temperature : undefined,
    raw: body,
  };
}

/** SSE frame carrying one chat-completion chunk. */
function chunk(delta: Record<string, unknown>, finish: string | null): string {
  return `data: ${JSON.stringify({
    id: 'chatcmpl-mock',
    object: 'chat.completion.chunk',
    created: 0,
    model: 'local',
    choices: [{ index: 0, delta, finish_reason: finish }],
  })}\n\n`;
}

async function writeTurn(res: ServerResponse, turn: MockTurn): Promise<void> {
  res.writeHead(200, {
    'content-type': 'text/event-stream',
    'cache-control': 'no-cache',
    connection: 'keep-alive',
  });
  res.write(chunk({ role: 'assistant', content: '' }, null));

  if (turn.text) {
    // Split into several deltas so text_delta streaming is genuinely exercised.
    for (const piece of turn.text.match(/\S+\s*/g) ?? [turn.text]) {
      res.write(chunk({ content: piece }, null));
    }
  }

  const calls = turn.toolCalls ?? [];
  calls.forEach((call, index) => {
    res.write(
      chunk(
        {
          tool_calls: [
            {
              index,
              id: call.id ?? `call_${Math.random().toString(36).slice(2, 10)}`,
              type: 'function',
              function: { name: call.name, arguments: JSON.stringify(call.args ?? {}) },
            },
          ],
        },
        null,
      ),
    );
  });

  res.write(chunk({}, calls.length > 0 ? 'tool_calls' : 'stop'));
  res.write('data: [DONE]\n\n');
  res.end();
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let raw = '';
    req.on('data', (part) => {
      raw += part;
    });
    req.on('end', () => resolve(raw));
    req.on('error', reject);
  });
}

export async function startMockModel(script: MockScript): Promise<MockModelServer> {
  const requests: CapturedRequest[] = [];

  const server: Server = createServer((req, res) => {
    void (async () => {
      const raw = await readBody(req);
      let body: Record<string, unknown> = {};
      try {
        body = raw ? (JSON.parse(raw) as Record<string, unknown>) : {};
      } catch {
        body = {};
      }
      const captured = capture(body);
      const call = requests.length;
      requests.push(captured);

      const turn =
        typeof script === 'function'
          ? script(call, captured)
          : (script[call] ?? { text: 'done.' });

      if (turn.delayMs) await new Promise((r) => setTimeout(r, turn.delayMs));
      await writeTurn(res, turn);
    })().catch(() => {
      // A mock that throws must fail the test as a model error, never as an
      // unhandled rejection that takes the whole runner down.
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
    baseUrl: `http://127.0.0.1:${port}/v1`,
    requests,
    get callCount() {
      return requests.length;
    },
    close: () =>
      new Promise<void>((resolve) => {
        server.closeAllConnections?.();
        server.close(() => resolve());
      }),
  };
}
