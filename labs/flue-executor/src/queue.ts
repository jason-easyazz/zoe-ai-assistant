/**
 * The queue contract: atomic single-lane claim + reason-mandatory transitions.
 *
 * Two rules here are the whole point of Phase 1 (see
 * docs/architecture/multica-executor-migration.md §4):
 *
 * 1. CLAIM IS ATOMIC AND SINGLE-LANE. A per-runtime advisory lock serializes
 *    claimers (SKIP LOCKED alone is not enough: two concurrent claimers would
 *    each skip the other's locked row and BOTH dispatch — the lock closes that
 *    race), then one UPDATE flips exactly one `queued` row to `dispatched`,
 *    and only when the runtime has no dispatched/running row already
 *    (today's POLL_DISPATCH_LIMIT=1 behaviour).
 *
 * 2. EVERY TRANSITION CARRIES A REASON, WRITTEN THROUGH TO activity_log IN THE
 *    SAME TRANSACTION. Hermes's queue recorded blocker_reason 0 times across
 *    128 blocked tickets; that silence is the failure mode this executor
 *    exists to end. An empty reason throws — it is not droppable.
 *
 * LAB ONLY.
 */
import type pg from 'pg';
import { LAB_WORKSPACE_ID } from './config.ts';

// The workspace_id stamped on every activity_log row. In lab mode this is the
// scratch-DB constant (no FK). In LIVE mode `activity_log.workspace_id` has a
// FOREIGN KEY to workspace(id), so a fake id makes every claim transaction roll
// back — the live runner MUST call setActivityWorkspaceId() with the executor
// runtime's real workspace at startup before it claims anything.
let _activityWorkspaceId = LAB_WORKSPACE_ID;
export function setActivityWorkspaceId(id: string): void {
  if (!id) throw new Error('setActivityWorkspaceId: empty workspace id');
  _activityWorkspaceId = id;
}
export function activityWorkspaceId(): string {
  return _activityWorkspaceId;
}

export interface TaskRow {
  id: string;
  agent_id: string;
  issue_id: string | null;
  status: string;
  attempt: number;
  max_attempts: number;
  context: Record<string, unknown> | null;
  work_dir: string | null;
  /** Used to bound the not-yet-created-worktree deferral (see spawn.ts). */
  created_at?: string | Date | null;
}

const TASK_COLS =
  'id, agent_id, issue_id, status, attempt, max_attempts, context, work_dir, created_at';

/**
 * Claim the next ready task for this runtime, or null when the lane is busy or
 * the queue is empty. Exactly one row moves queued -> dispatched, atomically.
 */
export async function claimNextTask(
  pool: pg.Pool,
  runtimeId: string,
  reason: string,
): Promise<TaskRow | null> {
  requireReason(reason);
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    // Serialize claimers for this runtime; released at COMMIT/ROLLBACK.
    await client.query('SELECT pg_advisory_xact_lock(hashtext($1))', [runtimeId]);
    const res = await client.query(
      `UPDATE agent_task_queue t
         SET status = 'dispatched', dispatched_at = now()
       WHERE t.id = (
         SELECT id FROM agent_task_queue
          WHERE runtime_id = $1 AND status = 'queued'
            AND NOT EXISTS (
              SELECT 1 FROM agent_task_queue busy
               WHERE busy.runtime_id = $1 AND busy.status IN ('dispatched','running')
            )
          ORDER BY priority DESC, created_at
          LIMIT 1
          FOR UPDATE SKIP LOCKED
       )
       RETURNING ${TASK_COLS}`,
      [runtimeId],
    );
    const row = (res.rows[0] as TaskRow | undefined) ?? null;
    if (row) {
      await logActivity(client, runtimeId, row, 'task_claimed', reason, {
        from: 'queued',
        to: 'dispatched',
      });
    }
    await client.query('COMMIT');
    return row;
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

/**
 * Transition a task and write the reason through to activity_log, atomically.
 *
 * Compare-and-swap semantics: the UPDATE only fires when the row is still in
 * `task.status` (the expected from-state). Two reporters can race on the same
 * row — e.g. the in-process exit handler and the reaper both see `running` —
 * and without the guard the LAST transaction would win, able to erase a valid
 * `completed` result or resurrect a terminal row back to `queued`. With it,
 * exactly one reporter wins; the loser returns false and must not proceed
 * (e.g. failOrRequeue skips the requeue when its `failed` write lost).
 */
export async function reportTransition(
  pool: pg.Pool,
  runtimeId: string,
  task: TaskRow,
  to: 'running' | 'completed' | 'failed' | 'queued',
  reason: string,
  opts: {
    action?: string;
    result?: unknown;
    workerPid?: number;
    /** to='running' only: recorded on the row in the SAME transaction as the
     * status flip, so a reaper can never observe a running row without its
     * ownership metadata (lane/nonce/session). */
    contextMerge?: Record<string, unknown>;
    /** to='running' only: session id stored atomically with the transition. */
    sessionId?: string;
    /** to='queued' only: requeue bumps attempt and clears run state. */
    requeue?: boolean;
    /** to='queued' only: return the task WITHOUT burning an attempt. For
     * "not ready yet" deferrals (e.g. the worktree the adapter creates just
     * after the row commits) — a deferral is not a failed attempt, and
     * counting it would exhaust max_attempts on a task that never ran. */
    keepAttempt?: boolean;
  } = {},
): Promise<boolean> {
  requireReason(reason);
  const action =
    opts.action ??
    ({ running: 'task_started', completed: 'task_completed', failed: 'task_failed', queued: 'task_requeued' } as const)[
      to
    ];
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    let updated: number;
    if (to === 'running') {
      const merge: Record<string, unknown> = { ...(opts.contextMerge ?? {}) };
      if (opts.workerPid !== undefined) merge['worker_pid'] = opts.workerPid;
      const res = await client.query(
        `UPDATE agent_task_queue
            SET status='running', started_at=now(),
                session_id = coalesce($4, session_id),
                context = coalesce(context,'{}'::jsonb) || $2::jsonb
          WHERE id=$1 AND status=$3`,
        [task.id, JSON.stringify(merge), task.status, opts.sessionId ?? null],
      );
      updated = res.rowCount ?? 0;
    } else if (to === 'completed') {
      const res = await client.query(
        `UPDATE agent_task_queue
            SET status='completed', completed_at=now(), result=$2::jsonb, failure_reason=NULL
          WHERE id=$1 AND status=$3`,
        [task.id, JSON.stringify(opts.result ?? null), task.status],
      );
      updated = res.rowCount ?? 0;
    } else if (to === 'failed') {
      const res = await client.query(
        `UPDATE agent_task_queue
            SET status='failed', completed_at=now(), failure_reason=$2
          WHERE id=$1 AND status=$3`,
        [task.id, reason, task.status],
      );
      updated = res.rowCount ?? 0;
    } else {
      // requeue: same row back to queued with attempt+1, ALL run state cleared
      // — including omnigent ownership markers, so a requeued task starts
      // clean and cannot be mistaken for one that still owns an old session.
      const res = await client.query(
        `UPDATE agent_task_queue
            SET status='queued', attempt=attempt+${opts.keepAttempt ? 0 : 1},
                dispatched_at=NULL, started_at=NULL, completed_at=NULL,
                failure_reason=NULL, session_id=NULL,
                context = (coalesce(context,'{}'::jsonb)
                           - 'worker_pid' - 'nonce' - 'evidence_stuck_logged')
                          || CASE WHEN coalesce(context,'{}'::jsonb)->>'lane' = 'omnigent'
                                  THEN '{"lane":"heavy"}'::jsonb ELSE '{}'::jsonb END
          WHERE id=$1 AND status=$2`,
        [task.id, task.status],
      );
      updated = res.rowCount ?? 0;
    }
    if (updated === 0) {
      // Lost the race: another reporter already moved the row. Log nothing,
      // change nothing — the winner's reason is the record.
      await client.query('ROLLBACK');
      return false;
    }
    await logActivity(client, runtimeId, task, action, reason, {
      from: task.status,
      to,
    });
    await client.query('COMMIT');
    return true;
  } catch (err) {
    await client.query('ROLLBACK');
    throw err;
  } finally {
    client.release();
  }
}

function requireReason(reason: string): void {
  if (!reason || !reason.trim()) {
    throw new Error(
      'Transition without a reason is forbidden — this executor exists because ' +
        'Hermes recorded blocker_reason 0/128 times. Provide a human-readable reason.',
    );
  }
}

async function logActivity(
  client: pg.PoolClient,
  runtimeId: string,
  task: TaskRow,
  action: string,
  reason: string,
  extra: Record<string, unknown>,
): Promise<void> {
  await client.query(
    `INSERT INTO activity_log (workspace_id, issue_id, actor_type, actor_id, action, details)
     VALUES ($1, $2, 'agent', $3, $4, $5::jsonb)`,
    [
      _activityWorkspaceId,
      task.issue_id,
      runtimeId,
      action,
      JSON.stringify({ task_id: task.id, reason, attempt: task.attempt, ...extra }),
    ],
  );
}
