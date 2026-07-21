/**
 * Lab executor config — where the lab DB lives and who this runtime is.
 *
 * The lab NEVER touches the live `multica` database. It uses its own scratch
 * database (`multica_executor_lab`) in the same Postgres container, so the
 * atomic-claim semantics are proven against the real engine (SKIP LOCKED,
 * partial indexes) without any risk to the board.
 *
 * Credentials are never committed: the connection string is taken from
 * `LAB_DATABASE_URL`, or derived from the live service env file's
 * `POSTGRES_URL` with the database name swapped to the lab DB.
 *
 * LAB ONLY. See ../README.md.
 */
import { readFileSync } from 'node:fs';

/** Fixed lab identities (arbitrary but stable UUIDs, used for actor/agent columns). */
export const LAB_WORKSPACE_ID = '00000000-0000-4000-8000-00000dab0001';
export const LAB_RUNTIME_ID = '00000000-0000-4000-8000-00000dab0002';
export const LAB_AGENT_ID = '00000000-0000-4000-8000-00000dab0003';

export const LAB_DB_NAME = 'multica_executor_lab';

export interface ExecutorConfig {
  /** Postgres URL pointing at the LAB database (never the live `multica` DB). */
  labDatabaseUrl: string;
  /** Same credentials pointing at the maintenance `zoe` DB, used only to CREATE DATABASE. */
  adminDatabaseUrl: string;
  /** This executor's runtime identity — rows are claimed per-runtime. */
  runtimeId: string;
  /** Poll interval between executor ticks, ms. */
  pollMs: number;
  /** Hard cap on a worker's wall-clock before it is killed and failed, ms. */
  workerTimeoutMs: number;
}

function swapDbName(url: string, dbName: string): string {
  // postgresql://user:pass@host:port/dbname[?params]
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
      `Set LAB_DATABASE_URL, or make ${envFile} readable (override path with ZOE_ENV_FILE): ${err}`,
    );
  }
  const line = text.split('\n').find((l) => l.startsWith('POSTGRES_URL='));
  if (!line) throw new Error(`No POSTGRES_URL in ${envFile}; set LAB_DATABASE_URL instead.`);
  return line.slice('POSTGRES_URL='.length).trim();
}

export function loadConfig(): ExecutorConfig {
  const base = process.env.LAB_DATABASE_URL ?? postgresUrlFromEnvFile();
  const labDatabaseUrl = process.env.LAB_DATABASE_URL ?? swapDbName(base, LAB_DB_NAME);
  if (new URL(labDatabaseUrl).pathname === '/multica') {
    throw new Error('Refusing to run the lab against the live `multica` database.');
  }
  return {
    labDatabaseUrl,
    adminDatabaseUrl: swapDbName(base, 'zoe'),
    runtimeId: process.env.LAB_RUNTIME_ID ?? LAB_RUNTIME_ID,
    pollMs: Number(process.env.LAB_POLL_MS ?? '1000'),
    workerTimeoutMs: Number(process.env.LAB_WORKER_TIMEOUT_MS ?? '300000'),
  };
}
