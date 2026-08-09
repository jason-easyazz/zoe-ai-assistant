'use agent';
/**
 * Build-time placeholder agent. NEVER DISPATCHED.
 *
 * WHY IT STILL EXISTS AFTER THE PORT. On 1.x this file was MANDATORY: `flue
 * build` discovered agents by directory, so a Flue app with no `src/agents/*`
 * did not build. Flue 2.x deletes directory discovery — registration is the
 * build-time `'use agent'` scan and mounting is explicit — so an app with zero
 * agents now builds and runs fine (verified: see README.md "What the port
 * proved"). The placeholder is therefore OPTIONAL on 2.x and is kept for two
 * deliberate reasons, not inertia:
 *   1. It preserves the 1.x HTTP surface. The beta's `app.route('/', flue())`
 *      auto-router served this agent at `/agents/zoe/:id`; `createAgentRouter`
 *      is mounted at the same path, so the port changes no URL.
 *   2. It is the ONLY exercise of the 2.x agent/tool/store wiring on this
 *      surface. Deleting it would make `src/db.ts` and the ported
 *      `post_telegram_message` tool dead code and leave the pathfinder unable to
 *      prove the `'use agent'` scan works here at all.
 *
 * THE DIRECTIVE ABOVE IS LOAD-BEARING AND FAILS SILENTLY. `createAgentRouter`
 * exposes routes whether or not this is an agent; "a converted module without
 * the directive is silently not an agent" — no error, no warning. That is why
 * test/agent_registration.test.ts asserts the directive is the first statement
 * AND that a POST to the mounted route is actually admitted.
 *
 * This channel does NOT answer via a Flue LLM agent: src/app.ts asks Zoe's REAL
 * brain (zoe-data's /api/chat, see src/brain.ts) and relays the reply. Because
 * it is never dispatched, no model provider is registered for it and the model
 * specifier below is deliberately fake/unused — in particular it does NOT point
 * at the local voice Gemma on :11434 (labs/AGENTS.md Forbidden). If a later
 * increment wants a real Flue-agent path, wire its own non-voice provider then.
 *
 * 2.x SHAPE: `defineAgent` is gone entirely — "the agent IS the function now".
 * `model:` → `useModel()`, `tools:` → one `useTool()` per tool, `instructions:`
 * → the RETURN VALUE, and the function MUST be synchronous. The beta's
 * initializer `ctx.id` is now the `{ id }` prop on the agent function, which is
 * how the per-chat reply tool still gets its chat id.
 *
 * PRODUCTION since the 2026-08-09 cutover (auto-deployed).
 */
import { useModel, useTool } from '@flue/runtime';
import { chatIdFromKey, postMessage } from '../telegram.ts';

const PERSONA = [
  'Placeholder persona — this agent is never dispatched. Real replies come from',
  "Zoe's brain via /api/chat (see src/brain.ts).",
].join('\n');

export function ZoeTelegram({ id }: { id: string }): string {
  // Fake/unused: no provider is registered for it; this agent is never invoked.
  useModel('placeholder/none');
  useTool(postMessage(chatIdFromKey(id)));
  return PERSONA;
}

// Pin the durable identity to `zoe` so the storage slug and the mount path
// (`/agents/zoe`) read the same, and a rename of the function is not silently a
// storage-identity change. On 1.x the identity came from the FILENAME; on 2.x it
// is the function name unless pinned here — an easy silent drift at port time.
ZoeTelegram.agentName = 'zoe';
