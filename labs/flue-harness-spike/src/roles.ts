/**
 * Agent roles for the harness pipeline, in Flue's real shape.
 *
 * Flue's durability unit is the WORKFLOW (one bound agent + one action); there is
 * no `case`-style per-step `step.run()` helper. So the pipeline is expressed the
 * Flue way: ONE orchestrator agent that owns a `local()` sandbox over the Zoe
 * checkout, and delegates the read-only reasoning phases to named SUBAGENTS via
 * `session.task({ agent })`. We reproduce the role-decomposition *pattern* of
 * workos/case (scout / implementer / verifier) from scratch — no case code/prompts.
 *
 * All three roles run on the SAME harness model (`HARNESS_LLM_MODEL`, an
 * `openrouter/*` string registered in app.ts), never the `:11434` voice brain.
 * Per-phase models are a one-line change later (give a profile its own `model`).
 *
 * LAB ONLY.
 */
import { defineAgent, defineAgentProfile } from '@flue/runtime';
import { local } from '@flue/runtime/node';

/** The harness model string, e.g. `openrouter/anthropic/claude-3.5-haiku`. */
export function harnessModel(): string {
  return process.env.HARNESS_LLM_MODEL ?? 'openrouter/anthropic/claude-3.5-haiku';
}

/**
 * SCOUT — reads the issue + relevant repo files and writes a concrete plan.
 * Subagent: read-only reasoning, no tools beyond the inherited sandbox view.
 */
export const scout = defineAgentProfile({
  name: 'scout',
  description: 'Reads a GitHub issue and the repo, then writes a minimal change plan.',
  instructions: [
    'You are the SCOUT in an autonomous PR harness.',
    'You are given a GitHub issue and a repo checkout you can read.',
    'Read the issue and the few files most relevant to it.',
    'Produce a SHORT markdown plan: the problem in one line, the single file',
    '(or two) to change, and the minimal, concrete change to make.',
    'Do NOT write code yet. Keep scope as small as possible — a tiny correct',
    'change beats an ambitious one. This is a spike.',
  ].join('\n'),
});

/**
 * VERIFIER — judges whether the change holds, from the diff + verify output only.
 */
export const verifier = defineAgentProfile({
  name: 'verifier',
  description: 'Judges PASS/FAIL of a change from its diff and verify-command output.',
  instructions: [
    'You are the VERIFIER in an autonomous PR harness.',
    'You are given a diff and the output of the repo verify command.',
    'Decide PASS or FAIL based ONLY on that evidence, and explain in one or two',
    'sentences. Be conservative: if the evidence is inconclusive, FAIL so a human',
    'looks. Never invent test output. Start your reply with "PASS" or "FAIL".',
  ].join('\n'),
});

/**
 * ORCHESTRATOR / IMPLEMENTER — the workflow's bound agent. Owns the writable
 * `local()` sandbox over the Zoe checkout (so its edits land on the branch), and
 * delegates to the scout/verifier subagents. `cwd` is the checkout to branch in.
 */
export function buildOrchestrator(cwd: string) {
  return defineAgent(() => ({
    model: harnessModel(),
    description: 'Orchestrates scout -> implement -> verify over a Zoe checkout.',
    instructions: [
      'You are the IMPLEMENTER/ORCHESTRATOR in an autonomous PR harness.',
      'You have a writable checkout (your sandbox cwd) already on a fresh branch.',
      'Given the SCOUT plan, apply the SMALLEST change that satisfies it.',
      'Touch only the files the plan names; do not refactor unrelated code.',
      'Use your shell and file tools to make the edits in the checkout.',
      'When done, reply with a single conventional-commit subject line only.',
    ].join('\n'),
    subagents: [scout, verifier],
    sandbox: local(),
    cwd,
  }));
}
