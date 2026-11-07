/**
 * The household conventions line on the live brain lane (zoe-data `household_locale.py`, `ZOE_LOCALE_HINT`) - the sidecar
 * half of the seam.
 *
 * WHY IT IS NOT IN THE USER MESSAGE (#2326 regression, QA round 3b 2026-11-07). zoe-data first folded the line ("This household
 * is in <country>: give measurements in metric, ...") into the `[Today — household context]` block INSIDE the user message, and
 * the 4B brain answered it as something the user had said: "do i have a pet?" -> "...and I'll make sure to use metric and
 * Australian English from now on". It is background about the house, so it belongs in the system prompt.
 *
 * HOW IT TRAVELS. One envelope line, JSON-encoded, the first line after the identity line (innermost; the first line of the
 * message when there is no identity line):
 *
 *   " zoe-persona:<JSON>\n zoe-manner:<JSON>\n zoe-uid:<id>\n zoe-locale:<JSON string>\n<blocks>\n<words>"
 *
 * `applyPolicies` reads it off the NEWEST user message, strips it from EVERY user message (the model never sees the line), and
 * appends it to that turn's system prompt, labelled as background, after the doctrines and the persona and BEFORE the manner
 * block and the user-model card: the line is the same for every member of the house, so the bytes up to the member-specific
 * blocks stay shared and llama-server's prefix cache holds. ABSENT (no country, flag off) = the prompt exactly as before.
 *
 * SAFETY. A JSON string, at most `LOCALE_MAX_CHARS`, no control characters; anything else is ignored (no line), loudly once,
 * and the envelope is still stripped. zoe-data strips a user-typed line from the user's own words.
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */
import type { Context } from '@earendil-works/pi-ai';

export const LOCALE_ENVELOPE_PREFIX = ' zoe-locale:';
const LOCALE_ENVELOPE_RE = new RegExp('^ zoe-locale:([^\\n]*)\\n');

/** zoe-data's own cap is 320 chars (household_locale.MAX_LINE_CHARS); a little headroom for the JSON escape. */
export const LOCALE_MAX_CHARS = 400;
const CONTROL_RE = new RegExp('[\\u0000-\\u001f\\u007f]');

/**
 * The label the line is placed under: background the model applies silently, never something the user said. It also carries
 * the background-context rule for the bracketed blocks zoe-data adds to a message ("I've noted all those details about the
 * household context", QA round 3b). Kept HERE, in this module's own system-prompt slot, rather than in doctrines.ts: the
 * compiled instructions (and their pinned golden hash) stay untouched, and the rule rides every turn that carries the line -
 * every turn on a box with a household country set.
 */
export const LOCALE_LABEL =
  'Background (not something the user said): bracketed notes inside a message ([Today …], [MEMORY CONTEXT …], [PENDING ' +
  'CONTACT OFFER …], [RAISE …]) and the household conventions below come from your own system. Use them quietly: never ' +
  'thank the user for them, say you have noted them, or promise to follow them - answer only what the user actually said. ' +
  'Household conventions:';

let warnedInvalid = false;

function rejected(why: string): string {
  if (!warnedInvalid) {
    warnedInvalid = true;
    console.warn('LOCALE envelope ignored (' + why + '); no conventions line');
  }
  return '';
}

/** Mirror of the seam wrap (tests, parity). An empty line returns the message unchanged. */
export function wrapMessageWithLocale(message: string, line: string): string {
  if (!line) return message;
  return LOCALE_ENVELOPE_PREFIX + JSON.stringify(line) + '\n' + message;
}

/** The line an envelope payload carries, or '' when it is not acceptable. */
export function parseLocalePayload(payload: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(payload);
  } catch {
    return rejected('not JSON');
  }
  if (typeof parsed !== 'string') return rejected('not a string');
  const line = parsed.trim();
  if (!line) return '';
  if (line.length > LOCALE_MAX_CHARS) return rejected('over ' + LOCALE_MAX_CHARS + ' chars');
  if (CONTROL_RE.test(line)) return rejected('control characters');
  return line;
}

type TextPart = { type?: string; text?: unknown };

function isTextPart(part: unknown): part is TextPart {
  return !!part && typeof part === 'object' && (part as TextPart).type === 'text';
}

function firstText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    for (const part of content) {
      if (isTextPart(part) && typeof part.text === 'string') return part.text;
    }
  }
  return '';
}

/** The line on the LAST user message (identity line already stripped), or ''. Pure read. */
export function forwardedLocaleFromMessages(messages: { role: string; content: unknown }[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role !== 'user') continue;
    const m = firstText(msg.content).match(LOCALE_ENVELOPE_RE);
    return m ? parseLocalePayload(m[1] ?? '') : '';
  }
  return '';
}

function stripFromContent(content: unknown): unknown {
  if (typeof content === 'string') return content.replace(LOCALE_ENVELOPE_RE, '');
  if (!Array.isArray(content)) return content;
  let touched = false;
  const parts = content.map((part) => {
    if (!isTextPart(part) || typeof part.text !== 'string') return part;
    const next = part.text.replace(LOCALE_ENVELOPE_RE, '');
    if (next === part.text) return part;
    touched = true;
    return { ...part, text: next };
  });
  return touched ? parts : content;
}

/** Copy of the messages with the envelope stripped from every user message (the same array when unchanged). */
export function stripLocaleEnvelope<T extends { role: string; content: unknown }>(messages: T[]): T[] {
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

/** The system suffix for a line; '' for none. */
export function localeSuffix(line: string): string {
  return line ? '\n\n' + LOCALE_LABEL + '\n' + line : '';
}

/** Append the line to the system prompt; '' returns the SAME Context object. */
export function withLocaleBlock(context: Context, line: string): Context {
  return line ? { ...context, systemPrompt: (context.systemPrompt ?? '') + localeSuffix(line) } : context;
}
