/**
 * Entry point — wires the providers + agents + durable workflow together and
 * runs the spike once.
 *
 * Run with: `npm run spike`  (after `cp .env.example .env` and editing it).
 *
 * LAB ONLY. Runs ON THE JETSON, isolated from the live voice path: the harness
 * agents run on the separate HARNESS_LLM_* model; the voice brain on :11434 is
 * untouched. See ../RUNBOOK.md.
 */
import { loadConfig } from './config.ts';
import { registerProviders } from './provider.ts';
import { buildAgents } from './agents.ts';
import { buildSpikeWorkflow } from './workflow.ts';

async function main(): Promise<void> {
  const cfg = loadConfig();
  console.log(
    `[spike] repo=${cfg.githubRepo} issue=#${cfg.targetIssue} ` +
      `voiceBrain=${cfg.llmBaseUrl} (untouched) harness=${cfg.harnessLlmBaseUrl}`,
  );

  // 1. Register BOTH providers: the live local voice brain (:11434, untouched)
  //    and the configurable harness model. The harness AGENTS run on the latter,
  //    so they never compete with the voice brain for the live GPU slot.
  const { harnessModel } = registerProviders(cfg);
  console.log(`[spike] harness agents bound to model="${harnessModel}"`);

  // 2. Build the role-specialized subagents bound to the harness model.
  const agents = buildAgents(harnessModel);

  // 3. Build + run the durable workflow: scout -> implement -> verify -> openPR.
  const workflow = buildSpikeWorkflow({ cfg, agents });

  // FLUE-API: how a workflow is executed (workflow.run / runtime.start) depends
  // on @flue/runtime. Confirm the run entry point on first install.
  const result = await (workflow as any).run({});

  console.log('\n[spike] DONE');
  console.log(`[spike] issue:  ${result.issueUrl}`);
  console.log(`[spike] PR:     ${result.prUrl}`);
}

main().catch((err) => {
  // Durable steps throw at the phase that failed — surface it plainly.
  console.error('\n[spike] FAILED at a phase boundary:');
  console.error(err?.stack ?? err?.message ?? err);
  process.exit(1);
});
