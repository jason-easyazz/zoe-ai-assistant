/**
 * The synthetic phase worker — a REAL Flue workflow (validated input/output,
 * app boot, run events) that stands in for a phase agent so the executor's
 * claim -> spawn -> report -> reap contract can be proven end-to-end without a
 * model or network. A real phase (scout/implement/verify...) plugs in here by
 * giving the agent a harness model + `local()` sandbox over the task worktree,
 * exactly as labs/flue-harness-spike/src/roles.ts already does.
 *
 * Modes:
 *   succeed — write a proof file into the task work_dir, report ok.
 *   fail    — write the proof file, report not-ok (exercises failure reporting).
 *   hang    — never return (exercises kill/reap paths).
 *
 * LAB ONLY.
 */
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { defineAgent, defineWorkflow } from '@flue/runtime';
import * as v from 'valibot';

// Bound agent is required by defineWorkflow but never prompted — the synthetic
// run() is fully deterministic. 'deadend/none' resolves to the dead-end
// provider registered in ../app.ts.
const syntheticAgent = defineAgent(() => ({
  model: 'deadend/none',
  description: 'Synthetic phase worker (never prompted).',
}));

export default defineWorkflow({
  agent: syntheticAgent,
  input: v.object({
    taskId: v.string(),
    phase: v.string(),
    workDir: v.string(),
    mode: v.picklist(['succeed', 'fail', 'hang']),
    resultPath: v.string(),
  }),
  output: v.object({ ok: v.boolean(), summary: v.string() }),

  async run({ input, log }) {
    log.info('synthetic phase worker start', { task: input.taskId, mode: input.mode });
    if (input.mode === 'hang') {
      await new Promise(() => {});
    }
    writeFileSync(
      join(input.workDir, `PROOF-${input.phase}.txt`),
      `task=${input.taskId} phase=${input.phase} worker pid=${process.pid}\n`,
    );
    const result = {
      ok: input.mode === 'succeed',
      summary: `synthetic ${input.phase} worker ${input.mode === 'succeed' ? 'wrote proof file' : 'reported deliberate failure'} in ${input.workDir}`,
    };
    writeFileSync(input.resultPath, JSON.stringify(result));
    return result;
  },
});
