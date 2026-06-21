/**
 * Entry point — wires the local provider + agents + durable workflow together
 * and runs the spike once.
 *
 * Run with: `npm run spike`  (after `cp .env.example .env` and editing it).
 *
 * LAB ONLY. Do not run on the production Jetson. See ../RUNBOOK.md.
 */
import { loadConfig } from './config.ts';
import { registerLocalLlm } from './provider.ts';
import { buildAgents } from './agents.ts';
import { buildSpikeWorkflow } from './workflow.ts';

async function main(): Promise<void> {
  const cfg = loadConfig();
  console.log(
    `[spike] repo=${cfg.githubRepo} issue=#${cfg.targetIssue} llm=${cfg.llmBaseUrl}`,
  );

  // 1. Point Flue at the local llama.cpp OpenAI-compatible endpoint.
  const model = registerLocalLlm(cfg);
  console.log(`[spike] registered local provider -> model="${model}"`);

  // 2. Build the role-specialized subagents bound to that local model.
  const agents = buildAgents(model);

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
