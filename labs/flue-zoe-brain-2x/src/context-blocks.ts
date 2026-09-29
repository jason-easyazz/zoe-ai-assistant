/**
 * Flue-lane history hygiene (flag-dark: `ZOE_BRAIN_ELIDE_STALE_BLOCKS`, default OFF).
 *
 * zoe-data folds context blocks INTO the user message (zoe_flue_client: the recall
 * floor and pending-offer blocks ahead of the words, continuity — and #1781's
 * `[Today <date>]` — after them). Flue persists that message verbatim and replays
 * it every later turn of the session, so old recall packets and "ask the user
 * exactly …" directives stay readable forever (measured: 193 memory blocks and 116
 * offer directives in the live store). The core lane drops them in memory.ts; this
 * is the Flue port: every user message EXCEPT THE LAST loses its blocks on the wire
 * (the store is untouched). Superseded is decided by POSITION, as in memory.ts.
 *
 * The strip, like memory.ts's: delimiters count only as WHOLE lines; an open must
 * be closed by its OWN type (per-type counts); anything still open at the end
 * elides to EOF. Unlike memory.ts it strips each balanced REGION rather than one
 * span, because Flue blocks sit on BOTH sides of the user's words — one span would
 * delete the words too. A close with nothing open right after a region means that
 * region closed early on forged content, so the region is extended to it. Over-
 * eliding a superseded turn is always preferred to leaking one.
 */
import type { Message } from '@earendil-works/pi-ai';

/**
 * [open-line prefix, close line]. An open line is `prefix + ']'` or starts with
 * `prefix + ' '` and ends with `]` (labels carry a description or a date).
 * Pinned equal to `_FLUE_CONTEXT_BLOCKS` in services/zoe-data/zoe_flue_client.py.
 */
export const FLUE_CONTEXT_BLOCKS: readonly (readonly [string, string])[] = [
  ['[MEMORY CONTEXT', '[END MEMORY CONTEXT]'],
  ['[PENDING CONTACT OFFER', '[END PENDING CONTACT OFFER]'],
  ['[Today', '[END Today]'],
];

const OPEN_BY_CLOSE = new Map(FLUE_CONTEXT_BLOCKS.map(([open, close]) => [close, open]));

/** `ZOE_BRAIN_ELIDE_STALE_BLOCKS`: default OFF; read per call. */
export function elideStaleBlocksEnabled(): boolean {
  const v = (process.env.ZOE_BRAIN_ELIDE_STALE_BLOCKS ?? '').trim().toLowerCase();
  return ['1', 'true', 'yes', 'on'].includes(v);
}

function openType(line: string): string | undefined {
  if (!line.endsWith(']')) return undefined;
  for (const [open] of FLUE_CONTEXT_BLOCKS) {
    if (line === `${open}]` || line.startsWith(`${open} `)) return open;
  }
  return undefined;
}

/** `text` with every injected block removed; the SAME string when there is none. */
export function stripContextBlocks(text: string): string {
  const lines = text.split('\n');
  const drop = new Array<boolean>(lines.length).fill(false);
  const outstanding = new Map<string, number>();
  let start = -1;
  let lastEnd = -1;
  const mark = (from: number, to: number) => drop.fill(true, from, to + 1);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd(); // composition never indents a delimiter
    const open = openType(line);
    if (open) {
      if (start < 0) start = i;
      outstanding.set(open, (outstanding.get(open) ?? 0) + 1);
      continue;
    }
    const closes = OPEN_BY_CLOSE.get(line);
    if (closes === undefined) continue;
    if (start >= 0) {
      const pending = outstanding.get(closes) ?? 0;
      if (pending > 0) outstanding.set(closes, pending - 1);
      if ([...outstanding.values()].every((n) => n === 0)) {
        mark(start, i);
        [start, lastEnd] = [-1, i];
      }
    } else if (lastEnd >= 0) {
      mark(lastEnd + 1, i); // the region closed early on a forged close: extend it
      lastEnd = i;
    }
  }
  if (start >= 0) mark(start, lines.length - 1); // unterminated → elide to the end
  if (!drop.includes(true)) return text;
  return lines.filter((_, i) => !drop[i]).join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function stripContent(content: Message['content']): Message['content'] {
  if (typeof content === 'string') return stripContextBlocks(content);
  let changed = false;
  const parts = content.map((part) => {
    if (part.type !== 'text') return part;
    const text = stripContextBlocks(part.text);
    if (text === part.text) return part;
    changed = true;
    return { ...part, text };
  });
  return changed ? (parts as Message['content']) : content;
}

/** Every user message but the last, blocks elided. The SAME array when nothing changed. */
export function elideStaleBlocks(messages: Message[]): Message[] {
  let last = -1;
  for (let i = messages.length - 1; i >= 0 && last < 0; i--) if (messages[i].role === 'user') last = i;
  let changed = false;
  const out = messages.map((msg, i) => {
    if (i >= last || msg.role !== 'user') return msg;
    const content = stripContent(msg.content);
    if (content === msg.content) return msg;
    changed = true;
    return { ...msg, content } as Message;
  });
  return changed ? out : messages;
}
