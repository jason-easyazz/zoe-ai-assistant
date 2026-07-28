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
import { existsSync } from 'node:fs';
import { join } from 'node:path';
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

/** Parse the adapter's `context.max_runtime` ("45m", "90m", "6h") into ms;
 * null when absent or unparseable (callers fall back to the lane default). */
export function maxRuntimeMs(raw: string | undefined): number | null {
  const m = /^(\d+)\s*([smh])$/i.exec((raw ?? '').trim());
  if (!m) return null;
  const unit = { s: 1_000, m: 60_000, h: 3_600_000 }[m[2].toLowerCase() as 's' | 'm' | 'h'];
  const ms = Number(m[1]) * unit;
  return ms > 0 ? ms : null;
}

/**
 * Effective wall-clock budget for an omnigent-lane task: the lane default
 * (`cfg.omnigentTimeoutMs`) is the FLOOR, and a longer per-task
 * `context.max_runtime` (6h overnight, 90m escalation) extends it. ONE shared
 * definition for the in-process poller AND the reaper — if they disagreed, a
 * reaper on the lane default would fail/requeue a healthy 6h session at ~1h,
 * exactly the early-kill the per-task budget exists to prevent (Codex, #1582).
 */
export function effectiveOmnigentTimeoutMs(cfg: ExecutorConfig, context: unknown): number {
  const ctx = (context ?? {}) as { max_runtime?: string };
  return Math.max(cfg.omnigentTimeoutMs, maxRuntimeMs(ctx.max_runtime) ?? 0);
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
  const ctx = (task.context ?? {}) as {
    phase?: string; brief?: string; body?: string; max_runtime?: string;
  };
  const phase = ctx.phase ?? 'implement';
  const nonce = randomBytes(6).toString('hex');
  const token = doneToken(nonce);

  // Mirror the local lane's work_dir race guard (spawn.ts): the dispatcher
  // commits the queue row BEFORE preparing the worktree, so a fast poll can
  // claim a task whose directory does not exist yet. Defer without burning an
  // attempt rather than briefing a remote agent against a missing checkout;
  // fail loudly if it never appears within the worker timeout.
  //
  // For a REAL brief, additionally require the worktree's .git entry:
  // `git worktree add` creates the target directory before writing its .git
  // pointer and finishing checkout, so exists-alone can pass mid-prepare
  // (Codex, #1582). The residual mid-checkout window is bounded — the
  // dispatcher's prepare is a synchronous subprocess, this lane stages a
  // session + runner + kick before any agent reads the tree, and a deferral
  // burns no attempt. Synthetic lab tasks use plain directories, not git
  // worktrees, so they keep the exists-only check.
  const briefedWorkDir = task.work_dir;
  const workDirReady =
    !briefedWorkDir ||
    (existsSync(briefedWorkDir) &&
      (!ctx.body || existsSync(join(briefedWorkDir, '.git'))));
  if (briefedWorkDir && !workDirReady) {
    const ageMs = task.created_at ? Date.now() - new Date(task.created_at).getTime() : 0;
    if (ageMs > cfg.workerTimeoutMs) {
      await reportTransition(pool, cfg.runtimeId, task, 'failed',
        `work_dir ${briefedWorkDir} never became a usable checkout (still absent or uninitialized ` +
        `${Math.round(ageMs / 1000)}s after the row was queued) — the dispatcher did not finish preparing the worktree`);
      return;
    }
    await reportTransition(pool, cfg.runtimeId, task, 'queued',
      `work_dir ${briefedWorkDir} is not ready yet (absent or mid-checkout; dispatcher still preparing ` +
      'the worktree) — returned to the queue without burning an attempt',
      { requeue: true, keepAttempt: true, action: 'task_deferred' });
    return;
  }

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
    // A real dispatch (Phase 2) carries the task brief in context.body (see
    // executor_queue_backend.create). context.brief is the lab's synthetic
    // field and MUST stay on the read-only connectivity-proof path — the e2e
    // seeds /tmp work dirs that don't exist in-container, and classifying it
    // as real would tell the agent to self-provision and do work.
    const realBrief = ctx.body;
    const brief = [
      realBrief
        ? `EXECUTOR TASK. Task id: ${task.id}, phase: ${phase}. ${
            phase === 'implement'
              ? 'Work in the directory named below; commit and push on its checked-out branch. Do not touch the live checkout (/home/zoe/assistant) directly.'
              : phase === 'retro'
                // Retro deliberately runs from the main checkout
                // (_workspace_for_phase pins it there): read-only learning
                // work, so the "don't touch the live checkout" rule would
                // contradict its own workspace.
                ? 'This phase is read-only orchestration/learning work run from the main checkout named below: never modify, commit, or push anything.'
                : 'This phase is analysis/verification: read and run checks from the directory named below, but do NOT commit or push unless the task brief explicitly says to. Do not touch the live checkout (/home/zoe/assistant) directly.'
          }`
        : `SYNTHETIC EXECUTOR TASK (flue-executor lab). Task id: ${task.id}, phase: ${phase}.`,
      realBrief
        ? [
            `Working directory: ${task.work_dir ?? '(unset — stop and report failure)'}`,
            // The runner is containerised with the live repo bind-mounted at
            // /workspace; host worktree paths under ~/.worktrees are usually
            // NOT visible in-container. Do not fail on that — self-provision.
            'If that directory is not accessible in your environment, create your own fresh',
            'git worktree (or clone) from the repo at /workspace on the branch the task names',
            '(or a new branch off origin/main) and work there. Never work directly in /workspace.',
          ].join('\n')
        : '',
      realBrief ?? ctx.brief ?? 'No task brief was provided; treat this as a connectivity proof.',
      '',
      // The brief above is authored for the Hermes terminal lane and mandates
      // kanban_show/kanban_complete/kanban_block. This container exposes only
      // Serena + codebase-memory (modules/omnigent/.mcp.json) and reports
      // completion by the nonce below — a worker that reads the embedded
      // protocol literally can call the absent tools a blocker and exit
      // without the nonce, stalling the queue until timeout (Codex, #1582).
      realBrief
        ? [
            'PROTOCOL OVERRIDE (this executor lane, overrides anything above):',
            '1. The brief above was written for a different runtime. Its BOARD API calls —',
            '   kanban_show, kanban_complete, kanban_block, and any other kanban_* verb —',
            '   DO NOT EXIST here and their absence is NOT a blocker. Skip exactly those',
            '   board calls and NOTHING else: every other instruction still applies, and the',
            '   tooling it names (git/gh, tests, validators, repo scripts such as',
            '   run_greploop_guard.sh and pipeline_evidence_commands.py) does exist here.',
            '   Report status ONLY via the handoff block and completion id described below.',
            '2. NEVER rewrite history or force-push (no --force, no --force-with-lease),',
            '   even where the brief above says to. If the PR branch is behind, update it',
            '   with a merge from origin/main (what GitHub update-branch does), or report',
            '   BLOCKER= instead.',
            '',
          ].join('\n')
        : '',
      realBrief
        ? [
            'When the task is genuinely done (or you are blocked), END your final reply with a',
            'handoff block — one field per line, each line starting at column 0 exactly as',
            '`FIELD=value` (single line per field, no markdown around the field names):',
            '  PR_URL=<the GitHub PR url, if one exists>',
            '  TESTS=<the exact test/check commands you ran and their results, e.g. "pytest tests/x -q: 12 passed">',
            '  VALIDATORS=<validator commands + results, e.g. "validate_structure.py: exit 0; validate_critical_files.py: exit 0">',
            '  SUMMARY=<one-line outcome>',
            '  BLOCKER=<ONLY if you are blocked: one line naming the blocker; omit this field entirely on success>',
            'These lines are machine-parsed by the pipeline evidence gate; without them the',
            'phase cannot complete, and a blocked run without BLOCKER= is recorded as a false',
            'success. After the block, add a final line consisting of the prefix',
          ].join('\n')
        : 'Do NOT modify, create, or delete any files. Do not run commands.\nWhen done, reply with a single line consisting of the prefix',
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
  // Per-task budget: the adapter stamps context.max_runtime ("6h" overnight,
  // "90m" quality-escalation, "45m" interactive — kanban_adapter._max_runtime)
  // and a long-running task must not be failed while its remote session is
  // still healthily working (Codex, #1582). Shared with the reaper via
  // effectiveOmnigentTimeoutMs — see its doc for why the lane default is the
  // FLOOR, never a cap.
  const timeoutMs = effectiveOmnigentTimeoutMs(cfg, task.context);
  const deadline = Date.now() + timeoutMs;
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
      const reply = await sessionTokenReply(cfg, sessionId, token);
      if (reply !== null) {
        await reportTransition(pool, cfg.runtimeId, runningTask, 'completed',
          `omnigent session ${sessionId} returned the completion token ${token}`,
          { result: { ok: true, summary: `omnigent session ${sessionId} completed: ${reply}`, sessionId } });
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
            `${timeoutMs}ms; kick log tail: ${kickTail || '(empty)'}`);
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

/**
 * Deepest usable reply text: walk every string in the item and return the
 * longest one containing the token. Item schemas nest reply text differently
 * per harness (top-level text, content strings, content[].text, deeper
 * wrappers); the serialized-JSON fallback escapes newlines ("\n"), which
 * breaks the column-zero FIELD= handoff parsing downstream
 * (pipeline_handoff._KV_RE) — so real text at ANY depth beats stringify
 * (Codex, #1582).
 */
export function deepTokenText(value: unknown, token: string): string {
  if (typeof value === 'string') return value.includes(token) ? value : '';
  let best = '';
  if (Array.isArray(value)) {
    for (const entry of value) {
      const found = deepTokenText(entry, token);
      if (found.length > best.length) best = found;
    }
  } else if (value && typeof value === 'object') {
    for (const entry of Object.values(value)) {
      const found = deepTokenText(entry, token);
      if (found.length > best.length) best = found;
    }
  }
  return best;
}

/** Scan the session's items for the completion token. */
export async function sessionHasToken(
  cfg: ExecutorConfig,
  sessionId: string,
  token: string,
): Promise<boolean> {
  return (await sessionTokenReply(cfg, sessionId, token)) !== null;
}

/**
 * Find the completion token in the session's items and return the text of the
 * item that carries it (the agent's final reply — which the brief asks to
 * include a summary line, e.g. the PR URL). Returns null when the token is
 * absent. The reply text travels into the completion result so the Zoe-side
 * evidence gate can recover the PR URL from prose instead of GATE_BLOCKing a
 * genuinely finished implement phase (observed live on ZOE-6106: PR opened,
 * gate saw only "omnigent session … completed").
 */
export async function sessionTokenReply(
  cfg: ExecutorConfig,
  sessionId: string,
  token: string,
): Promise<string | null> {
  const items = await api<{ data?: unknown[]; items?: unknown[] }>(
    cfg, 'GET', `/v1/sessions/${sessionId}/items`);
  const list = (items.data ?? items.items ?? []) as unknown[];
  for (const item of list) {
    const flat = JSON.stringify(item);
    if (!flat.includes(token)) continue;
    // Prefer readable text: top-level text/content strings, then the
    // message-item shape content: [{type:'output_text', text}], then a
    // recursive walk for any deeper-nested reply text, then the serialized
    // item as a true last resort (a JSON blob still lets the Zoe-side prose
    // PR-recovery regex work, but its escaped "\n" breaks FIELD= handoff
    // parsing — see deepTokenText).
    const rec = item as Record<string, unknown>;
    let text =
      (typeof rec.text === 'string' && rec.text) ||
      (typeof rec.content === 'string' && rec.content) ||
      '';
    if (!text && Array.isArray(rec.content)) {
      text = rec.content
        .map((c) => (c && typeof c === 'object' && typeof (c as Record<string, unknown>).text === 'string'
          ? ((c as Record<string, unknown>).text as string) : ''))
        .filter(Boolean)
        .join('\n');
    }
    if (!text) text = deepTokenText(item, token);
    // Keep the TAIL: the machine-parsed handoff block (FIELD= lines + token)
    // is at the end of the reply, and truncating it breaks the evidence gate.
    return (text || flat).slice(-4000);
  }
  return null;
}
