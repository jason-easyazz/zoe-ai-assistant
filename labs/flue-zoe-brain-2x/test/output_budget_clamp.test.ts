/**
 * Output-budget clamp coverage (LAB-ONLY, offline, no network beyond an
 * in-process mock model on localhost).
 *
 * THE LIVE BUG THIS PINS (diagnosed offline, reproduced 3×, PR #1616). The 2.x
 * dependency bump to `@earendil-works/pi-ai@0.83.0` introduced
 * `clampMaxTokensToContext` (dist/api/simple-options.js), which every
 * openai-completions request now passes through:
 *
 *     available = model.contextWindow − estimateContextTokens(context).tokens − 4096
 *     maxTokens = min(maxTokens, max(1, available))
 *
 * 0.79.10 (the 1.x lane) has no clamp at all, and the 1.x provider declared no
 * `contextWindow`. The port declared the REAL 8192-token window, so a ~4090-token
 * estimate left ~6 tokens of output — replies truncated to 1-8 tokens with
 * `stopReason: "length"` (the 2.x store shows output 1, 3, 7, 8). pi-agent-core
 * 0.83.0 then refuses to execute tool calls off a length-stopped message, writing
 * a `tool_outcome` with no `tool_results_committed`, and the next reduce throws
 * `ConversationRecordInvariantError` — the CANT_DO that killed the flip.
 *
 * THE FIX: declare `contextWindow: 0`, which pi-ai reads as "do not clamp", and
 * enforce the real constraint from the real budget instead —
 * `prompt ≤ W − reserve` (windowing) and `output ≤ reserve`
 * (`outputBudgetTokens`), so `prompt + output ≤ W`.
 *
 * WHY NOT JUST A BIGGER DECLARED WINDOW — the question this file exists to settle,
 * because it is the obvious idea and it is wrong. `estimateContextTokens` is
 * USAGE-ANCHORED (pi-ai dist/utils/estimate.js): with any retained assistant
 * message carrying non-zero usage — always, after turn 1, because @flue/runtime
 * rebuilds assistant messages with their recorded `usage` — it returns
 * `lastAssistantUsage.totalTokens + Σ(messages after it)` and ignores the system
 * prompt and tools entirely. So the number fed to the clamp is not the prompt:
 * the anchor alone can reach W (it includes the previous completion), the trailing
 * term is unbounded (this turn's tool results, which windowing keeps
 * unconditionally), and windowing never rebases the stale anchor. No additive
 * constant closes that. The rejected sizings are therefore not merely argued
 * against here — they are CONSTRUCTED and shown strangling output.
 *
 * Proven here:
 *   - the upstream clamp still has the shape and the 4096 safety constant the
 *     controls are built from (an instrument check — the REAL function is
 *     imported from node_modules, never re-implemented);
 *   - the declaration is 0 and the output budget is the reply reserve, so
 *     prompt budget + output budget is exactly the slot across several configs;
 *   - the full output budget survives every context shape, INCLUDING the
 *     usage-anchored ones production actually takes;
 *   - NEGATIVE CONTROLS: both rejected declarations — the pre-fix real window and
 *     the inflated `W + 4096 + reserve` — reproduce real recorded damage,
 *     including `maxTokens: 1` on a tool-result turn;
 *   - END TO END through the real agent, with the mock reporting usage so the
 *     agent runs on the SAME anchored branch as production.
 *
 * INSTRUMENT CAVEAT, learned the hard way while building this. pi-ai's estimator
 * and ours agree closely on a small un-windowed turn (measured 2937 vs 2957
 * tokens on a live harness turn) — but that agreement does NOT generalise, which
 * is exactly what the anchored cases below show. They also disagree wildly if a
 * test casts the raw Flue `zoeTools` (valibot schema objects) into
 * `Context['tools']`: pi stringifies the whole array and scores ~5300 tokens
 * against our ~1400. The contexts here use plain JSON-Schema tools or none — the
 * production shape.
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
  createZoeProvider,
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

/** Run `body` with env guaranteed restored even when an assertion throws. */
function withEnv(env: Record<string, string | undefined>, body: () => void): void {
  resetEnv();
  try {
    for (const [key, value] of Object.entries(env)) {
      if (value !== undefined) process.env[key] = value;
    }
    body();
  } finally {
    resetEnv();
  }
}

/** pi-ai's ~4 chars/token heuristic, the same one its estimator uses. */
const CHARS_PER_TOKEN = 4;

/**
 * A context with NO usage anchor, whose pi-ai estimate is exactly `tokens`.
 *
 * pi-ai scores a string-content user message as `ceil(len / 4)` and adds nothing
 * for an absent system prompt or an empty tool list. This is the STATIC-fallback
 * branch — real only for the very first turn of a session.
 */
function unanchoredContext(tokens: number): Context {
  const message = {
    role: 'user',
    content: 'x'.repeat(tokens * CHARS_PER_TOKEN),
    timestamp: 0,
  } as Message;
  return { systemPrompt: '', messages: [message], tools: [] } as Context;
}

/**
 * A context on the USAGE-ANCHORED branch — the one production takes from turn 2
 * onward. `priorPrompt`/`priorOutput` are the previous assistant turn's recorded
 * usage; `trailingTokens` is everything appended since (a new user message, or
 * this turn's tool results).
 *
 * The system prompt is deliberately large and deliberately irrelevant: pi-ai does
 * not count it on this branch, and asserting through a context that has one keeps
 * that fact visible.
 */
function anchoredContext(
  priorPrompt: number,
  priorOutput: number,
  trailingTokens: number,
): Context {
  return {
    systemPrompt: 'S'.repeat(2332 * CHARS_PER_TOKEN),
    tools: [],
    messages: [
      { role: 'user', content: 'first turn', timestamp: 1 },
      {
        role: 'assistant',
        content: [{ type: 'text', text: 'prior reply' }],
        timestamp: 2,
        stopReason: 'stop',
        usage: {
          input: priorPrompt,
          output: priorOutput,
          cacheRead: 0,
          cacheWrite: 0,
          totalTokens: priorPrompt + priorOutput,
        },
      },
      {
        role: 'user',
        content: 'y'.repeat(trailingTokens * CHARS_PER_TOKEN),
        timestamp: 3,
      },
    ],
  } as unknown as Context;
}

/** What pi-ai's estimator scores a context at, measured through the real clamp. */
function piEstimate(context: Context): number {
  const huge = 1_000_000;
  return (
    huge -
    PI_AI_CONTEXT_SAFETY_TOKENS -
    clampMaxTokensToContext({ contextWindow: huge } as Model<'openai-completions'>, context, 1e9)
  );
}

/** The model as the PRE-FIX code declared it: the real llama-server window. */
function preFixModel(): Model<'openai-completions'> {
  return { ...zoeLocalModel(), contextWindow: contextWindowTokens() || 8192 };
}

/**
 * The model as the FIRST attempt at this fix declared it — the window inflated by
 * the clamp's own overheads. Rejected; kept as an executable control so nobody
 * re-proposes it from the comment alone.
 */
function inflatedModel(): Model<'openai-completions'> {
  const w = contextWindowTokens() || 8192;
  return {
    ...zoeLocalModel(),
    contextWindow: w + PI_AI_CONTEXT_SAFETY_TOKENS + outputBudgetTokens(),
  };
}

/** What pi-ai would actually send as the output cap for this model + context. */
function sentMaxTokens(model: Model<'openai-completions'>, context: Context): number {
  return clampMaxTokensToContext(model, context, model.maxTokens);
}

// ─── instrument checks: the upstream mechanism the controls are built from ───

test('pi-ai 0.83 still clamps maxTokens, and its safety constant is still 4096', () => {
  resetEnv();
  // Probe the constant through the real function instead of trusting the source:
  // an empty context estimates 0, so `available = window − 0 − SAFETY`.
  const huge = 1_000_000;
  const probe = { contextWindow: huge } as Model<'openai-completions'>;
  const safety = huge - clampMaxTokensToContext(probe, unanchoredContext(0), huge);
  assert.equal(
    safety,
    PI_AI_CONTEXT_SAFETY_TOKENS,
    'pi-ai CONTEXT_SAFETY_TOKENS moved — the negative controls below are mis-sized',
  );
  // `contextWindow <= 0` is the documented no-clamp path the fix relies on.
  const off = { contextWindow: 0 } as Model<'openai-completions'>;
  assert.equal(clampMaxTokensToContext(off, unanchoredContext(9999), 2048), 2048);
});

test('pi-ai estimates from the USAGE ANCHOR, not from the prompt', () => {
  resetEnv();
  // The whole argument for `contextWindow: 0` rests on this. If upstream ever
  // makes the estimator prompt-faithful, a sized declaration becomes viable again
  // and this test is where that news arrives.
  assert.equal(
    piEstimate(anchoredContext(6656, 1536, 40)),
    6656 + 1536 + 40,
    'estimate must be prior total + trailing',
  );
  // ...and the 2332-token system prompt in that context contributed nothing.
  assert.equal(piEstimate(anchoredContext(100, 0, 0)), 100);

  // The real store's record, reproduced: replay-c86d12c148eb seq 79 carried
  // usage.totalTokens 4085, and the next assistant was clamped to 7 output
  // tokens — so the estimate pi-ai used was 4089, the anchor plus a ~4-token
  // trailing message, NOT a measurement of the ~3901-token prompt.
  assert.equal(piEstimate(anchoredContext(4073, 12, 4)), 4089);
  assert.equal(sentMaxTokens(preFixModel(), anchoredContext(4073, 12, 4)), 7);
});

test('pi-agent-core still refuses tool calls from a length-stopped message', () => {
  // Part 2 of the cascade. It is unreachable once no reply stops on "length",
  // but if upstream ever drops this guard the fix's rationale changes, so the
  // dependency is pinned rather than described.
  //
  // `dist/agent-loop.js` is not in the package's exports map (only ".", "./node"
  // and "./package.json" are), so resolve the manifest — which every npm package
  // must export — and read the file off disk relative to it. Hoisting-safe.
  const pkgRoot = dirname(require.resolve('@earendil-works/pi-agent-core/package.json'));
  const agentLoop = readFileSync(join(pkgRoot, 'dist', 'agent-loop.js'), 'utf8');
  assert.match(agentLoop, /failToolCallsFromTruncatedMessage/);
  assert.match(agentLoop, /stopReason === "length"/);
});

// ─── the declaration itself ──────────────────────────────────────────────────

test('the declared contextWindow is 0 — the clamp is off, by design', () => {
  resetEnv();
  assert.equal(declaredContextWindow(), 0);
  assert.equal(zoeLocalModel().contextWindow, 0);
  assert.equal(zoeLocalModel().maxTokens, outputBudgetTokens());

  // Not accidentally 0 because the env said so: it stays 0 for every window.
  for (const window of ['4096', '8192', '16384', '0']) {
    withEnv({ ZOE_BRAIN_CONTEXT_WINDOW: window }, () => {
      assert.equal(declaredContextWindow(), 0, `window=${window}`);
    });
  }
});

test('the provider PUBLISHES the declaration — not just the helper', () => {
  resetEnv();
  // Every other test calls zoeLocalModel() directly, but the running system holds
  // a boot-time snapshot taken inside createZoeProvider(). Assert the thing the
  // agent actually binds to, or the suite proves nothing about production.
  // `createProvider` does not expose `models` directly — it returns a
  // `getModels()` closure over the baseline list (pi-ai dist/models.js).
  const published = createZoeProvider().getModels();
  const model = published.find((m) => m.id === 'local') as Model<'openai-completions'>;
  assert.ok(model, 'the zoe provider did not publish its local model');
  assert.equal(model.contextWindow, 0);
  assert.equal(model.maxTokens, outputBudgetTokens());
});

test('the output budget IS the reply reserve, so a request always fits the slot', () => {
  // llama-server runs an 8192-token SLOT with context shifting OFF, so a request
  // for prompt + output > W is cut mid-reply by the server no matter what pi-ai
  // did. Windowing bounds the prompt at W − reserve; the output cap is the
  // reserve; the sum is exactly W.
  for (const [window, reserve] of [
    [undefined, undefined],
    ['4096', undefined],
    [undefined, '512'],
    ['2048', '4096'], // reserve clamped to half the window
  ] as [string | undefined, string | undefined][]) {
    withEnv(
      { ZOE_BRAIN_CONTEXT_WINDOW: window, ZOE_BRAIN_REPLY_RESERVE: reserve },
      () => {
        const w = contextWindowTokens();
        const promptBudget = w - replyReserveTokens(w);
        assert.equal(outputBudgetTokens(), replyReserveTokens(w));
        assert.equal(
          promptBudget + outputBudgetTokens(),
          w,
          `prompt budget + output budget must equal the slot (window=${window}, reserve=${reserve})`,
        );
      },
    );
  }
});

test('windowing off keeps a real reply cap instead of collapsing it', () => {
  withEnv({ ZOE_BRAIN_CONTEXT_WINDOW: '0' }, () => {
    assert.equal(contextWindowTokens(), 0, 'precondition: windowing is disabled');
    // replyReserveTokens(0) is 0, and a falsy maxTokens is dropped from the wire
    // entirely — so the fallback to the slot default is load-bearing, not cosmetic.
    assert.equal(outputBudgetTokens(), 1536, 'reply cap survives windowing being off');
    assert.equal(zoeLocalModel().maxTokens, 1536);
  });
  withEnv({ ZOE_BRAIN_CONTEXT_WINDOW: '0', ZOE_BRAIN_REPLY_RESERVE: '768' }, () => {
    assert.equal(outputBudgetTokens(), 768, "the operator's reserve still applies");
  });
});

// ─── THE ARITHMETIC PIN, with its negative controls ──────────────────────────

test('the full output budget survives EVERY context shape, anchored or not', () => {
  resetEnv();
  const w = contextWindowTokens();
  const budget = w - replyReserveTokens(w);
  assert.equal(budget, 6656, 'precondition: the documented default prompt budget');

  const shapes: [string, Context][] = [
    ['empty', unanchoredContext(0)],
    ['unanchored at the prompt budget', unanchoredContext(budget)],
    ['unanchored at the hard slot ceiling', unanchoredContext(w)],
    ['anchored, small trailing user message', anchoredContext(budget, 1536, 40)],
    ['anchored, 1200-token tool result', anchoredContext(budget, 600, 1200)],
    ['anchored, 2400-token recall packet', anchoredContext(budget, 200, 2400)],
    ['anchored, full reply + 1600-token tool result', anchoredContext(budget, 1536, 1600)],
    ['absurd', unanchoredContext(50_000)],
  ];
  for (const [label, context] of shapes) {
    assert.equal(
      sentMaxTokens(zoeLocalModel(), context),
      outputBudgetTokens(),
      `output budget cut on: ${label}`,
    );
  }
});

test('NEGATIVE CONTROL: the pre-fix declaration strangles output', () => {
  resetEnv();
  const w = contextWindowTokens();
  const budget = w - replyReserveTokens(w);

  // The recorded failure, reproduced from the store's own numbers.
  assert.equal(sentMaxTokens(preFixModel(), anchoredContext(4073, 12, 4)), 7);
  // And at a full prompt it collapses to the floor.
  assert.equal(sentMaxTokens(preFixModel(), unanchoredContext(budget)), 1);
  assert.equal(sentMaxTokens(preFixModel(), unanchoredContext(w)), 1);

  // It starts cutting the budget once the estimate passes 8192 − 4096 − 1536.
  let firstStrangled: number | null = null;
  for (let prompt = 0; prompt <= w; prompt += 128) {
    if (
      firstStrangled === null &&
      sentMaxTokens(preFixModel(), unanchoredContext(prompt)) < outputBudgetTokens()
    ) {
      firstStrangled = prompt;
    }
  }
  assert.equal(firstStrangled, 2688, 'first sweep step past 2560');
});

test('NEGATIVE CONTROL: the INFLATED declaration also strangles, on real shapes', () => {
  resetEnv();
  const budget = contextWindowTokens() - replyReserveTokens(contextWindowTokens());
  const full = outputBudgetTokens();

  // This is the sizing this PR first shipped and then rejected: W + 4096 +
  // reserve. It survives the unanchored cases, which is exactly why the first
  // version of this test suite passed...
  assert.equal(sentMaxTokens(inflatedModel(), unanchoredContext(budget)), full);
  assert.equal(sentMaxTokens(inflatedModel(), unanchoredContext(contextWindowTokens())), full);

  // ...and fails on the anchored shapes production actually takes, because the
  // anchor already spends the whole window and the trailing term is unbounded.
  const cases: [string, Context, number][] = [
    ['small trailing user message', anchoredContext(budget, 1536, 40), 1496],
    ['1200-token tool result', anchoredContext(budget, 600, 1200), 1272],
    ['2400-token recall packet', anchoredContext(budget, 200, 2400), 472],
    ['full reply + 1600-token tool result', anchoredContext(budget, 1536, 1600), 1],
  ];
  for (const [label, context, expected] of cases) {
    assert.equal(sentMaxTokens(inflatedModel(), context), expected, label);
    assert.ok(expected < full, `control must be strangled: ${label}`);
  }
  // The last row is the original bug, all the way back down to the floor.
  assert.equal(sentMaxTokens(inflatedModel(), anchoredContext(budget, 1536, 1600)), 1);
});

// ─── end to end: what the real agent actually puts on the wire ───────────────

test('a real multi-turn session sends the full output budget to llama-server', async () => {
  resetEnv();
  const filler = 'Here is a fairly long answer with plenty of words in it. '.repeat(12);
  // Report usage, so the agent runs on the SAME usage-anchored estimator branch
  // as production. Without this the whole end-to-end case sits on the static
  // fallback and proves nothing about the running system. The numbers are a
  // maximally-windowed turn: a full prompt budget and a full reply.
  const harness = await startBrainHarness(() => ({
    text: filler,
    usage: { prompt: 6656, completion: 1536 },
  }));
  try {
    for (let turn = 0; turn < 4; turn++) {
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

    // The control, on the same run: rebuild what the clamp would have seen under
    // each rejected declaration. The anchor is the usage the mock reported, so
    // these are the numbers the real estimator produced, not a re-derivation.
    const anchored = anchoredContext(6656, 1536, 40);
    assert.equal(sentMaxTokens(preFixModel(), anchored), 1, 'pre-fix control');
    assert.ok(
      sentMaxTokens(inflatedModel(), anchored) < outputBudgetTokens(),
      'inflated control',
    );
  } finally {
    await harness.stop();
    resetEnv();
  }
});
