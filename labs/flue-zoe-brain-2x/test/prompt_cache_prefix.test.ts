/**
 * llama-server prompt-cache reuse, end to end on the REAL sidecar (LAB-ONLY,
 * offline: in-process runtime, mock model on an ephemeral port).
 *
 * Why this exists: llama-server (`--parallel 1`, `--cache-ram 2048`) reuses only
 * a byte-identical PROMPT PREFIX. Gemma's chat template renders the system
 * prompt, THEN the tool declarations, THEN the history — so anything that varies
 * turn-to-turn in the system prompt or the tool block re-prefills the whole
 * session history before the first token (measured 2026-09-26: 282-854 tokens,
 * 0.6-1.5 s on a miss vs 11-20 tokens on a hit). These tests pin, on the bytes
 * the model actually received:
 *   1. two consecutive turns of one session share a byte-identical prefix —
 *      same system prompt, turn 1's tool block is a prefix of turn 2's, and
 *      turn 1's messages are a prefix of turn 2's — so only the new user
 *      message (the per-turn content) sits past the cached prefix;
 *   2. the NDJSON `{"done": true}` terminal forwards llama-server's per-call
 *      `prompt_n` / `cache_n` (from `usage.prompt_tokens_details.cached_tokens`)
 *      so zoe-data can log cache reuse per turn.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/prompt_cache_prefix.test.ts
 */
import assert from 'node:assert/strict';
import { after, before, describe, it } from 'node:test';
import {
  startBrainHarness,
  userMessageBody,
  waitFor,
  type BrainHarness,
} from './helpers/harness.ts';
import type { CapturedRequest } from './helpers/mock-model.ts';

const NDJSON = 'application/x-ndjson';

async function ndjsonTurn(h: BrainHarness, sid: string, text: string): Promise<unknown[]> {
  // Built by hand: `send()`'s `init` would REPLACE its headers (auth included).
  const res = await h.app.fetch(
    new Request(`http://brain.test/agents/zoe/${sid}`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        authorization: `Bearer ${h.token}`,
        accept: NDJSON,
      },
      body: userMessageBody(text),
    }),
  );
  assert.equal(res.status, 200, 'the streaming turn should upgrade to a 200 NDJSON stream');
  const body = await res.text();
  return body.split('\n').filter((l) => l.length > 0).map((l) => JSON.parse(l));
}

/** The request as a prefix-comparable sequence: system, then tools, then messages. */
function renderedParts(req: CapturedRequest): string[] {
  return [
    `system:${req.systemPrompt}`,
    ...req.toolNames.map((name) => `tool:${name}`),
    ...req.messages.filter((m) => m.role !== 'system').map((m) => `${m.role}:${m.text}`),
  ];
}

describe('prompt-cache prefix stability (real sidecar, mock model)', () => {
  let harness: BrainHarness;

  before(async () => {
    harness = await startBrainHarness([
      // turn 1: a weather-keyword turn the model answers in plain text — under
      // the old last-message-only disclosure its group RETRACTED on turn 2.
      { text: 'Looks sunny.', usage: { prompt: 3100, completion: 5, cached: 2338 } },
      // turn 2: no keyword at all.
      { text: 'Poach it gently.', usage: { prompt: 3140, completion: 6, cached: 3122 } },
    ]);
  });
  after(async () => {
    await harness.stop();
  });

  it('two consecutive turns share a byte-identical prefix; only the new user message follows it', async () => {
    const sid = 'cache-prefix-session';
    const first = await ndjsonTurn(harness, sid, 'what is the weather like outside?');
    assert.ok(await waitFor(() => harness.model.callCount >= 1));
    const second = await ndjsonTurn(harness, sid, 'thanks. how do I poach an egg?');
    assert.ok(await waitFor(() => harness.model.callCount >= 2));
    assert.deepEqual(first[first.length - 1], {
      done: true,
      prompt_cache: [{ prompt_n: 3100 - 2338, cache_n: 2338 }],
    });
    assert.deepEqual(second[second.length - 1], {
      done: true,
      prompt_cache: [{ prompt_n: 3140 - 3122, cache_n: 3122 }],
    });

    const [r1, r2] = harness.model.requests;
    // The stable block: byte-identical system prompt (no per-turn content).
    assert.equal(r2.systemPrompt, r1.systemPrompt, 'system prompt must not vary per turn');
    // The tool block only ever grows at its END within a session.
    assert.ok(r1.toolNames.includes('get_weather'), 'turn 1 disclosed the weather group');
    assert.deepEqual(r2.toolNames.slice(0, r1.toolNames.length), r1.toolNames);
    // Whole rendered request: turn 1's request is a prefix of turn 2's, and what
    // follows it is exactly turn 1's reply plus the new user message.
    const p1 = renderedParts(r1);
    const p2 = renderedParts(r2);
    assert.deepEqual(p2.slice(0, p1.length), p1, 'turn 2 must extend turn 1 byte-for-byte');
    const tail = p2.slice(p1.length);
    assert.equal(tail[tail.length - 1], 'user:thanks. how do I poach an egg?');
    assert.ok(tail.every((part) => part.startsWith('assistant:') || part.startsWith('user:')));
  });
});
