/**
 * Unit coverage for the non-streaming wire path (LAB-ONLY, offline, no network).
 *
 * Proves src/providers/nonstreaming-completions.ts:
 *   - the request body pi-ai built is captured via the onPayload seam and sent
 *     with `stream: false` (and `stream_options` dropped), body otherwise
 *     byte-identical — tool schemas included;
 *   - a text completion replays as start → text_start/delta/end → done, with
 *     the full text in ONE delta and in the final message;
 *   - reasoning_content replays as thinking_* BEFORE the text block;
 *   - tool_calls replay as toolcall_start/delta/end with parsed arguments and
 *     `done` reason "toolUse" — the agent loop's tool dispatch contract;
 *   - HTTP failures and pre-payload delegate failures surface as `error`
 *     events (never a hang);
 *   - ZOE_BRAIN_TOKEN_STREAMING gates the escape hatch.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/nonstreaming_completions.test.ts
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
import type { AssistantMessageEventStream } from '@earendil-works/pi-ai';
import type {
  AssistantMessage,
  AssistantMessageEvent,
  Context,
  Model,
  StreamOptions,
} from '@earendil-works/pi-ai';

const { captureWireParams, streamNonStreamingCompletions, tokenStreamingEnabled } = await import(
  '../src/providers/nonstreaming-completions.ts'
);

// ─── helpers ──────────────────────────────────────────────────────────────────

const MODEL = {
  id: 'gemma',
  api: 'openai-completions',
  provider: 'zoe',
  baseUrl: 'http://127.0.0.1:11434/v1',
} as unknown as Model<'openai-completions'>;

const CONTEXT: Context = { messages: [] };

/** Params "pi-ai built" for the probe — representative streaming body. */
const BUILT_PARAMS = {
  model: 'gemma',
  messages: [{ role: 'user', content: 'hi' }],
  stream: true,
  stream_options: { include_usage: true },
  tools: [{ type: 'function', function: { name: 'get_weather', parameters: {} } }],
  temperature: 0.7,
};

/**
 * Mimics pi-ai's streaming handler shape: builds params, calls onPayload
 * inside the async try (dist/providers/openai-completions.js:93-95), and turns
 * a throw into a terminal error event on the (discarded) probe stream.
 */
function fakeDelegate(params: Record<string, unknown> = BUILT_PARAMS) {
  return (
    model: Model<'openai-completions'>,
    _context: Context,
    options?: StreamOptions,
  ): AssistantMessageEventStream => {
    const s = createAssistantMessageEventStream();
    void (async () => {
      const err: AssistantMessage = {
        role: 'assistant',
        content: [],
        api: model.api,
        provider: model.provider,
        model: model.id,
        usage: {
          input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
        },
        stopReason: 'error',
        timestamp: 0,
      };
      try {
        await options?.onPayload?.(structuredClone(params), model);
        err.errorMessage = 'delegate issued its own streaming request';
      } catch (e) {
        err.errorMessage = e instanceof Error ? e.message : String(e);
      }
      s.push({ type: 'error', reason: 'error', error: err });
      s.end();
    })();
    return s;
  };
}

function fakeFetch(
  body: unknown,
  opts?: { status?: number; capture?: { url?: string; init?: RequestInit } },
): typeof fetch {
  return (async (url: unknown, init?: RequestInit) => {
    if (opts?.capture) {
      opts.capture.url = String(url);
      opts.capture.init = init;
    }
    return new Response(JSON.stringify(body), {
      status: opts?.status ?? 200,
      headers: { 'content-type': 'application/json' },
    });
  }) as typeof fetch;
}

async function collect(stream: AssistantMessageEventStream): Promise<AssistantMessageEvent[]> {
  const events: AssistantMessageEvent[] = [];
  for await (const event of stream) events.push(event);
  return events;
}

function textCompletion(text: string, extra?: Record<string, unknown>) {
  return {
    id: 'chatcmpl-1',
    model: 'gemma-file',
    choices: [{ index: 0, finish_reason: 'stop', message: { role: 'assistant', content: text, ...extra } }],
    usage: { prompt_tokens: 10, completion_tokens: 5, prompt_tokens_details: { cached_tokens: 4 } },
  };
}

// ─── request construction ─────────────────────────────────────────────────────

test('sends pi-ai’s captured body with stream:false and no stream_options', async () => {
  const capture: { url?: string; init?: RequestInit } = {};
  const stream = streamNonStreamingCompletions(
    fakeDelegate(),
    MODEL,
    CONTEXT,
    { apiKey: 'local-no-key' },
    { fetchFn: fakeFetch(textCompletion('hello'), { capture }) },
  );
  await stream.result();

  assert.equal(capture.url, 'http://127.0.0.1:11434/v1/chat/completions');
  assert.equal(capture.init?.method, 'POST');
  const sent = JSON.parse(String(capture.init?.body));
  assert.equal(sent.stream, false);
  assert.equal('stream_options' in sent, false);
  // Everything else pi-ai built is untouched — tool schemas included.
  assert.deepEqual(sent.tools, BUILT_PARAMS.tools);
  assert.deepEqual(sent.messages, BUILT_PARAMS.messages);
  assert.equal(sent.temperature, 0.7);
  const headers = capture.init?.headers as Record<string, string>;
  assert.equal(headers.authorization, 'Bearer local-no-key');
});

test('captureWireParams surfaces a pre-payload delegate failure', async () => {
  const broken = (): AssistantMessageEventStream => {
    const s = createAssistantMessageEventStream();
    const err = {
      role: 'assistant', content: [], api: 'openai-completions', provider: 'zoe', model: 'gemma',
      usage: {
        input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
      },
      stopReason: 'error', errorMessage: 'No API key for provider: zoe', timestamp: 0,
    } as AssistantMessage;
    s.push({ type: 'error', reason: 'error', error: err });
    s.end();
    return s;
  };
  await assert.rejects(
    captureWireParams(broken, MODEL, CONTEXT),
    /No API key for provider: zoe/,
  );
});

// ─── event replay ─────────────────────────────────────────────────────────────

test('text completion replays as one delta with the standard event sequence', async () => {
  const stream = streamNonStreamingCompletions(fakeDelegate(), MODEL, CONTEXT, undefined, {
    fetchFn: fakeFetch(textCompletion('It looks sunny.')),
  });
  const events = await collect(stream);

  assert.deepEqual(
    events.map((e) => e.type),
    ['start', 'text_start', 'text_delta', 'text_end', 'done'],
  );
  const delta = events.find((e) => e.type === 'text_delta');
  assert.equal(delta && 'delta' in delta ? delta.delta : '', 'It looks sunny.');
  const done = events.at(-1);
  assert.equal(done?.type, 'done');
  const message = await stream.result();
  assert.equal(message.stopReason, 'stop');
  assert.deepEqual(message.content, [{ type: 'text', text: 'It looks sunny.' }]);
  assert.equal(message.responseId, 'chatcmpl-1');
  assert.equal(message.responseModel, 'gemma-file');
  assert.equal(message.usage.input, 6); // 10 prompt - 4 cached
  assert.equal(message.usage.cacheRead, 4);
  assert.equal(message.usage.output, 5);
});

test('reasoning_content replays as thinking events before the text block', async () => {
  const stream = streamNonStreamingCompletions(fakeDelegate(), MODEL, CONTEXT, undefined, {
    fetchFn: fakeFetch(textCompletion('Answer.', { reasoning_content: 'pondering' })),
  });
  const events = await collect(stream);
  assert.deepEqual(
    events.map((e) => e.type),
    ['start', 'thinking_start', 'thinking_delta', 'thinking_end',
      'text_start', 'text_delta', 'text_end', 'done'],
  );
  const message = await stream.result();
  assert.deepEqual(message.content[0], {
    type: 'thinking',
    thinking: 'pondering',
    thinkingSignature: 'reasoning_content',
  });
});

test('tool_calls replay as toolcall events with parsed args and reason toolUse', async () => {
  const completion = {
    id: 'chatcmpl-2',
    choices: [{
      index: 0,
      finish_reason: 'tool_calls',
      message: {
        role: 'assistant',
        content: '',
        tool_calls: [{
          id: 'call-abc',
          type: 'function',
          function: { name: 'get_weather', arguments: '{"city":"Perth"}' },
        }],
      },
    }],
  };
  const stream = streamNonStreamingCompletions(fakeDelegate(), MODEL, CONTEXT, undefined, {
    fetchFn: fakeFetch(completion),
  });
  const events = await collect(stream);
  assert.deepEqual(
    events.map((e) => e.type),
    ['start', 'toolcall_start', 'toolcall_delta', 'toolcall_end', 'done'],
  );
  const done = events.at(-1);
  assert.equal(done?.type === 'done' && done.reason, 'toolUse');
  const message = await stream.result();
  // The agent loop dispatches on toolCall blocks in the final message.
  assert.deepEqual(message.content, [
    { type: 'toolCall', id: 'call-abc', name: 'get_weather', arguments: { city: 'Perth' } },
  ]);
});

// ─── failure paths ────────────────────────────────────────────────────────────

test('HTTP failure surfaces as a terminal error event', async () => {
  const stream = streamNonStreamingCompletions(fakeDelegate(), MODEL, CONTEXT, undefined, {
    fetchFn: fakeFetch({ error: 'boom' }, { status: 500 }),
  });
  const events = await collect(stream);
  assert.equal(events.length, 1);
  assert.equal(events[0].type, 'error');
  const message = await stream.result();
  assert.equal(message.stopReason, 'error');
  assert.match(message.errorMessage ?? '', /returned 500/);
});

test('a completion with no choices surfaces as a terminal error event', async () => {
  const stream = streamNonStreamingCompletions(fakeDelegate(), MODEL, CONTEXT, undefined, {
    fetchFn: fakeFetch({ choices: [] }),
  });
  const message = await stream.result();
  assert.equal(message.stopReason, 'error');
  assert.match(message.errorMessage ?? '', /no choices/);
});

// ─── escape hatch flag ────────────────────────────────────────────────────────

test('ZOE_BRAIN_TOKEN_STREAMING gates token-level streaming, default off', () => {
  const prior = process.env.ZOE_BRAIN_TOKEN_STREAMING;
  try {
    delete process.env.ZOE_BRAIN_TOKEN_STREAMING;
    assert.equal(tokenStreamingEnabled(), false);
    for (const v of ['1', 'true', 'YES', ' on ']) {
      process.env.ZOE_BRAIN_TOKEN_STREAMING = v;
      assert.equal(tokenStreamingEnabled(), true, v);
    }
    for (const v of ['', '0', 'false', 'off', 'banana']) {
      process.env.ZOE_BRAIN_TOKEN_STREAMING = v;
      assert.equal(tokenStreamingEnabled(), false, v);
    }
  } finally {
    if (prior === undefined) delete process.env.ZOE_BRAIN_TOKEN_STREAMING;
    else process.env.ZOE_BRAIN_TOKEN_STREAMING = prior;
  }
});
