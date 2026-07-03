/**
 * Per-request acting identity for the Flue Zoe-brain sidecar.
 *
 * WHY THIS EXISTS — Flue's `ToolContext` is only `{ input, signal }` (confirmed
 * in @flue/runtime docs api/agent-api + api/routing-api); there is NO built-in
 * per-request principal a tool can read. So the acting `user_id` has to be
 * threaded app-side, and it must reach the tool as the ONE user the current turn
 * is for — never a process-wide fallback, and never another concurrent user.
 *
 * WHY NOT AsyncLocalStorage — verified on-box (labs/flue-zoe-brain, 2026-07-03)
 * that ALS does NOT work through the `?wait=result` HTTP path and is unsafe under
 * concurrency. Flue admits the turn as a durable "submission" and runs the
 * agent+tool loop inside a per-instance fiber that does NOT inherit the route's
 * ALS store; binding the id at route time went stale, and mutating a shared cell
 * in the provider raced (two concurrent users' turns clobbered each other's id).
 *
 * WHAT ACTUALLY WORKS (proven, incl. a concurrent two-user test) — key the
 * identity by the turn's AbortSignal. pi-agent-core threads ONE `AbortController`
 * per turn: the SAME `signal` object is passed both to the model call (so our
 * provider receives it as `options.signal`) and to every tool execution (so the
 * tool receives it as `ToolContext.signal`) — see
 * node_modules/@earendil-works/pi-agent-core/dist/agent-loop.js (`runLoop` →
 * `streamAssistantResponse` + `executeToolCalls` share `signal`) and
 * @flue/runtime `parseToolInput` (`context: { input, signal }`). We store the
 * acting id in a `WeakMap<AbortSignal, string>`: the provider writes it (from the
 * trusted message envelope) before any tool runs, and the tool reads it by its
 * own `signal`. Because each turn has its own signal object, concurrent turns are
 * independent — no race, no leak — and entries are reclaimed automatically when
 * the signal is GC'd, so nothing grows unbounded.
 *
 * TRUST — the acting id is only ever set from the seam-forwarded value (trusted
 * server code; NOT model-chosen, NOT a tool arg). The route fails closed on auth
 * (ZOE_BRAIN_TOKEN / ZOE_BRAIN_OPEN) so only a trusted caller can drive the agent
 * at all. Outside a turn (unit tests, non-HTTP paths) no signal id is bound and
 * callers fall back to the env — see `actingUserId()` in src/tools/zoe-tools.ts.
 *
 * LAB ONLY.
 */

/**
 * Acting identity for a turn, keyed by that turn's AbortSignal — the one object
 * shared, race-free, between the model call (our provider) and the tool. A
 * WeakMap so a settled turn's entry is reclaimed with its signal.
 */
const identityBySignal = new WeakMap<AbortSignal, string>();

/**
 * Bind `userId` as the acting identity for the turn identified by `signal`.
 * Called by the provider on every model round from the trusted message envelope.
 * The id is stored trimmed; empty/whitespace becomes '' so downstream fail-closed
 * logic treats it as "no user". No-op when `signal` is absent (non-agent path).
 */
export function bindTurnUserId(signal: AbortSignal | undefined, userId: string): void {
  if (!signal) return;
  identityBySignal.set(signal, (userId ?? '').trim());
}

/**
 * The acting user bound for the turn identified by `signal`, or '' when nothing
 * is bound (non-HTTP / test paths, or before the provider ran). Never throws.
 */
export function currentUserId(signal: AbortSignal | undefined): string {
  if (!signal) return '';
  return identityBySignal.get(signal) ?? '';
}

/** Sentinel prefix that carries the trusted acting id inside the turn message. */
const IDENTITY_ENVELOPE_PREFIX = ' zoe-uid:';
const IDENTITY_ENVELOPE_RE = /^ zoe-uid:([^\n]*)\n/;

/**
 * Wrap `message` with a machine-readable acting-identity envelope. Called by the
 * zoe-data seam (services/zoe-data/zoe_flue_client.py mirrors this format) so the
 * trusted `user_id` rides the ONE field Flue persists into the fiber — the turn
 * message — since Flue's payload schema strips every other body field. The
 * provider reads it (forwardedIdentityFromMessages) and strips it
 * (stripIdentityEnvelope) before the model ever sees the text, so it never
 * pollutes the conversation. An empty id yields the message unchanged.
 */
export function wrapMessageWithIdentity(message: string, userId: string): string {
  // Strip embedded CR/LF too — trim() only removes leading/trailing whitespace,
  // but a newline inside the id would terminate the single-line envelope early and
  // leak the remainder into the prompt the model sees. Ids come from trusted server
  // code; this keeps the wire contract tight regardless.
  const uid = (userId ?? '').trim().replace(/[\r\n]/g, '');
  if (!uid) return message;
  return `${IDENTITY_ENVELOPE_PREFIX}${uid}\n${message}`;
}

/**
 * Extract the trusted acting id from the LAST user message's identity envelope.
 * Returns '' when the most recent user message carries no envelope (older client,
 * non-HTTP path). Pure read — does not mutate the messages.
 */
export function forwardedIdentityFromMessages(
  messages: { role: string; content: unknown }[],
): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role !== 'user') continue;
    const m = firstText(msg.content).match(IDENTITY_ENVELOPE_RE);
    return m ? (m[1] ?? '').trim() : '';
  }
  return '';
}

/**
 * Return a copy of `messages` with the identity envelope stripped from every user
 * message, so the model sees only the human-authored text. Only allocates new
 * objects for messages that actually carried an envelope; returns the same array
 * reference when nothing changed.
 */
export function stripIdentityEnvelope<T extends { role: string; content: unknown }>(
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
  if (typeof content === 'string') return content.replace(IDENTITY_ENVELOPE_RE, '');
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
        const next = orig.replace(IDENTITY_ENVELOPE_RE, '');
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
