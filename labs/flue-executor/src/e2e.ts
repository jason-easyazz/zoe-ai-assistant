/**
 * Synthetic end-to-end proof of the Phase-1 executor contract.
 *
 * Scenarios (each asserts hard; any failure exits 1):
 *   1. reason enforcement — a transition with an empty reason THROWS (negative
 *      control for the anti-silence contract).
 *   2. atomic claim — two concurrent claimers, one queued task each way:
 *      exactly ONE row becomes dispatched (advisory lock + SKIP LOCKED).
 *   3. happy path — synthetic ticket claimed, real `flue run phase-worker`
 *      spawned against a temp work_dir, proof file written BY THE WORKER,
 *      completed with reasons on every transition in activity_log.
 *   4. reap — a seeded `running` row with a dead pid is failed WITH a reason
 *      and requeued (the #685 zombie-lane behaviour), then runs to completion.
 *   5. single lane + kill — a hanging worker holds the lane (second task stays
 *      queued), killing it reports failure with a reason and frees the lane,
 *      and the queued task then completes.
 *
 * Run on the box:  npm run e2e     (needs Postgres up; creates/uses the
 *                                   multica_executor_lab scratch database)
 *
 * LAB ONLY.
 */
import { mkdtempSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import pg from 'pg';
import { loadConfig, LAB_AGENT_ID, type ExecutorConfig } from './config.ts';
import { bootstrapLabDb, makePool } from './labdb.ts';
import { claimNextTask, reportTransition, type TaskRow } from './queue.ts';
import { tick, type ExecutorState } from './executor.ts';

let failures = 0;
function assert(cond: boolean, label: string): void {
  if (cond) {
    console.log(`  PASS  ${label}`);
  } else {
    failures += 1;
    console.error(`  FAIL  ${label}`);
  }
}

async function seedTask(
  pool: pg.Pool,
  cfg: ExecutorConfig,
  opts: {
    mode: string;
    status?: string;
    priority?: number;
    maxAttempts?: number;
    workerPid?: number;
    /** status='dispatched' only: backdate dispatched_at by this many ms. */
    dispatchedAgoMs?: number;
  },
): Promise<{ id: string; workDir: string }> {
  const workDir = mkdtempSync(join(tmpdir(), 'flue-executor-e2e-'));
  const context: Record<string, unknown> = { phase: 'implement', mode: opts.mode };
  if (opts.workerPid !== undefined) context['worker_pid'] = opts.workerPid;
  const res = await pool.query(
    `INSERT INTO agent_task_queue
       (agent_id, issue_id, status, priority, runtime_id, work_dir, context, max_attempts,
        started_at, dispatched_at, trigger_summary)
     VALUES ($1, gen_random_uuid(), $2, $3, $4, $5, $6::jsonb, $7,
             CASE WHEN $2 = 'running' THEN now() ELSE NULL END,
             CASE WHEN $2 IN ('dispatched','running')
                  THEN now() - ($8::int * interval '1 millisecond') ELSE NULL END,
             'synthetic e2e ticket')
     RETURNING id`,
    [
      LAB_AGENT_ID,
      opts.status ?? 'queued',
      opts.priority ?? 0,
      cfg.runtimeId,
      workDir,
      JSON.stringify(context),
      opts.maxAttempts ?? 2,
      opts.dispatchedAgoMs ?? 0,
    ],
  );
  return { id: res.rows[0].id as string, workDir };
}

async function taskRow(pool: pg.Pool, id: string): Promise<Record<string, unknown>> {
  const res = await pool.query('SELECT * FROM agent_task_queue WHERE id=$1', [id]);
  return res.rows[0] as Record<string, unknown>;
}

async function activityActions(pool: pg.Pool, taskId: string): Promise<string[]> {
  const res = await pool.query(
    `SELECT action FROM activity_log WHERE details->>'task_id' = $1 ORDER BY created_at, id`,
    [taskId],
  );
  return res.rows.map((r: { action: string }) => r.action);
}

async function allReasonsNonEmpty(pool: pg.Pool, taskId: string): Promise<boolean> {
  const res = await pool.query(
    `SELECT count(*)::int AS n FROM activity_log
      WHERE details->>'task_id' = $1
        AND (details->>'reason' IS NULL OR btrim(details->>'reason') = '')`,
    [taskId],
  );
  return (res.rows[0].n as number) === 0;
}

async function untilStatus(
  pool: pg.Pool,
  cfg: ExecutorConfig,
  state: ExecutorState,
  taskId: string,
  wanted: string[],
  timeoutMs: number,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await tick(pool, cfg, state);
    const row = await taskRow(pool, taskId);
    if (wanted.includes(row.status as string)) return row.status as string;
    await new Promise((r) => setTimeout(r, 1500));
  }
  return `timeout(last=${(await taskRow(pool, taskId)).status})`;
}

async function main(): Promise<void> {
  const cfg = loadConfig();
  console.log('== bootstrap lab DB ==');
  await bootstrapLabDb(cfg);
  const pool = makePool(cfg);
  await pool.query('TRUNCATE agent_task_queue, activity_log');
  const state: ExecutorState = { trackedPids: new Set() };

  console.log('== 1. reason enforcement (negative control) ==');
  const t0 = await seedTask(pool, cfg, { mode: 'succeed' });
  const fake: TaskRow = {
    id: t0.id, agent_id: LAB_AGENT_ID, issue_id: null, status: 'queued',
    attempt: 1, max_attempts: 2, context: null, work_dir: t0.workDir,
  };
  let threw = false;
  try {
    await reportTransition(pool, cfg.runtimeId, fake, 'failed', '   ');
  } catch {
    threw = true;
  }
  assert(threw, 'transition with blank reason throws');
  let claimThrew = false;
  try {
    await claimNextTask(pool, cfg.runtimeId, '');
  } catch {
    claimThrew = true;
  }
  assert(claimThrew, 'claim with blank reason throws');
  await pool.query('TRUNCATE agent_task_queue, activity_log');

  console.log('== 2. atomic single-lane claim under concurrency ==');
  await seedTask(pool, cfg, { mode: 'succeed' });
  await seedTask(pool, cfg, { mode: 'succeed' });
  const [a, b] = await Promise.all([
    claimNextTask(pool, cfg.runtimeId, 'concurrent claimer A'),
    claimNextTask(pool, cfg.runtimeId, 'concurrent claimer B'),
  ]);
  const winners = [a, b].filter(Boolean);
  const dispatched = await pool.query(
    `SELECT count(*)::int AS n FROM agent_task_queue WHERE status='dispatched'`,
  );
  assert(winners.length === 1 && dispatched.rows[0].n === 1,
    `exactly one concurrent claimer wins (got ${winners.length} winners, ${dispatched.rows[0].n} dispatched)`);
  await pool.query('TRUNCATE agent_task_queue, activity_log');

  console.log('== 3. happy path: claim -> spawn flue worker -> report ==');
  const t1 = await seedTask(pool, cfg, { mode: 'succeed' });
  const s1 = await untilStatus(pool, cfg, state, t1.id, ['completed', 'failed'], 120_000);
  assert(s1 === 'completed', `synthetic ticket completed (got: ${s1})`);
  assert(existsSync(join(t1.workDir, 'PROOF-implement.txt')),
    'Flue worker wrote the proof file into the task work_dir');
  const acts1 = await activityActions(pool, t1.id);
  assert(
    JSON.stringify(acts1) === JSON.stringify(['task_claimed', 'task_started', 'task_completed']),
    `full transition chain logged with reasons (got: ${acts1.join(' -> ')})`,
  );
  assert(await allReasonsNonEmpty(pool, t1.id), 'every activity_log row carries a non-empty reason');
  const row1 = await taskRow(pool, t1.id);
  assert((row1.result as { ok?: boolean } | null)?.ok === true, 'result jsonb recorded on the queue row');
  // CAS guard: a stale reporter (e.g. reaper racing the exit handler) must
  // LOSE against the already-terminal row, not overwrite the result.
  const stale: TaskRow = {
    id: t1.id, agent_id: LAB_AGENT_ID, issue_id: null, status: 'running',
    attempt: 1, max_attempts: 2, context: null, work_dir: t1.workDir,
  };
  const won = await reportTransition(pool, cfg.runtimeId, stale, 'failed',
    'stale reporter racing a terminal row (must lose)');
  assert(won === false && (await taskRow(pool, t1.id)).status === 'completed',
    'stale transition loses the CAS race; completed result is not overwritten');

  console.log('== 4. reap: zombie running row with dead pid (the #685 behaviour) ==');
  // A pid that is certainly dead: spawn a no-op and wait for it.
  const { spawnSync } = await import('node:child_process');
  const dead = spawnSync('true');
  const deadPid = dead.pid ?? 999999;
  const t2 = await seedTask(pool, cfg, { mode: 'succeed', status: 'running', workerPid: deadPid });
  const s2 = await untilStatus(pool, cfg, state, t2.id, ['completed', 'failed'], 120_000);
  const acts2 = await activityActions(pool, t2.id);
  assert(acts2[0] === 'task_failed' && acts2[1] === 'task_requeued',
    `zombie was reaped with reason then requeued (got: ${acts2.slice(0, 2).join(' -> ')})`);
  assert(s2 === 'completed', `reaped task re-ran to completion on attempt 2 (got: ${s2})`);
  assert((await taskRow(pool, t2.id)).attempt === 2, 'attempt was bumped by the requeue');
  assert(await allReasonsNonEmpty(pool, t2.id), 'reap + requeue reasons are non-empty');
  await pool.query('TRUNCATE agent_task_queue, activity_log');

  console.log('== 4b. reap: stalled dispatched row that never reached running ==');
  const t2b = await seedTask(pool, cfg, {
    mode: 'succeed', status: 'dispatched',
    dispatchedAgoMs: cfg.workerTimeoutMs + 60_000,
  });
  const s2b = await untilStatus(pool, cfg, state, t2b.id, ['completed', 'failed'], 120_000);
  const acts2b = await activityActions(pool, t2b.id);
  assert(acts2b[0] === 'task_failed' && acts2b[1] === 'task_requeued',
    `stalled dispatched row was reaped with reason then requeued (got: ${acts2b.slice(0, 2).join(' -> ')})`);
  assert(s2b === 'completed', `stalled-dispatch task re-ran to completion (got: ${s2b})`);
  await pool.query('TRUNCATE agent_task_queue, activity_log');

  console.log('== 5. single lane + kill of a hung worker ==');
  const t3 = await seedTask(pool, cfg, { mode: 'hang', priority: 10, maxAttempts: 1 });
  const t4 = await seedTask(pool, cfg, { mode: 'succeed' });
  const s3run = await untilStatus(pool, cfg, state, t3.id, ['running'], 90_000);
  assert(s3run === 'running', `hang worker is running (got: ${s3run})`);
  await tick(pool, cfg, state);
  const t4row = await taskRow(pool, t4.id);
  assert(t4row.status === 'queued', `second task waits while the lane is busy (got: ${t4row.status})`);
  const hungPid = Number(((await taskRow(pool, t3.id)).context as Record<string, unknown>)['worker_pid']);
  assert(Number.isInteger(hungPid) && hungPid > 0, 'hung worker pid recorded on the queue row');
  process.kill(hungPid, 'SIGKILL');
  const s3 = await untilStatus(pool, cfg, state, t3.id, ['failed'], 60_000);
  assert(s3 === 'failed', `killed worker reported failed with reason, no requeue at max_attempts=1 (got: ${s3})`);
  const reason3 = await pool.query(
    `SELECT failure_reason FROM agent_task_queue WHERE id=$1`, [t3.id]);
  assert(/worker died|reaped/.test(reason3.rows[0].failure_reason ?? ''),
    `failure_reason names the death (got: ${reason3.rows[0].failure_reason})`);
  const s4 = await untilStatus(pool, cfg, state, t4.id, ['completed', 'failed'], 120_000);
  assert(s4 === 'completed', `lane freed; queued task then completed (got: ${s4})`);

  await pool.end();
  console.log(failures === 0 ? '\nE2E: ALL PASS' : `\nE2E: ${failures} FAILURE(S)`);
  process.exitCode = failures === 0 ? 0 : 1;
}

main().catch((err) => {
  console.error('E2E fatal:', err);
  process.exitCode = 1;
});
