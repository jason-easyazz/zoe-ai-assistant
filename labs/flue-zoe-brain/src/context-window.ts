/**
 * Prompt-fit history windowing for the Flue Zoe-brain sidecar.
 *
 * THE BUG THIS FIXES (live, 2026-07-07): durable Flue sessions grow without
 * bound, and nothing between the session store and llama-server ever shrinks
 * the assembled prompt. Once system prompt + tool schemas + full history
 * crossed the model context (8192 tokens on the shared llama-server,
 * `--ctx-size 8192`), EVERY subsequent turn on that session failed permanently:
 * `400 request (8288 tokens) exceeds the available context size (8192 tokens)`
 * (observed on the harness replay session at 198 stored entries). Any
 * long-lived real session — Telegram, voice — hits the same wall.
 *
 * WHY WINDOWING HERE, NOT FLUE'S NATIVE COMPACTION (checked first, per the
 * check-the-runtime-before-hand-rolling rule): @flue/runtime 1.0.0-beta.6 DOES
 * ship a supported compaction system (`packages/runtime/src/compaction.ts`,
 * `defineAgent({ compaction })`, `registerProvider({ contextWindow, maxTokens })`),
 * with a token-threshold trigger and overflow-recovery retry, and pi-ai 0.79.10
 * even pattern-matches llama.cpp's overflow error. It was measured against this
 * deployment and deliberately NOT enabled, for three reasons:
 *   1. It cannot rescue a session that is already past the wall: compaction
 *      summarizes `history - keepRecentTokens` through the SAME 8k-window model,
 *      so a well-oversized session overflows the summarizer itself, and the
 *      one-attempt overflow recovery then gives up — the exact permanent-500
 *      wedge we are fixing. Wire windowing recovers ANY stored size instantly.
 *   2. Its summarization pass is 1-2 extra Gemma calls at unpredictable turn
 *      boundaries — a nondeterministic multi-second stall on the latency-gated
 *      voice path (AGENTS.md: per-stage speed must not regress).
 *   3. The defaults are sized for coding-agent windows (keepRecentTokens 8000
 *      on our 8192 window can never converge), and the summary quality rests on
 *      the 4B brain.
 * Windowing is deterministic, adds zero model calls, and is the design fit for
 * THIS brain: memory is tool-based (recall_memory), so windowed-out facts are
 * recoverable — the per-turn extractor stored them — while a lossy 4B summary
 * would not be. Flue's compaction stays available via config if that trade ever
 * flips.
 *
 * WHERE IT RUNS: inside `applyPolicies` in src/providers/capped-completions.ts
 * — the sidecar's existing wire seam that every model call (every tool round of
 * every turn) already flows through. Nothing in @flue/runtime or pi-agent-core
 * is forked or patched; the durable session store keeps FULL history (nothing
 * is deleted), only the prompt sent to llama-server is windowed.
 *
 * WHAT IS GUARANTEED:
 *   - the system prompt (soul + every doctrine block) is NEVER touched — it is
 *     a separate Context field and this module only ever drops old MESSAGES;
 *   - the newest user message and everything after it (the current turn's tool
 *     rounds) ALWAYS survive intact, so the ` zoe-uid:` identity envelope on
 *     the last user message keeps working (it is read from the pre-windowed
 *     context anyway, see bindIdentityForRound);
 *   - history is dropped only in whole user-turn blocks, oldest first, so an
 *     assistant toolCall message is never separated from its toolResult.
 *
 * TOKEN BUDGET: prompt budget = ZOE_BRAIN_CONTEXT_WINDOW (default 8192, the
 * llama-server rock's --ctx-size) minus ZOE_BRAIN_REPLY_RESERVE (default 1536:
 * room for the spoken reply — prod caps voice turns at 512 tokens,
 * ZOE_CORE_VOICE_MODEL_MAXTOKENS — plus chat-template/special-token overhead
 * and estimator slack). Tokens are ESTIMATED at ~4 chars/token (the same
 * heuristic Flue's own compaction uses) plus per-message overhead; there is no
 * tokenizer in-process. FAILURE MODE, stated honestly: the estimate undercounts
 * token-dense text (CJK, emoji, dense code), so a session stuffed with such
 * content could still overflow — that turn errors, and unlike the pre-fix
 * behaviour the session can recover as those blocks age out of the window, but
 * recovery is not immediate. Accepted because this brain's input is Moonshine
 * English STT + English chat, where 4 chars/token slightly OVERcounts (safe
 * direction), and the 1536-token reserve absorbs the residual error.
 *
 * LAB ONLY (production-reachable via ZOE_BRAIN_BACKEND=flue — prod quality).
 */
import type { Context, Message, Tool } from '@earendil-works/pi-ai';

const DEFAULT_CONTEXT_WINDOW_TOKENS = 8192;
const DEFAULT_REPLY_RESERVE_TOKENS = 1536;

/** ~4 chars/token — Flue's own compaction heuristic; slightly conservative for English. */
const CHARS_PER_TOKEN = 4;
/** Per-message chat-template overhead (role headers, separators, toolCall framing). */
const PER_MESSAGE_OVERHEAD_TOKENS = 8;
/** Fixed per-request overhead (BOS, template preamble, tool-section framing). */
const PER_REQUEST_OVERHEAD_TOKENS = 64;
/** Flue's flat estimate for an image block in a tool result. */
const IMAGE_BLOCK_TOKENS = 4800;

/**
 * The model context window this sidecar budgets against. Default 8192 — the
 * shared llama-server's --ctx-size (scripts/setup/systemd/llama-server.service);
 * override via ZOE_BRAIN_CONTEXT_WINDOW. `0` explicitly DISABLES windowing
 * (pre-fix behaviour, A/B escape hatch). Read fresh per call, validated with
 * the default as fallback — same idiom as maxToolIters/brainTemperature.
 */
export function contextWindowTokens(): number {
  const raw = (process.env.ZOE_BRAIN_CONTEXT_WINDOW ?? '').trim();
  if (!raw) return DEFAULT_CONTEXT_WINDOW_TOKENS;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : DEFAULT_CONTEXT_WINDOW_TOKENS;
}

/**
 * Tokens held back from the window for the model's reply plus estimator slack.
 * Override via ZOE_BRAIN_REPLY_RESERVE; clamped to at most half the window so a
 * misconfigured reserve can never starve the prompt entirely.
 */
export function replyReserveTokens(windowTokens: number): number {
  const raw = (process.env.ZOE_BRAIN_REPLY_RESERVE ?? '').trim();
  const n = Number(raw);
  const reserve =
    raw && Number.isFinite(n) && n > 0 ? Math.floor(n) : DEFAULT_REPLY_RESERVE_TOKENS;
  return Math.min(reserve, Math.floor(windowTokens / 2));
}

/** chars/4 heuristic over a plain string. */
export function estimateTextTokens(text: string): number {
  return Math.ceil(text.length / CHARS_PER_TOKEN);
}

/**
 * Estimated prompt cost of one message — the same content walk Flue's
 * compaction uses (text + thinking + toolCall name/args + toolResult text,
 * flat cost per image), plus a small per-message template overhead.
 */
export function estimateMessageTokens(message: Message): number {
  let chars = 0;
  let images = 0;
  const { content } = message as { content: unknown };
  if (typeof content === 'string') {
    chars += content.length;
  } else if (Array.isArray(content)) {
    for (const block of content) {
      if (!block || typeof block !== 'object') continue;
      const b = block as {
        type?: string;
        text?: unknown;
        thinking?: unknown;
        name?: unknown;
        arguments?: unknown;
      };
      if (b.type === 'text' && typeof b.text === 'string') chars += b.text.length;
      else if (b.type === 'thinking' && typeof b.thinking === 'string')
        chars += b.thinking.length;
      else if (b.type === 'toolCall')
        chars += String(b.name ?? '').length + JSON.stringify(b.arguments ?? {}).length;
      else if (b.type === 'image') images += 1;
    }
  }
  return (
    Math.ceil(chars / CHARS_PER_TOKEN) +
    images * IMAGE_BLOCK_TOKENS +
    PER_MESSAGE_OVERHEAD_TOKENS
  );
}

/** Estimated prompt cost of the tool schemas the model will be offered. */
export function estimateToolTokens(tools: Tool[] | undefined): number {
  if (!tools || tools.length === 0) return 0;
  let chars = 0;
  for (const tool of tools) {
    chars += tool.name.length + (tool.description?.length ?? 0);
    try {
      chars += JSON.stringify(tool.parameters ?? {}).length;
    } catch {
      // Non-serializable schema: charge a flat conservative cost.
      chars += 2000;
    }
  }
  return Math.ceil(chars / CHARS_PER_TOKEN);
}

/** Estimated total prompt cost of a context as sent on the wire. */
export function estimateContextTokens(context: Context): number {
  let tokens =
    PER_REQUEST_OVERHEAD_TOKENS +
    estimateTextTokens(context.systemPrompt ?? '') +
    estimateToolTokens(context.tools);
  for (const message of context.messages) tokens += estimateMessageTokens(message);
  return tokens;
}

/**
 * Window `context.messages` so the estimated prompt fits the token budget.
 *
 * The message list is grouped into turn blocks, each starting at a `user`
 * message. The final block (newest user message + the current turn's tool
 * rounds) is kept UNCONDITIONALLY; older blocks are then kept newest-first,
 * whole blocks only, while the estimate stays under budget — the result is
 * always a contiguous suffix of the history starting at a user message.
 *
 * Returns the same Context reference when nothing needs dropping (the
 * no-alloc idiom of stripIdentityEnvelope). Windowing disabled
 * (ZOE_BRAIN_CONTEXT_WINDOW=0) or no user message in the history → unchanged.
 */
export function windowContextToBudget(context: Context): Context {
  const windowTokens = contextWindowTokens();
  if (windowTokens <= 0) return context;

  const budget = windowTokens - replyReserveTokens(windowTokens);
  if (estimateContextTokens(context) <= budget) return context;

  const { messages } = context;
  // Start index of each turn block: every user message begins a block.
  const blockStarts: number[] = [];
  for (let i = 0; i < messages.length; i++) {
    if (messages[i].role === 'user') blockStarts.push(i);
  }
  if (blockStarts.length === 0) return context;

  const fixedTokens =
    PER_REQUEST_OVERHEAD_TOKENS +
    estimateTextTokens(context.systemPrompt ?? '') +
    estimateToolTokens(context.tools);

  // Cost of each block (block k spans blockStarts[k] .. next start - 1).
  const blockTokens = blockStarts.map((start, k) => {
    const end = k + 1 < blockStarts.length ? blockStarts[k + 1] : messages.length;
    let tokens = 0;
    for (let i = start; i < end; i++) tokens += estimateMessageTokens(messages[i]);
    return tokens;
  });

  // Keep the newest block no matter what, then extend the contiguous window
  // backwards while it still fits.
  let keptFrom = blockStarts.length - 1;
  let used = fixedTokens + blockTokens[keptFrom];
  while (keptFrom > 0 && used + blockTokens[keptFrom - 1] <= budget) {
    keptFrom -= 1;
    used += blockTokens[keptFrom];
  }

  // keptFrom === 0 with an over-budget estimate means the overshoot comes from
  // messages BEFORE the first user turn (stray preamble): every user-turn block
  // fits, so drop the preamble and keep the invariant that the result is a
  // contiguous suffix starting at a user message (Greptile #1138 P2).
  return { ...context, messages: messages.slice(blockStarts[keptFrom]) };
}
