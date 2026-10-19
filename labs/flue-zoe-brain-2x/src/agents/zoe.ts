'use agent';
/**
 * The Zoe brain agent — a Flue-hosted Pi `Agent` on the local Gemma brain.
 *
 * FLUE 2.x SHAPE. `defineAgent` is gone entirely: "the agent IS the function
 * now" — an exported capitalized function composing behaviour with Agent Hooks,
 * in a module carrying the `'use agent'` directive above. The mapping is
 * `model:` → `useModel()`, `tools:` → `useTool()` per tool, `instructions:` →
 * the function's RETURN VALUE, and the function MUST be synchronous.
 *
 * THE DIRECTIVE IS LOAD-BEARING AND FAILS SILENTLY. Registration comes from the
 * build-time `'use agent'` scan (the `@flue/vite` plugin), NOT from mounting:
 * `createAgentRouter(Zoe)` exposes routes but registers nothing, and "a
 * converted module without the directive is silently not an agent" — no error,
 * no warning, just an agent that does not exist. That is exactly the class of
 * failure a unit test must catch, so test/agent_registration.test.ts asserts the
 * agent actually resolves an identity and answers on its mounted route.
 *
 * This is the port of zoe-core's `soul.ts` extension to Flue's Agent model
 * (docs/architecture/zoe-flue-integration.md §5): the persona that `soul.ts`
 * injects as the per-turn system prompt becomes the Agent's `instructions`.
 * The soul (src/soul.ts) is services/zoe-core/SOUL.md's paragraphs PLUS one
 * flue-only RECALL paragraph, held in this app so it stays self-contained (no
 * cross-build runtime read of zoe-core); test/soul_source.test.ts pins that
 * relationship byte for byte. The instructions are that soul PLUS the
 * sidecar-specific activator doctrine (src/doctrines.ts) that progressive tool
 * disclosure needs,
 * PLUS a voice-delivery doctrine ported from prod's spoken-mode soul
 * (_ZOE_SOUL_VOICE in services/zoe-data/zoe_agent.py) so this voice brain speaks
 * with the same tight, no-markdown, spoken-length discipline as prod.
 *
 * `useModel('zoe/local')` binds to the `zoe` provider registered in app.ts (the
 * live llama-server on :11434). app.ts mounts `createAgentRouter(Zoe)` at
 * `/agents/zoe`, keeping the deployed `POST /agents/zoe/:id` URL shape that
 * zoe-data's seam addresses.
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
 *   - Wave 3 (cut-list record §3): remember_fact → the new memory_store intent
 *     (MemoryService.ingest) — the explicit model-callable memory write (write gated)
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
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import { useModel, useTool } from '@flue/runtime';
// .ts extensions so the offline strip-types tests can resolve these too (see
// the note in zoe-tools.ts; the Vite build resolves .ts specifiers fine).
import { renderHarnessPrompt } from '../harness.ts';
import { ZOE_MODEL_SPECIFIER } from '../providers/capped-completions.ts';
import { optionalZoeTools, zoeTools } from '../tools/zoe-tools.ts';

// The doctrine constants and the instruction ORDER live in src/doctrines.ts (moved there so the
// provider and the harness overlay loader, src/harness.ts, can compose the instructions without
// importing this agent module). Re-exported here unchanged: the tests and parity tools import them
// from this module, and `ZOE_INSTRUCTIONS` is still the compiled-in, flag-off system prompt.
export {
  ACTIVATOR_DOCTRINE,
  EMOTIONAL_CAPTURE_DOCTRINE,
  EMOTIONAL_RECALL_DOCTRINE,
  IDENTITY_DOCTRINE,
  IN_SESSION_CONTEXT_DOCTRINE,
  PERSONAL_RECALL_DOCTRINE,
  PROMPT_CONFIDENTIALITY_DOCTRINE,
  RECALL_PRECEDENCE_DOCTRINE,
  VOICE_DELIVERY_DOCTRINE,
  ZOE_INSTRUCTIONS,
} from '../doctrines.ts';

// The fail-closed bearer gate that used to live here as `export const route` is
// now ordinary Hono middleware in src/auth.ts (`requireBrainToken`), mounted by
// app.ts ahead of the agent router. That convention was deleted in 2.x, and this
// module can no longer host it anyway — see the `'use agent'` note at the top.

/**
 * The Zoe brain agent.
 *
 * SYNCHRONOUS BY CONTRACT and re-rendered before EVERY model turn, so the body
 * must stay cheap and free of side effects: the instructions are a string the
 * harness (src/harness.ts) composed ONCE at load (`ZOE_INSTRUCTIONS` itself while
 * ZOE_BRAIN_HARNESS_DIR is unset), and the hook calls below are pure declarations.
 * Because it re-renders every turn, a harness reload (SIGHUP) takes effect on the
 * next model turn without a restart.
 *
 * `compaction: false` pins OFF Flue's threshold compaction. This is a deliberate,
 * measured choice, not an oversight — src/context-window.ts documents why this
 * deployment windows the prompt at the wire instead (compaction cannot rescue an
 * already-oversized session, it adds 1-2 unpredictable Gemma calls on a
 * latency-gated voice path, and its summary quality would rest on the 4B brain).
 * The beta had no equivalent knob to set; on 2.x threshold compaction would
 * engage on its own once the model declares a `contextWindow`, so leaving this
 * unset would silently introduce the very behaviour that was rejected. Overflow
 * recovery still remains as a backstop.
 *
 * `agentName` pins the durable identity to `'zoe'` so the storage slug, the mount
 * path (`/agents/zoe`), and the provider prefix all read the same. Without it the
 * identity would be the function name `Zoe`; both are valid, but pinning it means
 * a future rename of the function is not silently a storage migration.
 */
export function Zoe(): string {
  useModel(ZOE_MODEL_SPECIFIER, { compaction: false });
  // `tools:` → one useTool() per tool. Resources may legally be declared
  // conditionally on 2.x, but this brain declares ALL of them every render on
  // purpose: progressive disclosure happens at the WIRE (see tool-groups.ts), so
  // every tool must stay registered and executable while the model only SEES the
  // active subset. Moving disclosure into conditional useTool() calls would break
  // execution of a tool the model called from an earlier round's schema set.
  // B10.1: `optionalZoeTools()` is the flag-gated web_search (ZOE_WEB_SEARCH_TOOL=1); [] by default.
  for (const tool of [...zoeTools, ...optionalZoeTools()]) useTool(tool);
  return renderHarnessPrompt();
}

Zoe.agentName = 'zoe';

/**
 * A2 — bounded durability (@flue/runtime 2.1.1 `DurabilityConfig`; default 10
 * attempts / 1 h). Only process interruptions consume attempts (transient model
 * errors retry inside one), and startup recovery runs ahead of new work — so the
 * default could replay a stale voice turn before the live one for an hour. 120 s is
 * enforced preemptively by the node coordinator, under zoe-data's 180 s seam
 * timeout. Live store, 4,650 settled: max 21.6 s, no second attempt ever needed.
 */
Zoe.durability = { maxAttempts: 2, timeoutMs: 120_000 };
