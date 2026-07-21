/**
 * The Phase-1 executor loop: reap -> claim -> spawn, once per tick.
 *
 * This is the piece the migration doc says is missing ("the claim -> spawn ->
 * report loop, not the agent roles"). It is deterministic code around Flue
 * workers: each claimed task spawns one `flue run phase-worker` process bound
 * to the task's work_dir (the worktree handoff — worktree creation stays with
 * Python's worktree_bootstrap, see FINDINGS.md unknown 1).
 *
 * Run standalone:  npm run executor            (loops until Ctrl-C)
 *                  npm run executor -- --ticks 5   (bounded, for tests)
 *
 * LAB ONLY — hand-started, never a unit, never CI.
 */
import { pathToFileURL } from 'node:url';
import type pg from 'pg';
import { loadConfig, type ExecutorConfig } from './config.ts';
import { bootstrapLabDb, makePool } from './labdb.ts';
import { claimNextTask } from './queue.ts';
import { spawnWorker } from './spawn.ts';
import { reapDeadWorkers } from './reaper.ts';

export interface ExecutorState {
  /** pids of workers this process spawned and still owns. */
  trackedPids: Set<number>;
}

/** One executor tick: reap zombies, then claim + spawn if the lane is free. */
export async function tick(
  pool: pg.Pool,
  cfg: ExecutorConfig,
  state: ExecutorState,
): Promise<{ reaped: number; claimed: string | null }> {
  const reaped = await reapDeadWorkers(pool, cfg, state.trackedPids);
  const task = await claimNextTask(pool, cfg.runtimeId,
    'single lane free; claimed highest-priority oldest queued task for this runtime');
  if (!task) return { reaped, claimed: null };
  await spawnWorker(pool, cfg, task, state.trackedPids);
  return { reaped, claimed: task.id };
}

async function main(): Promise<void> {
  const cfg = loadConfig();
  await bootstrapLabDb(cfg);
  const pool = makePool(cfg);
  const state: ExecutorState = { trackedPids: new Set() };
  const ticksArg = process.argv.indexOf('--ticks');
  const maxTicks = ticksArg >= 0 ? Number(process.argv[ticksArg + 1]) : Infinity;
  console.log(`[executor] runtime ${cfg.runtimeId} polling every ${cfg.pollMs}ms`);
  for (let i = 0; i < maxTicks; i++) {
    try {
      const { reaped, claimed } = await tick(pool, cfg, state);
      if (reaped || claimed) console.log(`[executor] tick: reaped=${reaped} claimed=${claimed ?? '-'}`);
    } catch (err) {
      // A transient DB/spawn error must not kill the loop — the stalled-
      // dispatch reaper heals any row the failed tick left behind.
      console.error('[executor] tick failed (will retry):', err);
    }
    await new Promise((r) => setTimeout(r, cfg.pollMs));
  }
  await pool.end();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error('[executor] fatal:', err);
    process.exitCode = 1;
  });
}
