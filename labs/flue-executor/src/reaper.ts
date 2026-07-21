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
import type { TaskRow } from './queue.ts';

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
    const pid = Number((row.context ?? {})['worker_pid']);
    if (Number.isInteger(pid) && trackedPids.has(pid)) continue;
    if (Number.isInteger(pid) && pid > 0 && pidAlive(pid)) continue;
    await failOrRequeue(pool, cfg, row,
      Number.isInteger(pid) && pid > 0
        ? `reaped: recorded worker pid ${pid} is not alive (executor restart or worker crash left a zombie row holding the lane)`
        : 'reaped: running row has no recorded worker pid — cannot be alive');
    reaped += 1;
  }
  return reaped;
}
