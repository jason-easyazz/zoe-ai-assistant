/**
 * The harness workflow: scout -> implement -> verify -> openPR, in Flue's real
 * shape. Discovered by the CLI as the `harness` workflow.
 *
 * Run it:  flue run harness --input '{"issue": 715}'
 *
 * Flue's durability unit is THIS workflow (one bound agent + one action). The
 * phases are expressed as: the bound orchestrator agent (writable `local()`
 * sandbox over the Zoe checkout) delegating the read-only phases to the scout and
 * verifier SUBAGENTS via `session.task({ agent })`, with the deterministic git/PR
 * mechanics done in code. No `step.run()` — that helper does not exist in Flue.
 *
 * LAB ONLY: opens a PR, never merges. The harness model is the `openrouter/*`
 * model registered in ../src/app.ts, never the `:11434` voice brain.
 */
import { defineWorkflow } from '@flue/runtime';
import * as v from 'valibot';
import { loadConfig } from '../config.ts';
import { buildOrchestrator } from '../roles.ts';
import {
  captureDiff,
  commitAndPush,
  createBranch,
  fetchIssue,
  openPr,
  runVerify,
} from '../github.ts';

const cfg = loadConfig();

/** Cap text length so a huge log can't overflow the PR body. */
function capText(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max)}\n…[truncated]` : s;
}

/**
 * Best-effort redaction of obvious secrets before verify output is published to a
 * public PR. Masks the value after `KEY/TOKEN/SECRET/PASSWORD/AUTH...=` or `: `,
 * and anything that looks like a bearer/sk- token. Defence-in-depth, not a
 * guarantee — the operator still controls VERIFY_CMD.
 */
function redactSecrets(s: string): string {
  return s
    .replace(/\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH)[A-Z0-9_]*)\s*[=:]\s*\S+/gi, '$1=***')
    .replace(/\b(sk-[A-Za-z0-9-]{8,}|Bearer\s+\S+|gh[pousr]_[A-Za-z0-9]{8,})/g, '***');
}

export default defineWorkflow({
  agent: buildOrchestrator(cfg.zoeCheckout),
  input: v.object({ issue: v.optional(v.number()) }),
  output: v.object({ prUrl: v.string(), verdict: v.string() }),

  async run({ harness, input, log }) {
    const issueNumber = input?.issue ?? cfg.targetIssue;
    if (!Number.isInteger(issueNumber) || issueNumber <= 0) {
      throw new Error(
        'No issue to work on. Pass --input \'{"issue": <n>}\' or set TARGET_ISSUE in .env.',
      );
    }
    const issue = await fetchIssue({ ...cfg, targetIssue: issueNumber });
    log.info('scouting issue', { issue: issue.number, title: issue.title });

    // Fresh branch off origin/main in the checkout the agent sandbox edits.
    const branch = `flue-spike/issue-${issue.number}-${Date.now()}`;
    await createBranch({ ...cfg, targetIssue: issueNumber }, branch);

    const session = await harness.session();

    // PHASE 1: scout (subagent) — read-only reasoning -> a plan.
    const plan = (
      await session.task(
        [
          `Scout GitHub issue #${issue.number} in ${cfg.githubRepo}.`,
          `Title: ${issue.title}`,
          '',
          issue.body || '(no body)',
          '',
          'Produce the minimal change plan.',
        ].join('\n'),
        { agent: 'scout' },
      )
    ).text;
    log.info('scout produced plan', { chars: plan.length });

    // PHASE 2: implement — the orchestrator agent edits files in its sandbox.
    const commitSubject =
      (
        await session.prompt(
          [
            `Plan:\n${plan}`,
            '',
            `Apply the smallest change in the checkout on branch ${branch}.`,
            'Reply with ONLY a conventional-commit subject line.',
          ].join('\n'),
        )
      ).text.trim() || `spike: address issue #${issue.number}`;

    // PHASE 3: verify FIRST, then capture the diff — so the reviewed/committed
    // diff includes anything the verify command writes (formatters, snapshots,
    // generated files). Capturing before verify would let those slip past review.
    const verify = await runVerify(cfg);
    const diff = await captureDiff(cfg);
    if (!diff.stat.trim()) {
      throw new Error('IMPLEMENT produced no diff; stopping loudly at the phase boundary.');
    }

    // Redact obvious secrets and cap size before any verify output is published.
    const safeEvidence = capText(redactSecrets(verify.evidence), 8 * 1024);
    const verdict = (
      await session.task(
        [
          `Diff (stat):\n${diff.stat}`,
          '',
          `Verify command exit ok: ${verify.ok}`,
          `Verify output:\n${safeEvidence}`,
          '',
          'PASS or FAIL?',
        ].join('\n'),
        { agent: 'verifier' },
      )
    ).text.trim();
    log.info('verifier verdict', { verdict: verdict.slice(0, 80), verifyOk: verify.ok });

    // Gate on BOTH the actual verify exit code AND the model verdict — a mistaken
    // or prompt-influenced "PASS" must not publish code that failed the command.
    if (!verify.ok || !/^PASS/i.test(verdict)) {
      throw new Error(
        `VERIFY did not pass; not opening a PR.\nVerify command ok: ${verify.ok}\nVerdict: ${verdict}`,
      );
    }

    // PHASE 4: openPR (deterministic; never merges).
    await commitAndPush(cfg, branch, commitSubject);
    // Redact the PATCH too before it goes into the public PR body — not just the
    // verify evidence. A hostile issue can steer the write-capable agent into
    // writing an API key / .env value into a tracked file; captureDiff() then
    // stages it, and publishing the raw diff would leak it before human review.
    // Same defence-in-depth as safeEvidence (not a guarantee — operator owns the
    // sandbox/VERIFY_CMD), applied consistently to every secret-bearing surface.
    const redactedPatch = redactSecrets(diff.patch);
    const cappedPatch =
      redactedPatch.length > 48 * 1024
        ? `${redactedPatch.slice(0, 48 * 1024)}\n…[truncated; see GitHub diff view]`
        : redactedPatch;
    const body = [
      `Autonomous Flue-harness spike — issue #${issue.number}.`,
      '',
      `**Scout plan**\n\n${plan}`,
      '',
      `**Diff (stat)**\n\n\`\`\`\n${diff.stat}\n\`\`\``,
      '',
      `**Diff (patch)**\n\n<details><summary>show patch</summary>\n\n\`\`\`diff\n${cappedPatch}\n\`\`\`\n\n</details>`,
      '',
      `**Verify evidence**\n\n\`\`\`\n${safeEvidence}\n\`\`\``,
      '',
      `**Verifier verdict**\n\n${verdict}`,
      '',
      '> Generated by labs/flue-harness-spike (lab-only). Do not auto-merge.',
    ].join('\n');
    const prUrl = await openPr(cfg, branch, commitSubject, body);
    log.info('opened PR', { prUrl });

    return { prUrl, verdict };
  },
});
