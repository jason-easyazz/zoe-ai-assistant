/**
 * Prompt-fit history windowing coverage (LAB-ONLY, offline, no network beyond
 * an in-process fake llama-server on localhost).
 *
 * The live bug this pins: durable sessions grow unbounded, and once the
 * assembled prompt crossed the 8192-token model context every turn on that
 * session failed permanently ("400 request (8288 tokens) exceeds the available
 * context size (8192 tokens)", observed at 198 stored entries). The fix is
 * wire-level windowing in applyPolicies (src/context-window.ts): drop the
 * OLDEST whole user-turn blocks so the prompt always fits, never touching the
 * system prompt (soul + doctrines) or the newest turn.
 *
 * Proven here:
 *   - a session whose stored history is far over budget still assembles a
 *     prompt under budget AND answers end-to-end against a mocked provider
 *     (an in-process fake llama-server that 400s on oversized prompts exactly
 *     like the real one — and reproduces the live failure with windowing off);
 *   - every doctrine block and the newest user message survive windowing;
 *   - the ` zoe-uid:` identity envelope still binds/strips after windowing;
 *   - blocks are dropped whole (no orphan toolResult), oldest first;
 *   - env knobs: ZOE_BRAIN_CONTEXT_WINDOW / ZOE_BRAIN_REPLY_RESERVE validated,
 *     0 disables (pre-fix behaviour).
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/context_window.test.ts
 */
process.env.ZOE_BRAIN_USER_ID = 'jason';

import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { test } from 'node:test';
import type { AddressInfo } from 'node:net';
import type { Context, Message, Model } from '@earendil-works/pi-ai';

const {
  contextWindowTokens,
  replyReserveTokens,
  estimateContextTokens,
  windowContextToBudget,
} = await import('../src/context-window.ts');
const { applyPolicies, registerCappedCompletions, CAPPED_COMPLETIONS_API } = await import(
  '../src/providers/capped-completions.ts'
);
const { forwardedIdentityFromMessages, wrapMessageWithIdentity } = await import(
  '../src/request-identity.ts'
);
const {
  ZOE_INSTRUCTIONS,
  ACTIVATOR_DOCTRINE,
  VOICE_DELIVERY_DOCTRINE,
  IN_SESSION_CONTEXT_DOCTRINE,
  RECALL_PRECEDENCE_DOCTRINE,
  PERSONAL_RECALL_DOCTRINE,
  EMOTIONAL_RECALL_DOCTRINE,
  EMOTIONAL_CAPTURE_DOCTRINE,
  IDENTITY_DOCTRINE,
} = await import('../src/agents/zoe.ts');
const { zoeTools } = await import('../src/tools/zoe-tools.ts');

const ALL_DOCTRINES = [
  ACTIVATOR_DOCTRINE,
  VOICE_DELIVERY_DOCTRINE,
  IN_SESSION_CONTEXT_DOCTRINE,
  RECALL_PRECEDENCE_DOCTRINE,
  PERSONAL_RECALL_DOCTRINE,
  EMOTIONAL_RECALL_DOCTRINE,
  EMOTIONAL_CAPTURE_DOCTRINE,
  IDENTITY_DOCTRINE,
];

function resetEnv(): void {
  delete process.env.ZOE_BRAIN_CONTEXT_WINDOW;
  delete process.env.ZOE_BRAIN_REPLY_RESERVE;
}

// ─── helpers ──────────────────────────────────────────────────────────────────

function userMsg(text: string): Message {
  return { role: 'user', content: text, timestamp: 0 } as Message;
}

function assistantMsg(text: string): Message {
  return { role: 'assistant', content: [{ type: 'text', text }] } as unknown as Message;
}

function assistantToolCall(id: string, name: string): Message {
  return {
    role: 'assistant',
    content: [{ type: 'toolCall', id, name, arguments: { query: 'x' } }],
  } as unknown as Message;
}

function toolResult(id: string, text: string): Message {
  return {
    role: 'toolResult',
    toolCallId: id,
    toolName: 'recall_memory',
    content: [{ type: 'text', text }],
  } as unknown as Message;
}

/**
 * A long stored history in the shape the live wedge had: many complete
 * user→assistant turns (some with tool rounds), far past the 8192 window.
 */
function longHistory(turns: number, newestText: string): Message[] {
  const messages: Message[] = [];
  for (let i = 0; i < turns; i++) {
    messages.push(userMsg(`Turn ${i}: ${'family chat about dinner plans and school runs. '.repeat(8)}`));
    if (i % 3 === 0) {
      messages.push(assistantToolCall(`t${i}`, 'recall_memory'));
      messages.push(toolResult(`t${i}`, `recalled fact ${i}: ${'detail '.repeat(30)}`));
    }
    messages.push(assistantMsg(`Reply ${i}: ${'okay, noted — here is what I think. '.repeat(6)}`));
  }
  messages.push(userMsg(newestText));
  return messages;
}

function baseContext(messages: Message[]): Context {
  return { systemPrompt: ZOE_INSTRUCTIONS, messages, tools: zoeTools as Context['tools'] };
}

function promptBudget(): number {
  const window = contextWindowTokens();
  return window - replyReserveTokens(window);
}

// ─── env knobs ───────────────────────────────────────────────────────────────

test('contextWindowTokens defaults to the llama-server rock (8192)', () => {
  resetEnv();
  assert.equal(contextWindowTokens(), 8192);
});

test('ZOE_BRAIN_CONTEXT_WINDOW overrides; 0 disables; invalid falls back', () => {
  process.env.ZOE_BRAIN_CONTEXT_WINDOW = '4096';
  assert.equal(contextWindowTokens(), 4096);
  process.env.ZOE_BRAIN_CONTEXT_WINDOW = '0';
  assert.equal(contextWindowTokens(), 0);
  for (const bad of ['', 'abc', '-5', 'NaN']) {
    process.env.ZOE_BRAIN_CONTEXT_WINDOW = bad;
    assert.equal(contextWindowTokens(), 8192, `env ${JSON.stringify(bad)}`);
  }
  resetEnv();
});

test('replyReserveTokens defaults, overrides, and clamps to half the window', () => {
  resetEnv();
  assert.equal(replyReserveTokens(8192), 1536);
  process.env.ZOE_BRAIN_REPLY_RESERVE = '512';
  assert.equal(replyReserveTokens(8192), 512);
  process.env.ZOE_BRAIN_REPLY_RESERVE = '999999';
  assert.equal(replyReserveTokens(8192), 4096, 'clamped to window/2');
  for (const bad of ['abc', '-1', '0']) {
    process.env.ZOE_BRAIN_REPLY_RESERVE = bad;
    assert.equal(replyReserveTokens(8192), 1536, `env ${JSON.stringify(bad)}`);
  }
  resetEnv();
});

// ─── windowing invariants ────────────────────────────────────────────────────

test('an over-budget history is windowed under budget; system prompt and newest turn survive', () => {
  resetEnv();
  const newest = 'What time is soccer practice tomorrow?';
  const context = baseContext(longHistory(120, newest));
  assert.ok(
    estimateContextTokens(context) > 8192,
    'precondition: stored history must be well past the model window',
  );

  const windowed = windowContextToBudget(context);

  assert.ok(estimateContextTokens(windowed) <= promptBudget(), 'prompt fits the budget');
  assert.ok(windowed.messages.length < context.messages.length, 'old turns were dropped');
  assert.equal(windowed.systemPrompt, context.systemPrompt, 'system prompt untouched');
  const last = windowed.messages[windowed.messages.length - 1];
  assert.equal(last.role, 'user');
  assert.equal(last.content, newest, 'newest user message survives verbatim');
  assert.equal(windowed.messages[0].role, 'user', 'window starts at a turn boundary');
});

test('every doctrine block survives windowing (they live in the untouched system prompt)', () => {
  resetEnv();
  const windowed = windowContextToBudget(baseContext(longHistory(120, 'hi')));
  for (const doctrine of ALL_DOCTRINES) {
    assert.ok(
      (windowed.systemPrompt ?? '').includes(doctrine),
      `doctrine block missing after windowing: ${doctrine.slice(0, 60)}…`,
    );
  }
});

test('blocks are dropped whole: no toolResult without its toolCall in the window', () => {
  resetEnv();
  const windowed = windowContextToBudget(baseContext(longHistory(120, 'hi')));
  const seenToolCallIds = new Set<string>();
  for (const msg of windowed.messages) {
    if (msg.role === 'assistant' && Array.isArray(msg.content)) {
      for (const block of msg.content as Array<{ type: string; id?: string }>) {
        if (block.type === 'toolCall' && block.id) seenToolCallIds.add(block.id);
      }
    }
    if (msg.role === 'toolResult') {
      const id = (msg as unknown as { toolCallId: string }).toolCallId;
      assert.ok(seenToolCallIds.has(id), `orphan toolResult ${id} survived windowing`);
    }
  }
});

test('the newest turn is kept whole even when it alone exceeds the budget', () => {
  resetEnv();
  const giant = 'x'.repeat(64_000); // ~16k estimated tokens on its own
  const messages = [userMsg('old turn'), assistantMsg('old reply'), userMsg(giant)];
  const windowed = windowContextToBudget(baseContext(messages));
  const last = windowed.messages[windowed.messages.length - 1];
  assert.equal(last.content, giant, 'newest message never truncated or dropped');
});

test('over-budget stray preamble before the first user turn is dropped', () => {
  resetEnv();
  // All user-turn blocks fit; the overshoot comes entirely from non-user
  // messages before the first user message. The window must still come back
  // under budget as a contiguous suffix starting at a user message.
  const preamble = assistantMsg('p'.repeat(64_000)); // ~16k estimated tokens
  const messages = [preamble, userMsg('hello'), assistantMsg('hi'), userMsg('bye')];
  const windowed = windowContextToBudget(baseContext(messages));
  assert.ok(estimateContextTokens(windowed) <= promptBudget());
  assert.equal(windowed.messages[0].role, 'user');
  assert.equal(windowed.messages[0].content, 'hello');
  assert.equal(windowed.messages.length, 3);
});

test('an under-budget context passes through by reference (no-alloc idiom)', () => {
  resetEnv();
  const context = baseContext([userMsg('hello'), assistantMsg('hi'), userMsg('how are you?')]);
  assert.equal(windowContextToBudget(context), context);
});

test('ZOE_BRAIN_CONTEXT_WINDOW=0 disables windowing entirely (pre-fix behaviour)', () => {
  resetEnv();
  process.env.ZOE_BRAIN_CONTEXT_WINDOW = '0';
  const context = baseContext(longHistory(120, 'hi'));
  assert.equal(windowContextToBudget(context), context);
  resetEnv();
});

// ─── applyPolicies integration: identity envelope after windowing ───────────

test('identity envelope: still parsed for binding, stripped from the model view, after windowing', () => {
  resetEnv();
  const uid = 'family-user-7';
  const newest = wrapMessageWithIdentity('add milk to the shopping list', uid);
  const context = baseContext(longHistory(120, newest));

  // The trusted binding reads the PRE-windowed context (bindIdentityForRound
  // runs before applyPolicies) — but the newest message also survives
  // windowing, so the envelope parses from the windowed view too.
  assert.equal(forwardedIdentityFromMessages(context.messages), uid);

  const out = applyPolicies(context);
  assert.ok(estimateContextTokens(out) <= promptBudget(), 'policy output fits the budget');
  assert.equal(forwardedIdentityFromMessages(windowContextToBudget(context).messages), uid);

  const last = out.messages[out.messages.length - 1];
  assert.equal(last.role, 'user');
  assert.equal(
    last.content,
    'add milk to the shopping list',
    'model sees the human text, envelope stripped, message intact after windowing',
  );
});

// ─── end-to-end: over-budget session answers against a mocked provider ──────

type FakeLlama = {
  baseUrl: string;
  requests: Array<{ estTokens: number; body: { messages: Array<{ role: string; content?: unknown }> } }>;
  close: () => Promise<void>;
};

/**
 * In-process fake llama-server: OpenAI-compatible /chat/completions that
 * rejects oversized prompts with the REAL llama.cpp overflow error (the exact
 * live failure) and otherwise streams a short SSE answer.
 */
async function startFakeLlama(windowTokens: number): Promise<FakeLlama> {
  const requests: FakeLlama['requests'] = [];
  const server = createServer((req, res) => {
    let raw = '';
    req.on('data', (chunk) => (raw += chunk));
    req.on('end', () => {
      const body = JSON.parse(raw);
      // The same chars/4 heuristic the sidecar budgets with, applied to the
      // whole wire payload — a superset of what our estimator counted, so a
      // pass here is strictly harder than the estimator's own check.
      const estTokens = Math.ceil(raw.length / 4);
      requests.push({ estTokens, body });
      if (estTokens > windowTokens) {
        res.writeHead(400, { 'content-type': 'application/json' });
        res.end(
          JSON.stringify({
            error: {
              message: `request (${estTokens} tokens) exceeds the available context size (${windowTokens} tokens)`,
              type: 'invalid_request_error',
            },
          }),
        );
        return;
      }
      res.writeHead(200, { 'content-type': 'text/event-stream' });
      const chunk = (payload: unknown) => res.write(`data: ${JSON.stringify(payload)}\n\n`);
      chunk({
        id: 'cmpl-1',
        object: 'chat.completion.chunk',
        created: 0,
        model: 'local',
        choices: [{ index: 0, delta: { role: 'assistant', content: 'Practice is at 4pm.' }, finish_reason: null }],
      });
      chunk({
        id: 'cmpl-1',
        object: 'chat.completion.chunk',
        created: 0,
        model: 'local',
        choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
        usage: { prompt_tokens: estTokens, completion_tokens: 6, total_tokens: estTokens + 6 },
      });
      res.end('data: [DONE]\n\n');
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  return {
    baseUrl: `http://127.0.0.1:${port}/v1`,
    requests,
    close: () => new Promise((resolve) => server.close(() => resolve())),
  };
}

function fakeModel(baseUrl: string): Model<'openai-completions'> {
  return {
    id: 'local',
    name: 'fake-gemma',
    api: CAPPED_COMPLETIONS_API,
    provider: 'zoe',
    baseUrl,
    // Wire-handler-required metadata (transform-messages reads model.input,
    // usage parsing reads model.cost).
    input: ['text'],
    reasoning: false,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
  } as unknown as Model<'openai-completions'>;
}

test('an over-budget stored session still answers end-to-end through the capped provider', async () => {
  resetEnv();
  const { completeSimple } = await import('@earendil-works/pi-ai');
  registerCappedCompletions();
  const fake = await startFakeLlama(8192);
  try {
    const newest = wrapMessageWithIdentity('What time is soccer practice tomorrow?', 'jason');
    const context = baseContext(longHistory(120, newest));
    assert.ok(estimateContextTokens(context) > 8192, 'precondition: session is past the wall');

    // Control — windowing off reproduces the live permanent failure verbatim.
    process.env.ZOE_BRAIN_CONTEXT_WINDOW = '0';
    const wedged = await completeSimple(fakeModel(fake.baseUrl), context, { apiKey: 'local-no-key' });
    assert.equal(wedged.stopReason, 'error');
    assert.match(wedged.errorMessage ?? '', /exceeds the available context size/);

    // Fix — windowing on: the same session assembles under budget and answers.
    resetEnv();
    const answered = await completeSimple(fakeModel(fake.baseUrl), context, { apiKey: 'local-no-key' });
    assert.equal(answered.stopReason, 'stop', answered.errorMessage);
    const text = answered.content
      .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
      .map((b) => b.text)
      .join('');
    assert.equal(text, 'Practice is at 4pm.');

    const accepted = fake.requests[fake.requests.length - 1];
    assert.ok(accepted.estTokens <= 8192, 'wire payload fits the model context');
    const wireMessages = accepted.body.messages;
    const system = wireMessages.find((m) => m.role === 'system');
    for (const doctrine of ALL_DOCTRINES) {
      assert.ok(
        String(system?.content ?? '').includes(doctrine),
        `doctrine block missing on the wire: ${doctrine.slice(0, 60)}…`,
      );
    }
    const lastWire = wireMessages[wireMessages.length - 1];
    assert.equal(lastWire.role, 'user');
    assert.ok(
      String(lastWire.content).includes('What time is soccer practice tomorrow?'),
      'newest user message reached the model',
    );
    assert.ok(
      !String(lastWire.content).includes('zoe-uid:'),
      'identity envelope never reaches the model',
    );
  } finally {
    await fake.close();
    resetEnv();
  }
});
