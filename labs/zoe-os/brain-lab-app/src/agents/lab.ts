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

// ARM B: the live sidecar's tool-use doctrine, condensed from labs/flue-zoe-brain-2x/src/doctrines.ts
// ACTIVATOR_DOCTRINE (the lines that do not depend on its activate_abilities tool): "You have NO ... knowledge of
// your own. The ONLY way to know or do anything in those areas is to call a tool ... you MUST use a tool FIRST" and
// "Act proactively - don't ask a clarifying question first when the request is clear enough to act on."
export const SIDECAR_TOOL_DOCTRINE =
  ' You have NO list, alarm or reminder knowledge or ability of your own. None. The ONLY way to add to a list, ' +
  'set an alarm or set a reminder is to call a tool, and you MUST call it FIRST, before saying anything about it. ' +
  "Act proactively: do not ask a clarifying question first when the request is clear enough to act on " +
  '("add milk" -> add it). Do not claim you cannot do something until a tool has actually tried and failed.';

const tools = labTools();
const instructions = LAB_INSTRUCTIONS + ((process.env.OSLAB_POLICY ?? '') === 'sidecar' ? SIDECAR_TOOL_DOCTRINE : '');

export function Lab(): string {
  useModel(LAB_MODEL_SPECIFIER, { compaction: false });
  for (const tool of tools) useTool(tool);
  return instructions;
}

Lab.agentName = 'lab';
Lab.durability = { maxAttempts: 1, timeoutMs: 120_000 };
