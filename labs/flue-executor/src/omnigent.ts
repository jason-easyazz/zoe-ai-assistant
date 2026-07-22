/**
 * The Omnigent heavy lane — §5 decision 2: Omnigent is a PRIMARY executor lane
 * from day one, so the Phase-1 executor spawns heavy work here, not in a local
 * Flue worker.
 *
 * The kick recipe is the operator-verified one (2026-07-03): REST alone CANNOT
 * start a claude-sdk session — staging via REST (session + comment brief +
 * runner) must be followed by a `docker exec … omnigent run -r <SID>` kick, or
 * the session sits idle forever. Do not "simplify" the kick away.
 *
 * Completion signal: sessions settle to status `idle` after replying (there is
 * no `completed` status), so the executor detects completion by finding the
 * per-task nonce token in the session items, and failure by timeout or an
 * unreachable API. Every outcome is reported through the same reason-mandatory
 * queue transitions as the local lane.
 *
 * LAB ONLY.
 */
import { execFile } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { promisify } from 'node:util';
import type pg from 'pg';
import type { ExecutorConfig } from './config.ts';
import { reportTransition, type TaskRow } from './queue.ts';
import { failOrRequeue } from './spawn.ts';

const exec = promisify(execFile);

interface OmnigentSession {
  id: string;
  status: string;
}

/** API error carrying the HTTP status — a 404 is an AUTHORITATIVE answer
 * (the session does not exist), unlike a network failure (no answer at all). */
export class OmnigentApiError extends Error {
  readonly status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.status = status;
  }
}

async function api<T>(
  cfg: ExecutorConfig,
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${cfg.omnigentBaseUrl}${path}`, {
    method,
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) {
    throw new OmnigentApiError(
      `omnigent ${method} ${path} -> HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`,
      res.status,
    );
  }
  return (await res.json()) as T;
}

/** True when Omnigent has at least one online host. */
export async function omnigentHealthy(cfg: ExecutorConfig): Promise<boolean> {
  try {
    const hosts = await api<{ hosts: Array<{ host_id: string; status: string }> }>(
      cfg, 'GET', '/v1/hosts');
    return hosts.hosts.some((h) => h.status === 'online');
  } catch {
    return false;
  }
}

/** The token the brief asks the agent to output; found in items = task done. */
export function doneToken(nonce: string): string {
  return `FLUE-EXEC-DONE-${nonce}`;
}

/**
 * Spawn a heavy task on Omnigent: stage session + brief, launch a runner, kick
 * the claude-sdk run, then poll for the nonce until done or timeout. All state
 * transitions go through the reason-mandatory queue exactly like the local
 * lane; the session id is recorded on the queue row (session_id column) so a
 * restarted executor can still see which session owns the row.
 */
export async function spawnOmnigentWorker(
  pool: pg.Pool,
  cfg: ExecutorConfig,
  task: TaskRow,
): Promise<void> {
  const ctx = (task.context ?? {}) as { phase?: string; brief?: string };
  const phase = ctx.phase ?? 'implement';
  const nonce = randomBytes(6).toString('hex');
  const token = doneToken(nonce);

  if (!(await omnigentHealthy(cfg))) {
    await failOrRequeue(pool, cfg, task,
      `omnigent lane unavailable (no online host at ${cfg.omnigentBaseUrl}) — heavy task cannot run; local lane is unaffected`);
    return;
  }

  let sessionId: string;
  try {
    const session = await api<OmnigentSession>(cfg, 'POST', '/v1/sessions', {
      agent_id: cfg.omnigentAgentId,
      title: `flue-executor ${phase} task ${task.id}`,
    });
    sessionId = session.id;

    // Durable ownership BEFORE anything can start running: if the executor
    // dies after the kick but before the running transition commits, the
    // dispatched row must already carry the session pointer so the reaper
    // recovers by EVIDENCE instead of blind-requeuing a task whose remote
    // session is still working. (The only remaining crash window is between
    // session creation and this write — an unkicked session, which sits idle
    // and harmless.)
    await pool.query(
      `UPDATE agent_task_queue
          SET session_id=$2,
              context = coalesce(context,'{}'::jsonb) ||
                jsonb_build_object('lane','omnigent','nonce',$3::text)
        WHERE id=$1 AND status='dispatched'`,
      [task.id, sessionId, nonce],
    );

    // The assembled completion token must NEVER appear in the brief: staged
    // comments can surface in the session's items, and a scan that finds the
    // token in our own instruction text would self-complete the task. The
    // brief carries the prefix and the id as separate pieces the agent must
    // join — only a real agent reply can contain the assembled token.
    const brief = [
      `SYNTHETIC EXECUTOR TASK (flue-executor lab). Task id: ${task.id}, phase: ${phase}.`,
      ctx.brief ?? 'No task brief was provided; treat this as a connectivity proof.',
      '',
      'Do NOT modify, create, or delete any files. Do not run commands.',
      'When done, reply with a single line consisting of the prefix',
      `"FLUE-EXEC-DONE-" immediately followed (no space) by this completion id: ${nonce}`,
    ].join('\n');
    if (brief.includes(token)) {
      throw new Error('internal: assembled completion token leaked into the brief');
    }
    await api(cfg, 'POST', `/v1/sessions/${sessionId}/comments`, {
      path: 'README.md',
      body: brief,
      start_index: 0,
      end_index: 0,
    });

    const hosts = await api<{ hosts: Array<{ host_id: string; status: string }> }>(
      cfg, 'GET', '/v1/hosts');
    const host = hosts.hosts.find((h) => h.status === 'online');
    if (!host) throw new Error('no online omnigent host');
    await api(cfg, 'POST', `/v1/hosts/${host.host_id}/runners`, {
      session_id: sessionId,
      workspace: '/workspace',
    });

    // The kick — the step REST cannot do (see module doc).
    const kick =
      'Fetch your session comments for the task brief and follow it exactly. ' +
      'It ends with a completion token you must output verbatim.';
    await exec('docker', [
      'exec', '-d', cfg.omnigentContainer, 'sh', '-c',
      `cd /workspace && omnigent run --server ${cfg.omnigentBaseUrl} --harness claude-sdk ` +
        `-r ${sessionId} -p ${JSON.stringify(kick)} --no-log > /tmp/flue-exec-kick-${sessionId}.log 2>&1`,
    ]);
  } catch (err) {
    await failOrRequeue(pool, cfg, task,
      `omnigent spawn failed while staging/kicking the session: ${err}`);
    return;
  }

  // Ownership was written before the kick; merging it again here keeps the
  // running flip self-contained even if that earlier write raced a requeue.
  const startedOk = await reportTransition(pool, cfg.runtimeId, task, 'running',
    `omnigent session ${sessionId} staged, runner launched, claude-sdk run kicked for phase "${phase}"`,
    {
      action: 'task_started',
      sessionId,
      contextMerge: { lane: 'omnigent', nonce },
    });
  if (!startedOk) {
    console.error(`[executor] task ${task.id}: running transition lost after omnigent kick (row moved on)`);
    return;
  }

  const runningTask: TaskRow = { ...task, status: 'running' };
  const deadline = Date.now() + cfg.omnigentTimeoutMs;
  const poll = async (): Promise<void> => {
    // Ownership check first: if the row already left `running` (another
    // reporter, a reap, or test teardown), this poller has no claim — stop.
    // This also stops the chain when the pool is closed (query throws).
    try {
      const row = await pool.query(
        `SELECT status FROM agent_task_queue WHERE id=$1`, [task.id]);
      if (row.rows[0]?.status !== 'running') return;
    } catch {
      return;
    }
    try {
      if (await sessionHasToken(cfg, sessionId, token)) {
        await reportTransition(pool, cfg.runtimeId, runningTask, 'completed',
          `omnigent session ${sessionId} returned the completion token ${token}`,
          { result: { ok: true, summary: `omnigent session ${sessionId} completed`, sessionId } });
        return;
      }
      // Fail FAST on fatal kick errors — do not burn the full timeout when the
      // harness already told us why it cannot run at all. This list is grown
      // from real incidents, not guessed: logged-out harness (2026-07-22) and
      // exhausted account credits ("You're out of usage credits", same day —
      // which the earlier narrower pattern MISSED, costing a full timeout).
      const kickNow = await kickLogTail(cfg, sessionId);
      if (/not logged in|please run \/login|invalid api key|credit balance|usage credits|out of credit|rate limit|quota exceeded/i.test(kickNow)) {
        await failOrRequeue(pool, cfg, runningTask,
          `omnigent claude-sdk harness cannot run (fatal kick error): ${kickNow}`);
        return;
      }
      if (Date.now() > deadline) {
        // Surface the ROOT CAUSE in the reason, not just "timed out": the kick
        // log usually names it (e.g. "Not logged in · Please run /login").
        const kickTail = await kickLogTail(cfg, sessionId);
        await failOrRequeue(pool, cfg, runningTask,
          `omnigent session ${sessionId} did not return the completion token within ` +
            `${cfg.omnigentTimeoutMs}ms; kick log tail: ${kickTail || '(empty)'}`);
        return;
      }
    } catch (err) {
      if (Date.now() > deadline) return; // cannot report either — stop looping
      console.error(`[executor] omnigent poll error for task ${task.id}:`, err);
    }
    setTimeout(() => void poll(), cfg.omnigentPollMs).unref?.();
  };
  setTimeout(() => void poll(), cfg.omnigentPollMs).unref?.();
}

/** Last line(s) of the in-container kick log — the usual home of the real error. */
async function kickLogTail(cfg: ExecutorConfig, sessionId: string): Promise<string> {
  try {
    const { stdout } = await exec('docker', [
      'exec', cfg.omnigentContainer, 'sh', '-c',
      `tail -c 300 /tmp/flue-exec-kick-${sessionId}.log 2>/dev/null`,
    ]);
    return stdout.trim().split('\n').slice(-3).join(' | ');
  } catch {
    return '';
  }
}

/** Scan the session's items for the completion token. */
export async function sessionHasToken(
  cfg: ExecutorConfig,
  sessionId: string,
  token: string,
): Promise<boolean> {
  const items = await api<{ data?: unknown[]; items?: unknown[] }>(
    cfg, 'GET', `/v1/sessions/${sessionId}/items`);
  const list = items.data ?? items.items ?? [];
  return JSON.stringify(list).includes(token);
}
