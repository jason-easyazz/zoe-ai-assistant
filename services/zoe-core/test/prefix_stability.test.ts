/**
 * KV-prefix stability tests for the zoe-core (Pi) brain lane.
 *
 * llama.cpp reuses cached KV only for an EXACT common prefix of the tokenized
 * request — `--cache-reuse` is dropped from llama-server.service because KV
 * shifting is unsupported for Gemma's shared-KV + SWA attention. So every byte
 * of the request that varies turn-to-turn re-prefills everything after it, and
 * the two things that sit at the FRONT of every request are (a) the system
 * prompt and (b) the rendered tool block.
 *
 * Both used to move on every turn:
 *
 *   • `extensions/memory.ts` composed a freshly-fetched, message-keyed memory
 *     packet onto the system prompt, so the reusable prefix ended at the last
 *     byte of SOUL.md. In production (`ZOE_CORE_MEMORY_SEAM=1`) the packet now
 *     rides in the user message, folded in by zoe-data's `_compose_message`
 *     AHEAD of the user's own words — the seam, not Pi's post-user-message
 *     `custom` slot, which measurably cost tool selection.
 *   • `extensions/abilities.ts` recomputed progressive disclosure from the LAST
 *     MESSAGE ONLY, so the tool set oscillated (no ability declares tier "core",
 *     so an off-topic turn disclosed zero tools).
 *
 * These tests pin both fixes, and each one carries a NEGATIVE CONTROL that
 * reimplements the pre-fix behaviour verbatim and asserts it FAILS the same
 * assertion — so the suite cannot pass vacuously.
 *
 * Run:  node --test services/zoe-core/test/prefix_stability.test.ts
 *       (or `npm test` from services/zoe-core/)
 *
 * Needs Node >= 22.18 — the extensions are TypeScript and are executed via
 * Node's built-in type stripping, with no build step and no node_modules (every
 * import in the modules under test is either a node: builtin or `import type`,
 * which type stripping erases).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import soulExtension from "../extensions/soul.ts";
import memoryExtension, {
  MEMORY_SEAM_ENV,
  MEMORY_USAGE_DIRECTIVE,
  memoryBlock,
} from "../extensions/memory.ts";
import {
  createDisclosureHandler,
  createDisclosureState,
  nextActiveTools,
} from "../extensions/abilities.ts";

const _here = dirname(fileURLToPath(import.meta.url));
const SOUL_PATH = join(_here, "..", "SOUL.md");
const USER = "family-admin";

// ── A tiny stand-in for Pi's ExtensionAPI ────────────────────────────────────

interface Handler {
  (event: unknown): Promise<{ systemPrompt?: string; message?: unknown } | undefined | void>;
}

function stubPi() {
  const handlers: Handler[] = [];
  const activeToolCalls: string[][] = [];
  return {
    handlers,
    activeToolCalls,
    on(_event: string, handler: Handler) {
      handlers.push(handler);
    },
    registerTool() {},
    setActiveTools(names: string[]) {
      activeToolCalls.push([...names]);
    },
  };
}

/**
 * Run the real extension chain for one turn and return what Pi would apply.
 *
 * Mirrors `emitBeforeAgentStart` in pi-coding-agent's extension runner: handlers
 * run in manifest order, each sees the system prompt the previous one returned,
 * and returned `message`s are collected (Pi appends them AFTER the user message).
 */
async function runTurn(chain: Handler[], prompt: string, baseSystemPrompt = "") {
  let systemPrompt = baseSystemPrompt;
  const messages: unknown[] = [];
  for (const handler of chain) {
    const result = await handler({ type: "before_agent_start", prompt, systemPrompt });
    if (result && typeof result === "object") {
      if (result.message !== undefined) messages.push(result.message);
      if (result.systemPrompt !== undefined) systemPrompt = result.systemPrompt;
    }
  }
  return { systemPrompt, messages };
}

/** Install a stub /api/memories/for-prompt whose packet varies with the message. */
function stubForPromptFetch(packetFor: (message: string) => string) {
  const original = globalThis.fetch;
  const seen: string[] = [];
  globalThis.fetch = (async (input: URL | RequestInfo) => {
    const url = new URL(String(input));
    const message = url.searchParams.get("message") ?? "";
    seen.push(message);
    return {
      ok: true,
      json: async () => ({ packet: packetFor(message) }),
    } as Response;
  }) as typeof fetch;
  return {
    seen,
    restore() {
      globalThis.fetch = original;
    },
  };
}

/** Two different, realistic turns of one session. */
const TURN_A = "what's on my calendar tomorrow";
const TURN_B = "add oat milk to the shopping list";

function packetFor(message: string): string {
  // Deliberately message-keyed, exactly like the real endpoint's recall.
  return `## What I know about you\n- recall for ${message} [mem:${message.length}]`;
}

// ── (1) the system prompt is byte-identical across turns ─────────────────────

test("seam mode: system prompt is byte-identical across two different turns", async () => {
  process.env.ZOE_CORE_USER_ID = USER;
  process.env.ZOE_CORE_SOUL_PATH = SOUL_PATH;
  process.env[MEMORY_SEAM_ENV] = "1";
  const stub = stubForPromptFetch(packetFor);
  try {
    const pi = stubPi();
    soulExtension(pi as never);
    memoryExtension(pi as never);

    const first = await runTurn(pi.handlers, TURN_A);
    const second = await runTurn(pi.handlers, TURN_B);

    assert.equal(
      second.systemPrompt,
      first.systemPrompt,
      "the system prompt moved between turns — the KV prefix ends at the first differing byte",
    );
    // Not vacuous: the packets these turns WOULD have produced genuinely differ.
    assert.notEqual(packetFor(TURN_A), packetFor(TURN_B));
    // ONE injection site: in seam mode the extension does not fetch at all.
    assert.deepEqual(stub.seen, [], "the extension fetched a packet the seam already injects");
    assert.deepEqual(first.messages, [], "nothing may be appended after the user message");
    // Nothing of memory's is left on the system prompt — the seam owns the whole
    // block, directive included (an unconditional directive cost tool selection;
    // see the header). So the system prompt is exactly the soul.
    assert.ok(!first.systemPrompt.includes(MEMORY_USAGE_DIRECTIVE));
  } finally {
    stub.restore();
    delete process.env[MEMORY_SEAM_ENV];
  }
});

test("standalone mode: the extension still injects the packet itself", async () => {
  // The `pi` CLI, bench/ and test_brick3_memory.py have no seam to fold the
  // packet in, so the original self-service behaviour must survive unchanged.
  process.env.ZOE_CORE_USER_ID = USER;
  process.env.ZOE_CORE_SOUL_PATH = SOUL_PATH;
  delete process.env[MEMORY_SEAM_ENV];
  const stub = stubForPromptFetch(packetFor);
  try {
    const pi = stubPi();
    soulExtension(pi as never);
    memoryExtension(pi as never);

    const turn = await runTurn(pi.handlers, TURN_A);
    assert.deepEqual(stub.seen, [TURN_A]);
    assert.ok(turn.systemPrompt.includes(packetFor(TURN_A)), "standalone lost its memory packet");
    assert.ok(turn.systemPrompt.includes(MEMORY_USAGE_DIRECTIVE));
  } finally {
    stub.restore();
  }
});

test("NEGATIVE CONTROL: the pre-fix composition moves the system prompt every turn", async () => {
  // The old extensions/memory.ts body, verbatim: directive + packet appended to
  // the system prompt. If this ever passes the assertion above, the test is not
  // measuring what it claims to measure.
  const legacySystemPrompt = (base: string, packet: string) => {
    const block = memoryBlock(packet);
    if (!block) return base;
    return base ? `${base}\n\n${block}` : block;
  };
  const base = "SOUL";
  assert.notEqual(
    legacySystemPrompt(base, packetFor(TURN_B)),
    legacySystemPrompt(base, packetFor(TURN_A)),
    "the control is no longer controlling",
  );
});

test("the usage directive never appears without a packet", async () => {
  // MEASURED, not stylistic: an unconditional directive scored 6/15 on
  // test_tool_action_dispatches against a 14/15 baseline — told to lead with what
  // it remembers when it remembers nothing, the brain stops calling its tools.
  assert.equal(memoryBlock(""), "");
  assert.equal(memoryBlock("   "), "");
  const block = memoryBlock(packetFor(TURN_A));
  assert.ok(block.startsWith(MEMORY_USAGE_DIRECTIVE));
  assert.ok(block.endsWith(packetFor(TURN_A)));
});

test("unknown user: no packet fetched, and the system prompt is still stable", async () => {
  delete process.env.ZOE_CORE_USER_ID;
  delete process.env[MEMORY_SEAM_ENV];
  process.env.ZOE_CORE_SOUL_PATH = SOUL_PATH;
  const stub = stubForPromptFetch(packetFor);
  try {
    const pi = stubPi();
    soulExtension(pi as never);
    memoryExtension(pi as never);

    const first = await runTurn(pi.handlers, TURN_A);
    const second = await runTurn(pi.handlers, TURN_B);

    // The headline fail-closed guarantee, unchanged by this refactor.
    assert.deepEqual(stub.seen, [], "memory was fetched despite an unknown user");
    assert.deepEqual(first.messages, []);
    assert.equal(second.systemPrompt, first.systemPrompt);
    assert.ok(
      !first.systemPrompt.includes(MEMORY_USAGE_DIRECTIVE),
      "unknown user gets no memory scaffolding at all",
    );
  } finally {
    stub.restore();
    process.env.ZOE_CORE_USER_ID = USER;
  }
});

test("NEGATIVE CONTROL: standalone mode DOES move the system prompt", async () => {
  // Proves the seam-mode assertion above is measuring the flag, not a tautology:
  // the very same extension, with the seam flag off, fails it.
  process.env.ZOE_CORE_USER_ID = USER;
  process.env.ZOE_CORE_SOUL_PATH = SOUL_PATH;
  delete process.env[MEMORY_SEAM_ENV];
  const stub = stubForPromptFetch(packetFor);
  try {
    const pi = stubPi();
    soulExtension(pi as never);
    memoryExtension(pi as never);
    const first = await runTurn(pi.handlers, TURN_A);
    const second = await runTurn(pi.handlers, TURN_B);
    assert.notEqual(
      second.systemPrompt,
      first.systemPrompt,
      "the control is no longer controlling",
    );
  } finally {
    stub.restore();
  }
});

// ── (2) monotone tool disclosure ─────────────────────────────────────────────

interface FakeEntry {
  id: string;
  name: string;
  domain: string;
  tier: "core" | "on-demand";
  examples: string[];
  triggers?: RegExp[];
}

const ABILITIES: FakeEntry[] = [
  { id: "calendar", name: "calendar", domain: "calendar", tier: "on-demand", examples: [], triggers: [/calendar|meeting/] },
  { id: "lists", name: "lists", domain: "lists", tier: "on-demand", examples: [], triggers: [/list|shopping/] },
  { id: "media", name: "media", domain: "media", tier: "on-demand", examples: [], triggers: [/play|music/] },
  { id: "notes", name: "notes", domain: "notes", tier: "on-demand", examples: [], triggers: [/note/] },
];

/** The pre-fix selector, copied verbatim from extensions/abilities.ts. */
function legacyActiveTools(abilities: FakeEntry[], msg: string): string[] {
  const relevant = (entry: FakeEntry) =>
    entry.tier === "core" || (entry.triggers ?? []).some((re) => re.test(msg));
  return abilities.filter(relevant).map((a) => a.name);
}

// A realistic session: two calendar turns, an off-topic turn, then a list turn.
const SESSION = [
  "what's on my calendar tomorrow",
  "move that meeting to friday",
  "thanks, that's great",
  "add oat milk to the shopping list",
  "and bread",
];

function walk(selector: (msg: string) => string[]): string[][] {
  return SESSION.map((m) => selector(m.toLowerCase()));
}

test("disclosure never shrinks inside the retained window", async () => {
  const state = createDisclosureState();
  const sets = walk((msg) => nextActiveTools(ABILITIES as never, msg, state, SESSION.length));
  for (let i = 1; i < sets.length; i++) {
    for (const name of sets[i - 1]) {
      assert.ok(
        sets[i].includes(name),
        `turn ${i + 1} dropped "${name}" while it was still inside the retained window`,
      );
    }
  }
  // Non-vacuous: the session really did disclose more than one domain.
  assert.ok(sets.at(-1)!.length > sets[0].length, "fixture never grew the tool set");
});

test("an off-topic turn keeps the tool block byte-identical", async () => {
  const state = createDisclosureState();
  const rendered = walk((msg) =>
    nextActiveTools(ABILITIES as never, msg, state, SESSION.length),
  ).map((names) => names.join(","));
  // Turn 3 ("thanks, that's great") matches nothing at all.
  assert.equal(rendered[2], rendered[1], "an off-topic turn moved the tool block");
});

test("NEGATIVE CONTROL: last-message-only disclosure oscillates", async () => {
  const legacy = walk((msg) => legacyActiveTools(ABILITIES, msg)).map((n) => n.join(","));
  // The off-topic turn wipes the tool block entirely...
  assert.equal(legacy[2], "", "the control is no longer controlling");
  assert.notEqual(legacy[2], legacy[1]);
  // ...and calendar comes back and goes away again across the session.
  assert.ok(legacy[0].includes("calendar"));
  assert.ok(!legacy[3].includes("calendar"));
});

test("disclosure is bounded — a stale domain leaves after the window", async () => {
  const state = createDisclosureState();
  const windowTurns = 3;
  assert.deepEqual(nextActiveTools(ABILITIES as never, "play some music", state, windowTurns), ["media"]);
  for (let i = 0; i < windowTurns; i++) {
    nextActiveTools(ABILITIES as never, "thanks", state, windowTurns);
  }
  assert.deepEqual(
    nextActiveTools(ABILITIES as never, "thanks", state, windowTurns),
    [],
    "a domain untouched for longer than the window must be reclaimed",
  );
});

test("SAFETY FLOOR: setActiveTools is called on every turn, even an unchanged one", async () => {
  const pi = stubPi();
  const handler = createDisclosureHandler(pi as never, ABILITIES as never);
  await handler({ prompt: "what's on my calendar" });
  await handler({ prompt: "thanks" });
  await handler({ prompt: "thanks again" });
  assert.equal(
    pi.activeToolCalls.length,
    3,
    "skipping setActiveTools on an unchanged turn would let Pi's coding builtins back in",
  );
  // The active set is only ever Zoe's own abilities — never a Pi builtin.
  const known = new Set(ABILITIES.map((a) => a.name));
  for (const call of pi.activeToolCalls) {
    for (const name of call) assert.ok(known.has(name), `unexpected tool disclosed: ${name}`);
  }
});
