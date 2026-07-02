/**
 * Thin GitHub + local-git helpers for the spike.
 *
 * Deliberately shells out to `git` and `gh` rather than pulling an Octokit dep:
 * fewer moving parts for a spike, and `gh` reuses the box's existing auth.
 *
 * LAB ONLY.
 */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import type { SpikeConfig } from './config.ts';

const exec = promisify(execFile);

async function run(
  cmd: string,
  args: string[],
  opts: { cwd?: string; env?: NodeJS.ProcessEnv } = {},
): Promise<string> {
  const { stdout } = await exec(cmd, args, {
    cwd: opts.cwd,
    env: { ...process.env, ...opts.env },
    maxBuffer: 16 * 1024 * 1024,
  });
  return stdout.trim();
}

/** Fetch issue title + body so the scout can read what it's working on. */
export async function fetchIssue(
  cfg: SpikeConfig,
): Promise<{ number: number; title: string; body: string; url: string }> {
  const json = await run('gh', [
    'issue',
    'view',
    String(cfg.targetIssue),
    '-R',
    cfg.githubRepo,
    '--json',
    'number,title,body,url',
  ]);
  return JSON.parse(json);
}

/**
 * Create a fresh branch off main in the local checkout, from a PRISTINE tree.
 *
 * SAFETY: this resets the checkout hard and removes untracked files, so it MUST
 * only run against a disposable worktree — never an operator's normal checkout
 * with uncommitted work. We fail fast if the tree is dirty unless the caller has
 * explicitly confirmed it's disposable via `ZOE_HARNESS_DISPOSABLE_CHECKOUT=1`.
 * (The harness should be pointed at a dedicated `git worktree`, not a live tree.)
 */
export async function createBranch(cfg: SpikeConfig, branch: string): Promise<void> {
  await run('git', ['fetch', 'origin', 'main'], { cwd: cfg.zoeCheckout });

  const dirty = (
    await run('git', ['status', '--porcelain'], { cwd: cfg.zoeCheckout })
  ).trim();
  if (dirty && process.env.ZOE_HARNESS_DISPOSABLE_CHECKOUT !== '1') {
    throw new Error(
      `ZOE_CHECKOUT (${cfg.zoeCheckout}) has uncommitted changes; refusing to ` +
        `reset --hard / clean it (that would discard work). Point the harness at a ` +
        `disposable 'git worktree', or set ZOE_HARNESS_DISPOSABLE_CHECKOUT=1 to confirm ` +
        `this checkout is disposable.\n${dirty}`,
    );
  }

  // Branch from origin/main so the spike doesn't depend on the checkout's HEAD.
  await run('git', ['checkout', '-B', branch, 'origin/main'], { cwd: cfg.zoeCheckout });
  // Pristine origin/main tree so leftover edits from a previous run can't bleed
  // into this run's diff. Guarded by the dirty-check above.
  await run('git', ['reset', '--hard', 'origin/main'], { cwd: cfg.zoeCheckout });
  await run('git', ['clean', '-fd'], { cwd: cfg.zoeCheckout });
}

/** Stage everything, commit, and push the branch. */
export async function commitAndPush(
  cfg: SpikeConfig,
  branch: string,
  message: string,
): Promise<void> {
  await run('git', ['add', '-A'], { cwd: cfg.zoeCheckout });
  await run('git', ['commit', '-m', message], { cwd: cfg.zoeCheckout });
  await run('git', ['push', '-u', 'origin', branch, '--force-with-lease'], {
    cwd: cfg.zoeCheckout,
  });
}

/**
 * Capture the EXACT tree that will be committed: a stat summary plus the full
 * patch. Stages everything first (`git add -A`) and diffs the index, so newly
 * created/untracked files (snapshots, generated files) are in both the verifier
 * input and the PR body — not silently committed by `commitAndPush`'s `git add -A`.
 */
export async function captureDiff(
  cfg: SpikeConfig,
): Promise<{ stat: string; patch: string }> {
  await run('git', ['add', '-A'], { cwd: cfg.zoeCheckout });
  const stat = await run('git', ['diff', '--cached', '--stat', 'HEAD'], { cwd: cfg.zoeCheckout });
  const patch = await run('git', ['diff', '--cached', 'HEAD'], { cwd: cfg.zoeCheckout });
  return { stat, patch };
}

/** Run the configured verify command in the checkout; capture combined output. */
export async function runVerify(cfg: SpikeConfig): Promise<{ ok: boolean; evidence: string }> {
  try {
    const out = await run('bash', ['-lc', cfg.verifyCmd], { cwd: cfg.zoeCheckout });
    return { ok: true, evidence: `$ ${cfg.verifyCmd}\n${out}` };
  } catch (err) {
    const e = err as { stdout?: string; stderr?: string; message?: string };
    return {
      ok: false,
      evidence: `$ ${cfg.verifyCmd}\n${e.stdout ?? ''}${e.stderr ?? e.message ?? 'failed'}`,
    };
  }
}

/**
 * Open a PR. Returns the PR URL.
 *
 * IMPORTANT: this opens, it never merges. The human reviews by hand.
 */
export async function openPr(
  cfg: SpikeConfig,
  branch: string,
  title: string,
  body: string,
): Promise<string> {
  const url = await run(
    'gh',
    [
      'pr',
      'create',
      '-R',
      cfg.githubRepo,
      '--base',
      'main',
      '--head',
      branch,
      '--title',
      title,
      '--body',
      body,
    ],
    { cwd: cfg.zoeCheckout },
  );
  return url;
}
