/**
 * Executor config — two modes.
 *
 * `lab` (default): the executor NEVER touches the live `multica` database. It
 * uses its own scratch database (`multica_executor_lab`) so the atomic-claim
 * semantics are proven against the real engine (SKIP LOCKED, partial indexes)
 * with zero risk to the board. This is what `npm run e2e` uses.
 *
 * `live` (opt-in via `ZOE_EXECUTOR_MODE=live`): the executor claims from
 * Multica's REAL `agent_task_queue` — the queue `kanban_adapter` writes into
 * under `ZOE_KANBAN_BACKEND=executor`. Two guards make this safe:
 *   - it idles whenever the dispatch KILL SWITCH (`~/.zoe/multica_dispatch_paused`)
 *     is present, so it can be armed as a service long before the board is
 *     unpaused;
 *   - it defaults to `dry` dispatch (poll + log what it WOULD claim, mutate
 *     nothing) until `ZOE_EXECUTOR_DISPATCH=full` is set, so the wiring can be
 *     proven against the real (empty) queue at zero usage.
 * In live mode the runtime identity is RESOLVED BY NAME from Multica, so the
 * executor and the Python `executor_queue_backend` (which registers the row and
 * enqueues against it) agree on one identity with no UUID to keep in sync.
 *
 * Credentials are never committed: the connection string comes from
 * `LAB_DATABASE_URL` / `MULTICA_DATABASE_URL`, or is derived from the live
 * service env file's `POSTGRES_URL` with the database name swapped.
 */
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

/** Fixed lab identities (arbitrary but stable UUIDs, used for actor/agent columns). */
export const LAB_WORKSPACE_ID = '00000000-0000-4000-8000-00000dab0001';
export const LAB_RUNTIME_ID = '00000000-0000-4000-8000-00000dab0002';
export const LAB_AGENT_ID = '00000000-0000-4000-8000-00000dab0003';

export const LAB_DB_NAME = 'multica_executor_lab';
export const LIVE_DB_NAME = 'multica';

/** Contract with executor_queue_backend.py (_EXECUTOR_RUNTIME_NAME): the name
 * both sides resolve the shared runtime identity by. Changing it here without
 * changing it there splits the executor from the enqueue side. */
export const LIVE_RUNTIME_NAME = 'Flue Executor (Zoe)';

export type ExecutorMode = 'lab' | 'live';
export type DispatchMode = 'dry' | 'full';

export interface ExecutorConfig {
  mode: ExecutorMode;
  /** live only: `dry` polls + logs but never claims/spawns; `full` runs. */
  dispatch: DispatchMode;
  /** Postgres URL for the target DB (lab: scratch; live: `multica`). */
  databaseUrl: string;
  /** Credentials pointing at the maintenance `zoe` DB, used only to CREATE the
   * scratch DB in lab mode. Empty in live mode (never creates anything). */
  adminDatabaseUrl: string;
  /** Runtime identity rows are claimed per-runtime. lab: fixed UUID; live:
   * resolved by {@link runtimeName} at startup (empty until resolved). */
  runtimeId: string;
  /** live: the runtime NAME to resolve/agree on with the enqueue side. */
  runtimeName: string;
  /** live: while this file exists the executor idles (the dispatch kill switch). */
  killSwitchPath: string;
  /** Poll interval between executor ticks, ms. */
  pollMs: number;
  /** Hard cap on a worker's wall-clock before it is killed and failed, ms. */
  workerTimeoutMs: number;
  /** Omnigent heavy lane (§5 decision 2): API base URL. */
  omnigentBaseUrl: string;
  /** Omnigent agent to run heavy tasks (default: polly, the claude-sdk workhorse). */
  omnigentAgentId: string;
  /** Container name for the docker-exec kick (REST cannot start claude-sdk runs). */
  omnigentContainer: string;
  /** Hard cap on an Omnigent session before the task is failed, ms. */
  omnigentTimeoutMs: number;
  /** Interval between completion-token polls of an Omnigent session, ms. */
  omnigentPollMs: number;
}

function swapDbName(url: string, dbName: string): string {
  const u = new URL(url);
  u.pathname = `/${dbName}`;
  return u.toString();
}

function postgresUrlFromEnvFile(): string {
  const envFile = process.env.ZOE_ENV_FILE ?? '/home/zoe/assistant/services/zoe-data/.env';
  let text: string;
  try {
    text = readFileSync(envFile, 'utf8');
  } catch (err) {
    throw new Error(
      `Set LAB_DATABASE_URL/MULTICA_DATABASE_URL, or make ${envFile} readable ` +
        `(override path with ZOE_ENV_FILE): ${err}`,
    );
  }
  const line = text.split('\n').find((l) => l.startsWith('POSTGRES_URL='));
  if (!line) throw new Error(`No POSTGRES_URL in ${envFile}; set the database URL explicitly.`);
  return line.slice('POSTGRES_URL='.length).trim();
}

function commonOmnigent(): Pick<
  ExecutorConfig,
  'omnigentBaseUrl' | 'omnigentAgentId' | 'omnigentContainer' | 'omnigentTimeoutMs' | 'omnigentPollMs'
> {
  return {
    omnigentBaseUrl: process.env.LAB_OMNIGENT_URL ?? 'http://127.0.0.1:6767',
    omnigentAgentId: process.env.LAB_OMNIGENT_AGENT_ID ?? 'ag_057995d1517418e6839f51d340785dd6',
    omnigentContainer: process.env.LAB_OMNIGENT_CONTAINER ?? 'zoe-omnigent',
    omnigentTimeoutMs: Number(process.env.LAB_OMNIGENT_TIMEOUT_MS ?? '600000'),
    omnigentPollMs: Number(process.env.LAB_OMNIGENT_POLL_MS ?? '5000'),
  };
}

export function loadConfig(): ExecutorConfig {
  const mode: ExecutorMode =
    (process.env.ZOE_EXECUTOR_MODE ?? 'lab').trim().toLowerCase() === 'live' ? 'live' : 'lab';
  const base =
    process.env.LAB_DATABASE_URL ?? process.env.MULTICA_DATABASE_URL ?? postgresUrlFromEnvFile();

  if (mode === 'live') {
    const databaseUrl =
      process.env.MULTICA_DATABASE_URL ?? swapDbName(base, LIVE_DB_NAME);
    if (new URL(databaseUrl).pathname !== `/${LIVE_DB_NAME}`) {
      throw new Error(
        `live mode targets the "${LIVE_DB_NAME}" database, got ${new URL(databaseUrl).pathname}`,
      );
    }
    const dispatch: DispatchMode =
      (process.env.ZOE_EXECUTOR_DISPATCH ?? 'dry').trim().toLowerCase() === 'full' ? 'full' : 'dry';
    return {
      mode,
      dispatch,
      databaseUrl,
      adminDatabaseUrl: '',
      runtimeId: '', // resolved by name at startup (see live-runner)
      runtimeName: process.env.ZOE_EXECUTOR_RUNTIME_NAME ?? LIVE_RUNTIME_NAME,
      killSwitchPath:
        process.env.ZOE_MULTICA_KILL_SWITCH ?? join(homedir(), '.zoe', 'multica_dispatch_paused'),
      pollMs: Number(process.env.LAB_POLL_MS ?? '2000'),
      workerTimeoutMs: Number(process.env.LAB_WORKER_TIMEOUT_MS ?? '300000'),
      ...commonOmnigent(),
    };
  }

  // lab mode — the scratch DB, hard allowlisted (a denylist of "/multica" is
  // bypassable by typo: /zoe, /Multica, /multica/).
  const databaseUrl = process.env.LAB_DATABASE_URL ?? swapDbName(base, LAB_DB_NAME);
  if (new URL(databaseUrl).pathname !== `/${LAB_DB_NAME}`) {
    throw new Error(
      `Refusing to run: lab mode only operates on the "${LAB_DB_NAME}" scratch database, ` +
        `got ${new URL(databaseUrl).pathname}`,
    );
  }
  return {
    mode,
    dispatch: 'full',
    databaseUrl,
    adminDatabaseUrl: swapDbName(base, 'zoe'),
    runtimeId: process.env.LAB_RUNTIME_ID ?? LAB_RUNTIME_ID,
    runtimeName: LIVE_RUNTIME_NAME,
    killSwitchPath: process.env.ZOE_MULTICA_KILL_SWITCH ?? '/nonexistent-lab-never-idles',
    pollMs: Number(process.env.LAB_POLL_MS ?? '1000'),
    workerTimeoutMs: Number(process.env.LAB_WORKER_TIMEOUT_MS ?? '300000'),
    ...commonOmnigent(),
  };
}
