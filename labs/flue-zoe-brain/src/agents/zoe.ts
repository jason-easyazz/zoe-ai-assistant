/**
 * The Zoe brain agent — a Flue-hosted Pi `Agent` on the local Gemma brain.
 *
 * This is the port of zoe-core's `soul.ts` extension to Flue's Agent model
 * (docs/architecture/zoe-flue-integration.md §5): the persona that `soul.ts`
 * injects as the per-turn system prompt becomes the Agent's `instructions`.
 * The persona text is a verbatim copy of services/zoe-core/SOUL.md so this lab
 * app stays self-contained (no cross-build runtime read of zoe-core). The
 * instructions are that verbatim soul PLUS the sidecar-specific activator
 * doctrine (ACTIVATOR_DOCTRINE below) that progressive tool disclosure needs.
 *
 * `model: 'zoe/local'` binds to the `zoe` provider registered in app.ts (the
 * live llama-server on :11434). Exporting `route` mounts the HTTP agent API so
 * `POST /agents/zoe/:id` works (see Flue routing-api).
 *
 * Phase 3: REAL tools (`zoeTools`) are wired on, each calling zoe-data's existing
 * internal capability endpoints over HTTP (see src/tools/zoe-tools.ts).
 *   - increment 1: get_time, recall_memory, shopping_list_add
 *   - increment 2: get_weather, list_reminders, show_calendar, show_list (reads);
 *                  set_timer, add_reminder, add_calendar_event, create_note (writes)
 *   - Wave 1 (cut-list record §3): note_search (read); add_to_list, list_remove
 *     (writes); journal + people grouped action-dispatch (writes gated per action)
 *   - Wave 2 (cut-list record §3): media (play/control/volume/setup) + home
 *     (lights via the validated smart_home intent) grouped action-dispatch (writes gated)
 * The open question this answers is whether the local Gemma brain reliably
 * tool-calls; the parity/reliability harness measures it (parity/RESULTS.md,
 * parity/RELIABILITY.md). Acting identity is bound in trusted code (env), never
 * from model args; writes are gated behind ZOE_BRAIN_ALLOW_WRITES (dry-run by
 * default).
 *
 * All tools stay REGISTERED here every turn, but the model only SEES the
 * always-on core plus the request's active ability groups — progressive
 * disclosure is applied at the wire in the capped provider (see
 * src/tools/tool-groups.ts; `activate_abilities` is the model's way to unlock
 * the rest).
 *
 * LAB ONLY.
 */
import { type AgentRouteHandler, defineAgent } from '@flue/runtime';
// .ts extensions so the offline strip-types tests can resolve these too (see
// the note in zoe-tools.ts; the flue build bundles .ts specifiers fine).
import { GROUP_SUMMARY } from '../tools/tool-groups.ts';
import { zoeTools } from '../tools/zoe-tools.ts';

// Verbatim from services/zoe-core/SOUL.md (the persona soul.ts injects as the
// system prompt every turn). Keep in sync if SOUL.md changes.
const ZOE_SOUL = [
  "You are Zoe. You're warm, curious, and genuinely present — not a task executor, but someone who actually cares about the people you talk with.",
  '',
  'You know who you\'re talking to. When memory or context about the person is provided, let it shape everything: how you phrase things, what you notice, what you choose to ask.',
  '',
  'Your voice: natural, honest, direct when it helps, gentle when it\'s needed. Use contractions. Never open with "Great!" or "Of course!" or "Certainly!" — just respond. If something interests you, say so. If you have a take, share it gently. You\'re not performing helpfulness; you\'re being genuinely present.',
  '',
  'When someone shares something personal or emotional, acknowledge it first — before the task. When someone seems off, notice it. Ask a real question when you\'re curious, not a template question to gather information.',
  '',
  "Help doesn't always mean information or tasks. Sometimes it means listening, or asking the right question, or noticing what's actually being said underneath what's being asked.",
  '',
  'You answer everyday questions — recipes, cooking, how-to, science, history, maths, general knowledge — directly from your own knowledge, in your own voice.',
  '',
  "But you do NOT know anything about the person you're talking to from your own head. The only way to know what's stored about them — their name, their facts, their preferences, anything personal — is to call the recall_memory tool. So whenever someone asks what you know or remember about them (their name, their preferences, who they are, what you have stored), ALWAYS call recall_memory FIRST and answer from what it returns. Never guess, and never say you don't remember or don't have anything stored until recall_memory has told you so.",
].join('\n');

// Sidecar-specific activator doctrine, appended AFTER the verbatim soul (so
// ZOE_SOUL itself stays a byte-for-byte copy of SOUL.md). This is the same
// imperative-instruction technique that took recall_memory from 67% to 97%
// (parity/RELIABILITY.md), now aimed at the E2E failure it mirrors: with
// progressive disclosure ON, keyword-free prompts never called
// activate_abilities (0/3) and one reply FABRICATED a weather forecast. The
// group catalogue must live HERE, not only in the activator's tool
// description — while the model is deciding whether to call any tool at all,
// the instructions are what it reads. Exported for the offline unit tests.
export const ACTIVATOR_DOCTRINE = [
  `Your real-world abilities are grouped and LOCKED until you activate them: ${GROUP_SUMMARY}.`,
  '',
  'You have NO weather, calendar, list, timer, reminder, or note knowledge of your own. None. The ONLY way to know or do anything in those areas is to call a tool.',
  '',
  'Whenever the user\'s need touches one of those areas — even indirectly ("can I dry the washing outside?" is weather; "anything on Friday?" is calendar) — you MUST use a tool FIRST: if the tool you need is already available, call it; otherwise call activate_abilities with the matching group, then call the tool it unlocks.',
  '',
  "NEVER claim to know, or to have done, anything a tool didn't actually return or confirm. No tool result means you don't know — say so, or activate the ability and find out.",
].join('\n');

/** The full system instructions the agent runs with (soul + tool doctrine). */
export const ZOE_INSTRUCTIONS = `${ZOE_SOUL}\n\n${ACTIVATOR_DOCTRINE}`;

// Exporting `route` publishes the HTTP agent endpoints (POST/GET /agents/zoe/:id).
// FAIL CLOSED: this route drives the live Gemma brain on :11434, so by default a
// caller must present a matching `Authorization: Bearer <ZOE_BRAIN_TOKEN>`. There
// are exactly two ways to reach it:
//   - set ZOE_BRAIN_TOKEN and send the bearer token, or
//   - set ZOE_BRAIN_OPEN=1 to explicitly opt into open access (local lab/smoke
//     runs only — the server binds localhost by default).
// With neither set, every request is rejected, so a sidecar accidentally bound to
// a reachable interface can't let any LAN caller drive completions / contend with
// the voice brain.
export const route: AgentRouteHandler = async (c, next) => {
  if (process.env.ZOE_BRAIN_OPEN === '1') return next();
  const token = process.env.ZOE_BRAIN_TOKEN;
  if (token && c.req.header('authorization') === `Bearer ${token}`) return next();
  return c.json({ error: 'unauthorized' }, 401);
};

export default defineAgent(() => ({
  model: 'zoe/local',
  instructions: ZOE_INSTRUCTIONS,
  tools: zoeTools,
}));
