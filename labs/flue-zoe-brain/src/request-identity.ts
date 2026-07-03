/**
 * Per-request acting identity for the Flue Zoe-brain sidecar.
 *
 * WHY THIS EXISTS — Flue's `ToolContext` is only `{ input, signal }` (confirmed
 * in @flue/runtime docs api/agent-api + api/routing-api); there is NO built-in
 * per-request principal a tool can read. So the acting `user_id` has to be
 * threaded app-side. This module carries it in an `AsyncLocalStorage` so the
 * whole agent operation — every tool call inside a single turn — sees the ONE
 * user the request was for, instead of the process-wide `ZOE_BRAIN_USER_ID`.
 *
 * The value is set exactly once, in the exported `route` handler
 * (src/agents/zoe.ts), from the TRUSTED forwarded `user_id` in the request body.
 * That id is trustworthy for two independent reasons and must stay so:
 *   1. zoe-data's seam (services/zoe-data/zoe_flue_client.py) resolves it from
 *      authenticated session state, in trusted server code — it is NOT chosen by
 *      the model, and NOT a tool argument (tool args are never an auth boundary).
 *   2. The `route` handler fails closed on auth: an unauthorized caller can't
 *      reach the agent at all (ZOE_BRAIN_TOKEN / ZOE_BRAIN_OPEN), so only a
 *      trusted caller can ever set this id.
 *
 * AsyncLocalStorage propagates across every `await` within `run()`, and the
 * `?wait=result` agent path runs the whole agent + tool loop synchronously
 * inside the route's `next()` (flue handleAgentRequest → runDirectSyncMode is
 * awaited within the route middleware), so a value set here reaches every tool
 * call of that turn. Outside any `run()` (unit tests, non-HTTP paths) the store
 * is empty and callers fall back to the env — see `actingUserId()` in
 * src/tools/zoe-tools.ts.
 *
 * LAB ONLY.
 */
import { AsyncLocalStorage } from 'node:async_hooks';

interface RequestIdentity {
  /** The acting user for this request, forwarded (trusted) by the zoe-data seam. */
  userId: string;
}

const identityStore = new AsyncLocalStorage<RequestIdentity>();

/**
 * Run `fn` with `userId` bound as the acting identity for the current request.
 * Everything `fn` awaits — the agent operation and all its tool calls — reads
 * that id via `currentUserId()`. The id is stored trimmed; an empty/whitespace
 * id is stored as '' so downstream fail-closed logic treats it as "no user".
 */
export function runWithUserId<T>(userId: string, fn: () => T): T {
  return identityStore.run({ userId: (userId ?? '').trim() }, fn);
}

/**
 * The acting user bound for the current request, or '' when called outside any
 * `runWithUserId(...)` context (non-HTTP / test paths). Never throws.
 */
export function currentUserId(): string {
  return identityStore.getStore()?.userId ?? '';
}
