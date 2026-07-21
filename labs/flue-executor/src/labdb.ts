/**
 * Lab database access + schema bootstrap.
 *
 * The schema MIRRORS the live Multica tables this executor will target in
 * Phase 2 — `agent_task_queue` and `activity_log`, DDL taken from `\d` against
 * the live DB on 2026-07-21 — minus foreign keys (the lab DB has no `issue` /
 * `agent` / `agent_runtime` tables) and minus columns the executor never
 * touches. The load-bearing parts are kept exactly: the status lifecycle CHECK,
 * the claim-candidates partial index, the one-pending-task-per-(issue,agent)
 * unique partial index, and attempt/max_attempts/failure_reason.
 *
 * LAB ONLY.
 */
import pg from 'pg';
import { LAB_DB_NAME, type ExecutorConfig } from './config.ts';

const DDL = `
CREATE TABLE IF NOT EXISTS agent_task_queue (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id uuid NOT NULL,
  issue_id uuid,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','dispatched','running','completed','failed','cancelled')),
  priority integer NOT NULL DEFAULT 0,
  dispatched_at timestamptz,
  started_at timestamptz,
  completed_at timestamptz,
  result jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  context jsonb,
  runtime_id uuid NOT NULL,
  session_id text,
  work_dir text,
  attempt integer NOT NULL DEFAULT 1,
  max_attempts integer NOT NULL DEFAULT 2,
  parent_task_id uuid,
  failure_reason text,
  trigger_summary text
);
CREATE INDEX IF NOT EXISTS idx_agent_task_queue_claim_candidates
  ON agent_task_queue (runtime_id, priority DESC, created_at) WHERE status = 'queued';
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_pending_task_per_issue_agent
  ON agent_task_queue (issue_id, agent_id) WHERE status IN ('queued','dispatched');

CREATE TABLE IF NOT EXISTS activity_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id uuid NOT NULL,
  issue_id uuid,
  actor_type text CHECK (actor_type IN ('member','agent','system')),
  actor_id uuid,
  action text NOT NULL,
  details jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activity_log_issue_keyset
  ON activity_log (issue_id, created_at DESC, id DESC);
`;

/** Create the lab database if missing (via the admin/zoe DB), then apply DDL. */
export async function bootstrapLabDb(cfg: ExecutorConfig): Promise<void> {
  const admin = new pg.Client({ connectionString: cfg.adminDatabaseUrl });
  await admin.connect();
  try {
    const exists = await admin.query('SELECT 1 FROM pg_database WHERE datname = $1', [
      LAB_DB_NAME,
    ]);
    if (exists.rowCount === 0) {
      // CREATE DATABASE cannot be parameterized; the name is a compile-time constant.
      await admin.query(`CREATE DATABASE ${LAB_DB_NAME}`);
    }
  } finally {
    await admin.end();
  }
  const lab = new pg.Client({ connectionString: cfg.labDatabaseUrl });
  await lab.connect();
  try {
    await lab.query(DDL);
  } finally {
    await lab.end();
  }
}

export function makePool(cfg: ExecutorConfig): pg.Pool {
  return new pg.Pool({ connectionString: cfg.labDatabaseUrl, max: 4 });
}
