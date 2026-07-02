/**
 * Build-time placeholder agent. NEVER INVOKED.
 *
 * Flue's `flue build` discovers and bundles agents under src/agents/, so this
 * file must exist and export a valid agent for the build to succeed. But this
 * channel does NOT answer via a Flue LLM agent: src/app.ts asks Zoe's REAL brain
 * (zoe-data's /api/chat, see src/brain.ts) and relays the reply. Nothing ever
 * `dispatch()`es this agent.
 *
 * Because it is never run, no model provider is registered for it and the model
 * string below is deliberately fake/unused — in particular it does NOT point at
 * the local voice Gemma on :11434 (labs/AGENTS.md Forbidden). If a later
 * increment wants a real Flue-agent path, wire its own non-voice provider then.
 *
 * LAB ONLY.
 */
import { defineAgent } from '@flue/runtime';
import { chatIdFromKey, postMessage } from '../telegram.ts';

const PERSONA = [
  'Placeholder persona — this agent is never dispatched. Real replies come from',
  "Zoe's brain via /api/chat (see src/brain.ts).",
].join('\n');

export default defineAgent(({ id }) => ({
  // Fake/unused: no provider is registered for it; this agent is never invoked.
  model: 'placeholder/none',
  instructions: PERSONA,
  tools: [postMessage(chatIdFromKey(id))],
}));
