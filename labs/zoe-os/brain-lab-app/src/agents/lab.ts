'use agent';
/**
 * The lab brain agent: a Flue-hosted Pi agent whose only tools are the kernel bridge's. The
 * instruction text is short on purpose (a small prompt keeps the household's one llama slot busy
 * for as little time as possible) and tells the model to repeat the kernel's sentence, not embroider it.
 */
import { useModel, useTool } from '@flue/runtime';
import { LAB_MODEL_SPECIFIER } from '../provider.ts';
import { labTools } from '../tools.ts';

export const LAB_INSTRUCTIONS =
  'You are Zoe, a household assistant, running a lab check. You can act ONLY through the tools you are given. ' +
  'When the person asks you to add something to a list, set a reminder, or set an alarm, call the matching tool once. ' +
  'The tool answers with one sentence. Tell the person that sentence in your own short words and add nothing it did not say. ' +
  'If the tool says it could not do something, say so plainly; never claim it was done. ' +
  'If the request needs no tool, answer in one short sentence.';

const tools = labTools();

export function Lab(): string {
  useModel(LAB_MODEL_SPECIFIER, { compaction: false });
  for (const tool of tools) useTool(tool);
  return LAB_INSTRUCTIONS;
}

Lab.agentName = 'lab';
Lab.durability = { maxAttempts: 1, timeoutMs: 120_000 };
