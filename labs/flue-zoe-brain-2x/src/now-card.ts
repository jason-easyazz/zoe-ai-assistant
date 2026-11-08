/**
 * The now card (flag-dark `ZOE_MEMORY_NOW_CARD`, owned by zoe-data `now_card.py`; docs/knowledge/now-card.md) - the sidecar half.
 * zoe-data builds the per-member card at night, decides per turn whether the turn may carry it (household, a CONFIRMED voice) and
 * owns every word; the sidecar only places it. One JSON-encoded envelope line, innermost before the identity line:
 *
 *   " zoe-spec:<id>\n zoe-replay:1\n zoe-persona:<JSON>\n zoe-manner:<JSON>\n zoe-nowcard:<JSON string>\n zoe-uid:<id>\n<blocks>\n<words>"
 *
 * Absent = the system prompt exactly as today. Present: `applyPolicies` strips it and appends the card to THAT turn's system prompt
 * AFTER the user-model card (every byte up to the card is unchanged; the card changes at most nightly, so it stays cached). Read
 * from the newest user message only. A payload that is not a JSON string <= NOW_CARD_MAX_CHARS without control characters adds
 * nothing (warned once) and is still stripped.
 */
import type { Context } from '@earendil-works/pi-ai';

export const NOW_CARD_ENVELOPE_PREFIX = ' zoe-nowcard:';
const NOW_CARD_ENVELOPE_RE = new RegExp('^ zoe-nowcard:([^\\n]*)\\n');

// zoe-data caps the card at 180 tokens (chars/4); the wire ceiling leaves headroom.
export const NOW_CARD_MAX_TOKENS = 200;
export const NOW_CARD_MAX_CHARS = NOW_CARD_MAX_TOKENS * 4;
const CONTROL_RE = new RegExp('[\\u0000-\\u0009\\u000b-\\u001f\\u007f]');

let warnedInvalid = false;

function rejected(why: string): string {
  if (!warnedInvalid) {
    warnedInvalid = true;
    console.warn('NOW_CARD envelope ignored (' + why + '); no card');
  }
  return '';
}

// Mirror of the seam wrap (tests, parity). An empty card returns the message unchanged.
export function wrapMessageWithNowCard(message: string, card: string): string {
  if (!card) return message;
  return NOW_CARD_ENVELOPE_PREFIX + JSON.stringify(card) + '\n' + message;
}

// The card an envelope payload carries, or '' when it is not acceptable.
export function parseNowCardPayload(payload: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(payload);
  } catch {
    return rejected('not JSON');
  }
  if (typeof parsed !== 'string') return rejected('not a string');
  const card = parsed.trim();
  if (!card) return '';
  if (card.length > NOW_CARD_MAX_CHARS) return rejected('over ' + NOW_CARD_MAX_CHARS + ' chars');
  if (CONTROL_RE.test(card)) return rejected('control characters');
  return card;
}

type TextPart = { type?: string; text?: unknown };

function firstText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  for (const part of content as TextPart[]) {
    if (part && typeof part === 'object' && part.type === 'text' && typeof part.text === 'string') return part.text;
  }
  return '';
}

// The card on the LAST user message envelope, or ''. Pure read.
export function forwardedNowCardFromMessages(messages: { role: string; content: unknown }[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role !== 'user') continue;
    const m = firstText(messages[i].content).match(NOW_CARD_ENVELOPE_RE);
    return m ? parseNowCardPayload(m[1] ?? '') : '';
  }
  return '';
}

function stripFromContent(content: unknown): unknown {
  if (typeof content === 'string') return content.replace(NOW_CARD_ENVELOPE_RE, '');
  if (!Array.isArray(content)) return content;
  let touched = false;
  const parts = (content as TextPart[]).map((part) => {
    if (!part || typeof part !== 'object' || part.type !== 'text' || typeof part.text !== 'string') return part;
    const next = part.text.replace(NOW_CARD_ENVELOPE_RE, '');
    if (next === part.text) return part;
    touched = true;
    return { ...part, text: next };
  });
  return touched ? parts : content;
}

// Copy of the messages with the envelope stripped from every user message (same array when unchanged).
export function stripNowCardEnvelope<T extends { role: string; content: unknown }>(messages: T[]): T[] {
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

/** Append the card to the system prompt; '' returns the SAME Context object (today's prompt). */
export function withNowCardBlock(context: Context, card: string): Context {
  return card ? { ...context, systemPrompt: (context.systemPrompt ?? '') + '\n\n' + card } : context;
}
