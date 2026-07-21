/**
 * Worker spawn + exit handling.
 *
 * A worker is one `flue run phase-worker` child process — a real Flue workflow
 * invocation (the CLI boots the app, validates input, runs the workflow, prints
 * the result and exits). The executor records the child's pid on the queue row
 * (so the reaper can detect a dead worker after an executor restart — the #685
 * behaviour), waits for exit, and reports the terminal state WITH a reason.
 *
 * The worker communicates its result via a result.json file in the task's
 * work_dir, not by parsing CLI stdout — robust to event/log noise on stdout.
 *
 * LAB ONLY.
 */
import { spawn } from 'node:child_process';
import { openSync, closeSync, readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import type pg from 'pg';
import type { ExecutorConfig } from './config.ts';
import { reportTransition, type TaskRow } from './queue.ts';

const LAB_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

export interface WorkerHandle {
  taskId: string;
  pid: number;
}

export interface WorkerResult {
  ok: boolean;
  summary: string;
}

/**
 * Spawn the Flue phase worker for a claimed (dispatched) task and report
 * dispatched->running. On exit, report the terminal state. Returns the handle,
 * or null if the spawn itself failed (already reported as failed).
 */
export async function spawnWorker(
  pool: pg.Pool,
  cfg: ExecutorConfig,
  task: TaskRow,
  /** Live-worker registry owned by the executor. The pid is ADDED here the
   * moment the child exists and REMOVED in the exit handler BEFORE the exit
   * report — so a report that fails on a transient DB error leaves an
   * untracked dead pid on a `running` row, which the reaper then heals on the
   * next tick instead of the tracked pid stalling the lane forever. Tracking
   * both sides inside this function closes the instant-exit race (a child that
   * dies before the caller could have registered it). */
  trackedPids: Set<number> = new Set(),
): Promise<WorkerHandle | null> {
  const ctx = (task.context ?? {}) as { phase?: string; mode?: string };
  const phase = ctx.phase ?? 'implement';
  const workDir = task.work_dir;
  if (!workDir) {
    await reportTransition(pool, cfg.runtimeId, task, 'failed',
      'task has no work_dir (worktree handoff missing) — cannot spawn a worker');
    return null;
  }
  const resultPath = join(workDir, 'result.json');
  const input = JSON.stringify({
    taskId: task.id,
    phase,
    workDir,
    mode: ctx.mode ?? 'succeed',
    resultPath,
  });

  const outFd = openSync(join(workDir, 'worker.out.log'), 'a');
  const errFd = openSync(join(workDir, 'worker.err.log'), 'a');
  let child;
  try {
    child = spawn(
      join(LAB_ROOT, 'node_modules', '.bin', 'flue'),
      ['run', 'phase-worker', '--target', 'node', '--input', input],
      { cwd: LAB_ROOT, stdio: ['ignore', outFd, errFd] },
    );
  } catch (err) {
    closeSync(outFd);
    closeSync(errFd);
    await reportTransition(pool, cfg.runtimeId, task, 'failed',
      `worker spawn failed before start: ${err}`);
    return null;
  }
  const pid = child.pid;
  if (pid === undefined) {
    closeSync(outFd);
    closeSync(errFd);
    await reportTransition(pool, cfg.runtimeId, task, 'failed',
      'worker spawn returned no pid');
    return null;
  }

  trackedPids.add(pid);

  await reportTransition(pool, cfg.runtimeId, task, 'running',
    `worker pid ${pid} spawned for phase "${phase}" via \`flue run phase-worker\` in ${workDir}`,
    { workerPid: pid });

  const timeout = setTimeout(() => {
    child.kill('SIGKILL');
  }, cfg.workerTimeoutMs);

  child.on('exit', (code, signal) => {
    clearTimeout(timeout);
    closeSync(outFd);
    closeSync(errFd);
    trackedPids.delete(pid);
    void reportWorkerExit(pool, cfg, { ...task, status: 'running' }, resultPath, code, signal)
      .catch((err) => console.error(`[executor] failed to report exit of task ${task.id}:`, err));
  });

  return { taskId: task.id, pid };
}

async function reportWorkerExit(
  pool: pg.Pool,
  cfg: ExecutorConfig,
  task: TaskRow,
  resultPath: string,
  code: number | null,
  signal: NodeJS.Signals | null,
): Promise<void> {
  if (code === 0) {
    let result: WorkerResult;
    try {
      result = JSON.parse(readFileSync(resultPath, 'utf8')) as WorkerResult;
    } catch (err) {
      await failOrRequeue(pool, cfg, task,
        `worker exited 0 but left no readable result.json (${err}) — treating as failure`);
      return;
    }
    if (result.ok) {
      await reportTransition(pool, cfg.runtimeId, task, 'completed',
        `worker finished: ${result.summary}`, { result });
    } else {
      await failOrRequeue(pool, cfg, task, `worker reported failure: ${result.summary}`);
    }
    return;
  }
  await failOrRequeue(pool, cfg, task,
    `worker died: exit code=${code} signal=${signal ?? 'none'} (see worker.err.log in work_dir)`);
}

/** Fail the task; requeue on the same row when attempts remain. */
export async function failOrRequeue(
  pool: pg.Pool,
  cfg: ExecutorConfig,
  task: TaskRow,
  reason: string,
): Promise<void> {
  const won = await reportTransition(pool, cfg.runtimeId, task, 'failed', reason);
  if (!won) {
    // Another reporter (exit handler vs reaper) already moved this row to a
    // terminal state — do NOT requeue over their result.
    return;
  }
  if (task.attempt < task.max_attempts) {
    await reportTransition(pool, cfg.runtimeId, { ...task, status: 'failed' }, 'queued',
      `requeued for attempt ${task.attempt + 1}/${task.max_attempts} after: ${reason}`,
      { requeue: true });
  }
}
