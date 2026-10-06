/**
 * Household persona + member mode on the live brain lane (flag-dark `ZOE_PERSONA_LAYER`,
 * owned by zoe-data `persona_layer.py`) - the sidecar half of the seam.
 *
 * WHY THE SIDECAR NEEDS A SEAM AT ALL. The persona is DATA the household reads and resets
 * (`household_persona` + `member_modes`, in zoe-data's database), rendered deterministically
 * by `persona_layer.render_persona_block`. The fixed persona paragraphs live HERE
 * (src/soul.ts), in the system prompt this brain assembles, so a zoe-data-side swap never
 * reached this lane: with the flag on the system prompt was byte-identical to flag off.
 *
 * HOW IT TRAVELS. zoe-data renders the block per turn (flag read per call, the member's own
 * mode, the household policy: guests and synthetic ids get the household tone and no
 * relationship mode, a minor keeps the fixed persona until the governance note's crisis path
 * ships) and forwards it on a machine-readable envelope line, exactly as the acting
 * identity, replay marker and speculative-turn id travel (Flue's payload schema drops every
 * body field except the message):
 *
 *   " zoe-spec:<turn_id>\n zoe-replay:1\n zoe-persona:<JSON string>\n zoe-uid:<id>\n<blocks>\n<words>"
 *
 * The envelope is OPTIONAL and ABSENT whenever the flag is off, nothing is loaded, or the
 * member is held on the fixed persona: absent = `ZOE_INSTRUCTIONS` byte-for-byte (pinned by
 * test/persona_layer.test.ts). Present, `applyPolicies` strips it (the model never sees the
 * envelope) and swaps `ZOE_PERSONA_FIXED` for the block in the system prompt of THAT turn.
 * It is read from the newest user message on every model round, so every tool round of a
 * turn sees the same prompt; stored older messages carry their own turn's envelope, which
 * is stripped and ignored. Nothing is bound to a signal or cached: a member's mode can
 * never be carried from one turn to another member's.
 *
 * SAFETY. The block is validated here as well as at zoe-data's write time: a string, at
 * most `PERSONA_MAX_CHARS` (400 tokens at chars/4 - the live prompt's slot budget), no
 * control characters, and it must open with "You are Zoe" (the identity anchor). Anything
 * else is ignored and the fixed persona stands, loudly once. A malformed envelope is
 * still stripped so it can never reach the model as text.
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import type { Context } from '@earendil-works/pi-ai';
import { ZOE_PERSONA_FIXED, ZOE_PERSONA_KEEP } from './soul.ts';

export const PERSONA_ENVELOPE_PREFIX = ' zoe-persona:';
const PERSONA_ENVELOPE_RE = new RegExp('^ zoe-persona:([^\\n]*)\\n');

// 400 tokens at the repo chars/4 convention. The renderer's own cap is 175 tokens (700 chars).
export const PERSONA_MAX_TOKENS = 400;
export const PERSONA_MAX_CHARS = PERSONA_MAX_TOKENS * 4;
// Every rendered block opens with the identity anchor (persona_layer._FIXED_LEAD).
const PERSONA_ANCHOR = 'You are Zoe';
// Control characters and DEL, except the newline (0x0a): the block's own line separator.
const CONTROL_RE = new RegExp('[\\u0000-\\u0009\\u000b-\\u001f\\u007f]');

let warnedInvalid = false;
let warnedMissingFixed = false;

function rejected(why: string): string {
  if (!warnedInvalid) {
    warnedInvalid = true;
    console.warn('PERSONA envelope ignored (' + why + '); the fixed persona stands');
  }
  return '';
}

// Mirror of the seam wrap (tests, parity). An empty block returns the message unchanged.
export function wrapMessageWithPersona(message: string, block: string): string {
  if (!block) return message;
  return PERSONA_ENVELOPE_PREFIX + JSON.stringify(block) + '\n' + message;
}

// The block an envelope payload carries, or '' when it is not an acceptable block.
export function parsePersonaPayload(payload: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(payload);
  } catch {
    return rejected('not JSON');
  }
  if (typeof parsed !== 'string') return rejected('not a string');
  const block = parsed.trim();
  if (!block) return '';
  if (block.length > PERSONA_MAX_CHARS) return rejected('over ' + PERSONA_MAX_CHARS + ' chars');
  if (CONTROL_RE.test(block)) return rejected('control characters');
  if (!block.startsWith(PERSONA_ANCHOR)) return rejected('missing identity anchor');
  return block;
}

type TextPart = { type?: string; text?: unknown };

function isTextPart(part: unknown): part is TextPart {
  if (!part) return false;
  if (typeof part !== 'object') return false;
  return (part as TextPart).type === 'text';
}

function firstText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    for (const part of content) {
      if (!isTextPart(part)) continue;
      if (typeof part.text === 'string') return part.text;
    }
  }
  return '';
}

// The block on the LAST user message envelope, or empty (no envelope or not acceptable). Pure read.
export function forwardedPersonaFromMessages(
  messages: { role: string; content: unknown }[],
): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role !== 'user') continue;
    const m = firstText(msg.content).match(PERSONA_ENVELOPE_RE);
    return m ? parsePersonaPayload(m[1] ?? '') : '';
  }
  return '';
}

function stripFromContent(content: unknown): unknown {
  if (typeof content === 'string') return content.replace(PERSONA_ENVELOPE_RE, '');
  if (!Array.isArray(content)) return content;
  let touched = false;
  const parts = content.map((part) => {
    if (!isTextPart(part)) return part;
    if (typeof part.text !== 'string') return part;
    const next = part.text.replace(PERSONA_ENVELOPE_RE, '');
    if (next === part.text) return part;
    touched = true;
    return { ...part, text: next };
  });
  return touched ? parts : content;
}

// Copy of the messages with the envelope stripped from every user message (same array when unchanged).
export function stripPersonaEnvelope<T extends { role: string; content: unknown }>(messages: T[]): T[] {
  let changed = false;
  const out = messages.map((msg) => {
    if (msg.role !== 'user') return msg;
    const stripped = stripFromContent(msg.content);
    if (stripped === msg.content) return msg;
    changed = true;
    return { ...msg, content: stripped };
  });
  return changed ? out : messages;
}

// What replaces the fixed persona paragraphs: the block, then the two soul paragraphs it does not carry.
export function personaReplacement(block: string): string {
  return block + '\n\n' + ZOE_PERSONA_KEEP;
}

/**
 * The system prompt with the fixed persona paragraphs swapped for the block. An empty block
 * returns the SAME Context object (flag off, or nothing forwarded: today prompt, no
 * allocation). A prompt that no longer contains the fixed paragraphs (a soul edit that moved
 * them) degrades to today persona, loudly once - the swap never guesses.
 */
export function withPersonaBlock(context: Context, block: string): Context {
  if (!block) return context;
  const system = context.systemPrompt ?? '';
  const at = system.indexOf(ZOE_PERSONA_FIXED);
  if (at < 0) {
    if (!warnedMissingFixed) {
      warnedMissingFixed = true;
      console.warn('PERSONA fixed paragraphs not found in the system prompt; ZOE_PERSONA_LAYER has no effect until re-pinned');
    }
    return context;
  }
  const swapped = system.slice(0, at) + personaReplacement(block) + system.slice(at + ZOE_PERSONA_FIXED.length);
  return { ...context, systemPrompt: swapped };
}
