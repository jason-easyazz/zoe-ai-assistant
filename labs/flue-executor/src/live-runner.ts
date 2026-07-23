/**
 * Live runner — the supervised process that consumes Multica's REAL
 * `agent_task_queue` (Phase 2 of the executor migration).
 *
 * This is the piece that was missing: under `ZOE_KANBAN_BACKEND=executor`,
 * `kanban_adapter` WRITES phase tasks into `agent_task_queue`; this process
 * CLAIMS them one at a time and spawns workers. It is the same claim → spawn →
 * report → reap loop proven in the lab (`executor.ts`), pointed at the live DB.
 *
 * Three safety properties, all on by default:
 *   1. KILL SWITCH — while `~/.zoe/multica_dispatch_paused` exists the runner
 *      idles and claims nothing. It can be armed as a service long before the
 *      board is unpaused; removing the file is the single go-live action.
 *   2. DRY DISPATCH — `ZOE_EXECUTOR_DISPATCH=dry` (the default) polls and logs
 *      what it WOULD claim, mutating nothing, so the wiring is provable against
 *      the real (empty) queue at zero usage. `full` runs the real loop.
 *   3. SINGLE LANE — inherited from `claimNextTask`: at most one dispatched/
 *      running task per runtime, so a board of issues can never fan out into
 *      concurrent Omnigent sessions (the "don't eat usage" guard).
 *
 * Run:  ZOE_EXECUTOR_MODE=live npm run live         # dry by default
 *       ZOE_EXECUTOR_MODE=live ZOE_EXECUTOR_DISPATCH=full npm run live
 */
import { existsSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import type pg from 'pg';
import { loadConfig, type ExecutorConfig } from './config.ts';
import { makePool } from './labdb.ts';
import { setActivityWorkspaceId } from './queue.ts';
import { tick, type ExecutorState } from './executor.ts';

/** Resolve the shared runtime identity by NAME, agreeing with the enqueue side
 * (`executor_queue_backend._EXECUTOR_RUNTIME_NAME`). Fails loudly if absent —
 * the row is created by the enqueue side / the verify script, never guessed. */
export async function resolveRuntimeId(pool: pg.Pool, runtimeName: string): Promise<string> {
  const res = await pool.query(
    'SELECT id::text AS id FROM agent_runtime WHERE name = $1 ORDER BY created_at',
    [runtimeName],
  );
  if (res.rows.length === 0) {
    throw new Error(
      `no agent_runtime named "${runtimeName}" in the live database. Register it first ` +
        '(scripts/maintenance/verify_executor_queue_backend.py registers it), then restart.',
    );
  }
  if (res.rows.length > 1) {
    // Ambiguous: the enqueue side (executor_queue_backend.py) and this runner
    // could bind to DIFFERENT rows and split-brain. A duplicate is a
    // misconfiguration (re-registration / schema reset) — fail loudly rather
    // than silently pick the oldest and claim a queue nobody enqueues into.
    const ids = res.rows.map((r) => r.id as string).join(', ');
    throw new Error(
      `${res.rows.length} agent_runtime rows named "${runtimeName}" (${ids}). ` +
        'Deduplicate to exactly one before running the executor — the enqueue side ' +
        'and this runner must bind to the same runtime.',
    );
  }
  return res.rows[0].id as string;
}

/** The runtime's OWN workspace — used to stamp activity_log rows. Live
 * Multica enforces `activity_log.workspace_id -> workspace(id)` with a FK, so
 * this must be a real workspace, not the lab constant. Resolved from the same
 * runtime row we claim under, so the two can never disagree. */
export async function resolveWorkspaceId(pool: pg.Pool, runtimeId: string): Promise<string> {
  const res = await pool.query(
    'SELECT workspace_id::text AS id FROM agent_runtime WHERE id = $1',
    [runtimeId],
  );
  const id = res.rows[0]?.id as string | undefined;
  if (!id) throw new Error(`agent_runtime ${runtimeId} has no workspace_id`);
  return id;
}

/** What the runner WOULD claim next, without mutating anything (dry mode). */
async function previewNextClaim(pool: pg.Pool, runtimeId: string): Promise<string | null> {
  const res = await pool.query(
    `SELECT id::text AS id FROM agent_task_queue
      WHERE runtime_id = $1 AND status = 'queued'
        AND NOT EXISTS (
          SELECT 1 FROM agent_task_queue busy
           WHERE busy.runtime_id = $1 AND busy.status IN ('dispatched','running'))
      ORDER BY priority DESC, created_at LIMIT 1`,
    [runtimeId],
  );
  return (res.rows[0]?.id as string | undefined) ?? null;
}

function killSwitchPresent(cfg: ExecutorConfig): boolean {
  return existsSync(cfg.killSwitchPath);
}

async function main(): Promise<void> {
  const cfg = loadConfig();
  if (cfg.mode !== 'live') {
    throw new Error('live-runner requires ZOE_EXECUTOR_MODE=live (use `npm run e2e` for the lab).');
  }
  const pool = makePool(cfg);
  cfg.runtimeId = await resolveRuntimeId(pool, cfg.runtimeName);
  // MUST happen before any claim: live activity_log has a workspace FK, so the
  // lab-constant default would roll back every claim transaction.
  setActivityWorkspaceId(await resolveWorkspaceId(pool, cfg.runtimeId));
  const state: ExecutorState = { trackedPids: new Set() };

  const ticksArg = process.argv.indexOf('--ticks');
  const maxTicks = ticksArg >= 0 ? Number(process.argv[ticksArg + 1]) : Infinity;

  console.log(
    `[live] runtime "${cfg.runtimeName}" (${cfg.runtimeId}) dispatch=${cfg.dispatch} ` +
      `poll=${cfg.pollMs}ms kill-switch=${cfg.killSwitchPath}`,
  );
  if (cfg.dispatch === 'dry') {
    console.log('[live] DRY dispatch — will report what it WOULD claim, mutating nothing.');
  }

  // Graceful shutdown: `systemctl stop` sends SIGTERM. Let the in-flight tick
  // finish (ticks are awaited sequentially), then break and drain the pool so
  // no task is abandoned mid-transition and no server-side connection leaks.
  let stopping = false;
  const onSignal = (sig: string) => {
    if (!stopping) console.log(`[live] ${sig} received — finishing the current tick, then draining.`);
    stopping = true;
  };
  process.on('SIGTERM', () => onSignal('SIGTERM'));
  process.on('SIGINT', () => onSignal('SIGINT'));

  let pausedLogged = false;
  for (let i = 0; i < maxTicks && !stopping; i++) {
    try {
      if (killSwitchPresent(cfg)) {
        if (!pausedLogged) {
          console.log('[live] kill switch present — idling (claiming nothing).');
          pausedLogged = true;
        }
      } else {
        pausedLogged = false;
        if (cfg.dispatch === 'dry') {
          const wouldClaim = await previewNextClaim(pool, cfg.runtimeId);
          console.log(`[live] dry tick: would claim ${wouldClaim ?? '- (queue empty / lane busy)'}`);
        } else {
          const { reaped, claimed } = await tick(pool, cfg, state);
          if (reaped || claimed) console.log(`[live] tick: reaped=${reaped} claimed=${claimed ?? '-'}`);
        }
      }
    } catch (err) {
      // A transient DB/spawn error must not kill the supervised loop.
      console.error('[live] tick failed (will retry):', err);
    }
    await new Promise((r) => setTimeout(r, cfg.pollMs));
  }
  await pool.end();
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error('[live] fatal:', err);
    process.exitCode = 1;
  });
}
