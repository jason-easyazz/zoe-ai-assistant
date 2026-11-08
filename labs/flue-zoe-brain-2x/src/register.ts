/**
 * The member register line on the live brain lane (flag-dark `ZOE_MEMBER_REGISTER`, owned by zoe-data `member_register.py`) - the
 * sidecar half of the seam. Same shape as the manner block (src/manner.ts).
 *
 * WHAT IT IS. One short line (at most 100 tokens) saying how Zoe speaks with THIS person: length, formality, humour, pace, liked
 * topics (stored per member, stated or confirmed only), a late-night quiet line, or - for a child - a fixed kid-safe register.
 * zoe-data owns the words, the store and the who-gets-what decision; the sidecar only places what it was sent.
 *
 * HOW IT TRAVELS. One machine-readable envelope line, JSON-encoded, between the manner and identity lines:
 *
 *   " zoe-spec:<id>\n zoe-replay:1\n zoe-persona:<JSON>\n zoe-manner:<JSON>\n zoe-register:<JSON string>\n zoe-uid:<id>\n<blocks>\n<words>"
 *
 * ABSENT whenever the flag is off, the member is a guest or unknown, a lookup failed, or the wire is 1.x: absent = the system prompt
 * exactly as it is today. Present, `applyPolicies` strips it (the model never sees the envelope) and appends the line to the system
 * prompt of THAT turn - after the doctrines, persona and manner block, BEFORE the user-model card - so the bytes up to it are the
 * stable cached prefix, identical for every member. Never a mid-conversation message (the Gemma template folds system text into
 * the first user turn).
 *
 * SAFETY. The payload is validated: a JSON string, at most `REGISTER_MAX_CHARS`, no control characters except newline. Anything
 * else is ignored (no line), loudly once, and the line is still stripped so it can never reach the model as text.
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import type { Context } from '@earendil-works/pi-ai';

export const REGISTER_ENVELOPE_PREFIX = ' zoe-register:';
const REGISTER_ENVELOPE_RE = new RegExp('^ zoe-register:([^\\n]*)\\n');

// 120 tokens at the repo chars/4 convention; zoe-data's own budget for the line is 100 tokens.
export const REGISTER_MAX_TOKENS = 120;
export const REGISTER_MAX_CHARS = REGISTER_MAX_TOKENS * 4;
const CONTROL_RE = new RegExp('[\\u0000-\\u0009\\u000b-\\u001f\\u007f]');

let warnedInvalid = false;

function rejected(why: string): string {
  if (!warnedInvalid) {
    warnedInvalid = true;
    console.warn('REGISTER envelope ignored (' + why + '); no register line');
  }
  return '';
}

// Mirror of the seam wrap (tests, parity). An empty block returns the message unchanged.
export function wrapMessageWithRegister(message: string, block: string): string {
  if (!block) return message;
  return REGISTER_ENVELOPE_PREFIX + JSON.stringify(block) + '\n' + message;
}

// The block an envelope payload carries, or '' when it is not an acceptable block.
export function parseRegisterPayload(payload: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(payload);
  } catch {
    return rejected('not JSON');
  }
  if (typeof parsed !== 'string') return rejected('not a string');
  const block = parsed.trim();
  if (!block) return '';
  if (block.length > REGISTER_MAX_CHARS) return rejected('over ' + REGISTER_MAX_CHARS + ' chars');
  if (CONTROL_RE.test(block)) return rejected('control characters');
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
export function forwardedRegisterFromMessages(messages: { role: string; content: unknown }[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role !== 'user') continue;
    const m = firstText(msg.content).match(REGISTER_ENVELOPE_RE);
    return m ? parseRegisterPayload(m[1] ?? '') : '';
  }
  return '';
}

function stripFromContent(content: unknown): unknown {
  if (typeof content === 'string') return content.replace(REGISTER_ENVELOPE_RE, '');
  if (!Array.isArray(content)) return content;
  let touched = false;
  const parts = content.map((part) => {
    if (!isTextPart(part)) return part;
    if (typeof part.text !== 'string') return part;
    const next = part.text.replace(REGISTER_ENVELOPE_RE, '');
    if (next === part.text) return part;
    touched = true;
    return { ...part, text: next };
  });
  return touched ? parts : content;
}

// Copy of the messages with the envelope stripped from every user message (same array when unchanged).
export function stripRegisterEnvelope<T extends { role: string; content: unknown }>(messages: T[]): T[] {
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

/** The system suffix for a block; '' for none (prompt unchanged). */
export function registerSuffix(block: string): string {
  return block ? '\n\n' + block : '';
}

/** Append the block to the system prompt; '' returns the SAME Context object (today's prompt, no allocation). */
export function withRegisterBlock(context: Context, block: string): Context {
  return block ? { ...context, systemPrompt: (context.systemPrompt ?? '') + registerSuffix(block) } : context;
}
