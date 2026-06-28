/**
 * The Zoe brain agent — a Flue-hosted Pi `Agent` on the local Gemma brain.
 *
 * This is the port of zoe-core's `soul.ts` extension to Flue's Agent model
 * (docs/architecture/zoe-flue-integration.md §5): the persona that `soul.ts`
 * injects as the per-turn system prompt becomes the Agent's `instructions`.
 * The persona text is a verbatim copy of services/zoe-core/SOUL.md so this lab
 * app stays self-contained (no cross-build runtime read of zoe-core).
 *
 * `model: 'zoe/local'` binds to the `zoe` provider registered in app.ts (the
 * live llama-server on :11434). Exporting `route` mounts the HTTP agent API so
 * `POST /agents/zoe/:id` works (see Flue routing-api).
 *
 * Tools/memory are deliberately OUT of this increment: small local models do
 * not reliably call tools, and per the plan §6 abilities/memory (Seam B) land
 * in a later phase. This increment produces a TEXT reply only — persona
 * conversation parity is the lab bar.
 *
 * LAB ONLY.
 */
import { type AgentRouteHandler, defineAgent } from '@flue/runtime';

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
].join('\n');

// Exporting `route` is what publishes the HTTP agent endpoints for this agent
// (POST/GET /agents/zoe/:id). A no-op middleware exposes it without auth — fine
// for the lab; production cutover would add operator middleware here.
export const route: AgentRouteHandler = async (_c, next) => next();

export default defineAgent(() => ({
  model: 'zoe/local',
  instructions: ZOE_SOUL,
}));
