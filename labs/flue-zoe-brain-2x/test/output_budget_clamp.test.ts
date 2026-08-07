/**
 * Output-budget clamp coverage (LAB-ONLY, offline, no network beyond an
 * in-process mock model on localhost).
 *
 * THE LIVE BUG THIS PINS (diagnosed offline, reproduced 3×, PR #1616). The 2.x
 * dependency bump to `@earendil-works/pi-ai@0.83.0` introduced
 * `clampMaxTokensToContext` (dist/api/simple-options.js), which every
 * openai-completions request now passes through:
 *
 *     available = model.contextWindow − estimateContextTokens(context) − 4096
 *     maxTokens = min(maxTokens, max(1, available))
 *
 * The file does not exist in 0.79.10 (the 1.x lane), and the 1.x provider
 * declared no `contextWindow`, so nothing clamped there. The port declared the
 * REAL 8192-token window, so a ~4090-token prompt left ~6 tokens of output —
 * replies truncated to 1-8 tokens with `stopReason: "length"` (the 2.x store
 * shows output 1, 3, 7, 8 verbatim). pi-agent-core@0.83.0 then refuses to
 * execute tool calls off a length-stopped message, writing a `tool_outcome`
 * with no `tool_results_committed`, and the next reduce throws
 * `ConversationRecordInvariantError` — the CANT_DO that killed the flip.
 *
 * THE FIX: `declaredContextWindow()` decouples the DECLARED window from the
 * windowing budget — `contextWindowTokens() + 4096 + outputBudgetTokens()` — so
 * for any prompt that would fit llama-server at all the clamp leaves the full
 * intended output budget. The real prompt budget stays enforced by
 * src/context-window.ts, and the output budget IS its reply reserve, so
 * `prompt + output ≤ W` and the request always fits the server's slot.
 *
 * Proven here:
 *   - the upstream clamp still has the shape and the 4096 safety constant this
 *     fix is sized against (an instrument check — the REAL function is imported
 *     from node_modules, never re-implemented);
 *   - at the full prompt budget, at the hard server ceiling, and across a sweep
 *     in between, the real clamp leaves exactly outputBudgetTokens();
 *   - NEGATIVE CONTROL: reverting to the pre-fix declaration on the identical
 *     context reproduces the store's recorded truncation (8 output tokens at the
 *     observed prompt size, 1 at budget) — so a regression cannot pass silently;
 *   - windowing disabled (ZOE_BRAIN_CONTEXT_WINDOW=0) disables the clamp rather
 *     than strangling output by an unbounded amount;
 *   - the output budget equals the reply reserve, so prompt budget + output
 *     budget is exactly the slot size across several env configurations;
 *   - END TO END through the real agent: a multi-turn session's request carries
 *     the full output budget on the wire, at a prompt size where the pre-fix
 *     declaration would have left a handful of tokens.
 *
 * INSTRUMENT CAVEAT, learned the hard way while building this. pi-ai's estimator
 * and ours agree closely on the REAL path (measured 2937 vs 2957 tokens on a
 * live harness turn) because Flue hands pi converted `Tool` objects with plain
 * JSON-Schema `parameters`. They do NOT agree if a test casts the raw Flue
 * `zoeTools` (valibot schema objects) into `Context['tools']`: pi stringifies the
 * whole array and scores ~5300 tokens against our ~1400, which would make the
 * arithmetic below look broken when it is not. The contexts here therefore carry
 * either no tools or plain JSON-Schema tools — the production shape.
 *
 * Run (Node 22, type-stripping):
 *   node --experimental-strip-types --test test/output_budget_clamp.test.ts
 */
process.env.ZOE_BRAIN_USER_ID = 'jason';

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import type { Context, Message, Model } from '@earendil-works/pi-ai';
// The REAL 0.83.0 clamp, imported from the installed package rather than
// re-implemented: if upstream changes the formula, these tests change colour.
import { clampMaxTokensToContext } from '@earendil-works/pi-ai/api/simple-options';

const {
  declaredContextWindow,
  zoeLocalModel,
  PI_AI_CONTEXT_SAFETY_TOKENS,
  outputBudgetTokens,
} = await import('../src/providers/capped-completions.ts');
const { contextWindowTokens, replyReserveTokens } = await import(
  '../src/context-window.ts'
);
const { startBrainHarness, waitFor } = await import('./helpers/harness.ts');

const require = createRequire(import.meta.url);

function resetEnv(): void {
  delete process.env.ZOE_BRAIN_CONTEXT_WINDOW;
  delete process.env.ZOE_BRAIN_REPLY_RESERVE;
}

/** pi-ai's ~4 chars/token heuristic, the same one its estimator uses. */
const CHARS_PER_TOKEN = 4;

/**
 * A context whose pi-ai estimate is EXACTLY `tokens`.
 *
 * pi-ai scores a string-content user message as `ceil(len / 4)` and adds nothing
 * for an absent system prompt or an empty tool list, so one message of `4 × n`
 * characters pins the estimate precisely — no reliance on our own estimator, and
 * no dependence on which side of pi's usage-anchored branch we land on (a lone
 * user message has no assistant usage to anchor to).
 */
function contextOfExactly(tokens: number): Context {
  const message = {
    role: 'user',
    content: 'x'.repeat(tokens * CHARS_PER_TOKEN),
    timestamp: 0,
  } as Message;
  return { systemPrompt: '', messages: [message], tools: [] } as Context;
}

/** The model as the PRE-FIX code declared it: the real llama-server window. */
function preFixModel(): Model<'openai-completions'> {
  return { ...zoeLocalModel(), contextWindow: contextWindowTokens() || 8192 };
}

/** What pi-ai would actually send as the output cap for this model + context. */
function sentMaxTokens(model: Model<'openai-completions'>, context: Context): number {
  return clampMaxTokensToContext(model, context, model.maxTokens);
}

// ─── instrument checks: the upstream mechanism this fix is sized against ─────

test('pi-ai 0.83 still clamps maxTokens, and its safety constant is still 4096', () => {
  resetEnv();
  // Probe the constant through the real function instead of trusting the source:
  // an empty context estimates 0, so `available = window − 0 − SAFETY`.
  const huge = 1_000_000;
  const probe = { contextWindow: huge } as Model<'openai-completions'>;
  const safety = huge - clampMaxTokensToContext(probe, contextOfExactly(0), huge);
  assert.equal(
    safety,
    PI_AI_CONTEXT_SAFETY_TOKENS,
    'pi-ai CONTEXT_SAFETY_TOKENS moved — re-size declaredContextWindow()',
  );
  // `contextWindow <= 0` is the documented no-clamp escape hatch we rely on.
  const off = { contextWindow: 0 } as Model<'openai-completions'>;
  assert.equal(clampMaxTokensToContext(off, contextOfExactly(9999), 2048), 2048);
});

test('pi-agent-core still refuses tool calls from a length-stopped message', () => {
  // Part 2 of the cascade. It is unreachable once no reply stops on "length",
  // but if upstream ever drops this guard the fix's rationale changes, so the
  // dependency is pinned rather than described.
  // `dist/agent-loop.js` is not in the package's exports map (only ".", "./node"
  // and "./package.json" are), so resolve the manifest — which every npm package
  // must export — and read the file off disk relative to it. Hoisting-safe.
  const pkgRoot = dirname(require.resolve('@earendil-works/pi-agent-core/package.json'));
  const agentLoop = readFileSync(join(pkgRoot, 'dist', 'agent-loop.js'), 'utf8');
  assert.match(agentLoop, /failToolCallsFromTruncatedMessage/);
  assert.match(agentLoop, /stopReason === "length"/);
});

// ─── the declaration itself ──────────────────────────────────────────────────

test('declaredContextWindow is the real window plus the clamp overhead', () => {
  resetEnv();
  assert.equal(
    declaredContextWindow(),
    8192 + PI_AI_CONTEXT_SAFETY_TOKENS + outputBudgetTokens(),
  );
  assert.equal(declaredContextWindow(), 8192 + 4096 + 1536);
  assert.equal(zoeLocalModel().contextWindow, declaredContextWindow());
  assert.equal(zoeLocalModel().maxTokens, outputBudgetTokens());

  process.env.ZOE_BRAIN_CONTEXT_WINDOW = '4096';
  assert.equal(declaredContextWindow(), 4096 + 4096 + 1536);
  resetEnv();
});

test('the output budget IS the reply reserve, so a request always fits the slot', () => {
  resetEnv();
  // The property the flip runbook's zero-length-stop assertion rests on:
  // llama-server runs an 8192-token SLOT with context shifting OFF, so a request
  // for prompt + output > W is cut mid-reply by the server no matter what pi-ai
  // clamped to. Windowing bounds the prompt at W − reserve; the output cap is the
  // reserve; the sum is exactly W.
  for (const [window, reserve] of [
    [undefined, undefined],
    ['4096', undefined],
    [undefined, '512'],
    ['2048', '4096'], // reserve clamped to half the window
  ] as [string | undefined, string | undefined][]) {
    resetEnv();
    if (window) process.env.ZOE_BRAIN_CONTEXT_WINDOW = window;
    if (reserve) process.env.ZOE_BRAIN_REPLY_RESERVE = reserve;

    const w = contextWindowTokens();
    const promptBudget = w - replyReserveTokens(w);
    assert.equal(outputBudgetTokens(), replyReserveTokens(w));
    assert.equal(
      promptBudget + outputBudgetTokens(),
      w,
      `prompt budget + output budget must equal the slot (window=${window}, reserve=${reserve})`,
    );
    // ...and the clamp still leaves that whole output budget at a full prompt.
    assert.equal(
      sentMaxTokens(zoeLocalModel(), contextOfExactly(promptBudget)),
      outputBudgetTokens(),
    );
  }
  resetEnv();
});

test('windowing off declares 0, which turns the clamp off rather than strangling it', () => {
  process.env.ZOE_BRAIN_CONTEXT_WINDOW = '0';
  assert.equal(contextWindowTokens(), 0, 'precondition: windowing is disabled');
  assert.equal(declaredContextWindow(), 0);
  // The reply cap must NOT collapse with the window: replyReserveTokens(0) is 0,
  // and a falsy maxTokens is dropped from the wire entirely, so the fallback to
  // the default slot is load-bearing rather than cosmetic.
  assert.equal(outputBudgetTokens(), 1536, 'reply cap survives windowing being off');
  assert.equal(zoeLocalModel().maxTokens, 1536);

  const model = zoeLocalModel();
  // An absurdly large prompt still gets the full output budget: with our
  // windowing off there is no `prompt ≤ W` premise to size a margin against, so
  // the honest guard is llama-server's loud 400, not a silent truncation.
  assert.equal(sentMaxTokens(model, contextOfExactly(50_000)), outputBudgetTokens());

  // ...and the operator's own reserve still applies in that mode.
  process.env.ZOE_BRAIN_REPLY_RESERVE = '768';
  assert.equal(outputBudgetTokens(), 768);
  resetEnv();
});

// ─── THE ARITHMETIC PIN, with its negative control ───────────────────────────

test('a prompt at the full real budget still gets the whole output budget', () => {
  resetEnv();
  const window = contextWindowTokens();
  const budget = window - replyReserveTokens(window);
  assert.equal(budget, 6656, 'precondition: the documented default prompt budget');

  const context = contextOfExactly(budget);
  assert.equal(sentMaxTokens(zoeLocalModel(), context), outputBudgetTokens());

  // NEGATIVE CONTROL — revert the declared window to the pre-fix value and the
  // same prompt is strangled to a single token.
  assert.equal(sentMaxTokens(preFixModel(), context), 1);
});

test('even a prompt at the hard llama-server ceiling keeps the full output budget', () => {
  resetEnv();
  const window = contextWindowTokens();
  // W is the largest prompt llama-server would accept at all; anything past it
  // is a 400 regardless of what we declare. So this is the worst admissible case.
  const context = contextOfExactly(window);
  assert.equal(sentMaxTokens(zoeLocalModel(), context), outputBudgetTokens());
  assert.equal(sentMaxTokens(preFixModel(), context), 1);
});

test('the pre-fix declaration reproduces the truncation recorded in the 2.x store', () => {
  resetEnv();
  // The store's failing record: usage.input 3900, stopReason "length", output 8.
  // pi-ai's estimate of that prompt is 8192 − 4096 − 8 = 4088 tokens; at exactly
  // that size the pre-fix declaration yields the recorded 8-token budget, and
  // the fix yields the whole reserve.
  const context = contextOfExactly(4088);
  assert.equal(sentMaxTokens(preFixModel(), context), 8, 'the recorded failure');
  assert.equal(sentMaxTokens(zoeLocalModel(), context), outputBudgetTokens());
});

test('across every admissible prompt size the fix never cuts the output budget', () => {
  resetEnv();
  const window = contextWindowTokens();
  let firstStrangledPreFix: number | null = null;
  for (let prompt = 0; prompt <= window; prompt += 128) {
    const context = contextOfExactly(prompt);
    assert.equal(
      sentMaxTokens(zoeLocalModel(), context),
      outputBudgetTokens(),
      `output budget cut at a ${prompt}-token prompt`,
    );
    if (
      firstStrangledPreFix === null &&
      sentMaxTokens(preFixModel(), context) < outputBudgetTokens()
    ) {
      firstStrangledPreFix = prompt;
    }
  }
  // The control again: the pre-fix declaration starts cutting the output budget
  // once the prompt passes 8192 − 4096 − 1536 = 2560 tokens — under a third of
  // the window, and well inside the 6656-token prompt budget windowing allows.
  // The first sweep step past that is 2688.
  assert.equal(firstStrangledPreFix, 2688);
});

// ─── end to end: what the real agent actually puts on the wire ───────────────

test('a real multi-turn session sends the full output budget to llama-server', async () => {
  resetEnv();
  // Long scripted replies so the session's prompt grows past the size that broke
  // the flip, while staying inside the windowing budget.
  const filler = 'Here is a fairly long answer with plenty of words in it. '.repeat(12);
  const harness = await startBrainHarness(() => ({ text: filler }));
  try {
    for (let turn = 0; turn < 6; turn++) {
      const res = await harness.send(
        'clamp-1',
        `Turn ${turn}: tell me about the week ahead, in detail please. ` +
          'I want the full picture, every appointment and every errand. '.repeat(6),
      );
      assert.equal(res.status, 202, 'turn admitted');
      const expected = turn + 1;
      assert.ok(
        await waitFor(() => harness.model.callCount >= expected),
        `model call ${expected} never arrived`,
      );
    }

    const last = harness.model.requests[harness.model.requests.length - 1];
    const raw = last.raw as { max_completion_tokens?: number; max_tokens?: number };
    const sent = raw.max_completion_tokens ?? raw.max_tokens;
    assert.equal(
      sent,
      outputBudgetTokens(),
      'the real wire request must carry the full output budget',
    );

    // The control, computed from the SAME measured request: pi's estimator is
    // chars/4 over the serialized prompt, so this closely tracks what the clamp
    // saw (measured agreement on this path: 2937 vs 2957 tokens). At this size
    // the pre-fix declaration would have left a handful of tokens.
    const promptChars =
      JSON.stringify(last.raw.messages ?? []).length +
      JSON.stringify(last.raw.tools ?? []).length;
    const promptTokens = Math.ceil(promptChars / CHARS_PER_TOKEN);
    assert.ok(
      promptTokens > 3500,
      `prompt must be big enough to be a real control (was ${promptTokens})`,
    );
    const preFixBudget = Math.max(
      1,
      (contextWindowTokens() || 8192) - promptTokens - PI_AI_CONTEXT_SAFETY_TOKENS,
    );
    assert.ok(
      preFixBudget <= 64,
      `control is not red: the pre-fix declaration would have allowed ${preFixBudget} tokens`,
    );
  } finally {
    await harness.stop();
    resetEnv();
  }
});
