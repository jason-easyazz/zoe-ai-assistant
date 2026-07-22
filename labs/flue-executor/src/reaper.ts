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
import type { ExecutorConfig } from './config.ts';
import { failOrRequeue } from './spawn.ts';
import { doneToken, sessionHasToken } from './omnigent.ts';
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
  const ctx = (row.context ?? {}) as { nonce?: string };
  const full = await pool.query(
    `SELECT session_id, started_at FROM agent_task_queue WHERE id=$1`, [row.id]);
  const sessionId = full.rows[0]?.session_id as string | null;
  const startedAt = full.rows[0]?.started_at as Date | null;
  // Evidence states: token present -> complete; VERIFIED absent -> eligible
  // for the normal age fail; unobservable (API unreachable) -> the row may
  // belong to a session that already finished, so failing on the normal
  // timeout would throw completed work away. Only a much larger hard cap
  // (3x timeout) may fail an unobservable row, and its reason says so.
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
    } catch {
      // API unreachable — evidence unobservable this tick.
    }
  } else {
    // No session/nonce recorded: there is no evidence to consult — age rules.
    evidenceObserved = true;
  }
  const ageMs = startedAt ? Date.now() - startedAt.getTime() : Infinity;
  if (evidenceObserved && ageMs > cfg.omnigentTimeoutMs + 30_000) {
    await failOrRequeue(pool, cfg, row,
      `reaped: omnigent-lane row exceeded session timeout + grace without a completion token (session ${sessionId ?? 'unknown'})`);
    return 1;
  }
  if (!evidenceObserved && ageMs > 3 * cfg.omnigentTimeoutMs) {
    await failOrRequeue(pool, cfg, row,
      `reaped: omnigent-lane row exceeded 3x session timeout and the omnigent API stayed unreachable — completion evidence was unobservable (session ${sessionId})`);
    return 1;
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
    await failOrRequeue(pool, cfg, row,
      'reaped: dispatched row never reached running within worker-timeout + grace ' +
        '(running transition lost or executor died mid-spawn) — freeing the lane');
    reaped += 1;
  }
  return reaped;
}
