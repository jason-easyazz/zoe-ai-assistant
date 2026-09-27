/**
 * B1.1 speculative voice turns — the sidecar half of the turn-id echo.
 *
 * A speculative voice turn (zoe-data `voice_speculation`, flag-dark
 * `ZOE_SPECULATIVE_TURN`) runs the brain BEFORE the panel daemon's verdict. Its
 * write tools reach zoe-data as a separate POST /api/system/intent-dispatch, which
 * the turn's server-side context cannot follow. The seam
 * (services/zoe-data/zoe_flue_client.py `_wrap_message_with_speculative_turn`)
 * puts the turn id on an OUTERMOST envelope line; the provider binds it to the
 * turn's AbortSignal (the object pi-agent-core threads to every tool call, see
 * request-identity.ts), `applyPolicies` strips it so the model never sees it, and
 * `dispatchIntent` echoes it as `speculative_turn_id`. zoe-data then holds exactly
 * that turn's writes for its verdict — never another session's.
 *
 * WIRE ORDER (both parsers ^-anchored, stripped outermost first):
 *   " zoe-spec:<turn_id>\n zoe-replay:1\n zoe-uid:<id>\n<blocks>\n<user message>"
 * Absent (every non-speculative turn) → nothing bound, dispatch body unchanged.
 */

/** Turn id per turn, keyed by the turn's AbortSignal; reclaimed with the signal. */
const turnIdBySignal = new WeakMap<AbortSignal, string>();

/** Same charset the seam forwards (voice_speculation._TURN_ID_RE). */
const TURN_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

export const SPECULATIVE_ENVELOPE_PREFIX = ' zoe-spec:';
const SPECULATIVE_ENVELOPE_RE = /^ zoe-spec:([^\n]*)\n/;

/** Bind (or clear, with '') the speculative turn id for the turn owning `signal`. */
export function bindTurnSpeculativeId(signal: AbortSignal | undefined, turnId: string): void {
  if (!signal) return;
  const id = (turnId ?? '').trim();
  if (id && TURN_ID_RE.test(id)) turnIdBySignal.set(signal, id);
  else turnIdBySignal.delete(signal);
}

/** The speculative turn id bound for `signal`, or '' for every ordinary turn. */
export function currentSpeculativeTurnId(signal: AbortSignal | undefined): string {
  if (!signal) return '';
  return turnIdBySignal.get(signal) ?? '';
}

/** Mirror of the seam's wrap (tests / parity). `turnId` empty → unchanged. */
export function wrapMessageWithSpeculativeTurn(message: string, turnId: string): string {
  if (!turnId) return message;
  return `${SPECULATIVE_ENVELOPE_PREFIX}${turnId}\n${message}`;
}

/** The id on the LAST user message's envelope, or ''. Pure read. */
export function forwardedSpeculativeTurnFromMessages(
  messages: { role: string; content: unknown }[],
): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role !== 'user') continue;
    const m = firstText(msg.content).match(SPECULATIVE_ENVELOPE_RE);
    return m ? (m[1] ?? '').trim() : '';
  }
  return '';
}

/** Copy of `messages` with the envelope stripped from every user message (same
 * array reference when nothing changed). */
export function stripSpeculativeEnvelope<T extends { role: string; content: unknown }>(
  messages: T[],
): T[] {
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

function firstText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    for (const part of content) {
      if (part && typeof part === 'object' && (part as { type?: string }).type === 'text') {
        const t = (part as { text?: unknown }).text;
        if (typeof t === 'string') return t;
      }
    }
  }
  return '';
}

function stripFromContent(content: unknown): unknown {
  if (typeof content === 'string') return content.replace(SPECULATIVE_ENVELOPE_RE, '');
  if (Array.isArray(content)) {
    let touched = false;
    const parts = content.map((part) => {
      if (
        part &&
        typeof part === 'object' &&
        (part as { type?: string }).type === 'text' &&
        typeof (part as { text?: unknown }).text === 'string'
      ) {
        const orig = (part as { text: string }).text;
        const next = orig.replace(SPECULATIVE_ENVELOPE_RE, '');
        if (next !== orig) {
          touched = true;
          return { ...part, text: next };
        }
      }
      return part;
    });
    return touched ? parts : content;
  }
  return content;
}
