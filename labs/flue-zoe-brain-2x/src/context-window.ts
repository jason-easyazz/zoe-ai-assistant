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
 * check-the-runtime-before-hand-rolling rule): @flue/runtime DOES ship a
 * supported compaction system — on 2.x it is `useModel(model, { compaction })`
 * with `reserveTokens` / `keepRecentTokens` / a cheaper summariser `model`,
 * plus overflow recovery — and pi-ai even pattern-matches llama.cpp's overflow
 * error. It was measured against this deployment and deliberately NOT enabled,
 * for three reasons:
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
 * flips; the agent pins `compaction: false` so the threshold trigger cannot fire
 * behind this module's back (src/agents/zoe.ts).
 *
 * ANCHOR STABILITY (added 2026-08-03 — see `windowContextToBudget`): the first
 * version of this module recomputed the window head from scratch on every model
 * call with a greedy fill-to-budget rule, which made the head SLIDE on every
 * tool round of every turn. Correct output, ruinous prompt caching: llama-server's
 * KV prefix cache keys on a byte-identical prefix, so a head that moves each
 * round re-prefills the whole prompt each round. The head is now derived from a
 * basis that does not change mid-turn, and quantised so it steps rather than
 * creeps. The sibling fix on the prod side is PR #1612 /
 * services/zoe-data/tests/test_zoe_agent_kv_prefix.py.
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
 * Fraction of the prompt budget the anchor quantum is sized at. When the window
 * has to move it moves by at least this much, so the head then holds still for
 * several turns instead of creeping forward every turn. 0.25 → the retained
 * history sits 0-25% below budget after a re-anchor (~25-33% headroom in
 * practice once the in-flight tail is counted), and the head steps at most once
 * per ~1/4-budget of conversation growth.
 */
const ANCHOR_QUANTUM_FRACTION = 0.25;

/** Start index of every turn block: a block begins at each `user` message. */
function turnBlockStarts(messages: Message[]): number[] {
  const starts: number[] = [];
  for (let i = 0; i < messages.length; i++) {
    if (messages[i].role === 'user') starts.push(i);
  }
  return starts;
}

/**
 * The head index this context windows to, expressed as a message index, or
 * `null` when no windowing is needed. Pure and exported so the anchor-stability
 * tests can assert on the head directly rather than inferring it from output.
 *
 * THE STABILITY CONTRACT, and why each half of it exists:
 *
 * 1. THE BASIS EXCLUDES THE IN-FLIGHT TAIL. The budget arithmetic runs over the
 *    messages up to and including the LAST USER MESSAGE. Everything after it —
 *    this turn's assistant/toolResult rounds — is kept unconditionally and is
 *    NOT allowed to influence the head. The beta counted it, so every tool round
 *    grew `used`, pushed the head forward, and changed the prompt prefix
 *    mid-turn: with an 8-round cap that is up to 8 full prefills for one turn.
 *    The head is now constant for the whole turn by construction.
 *
 * 2. THE HEAD IS QUANTISED. Given a required drop, we drop up to the next
 *    multiple of `quantum` instead of the minimum that fits. A minimum-drop rule
 *    advances the head roughly every turn once a session is over budget (each new
 *    turn adds tokens, so the minimum drop grows); quantising makes it a step
 *    function that only moves when the required drop crosses a quantum boundary.
 *
 * HONEST FAILURE MODE, and the valve that bounds it: because (1) excludes the
 * in-flight tail from the budget, a turn whose tool results are enormous can
 * push the real prompt past budget. The quantum headroom absorbs the normal
 * case; when it does not, the SAFETY VALVE below drops further — un-quantised,
 * deliberately sacrificing prefix stability — because a 400 `exceeds the
 * available context size` from llama-server is a hard failure and a cold cache
 * is only slow. So the guarantee is: never overflow; keep the prefix stable
 * whenever staying under budget allows it.
 */
export function windowHeadIndex(context: Context): number | null {
  const windowTokens = contextWindowTokens();
  if (windowTokens <= 0) return null;

  const budget = windowTokens - replyReserveTokens(windowTokens);
  // Fast path: the whole context already fits, so nothing is dropped — not even
  // a pre-user preamble. Windowing is a rescue, never a routine rewrite.
  if (estimateContextTokens(context) <= budget) return null;

  const { messages } = context;
  const blockStarts = turnBlockStarts(messages);
  if (blockStarts.length === 0) return null;

  const fixedTokens =
    PER_REQUEST_OVERHEAD_TOKENS +
    estimateTextTokens(context.systemPrompt ?? '') +
    estimateToolTokens(context.tools);

  // (1) The anchor basis: everything up to and including the last user message.
  // The in-flight tail after it is excluded from the arithmetic entirely.
  const lastUserIndex = blockStarts[blockStarts.length - 1];
  const messageTokens = messages.map(estimateMessageTokens);
  let basisTokens = fixedTokens;
  for (let i = 0; i <= lastUserIndex; i++) basisTokens += messageTokens[i];

  const quantum = Math.max(1, Math.floor(budget * ANCHOR_QUANTUM_FRACTION));

  // `cumulative[k]` = tokens dropped by starting the window at block k.
  const cumulative: number[] = [];
  let dropped = 0;
  for (let k = 0; k < blockStarts.length; k++) {
    cumulative.push(dropped);
    const end = k + 1 < blockStarts.length ? blockStarts[k + 1] : messages.length;
    for (let i = blockStarts[k]; i < end; i++) dropped += messageTokens[i];
  }

  // (2) Quantised head: the first block whose cumulative drop meets the target.
  // A non-positive required drop means the basis fits — head stays at block 0.
  const requiredDrop = basisTokens - budget;
  let head = 0;
  if (requiredDrop > 0) {
    const targetDrop = Math.ceil(requiredDrop / quantum) * quantum;
    head = blockStarts.length - 1;
    for (let k = 0; k < blockStarts.length; k++) {
      if (cumulative[k] >= targetDrop) {
        head = k;
        break;
      }
    }
  }

  // SAFETY VALVE: re-check against the REAL total (in-flight tail included) and
  // drop further, un-quantised, if the quantum headroom was not enough. Never
  // past the newest block — that one is kept unconditionally.
  let realTotal = fixedTokens;
  for (let i = blockStarts[head]; i < messages.length; i++) realTotal += messageTokens[i];
  while (head < blockStarts.length - 1 && realTotal > budget) {
    const end = head + 1 < blockStarts.length ? blockStarts[head + 1] : messages.length;
    for (let i = blockStarts[head]; i < end; i++) realTotal -= messageTokens[i];
    head += 1;
  }

  // We only get here when the context did NOT fit. head === 0 therefore means the
  // overshoot comes from messages BEFORE the first user turn (stray preamble):
  // every user-turn block fits, so drop the preamble and keep the invariant that
  // the result is a contiguous suffix starting at a user message (Greptile #1138
  // P2). `blockStarts[0] === 0` (no preamble) yields 0 and windows nothing.
  return blockStarts[head];
}

/**
 * Window `context.messages` so the estimated prompt fits the token budget.
 *
 * The message list is grouped into turn blocks, each starting at a `user`
 * message. The final block (newest user message + the current turn's tool
 * rounds) is kept UNCONDITIONALLY; older blocks are dropped oldest-first, whole
 * blocks only — the result is always a contiguous suffix of the history starting
 * at a user message. The head index and its stability contract are
 * `windowHeadIndex` above.
 *
 * Returns the same Context reference when nothing needs dropping (the
 * no-alloc idiom of stripIdentityEnvelope). Windowing disabled
 * (ZOE_BRAIN_CONTEXT_WINDOW=0) or no user message in the history → unchanged.
 */
export function windowContextToBudget(context: Context): Context {
  const head = windowHeadIndex(context);
  if (head === null || head === 0) return context;
  return { ...context, messages: context.messages.slice(head) };
}
