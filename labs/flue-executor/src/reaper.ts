/**
 * Dead-worker reaper — the #685 behaviour.
 *
 * A `running` row whose recorded worker pid is no longer alive is a zombie
 * holding the single lane (typically: the executor crashed or restarted after
 * spawning, so no in-process exit handler will ever fire). Each tick, before
 * claiming, the reaper fails such rows WITH a reason and requeues them while
 * attempts remain — the lane is never silently stranded.
 *
 * LAB ONLY.
 */
import type pg from 'pg';
import { LAB_WORKSPACE_ID, type ExecutorConfig } from './config.ts';
import { failOrRequeue } from './spawn.ts';
import { doneToken, sessionHasToken, OmnigentApiError } from './omnigent.ts';
import { reportTransition, type TaskRow } from './queue.ts';

function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * Reap running rows whose worker died. `trackedPids` are workers this process
 * spawned and still tracks — their exit handlers own the report, so the reaper
 * leaves them alone even mid-exit.
 */
export async function reapDeadWorkers(
  pool: pg.Pool,
  cfg: ExecutorConfig,
  trackedPids: Set<number>,
): Promise<number> {
  const res = await pool.query(
    `SELECT id, agent_id, issue_id, status, attempt, max_attempts, context, work_dir
       FROM agent_task_queue
      WHERE runtime_id = $1 AND status = 'running'`,
    [cfg.runtimeId],
  );
  let reaped = 0;
  for (const row of res.rows as TaskRow[]) {
    const ctx = (row.context ?? {}) as { lane?: string; worker_pid?: unknown };
    if (ctx.lane === 'omnigent') {
      reaped += await reapOmnigentRow(pool, cfg, row);
      continue;
    }
    const pid = Number(ctx.worker_pid);
    if (Number.isInteger(pid) && trackedPids.has(pid)) continue;
    if (Number.isInteger(pid) && pid > 0 && pidAlive(pid)) continue;
    await failOrRequeue(pool, cfg, row,
      Number.isInteger(pid) && pid > 0
        ? `reaped: recorded worker pid ${pid} is not alive (executor restart or worker crash left a zombie row holding the lane)`
        : 'reaped: running row has no recorded worker pid — cannot be alive');
    reaped += 1;
  }
  return reaped + (await reapStalledDispatched(pool, cfg));
}

/**
 * Omnigent-lane rows have no local pid — their worker is a remote session.
 * The in-process poller normally owns the report; the reaper only matters when
 * the executor restarted and the poller is gone. Recovery is by evidence, not
 * liveness: if the session already holds the completion token, complete the
 * row (the work happened — do not throw it away); if the row is older than the
 * omnigent timeout + grace, fail it with a reason. Either way the CAS
 * transitions make a race with a live poller harmless.
 */
async function reapOmnigentRow(
  pool: pg.Pool,
  cfg: ExecutorConfig,
  row: TaskRow & { session_id?: string | null; started_at?: Date | null },
): Promise<number> {
  const ctx = (row.context ?? {}) as { nonce?: string; evidence_stuck_logged?: boolean };
  const full = await pool.query(
    `SELECT session_id, started_at, dispatched_at FROM agent_task_queue WHERE id=$1`,
    [row.id]);
  const sessionId = full.rows[0]?.session_id as string | null;
  const anchor = (full.rows[0]?.started_at ?? full.rows[0]?.dispatched_at) as Date | null;
  // Evidence states:
  //   token present            -> complete (recover the work);
  //   VERIFIED absent          -> eligible for the age fail. A 404 counts as
  //                               verified: the API answered authoritatively
  //                               that the session no longer exists;
  //   unobservable (API down)  -> NEVER destructive. The session may have
  //                               finished; failing/requeuing would throw
  //                               completed work away. Log a loud stuck
  //                               entry once and leave the row recoverable —
  //                               the evidence path resolves it when the API
  //                               returns.
  let evidenceObserved = false;
  if (sessionId && ctx.nonce) {
    try {
      const hasToken = await sessionHasToken(cfg, sessionId, doneToken(ctx.nonce));
      evidenceObserved = true;
      if (hasToken) {
        const won = await reportTransition(pool, cfg.runtimeId, row, 'completed',
          `reaper found the completion token in omnigent session ${sessionId} (executor poller was lost)`,
          { result: { ok: true, summary: `omnigent session ${sessionId} completed (recovered by reaper)`, sessionId } });
        return won ? 1 : 0;
      }
    } catch (err) {
      if (err instanceof OmnigentApiError && err.status === 404) {
        evidenceObserved = true; // authoritative: the session is gone
      }
      // otherwise: unobservable this tick
    }
  } else {
    // No session/nonce recorded: there is no evidence to consult — age rules.
    evidenceObserved = true;
  }
  const ageMs = anchor ? Date.now() - anchor.getTime() : Infinity;
  if (ageMs <= cfg.omnigentTimeoutMs + 30_000) return 0;
  if (evidenceObserved) {
    await failOrRequeue(pool, cfg, row,
      `reaped: omnigent-lane row exceeded session timeout + grace without a completion token (session ${sessionId ?? 'unknown'})`);
    return 1;
  }
  if (!ctx.evidence_stuck_logged) {
    // Loud, reasoned, on the board — but non-destructive: the lane stays
    // held by this row until evidence becomes observable again or the
    // operator intervenes via multica-web.
    //
    // The dedupe marker and the activity entry MUST commit together. Written
    // separately, a crash between them would set the marker while losing the
    // entry, and every later pass would skip the block — leaving a held row
    // with NO board-visible reason, precisely the silence this executor
    // exists to end. One transaction: either both land or neither does (and
    // the next pass retries).
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      await client.query(
        `UPDATE agent_task_queue
            SET context = coalesce(context,'{}'::jsonb) || '{"evidence_stuck_logged":true}'::jsonb
          WHERE id=$1`,
        [row.id]);
      await client.query(
        `INSERT INTO activity_log (workspace_id, issue_id, actor_type, actor_id, action, details)
         VALUES ($1, $2, 'agent', $3, 'task_stuck_evidence_unobservable', $4::jsonb)`,
        [LAB_WORKSPACE_ID, row.issue_id, cfg.runtimeId, JSON.stringify({
          task_id: row.id,
          reason: `omnigent session ${sessionId} exceeded its timeout but the omnigent API is unreachable — ` +
            'completion evidence is unobservable, so the executor is holding the row rather than ' +
            'destroying possibly-completed work; will resolve when the API returns',
        })]);
      await client.query('COMMIT');
    } catch (err) {
      await client.query('ROLLBACK');
      throw err;
    } finally {
      client.release();
    }
  }
  return 0;
}

/**
 * A `dispatched` row that never reached `running` can also wedge the lane:
 * the claim treats dispatched as busy, but the pid is only recorded by the
 * running transition — so if that transition is lost (transient DB error after
 * spawn, executor crash mid-spawn, or a worker so fast its exit report raced
 * the still-uncommitted running update), no exit handler and no pid-based reap
 * will ever free it. Any dispatched row older than worker-timeout + grace is
 * provably dead: a real worker would have been SIGKILLed by the spawn timeout
 * before then. Fail it with a reason and requeue while attempts remain.
 */
const DISPATCH_STALL_GRACE_MS = 30_000;

async function reapStalledDispatched(pool: pg.Pool, cfg: ExecutorConfig): Promise<number> {
  const res = await pool.query(
    `SELECT id, agent_id, issue_id, status, attempt, max_attempts, context, work_dir
       FROM agent_task_queue
      WHERE runtime_id = $1 AND status = 'dispatched'
        AND dispatched_at < now() - ($2::int * interval '1 millisecond')`,
    [cfg.runtimeId, cfg.workerTimeoutMs + DISPATCH_STALL_GRACE_MS],
  );
  let reaped = 0;
  for (const row of res.rows as TaskRow[]) {
    // A dispatched row that already carries omnigent ownership (session
    // staged/kicked before the running flip committed) must be recovered by
    // EVIDENCE, not blind-requeued — its remote session may be running or
    // even already done.
    if (((row.context ?? {}) as { lane?: string }).lane === 'omnigent') {
      reaped += await reapOmnigentRow(pool, cfg, row);
      continue;
    }
    await failOrRequeue(pool, cfg, row,
      'reaped: dispatched row never reached running within worker-timeout + grace ' +
        '(running transition lost or executor died mid-spawn) — freeing the lane');
    reaped += 1;
  }
  return reaped;
}
