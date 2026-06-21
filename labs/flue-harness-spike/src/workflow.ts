/**
 * The durable workflow: scout -> implement -> verify -> openPR.
 *
 * This is the minimal slice of the workos/case PATTERN
 * (scout -> implementer -> verifier -> reviewer -> closer -> retrospective).
 * We implement the first four steps; the last three are deliberately omitted for
 * the spike. The PATTERN is reproduced from scratch — no case code is copied.
 *
 * Each phase is a durable STEP: Flue checkpoints progress so that if a phase
 * fails, the run stops *at that phase boundary* (visible in logs) instead of
 * silently. That "fail loudly at the roadblock" property is exactly what we want
 * to evaluate for Zoe's harness.
 *
 * FLUE-API: `defineWorkflow` + the `step.run(...)` durable-step helper are
 * written against the documented Flue durable-execution shape. Confirm the exact
 * names/signature against the installed @flue/runtime on first install and adjust
 * (see ../FINDINGS.md).
 *
 * LAB ONLY.
 */
// FLUE-API: confirm import path/name.
import { defineWorkflow } from '@flue/runtime';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import type { SpikeConfig } from './config.ts';
import type { SpikeAgents } from './agents.ts';
import {
  fetchIssue,
  createBranch,
  commitAndPush,
  openPr,
} from './github.ts';

const exec = promisify(execFile);

/** Captured evidence handed from phase to phase. */
interface ScoutResult {
  issueTitle: string;
  issueUrl: string;
  plan: string;
}
interface ImplementResult {
  branch: string;
  commitSubject: string;
  diff: string;
}
interface VerifyResult {
  passed: boolean;
  evidence: string;
  reasoning: string;
}

export interface SpikeDeps {
  cfg: SpikeConfig;
  agents: SpikeAgents;
}

/**
 * Build the durable workflow. `deps` carries the config + the local-LLM-bound
 * agents so the workflow body stays declarative.
 */
export function buildSpikeWorkflow(deps: SpikeDeps) {
  const { cfg, agents } = deps;

  // FLUE-API: defineWorkflow signature (name + handler({ step })) is the
  // expected durable-execution shape; verify against @flue/runtime.
  return defineWorkflow({
    name: 'zoe-harness-spike',
    async run({ step }: { step: { run<T>(id: string, fn: () => Promise<T>): Promise<T> } }) {
      // ---- PHASE 1: SCOUT -------------------------------------------------
      const scouted = await step.run<ScoutResult>('scout', async () => {
        const issue = await fetchIssue(cfg);
        // FLUE-API: agent invocation API (agent.run / .invoke) — confirm name.
        const plan = await (agents.scout as any).run({
          input:
            `Issue #${issue.number}: ${issue.title}\n\n${issue.body}\n\n` +
            `Repo checkout: ${cfg.zoeCheckout}\n` +
            `Produce the minimal-change plan.`,
        });
        return {
          issueTitle: issue.title,
          issueUrl: issue.url,
          plan: typeof plan === 'string' ? plan : JSON.stringify(plan),
        };
      });

      // ---- PHASE 2: IMPLEMENT --------------------------------------------
      const implemented = await step.run<ImplementResult>('implement', async () => {
        const branch = `spike/harness-issue-${cfg.targetIssue}-${Date.now()}`;
        await createBranch(cfg, branch);

        // FLUE-API: the implementer is expected to apply edits to files in
        // cfg.zoeCheckout via Flue's file-edit tools. Confirm tool wiring.
        const out = await (agents.implementer as any).run({
          input:
            `Plan:\n${scouted.plan}\n\n` +
            `Apply the smallest change in checkout ${cfg.zoeCheckout} on ` +
            `branch ${branch}. Return only a conventional-commit subject.`,
        });
        const commitSubject =
          (typeof out === 'string' ? out : out?.subject)?.trim() ||
          `spike: address issue #${cfg.targetIssue}`;

        const { stdout: diff } = await exec('git', ['diff', '--stat', 'HEAD'], {
          cwd: cfg.zoeCheckout,
          maxBuffer: 16 * 1024 * 1024,
        });
        if (!diff.trim()) {
          // Loud failure at the phase boundary — implementer produced no diff.
          throw new Error('IMPLEMENT phase produced no diff; stopping.');
        }
        return { branch, commitSubject, diff };
      });

      // ---- PHASE 3: VERIFY ----------------------------------------------
      const verified = await step.run<VerifyResult>('verify', async () => {
        let evidence: string;
        let cmdOk = true;
        try {
          const { stdout, stderr } = await exec('bash', ['-lc', cfg.verifyCmd], {
            cwd: cfg.zoeCheckout,
            maxBuffer: 16 * 1024 * 1024,
          });
          evidence = `$ ${cfg.verifyCmd}\n${stdout}${stderr}`;
        } catch (err: any) {
          cmdOk = false;
          evidence = `$ ${cfg.verifyCmd}\n[exit ${err?.code ?? '?'}]\n${err?.stdout ?? ''}${err?.stderr ?? ''}`;
        }

        // FLUE-API: confirm agent invocation API.
        const verdict = await (agents.verifier as any).run({
          input:
            `Diff:\n${implemented.diff}\n\nVerify output:\n${evidence}\n\n` +
            `Respond starting with PASS or FAIL.`,
        });
        const verdictText = typeof verdict === 'string' ? verdict : JSON.stringify(verdict);
        const passed = cmdOk && /^\s*PASS/i.test(verdictText);
        return { passed, evidence, reasoning: verdictText };
      });

      // ---- PHASE 4: OPEN PR ---------------------------------------------
      const prUrl = await step.run<string>('openPR', async () => {
        if (!verified.passed) {
          // Stop loudly rather than open a PR on un-verified work.
          throw new Error(
            `VERIFY did not pass; not opening a PR.\nReasoning: ${verified.reasoning}`,
          );
        }
        await commitAndPush(cfg, implemented.branch, implemented.commitSubject);

        const body = [
          `Autonomous harness spike — issue #${cfg.targetIssue}.`,
          '',
          `**Scout plan**\n\n${scouted.plan}`,
          '',
          `**Diff (stat)**\n\n\`\`\`\n${implemented.diff}\n\`\`\``,
          '',
          `**Verify evidence**\n\n\`\`\`\n${verified.evidence}\n\`\`\``,
          '',
          `**Verifier verdict**\n\n${verified.reasoning}`,
          '',
          '> Generated by labs/flue-harness-spike (lab-only). Do not auto-merge.',
        ].join('\n');

        return openPr(
          cfg,
          implemented.branch,
          `[spike] ${implemented.commitSubject}`,
          body,
        );
      });

      return { prUrl, issueUrl: scouted.issueUrl };
    },
  });
}
