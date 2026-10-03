/**
 * Abort-on-cancel + first-chunk/stall deadlines (A1/A5,
 * docs/research/flue-and-agent-runtimes-2026-10-03.md §4). OPT-IN PER REQUEST:
 * nothing acts unless the turn POST carries `x-zoe-abort-on-cancel: 1` (zoe-data's
 * ZOE_FLUE_ABORT_ON_CANCEL); without it the stream is byte-identical to before.
 * Sidecar kill switch: `ZOE_FLUE_ABORT_GUARD=0|false|no|off` ignores the header.
 *
 * WHY A GUARD, NOT A BARE `POST /:id/abort`: Flue 2.1.1 aborts per INSTANCE —
 * `requestSessionAbort` stamps EVERY unsettled submission of the session. After a
 * barge-in the next turn for that session follows within milliseconds, and an
 * abort landing after its admission would kill the turn the user is waiting for.
 * So an abort names its submission and is refused unless that is still the
 * instance's latest admission with none in flight; admissions arriving while an
 * abort is being recorded wait for it (bounded). Check, call and gate registration
 * run in one synchronous step, so nothing interleaves.
 */

import type { MiddlewareHandler } from 'hono';

export const ABORT_OPT_IN_HEADER = 'x-zoe-abort-on-cancel';
export const SUBMISSION_HEADER = 'x-flue-submission-id';
export const ABORT_SUBMISSION_HEADER = 'x-zoe-abort-submission';
export const ABORT_REASON_HEADER = 'x-zoe-abort-reason';
/** Hono context key: the streaming middleware hands the admitted id outward. */
export const ADMITTED_KEY = 'zoeAdmittedSubmission';

/** No model output this long after a model call starts (prefill + slot queue), or
 *  this long mid-call. Derivation: docs/knowledge/voice-pipeline.md (barge-in). */
export const DEFAULT_FIRST_CHUNK_MS = 30_000;
export const DEFAULT_STALL_MS = 10_000;
const ABORT_CALL_TIMEOUT_MS = 2_000;
const ADMISSION_GATE_MAX_MS = 2_000;
const MAX_TRACKED_INSTANCES = 2_048;

const OFF = ['0', 'false', 'no', 'off'];

/** True when this request opted in AND the sidecar kill switch is not thrown. */
export function abortGuardRequested(headerValue: string | null | undefined): boolean {
  if ((headerValue ?? '').trim() !== '1') return false;
  return !OFF.includes((process.env.ZOE_FLUE_ABORT_GUARD ?? '').trim().toLowerCase());
}

/** A positive ms value from env, `fallback` when unset/invalid, 0 = disabled. */
export function deadlineMsFromEnv(name: 'ZOE_FLUE_FIRST_CHUNK_MS' | 'ZOE_FLUE_STALL_MS', fallback: number): number {
  const raw = (process.env[name] ?? '').trim();
  if (raw === '') return fallback;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : fallback;
}

export interface AbortOutcome {
  /** `requested` (Flue recorded the intent) | `skipped:<why>` | `error`. */
  outcome: string;
  latencyMs: number;
}

export type AbortInstanceFn = (instanceId: string) => Promise<unknown>;

export class TurnGuard {
  private readonly latest = new Map<string, string>();
  private readonly admitting = new Map<string, number>();
  private readonly aborting = new Map<string, Promise<unknown>>();

  private readonly abortInstance: AbortInstanceFn;

  // No TS parameter property: `node --experimental-strip-types` (npm test) rejects it.
  constructor(abortInstance: AbortInstanceFn) {
    this.abortInstance = abortInstance;
  }

  /** Call SYNCHRONOUSLY at middleware entry for a turn POST; await the result. */
  beforeAdmission(instanceId: string): Promise<void> {
    this.admitting.set(instanceId, (this.admitting.get(instanceId) ?? 0) + 1);
    const inFlight = this.aborting.get(instanceId);
    if (!inFlight) return Promise.resolve();
    return Promise.race([
      inFlight.then(() => {}, () => {}),
      new Promise<void>((resolve) => setTimeout(resolve, ADMISSION_GATE_MAX_MS).unref?.()),
    ]);
  }

  /** Pair of beforeAdmission; `submissionId` null when nothing was admitted. */
  afterAdmission(instanceId: string, submissionId: string | null): void {
    const n = (this.admitting.get(instanceId) ?? 1) - 1;
    if (n > 0) this.admitting.set(instanceId, n);
    else this.admitting.delete(instanceId);
    if (!submissionId) return;
    this.latest.delete(instanceId); // re-insert = most recent, for the size cap
    this.latest.set(instanceId, submissionId);
    if (this.latest.size > MAX_TRACKED_INSTANCES) {
      const oldest = this.latest.keys().next().value;
      if (oldest !== undefined) this.latest.delete(oldest);
    }
  }

  /** Abort `submissionId` iff it is still the instance's live turn. Never throws. */
  async abort(instanceId: string, submissionId: string | null): Promise<AbortOutcome> {
    const t0 = performance.now();
    const done = (outcome: string): AbortOutcome => ({ outcome, latencyMs: Math.round(performance.now() - t0) });
    if (!submissionId) return done('skipped:no_submission_id');
    if (this.admitting.get(instanceId)) return done('skipped:admission_in_flight');
    const latest = this.latest.get(instanceId);
    if (latest === undefined) return done('skipped:unknown_instance');
    if (latest !== submissionId) return done('skipped:superseded');
    let call: Promise<unknown>;
    try {
      call = this.abortInstance(instanceId); // the stamp happens synchronously in here
    } catch {
      return done('error');
    }
    this.aborting.set(instanceId, call);
    try {
      await Promise.race([
        call,
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error('abort timeout')), ABORT_CALL_TIMEOUT_MS).unref?.()),
      ]);
      return done('requested');
    } catch {
      return done('error');
    } finally {
      if (this.aborting.get(instanceId) === call) this.aborting.delete(instanceId);
    }
  }
}

/**
 * Mounted AHEAD of the streaming middleware: admission bookkeeping for every
 * turn POST (streaming or not), and the guarded abort route — `POST
 * /agents/:name/:id/abort` WITH `x-zoe-abort-submission`. Without that header the
 * request is Flue's own instance-wide abort, passed through untouched.
 */
export function turnGuardMiddleware(guard: TurnGuard, log: (line: string) => void = console.log): MiddlewareHandler {
  return async (c, next) => {
    if (c.req.method !== 'POST') return next();
    const path = new URL(c.req.url).pathname;
    const abort = path.match(/^\/agents\/[^/]+\/([^/]+)\/abort$/);
    const named = c.req.header(ABORT_SUBMISSION_HEADER);
    if (abort && named) {
      const session = decodeURIComponent(abort[1]);
      const result = await guard.abort(session, named);
      const reason = (c.req.header(ABORT_REASON_HEADER) ?? '').replace(/[^a-z_]/g, '').slice(0, 24);
      log(flueAbortLine(session, named, `client_${reason || 'unknown'}`, -1, -1, result));
      return c.json({ outcome: result.outcome, latency_ms: result.latencyMs });
    }
    const turn = path.match(/^\/agents\/[^/]+\/([^/]+)$/);
    if (!turn) return next();
    const id = decodeURIComponent(turn[1]);
    const gate = guard.beforeAdmission(id); // counts the admission synchronously
    let admitted: string | null = null;
    try {
      await gate;
      await next();
      admitted = (c.get(ADMITTED_KEY as never) as string | null | undefined) ?? (await admittedSubmissionId(c.res));
    } finally {
      guard.afterAdmission(id, admitted);
    }
  };
}

/** The submission id from a 202 admission (`{"submissionId": …}`), else null. */
export async function admittedSubmissionId(res: Response): Promise<string | null> {
  if (res.status !== 202) return null;
  try {
    const body = (await res.clone().json()) as { submissionId?: unknown };
    return typeof body?.submissionId === 'string' && body.submissionId ? body.submissionId : null;
  } catch {
    return null;
  }
}

/** One greppable line per guarded abort (A3 reads `emitted_chars`; -1 = unknown). */
export function flueAbortLine(session: string, submission: string | null, reason: string,
  emittedChars: number, deltas: number, result: AbortOutcome): string {
  return `FLUE_ABORT side=sidecar session=${session} submission=${submission ?? '-'} reason=${reason}`
    + ` emitted_chars=${emittedChars} deltas=${deltas} outcome=${result.outcome} latency_ms=${result.latencyMs}`;
}
