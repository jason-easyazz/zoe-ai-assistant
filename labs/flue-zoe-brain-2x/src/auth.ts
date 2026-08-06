/**
 * The fail-closed bearer gate for the Flue Zoe-brain sidecar.
 *
 * WHY THIS IS ITS OWN MODULE NOW: in the beta this was `export const route` in
 * `src/agents/zoe.ts` — an agent-module convention Flue 2.x DELETED outright
 * ("The agent-module `export const route` and `export const attachments`
 * conventions are deleted. Per-agent middleware becomes ordinary Hono
 * middleware registered before the mount." — @flue/runtime migration guide,
 * "Routing"). It cannot live in the agent module any more for a second reason:
 * that module carries the `'use agent'` directive, and every exported
 * capitalized function in a marked module is registered as an agent.
 *
 * This is the security boundary the entire per-request identity trust model
 * rests on, so the port is proven by negative control, not by inspection — see
 * test/auth_gate.test.ts (no token, wrong token, wrong scheme, no configured
 * token, and the deliberate ZOE_BRAIN_OPEN escape all asserted).
 *
 * FAIL CLOSED: this gate fronts the live Gemma brain on :11434, so by default a
 * caller must present a matching `Authorization: Bearer <ZOE_BRAIN_TOKEN>`.
 * There are exactly two ways to reach the agent:
 *   - set ZOE_BRAIN_TOKEN and send the bearer token, or
 *   - set ZOE_BRAIN_OPEN=1 to explicitly opt into open access (local lab/smoke
 *     runs only — the server binds localhost by default).
 * With NEITHER set, every request is rejected. That is the important half: an
 * unconfigured sidecar refuses all traffic rather than serving it, so one
 * accidentally bound to a reachable interface can't let any LAN caller drive
 * completions or contend with the voice brain.
 *
 * PER-REQUEST IDENTITY: this gate only enforces auth. The trusted acting user_id
 * is NOT read here — it rides an envelope on the turn MESSAGE (set by the
 * zoe-data seam, services/zoe-data/zoe_flue_client.py), and the capped provider
 * binds it to the turn's AbortSignal before any tool runs; the tool reads it back
 * by that same signal (see src/request-identity.ts and
 * src/providers/capped-completions.ts). The id is only ever set from the
 * seam-forwarded envelope / env, NEVER from model input.
 *
 * TRUST BOUNDARY — honest about the two modes:
 *   - PRODUCTION (ZOE_BRAIN_TOKEN set, ZOE_BRAIN_OPEN unset): this gate rejects
 *     any caller without the bearer token, so the ONLY caller that can reach the
 *     agent is zoe-data's seam. The forwarded envelope user_id is trusted
 *     PRECISELY BECAUSE the token gate means zoe-data — which resolved it from
 *     auth — is the sole caller. THIS token gate is the security boundary.
 *   - ZOE_BRAIN_OPEN=1 (lab/dev only): the gate is bypassed, so the envelope
 *     user_id is CALLER-SUPPLIED and is NOT a trust boundary — any localhost
 *     caller can name any user_id. That is acceptable ONLY because open mode is
 *     localhost-bound smoke/lab use, never production, and writes still stay
 *     dry-run-gated behind ZOE_BRAIN_ALLOW_WRITES.
 *
 * LAB ONLY (production-reachable via ZOE_BRAIN_BACKEND=flue — prod quality).
 */
import type { MiddlewareHandler } from 'hono';

/**
 * Hono middleware enforcing the bearer gate. Mount it on the agent path space
 * BEFORE `createAgentRouter(...)` is mounted, so nothing reaches the runtime —
 * not admission, not payload validation — without passing here first.
 *
 * Env is read PER REQUEST, never captured at module load, so a token rotation
 * or an operator flipping ZOE_BRAIN_OPEN takes effect without a restart and a
 * test can exercise every mode in one process.
 */
export function requireBrainToken(): MiddlewareHandler {
  return async (c, next) => {
    if (process.env.ZOE_BRAIN_OPEN !== '1') {
      const token = process.env.ZOE_BRAIN_TOKEN;
      // No configured token → reject. Deliberately the same branch as a bad
      // token: an unconfigured sidecar must refuse everything, not open up.
      if (!token || c.req.header('authorization') !== `Bearer ${token}`) {
        return c.json({ error: 'unauthorized' }, 401);
      }
    }
    return next();
  };
}
