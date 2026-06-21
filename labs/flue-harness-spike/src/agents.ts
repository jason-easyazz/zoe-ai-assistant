/**
 * Subagent definitions — the specialized roles in the pipeline.
 *
 * We reproduce the *role-decomposition pattern* of workos/case (scout /
 * implementer / verifier / ...) FROM SCRATCH. None of case's prompts or code are
 * copied — these prompts are written fresh for this spike.
 *
 * This slice defines only the first three roles; `openPR` is plain code in
 * workflow.ts (no LLM needed to call `gh`).
 *
 * FLUE-API: `defineAgent` is the documented Flue entry point. Confirm its option
 * names (model / instructions / tools) against the installed @flue/sdk on first
 * install and adjust (see ../FINDINGS.md).
 *
 * LAB ONLY.
 */
// FLUE-API: confirm import path/name.
import { defineAgent } from '@flue/sdk';

/**
 * Build the three subagents bound to the local model string returned by
 * registerLocalLlm(). Passing the model in (rather than reading env here) keeps
 * the local-only seam in one place.
 */
export function buildAgents(model: string) {
  /**
   * SCOUT — reads the issue + relevant repo files and writes a concrete plan.
   * Output contract: a short markdown plan naming the file(s) to touch and the
   * minimal change to make.
   */
  const scout = defineAgent({
    name: 'scout',
    model,
    instructions: [
      'You are the SCOUT in an autonomous PR harness.',
      'You are given a GitHub issue and read-only access to a repo checkout.',
      'Read the issue and the few files most relevant to it.',
      'Produce a SHORT markdown plan: the problem in one line, the single',
      'file (or two) to change, and the minimal, concrete change to make.',
      'Do NOT write code yet. Keep the scope as small as possible — this is a',
      'spike; a tiny correct change beats an ambitious one.',
    ].join('\n'),
    // FLUE-API: tool wiring depends on Flue's tool API. For the spike the
    // implementer does the actual edits; scout only needs read/inspect.
  });

  /**
   * IMPLEMENTER — turns the scout's plan into an actual diff on the branch.
   * Output contract: edits applied to files in the checkout (via Flue's
   * file-edit tools) + a one-line commit subject.
   */
  const implementer = defineAgent({
    name: 'implementer',
    model,
    instructions: [
      'You are the IMPLEMENTER in an autonomous PR harness.',
      "You receive the scout's plan and a writable repo checkout on a fresh",
      'branch. Apply the SMALLEST change that satisfies the plan.',
      'Touch only the files the plan names. Do not refactor unrelated code.',
      'Return a concise conventional-commit subject line for the change.',
    ].join('\n'),
  });

  /**
   * VERIFIER — runs the scoped check and judges whether the change holds.
   * Output contract: a verdict (pass/fail) + the captured command output to
   * embed as PR evidence.
   */
  const verifier = defineAgent({
    name: 'verifier',
    model,
    instructions: [
      'You are the VERIFIER in an autonomous PR harness.',
      'You are given a diff and the output of the repo verify command.',
      'Decide PASS or FAIL based ONLY on that evidence, and explain in one or',
      'two sentences. Be conservative: if the evidence is inconclusive, FAIL',
      'so a human looks. Never invent test output.',
    ].join('\n'),
  });

  return { scout, implementer, verifier };
}

export type SpikeAgents = ReturnType<typeof buildAgents>;
