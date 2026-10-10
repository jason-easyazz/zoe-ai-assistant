/**
 * The manner block on the live brain lane (flag-dark `ZOE_MANNER_BLOCK`, owned by zoe-data
 * `manner_block.py`) - the sidecar half of the seam.
 *
 * WHAT IT IS. A counted-behaviours paragraph (reflect before advising, name one specific true
 * thing, one question at a time, no hooks, disagree once kindly, no flattery) of at most 160
 * tokens, held as DATA in zoe-data's `lexicons_data/<lang>.json` (English + Spanish) and sent
 * per turn. The sidecar owns no manner text: it only places what zoe-data decided to send.
 *
 * HOW IT TRAVELS. Exactly as the household persona does (src/persona.ts): one machine-readable
 * envelope line, JSON-encoded so a multi-line block stays on one line, ahead of the identity line:
 *
 *   " zoe-spec:<id>\n zoe-replay:1\n zoe-persona:<JSON>\n zoe-manner:<JSON string>\n zoe-uid:<id>\n<blocks>\n<words>"
 *
 * ABSENT whenever the flag is off, the member is a minor or a guest, a lookup failed, or the wire
 * is 1.x: absent = the system prompt exactly as it is today (pinned by test/manner_block.test.ts).
 * Present, `applyPolicies` strips it (the model never sees the envelope) and appends the block to
 * the system prompt of THAT turn - after the doctrines and the persona, BEFORE the user-model card,
 * so the bytes up to the card are identical for every member and llama-server's prefix cache is
 * shared. It is read from the newest user message on every model round (every tool round of a turn
 * sees one prompt); stored older messages carry their own turn's line, stripped and ignored. It is
 * NEVER a mid-conversation message (the Gemma template folds system text into the first user turn).
 *
 * SAFETY. The payload is validated: a JSON string, at most `MANNER_MAX_CHARS` (200 tokens at
 * chars/4), no control characters except newline. Anything else is ignored (no block), loudly once,
 * and the line is still stripped so it can never reach the model as text.
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import type { Context } from '@earendil-works/pi-ai';

export const MANNER_ENVELOPE_PREFIX = ' zoe-manner:';
const MANNER_ENVELOPE_RE = new RegExp('^ zoe-manner:([^\\n]*)\\n');

// 200 tokens at the repo chars/4 convention; zoe-data's own budget for the block is 160 tokens.
export const MANNER_MAX_TOKENS = 200;
export const MANNER_MAX_CHARS = MANNER_MAX_TOKENS * 4;
const CONTROL_RE = new RegExp('[\\u0000-\\u0009\\u000b-\\u001f\\u007f]');

let warnedInvalid = false;

function rejected(why: string): string {
  if (!warnedInvalid) {
    warnedInvalid = true;
    console.warn('MANNER envelope ignored (' + why + '); no manner block');
  }
  return '';
}

// Mirror of the seam wrap (tests, parity). An empty block returns the message unchanged.
export function wrapMessageWithManner(message: string, block: string): string {
  if (!block) return message;
  return MANNER_ENVELOPE_PREFIX + JSON.stringify(block) + '\n' + message;
}

// The block an envelope payload carries, or '' when it is not an acceptable block.
export function parseMannerPayload(payload: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(payload);
  } catch {
    return rejected('not JSON');
  }
  if (typeof parsed !== 'string') return rejected('not a string');
  const block = parsed.trim();
  if (!block) return '';
  if (block.length > MANNER_MAX_CHARS) return rejected('over ' + MANNER_MAX_CHARS + ' chars');
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
export function forwardedMannerFromMessages(messages: { role: string; content: unknown }[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role !== 'user') continue;
    const m = firstText(msg.content).match(MANNER_ENVELOPE_RE);
    return m ? parseMannerPayload(m[1] ?? '') : '';
  }
  return '';
}

function stripFromContent(content: unknown): unknown {
  if (typeof content === 'string') return content.replace(MANNER_ENVELOPE_RE, '');
  if (!Array.isArray(content)) return content;
  let touched = false;
  const parts = content.map((part) => {
    if (!isTextPart(part)) return part;
    if (typeof part.text !== 'string') return part;
    const next = part.text.replace(MANNER_ENVELOPE_RE, '');
    if (next === part.text) return part;
    touched = true;
    return { ...part, text: next };
  });
  return touched ? parts : content;
}

// Copy of the messages with the envelope stripped from every user message (same array when unchanged).
export function stripMannerEnvelope<T extends { role: string; content: unknown }>(messages: T[]): T[] {
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
export function mannerSuffix(block: string): string {
  return block ? '\n\n' + block : '';
}

/** Append the block to the system prompt; '' returns the SAME Context object (today's prompt, no allocation). */
export function withMannerBlock(context: Context, block: string): Context {
  return block ? { ...context, systemPrompt: (context.systemPrompt ?? '') + mannerSuffix(block) } : context;
}
