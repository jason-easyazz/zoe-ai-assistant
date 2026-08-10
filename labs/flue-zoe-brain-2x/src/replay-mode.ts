/**
 * Per-request REPLAY ISOLATION for the Flue Zoe-brain sidecar.
 *
 * WHY THIS EXISTS — the replay gate (scripts/maintenance/voice_regression_probe.py
 * → services/zoe-data/tests/replay_samples.py) feeds Jason's real-voice corpus back
 * through the LIVE pipeline. The harness passes `allow_writes=False`, but that
 * governs only `fast_tiers`; on brain fall-through the turn reaches this sidecar,
 * whose tools hold `ZOE_BRAIN_ALLOW_WRITES=true` (both lanes' .env). So a corpus
 * command like "remember my anniversary is June 3rd" or "add bread to the shopping
 * list" executed a REAL write into live zoe-data on every gate run.
 *
 * The probe's cleanup swept only `events` and `list_items` — but the tool surface
 * also writes reminders, notes, journal_entries, people, users, lists, MemPalace
 * memories (three separate paths), Home Assistant device state and Music Assistant
 * playback. Everything outside those two classes leaked silently, and every NEW
 * mutating tool would leak by default. Extending the cleanup class-by-class is a
 * treadmill that loses to the next tool added; blocking the write at the seam is
 * not.
 *
 * WHY NOT AN ENV FLIP — the decisive reason is the SCORER, and it holds on both
 * lanes: `ZOE_BRAIN_ALLOW_WRITES=false` is NOT scorer-neutral. Its dry-run text
 * instructs the model to say "you can't do that yet", which
 * `replay_samples.py::_CANT_DO_RE` scores CANT_DO — turning the env off would
 * redden the gate on every write command in the corpus. That is precisely why the
 * .env carries `true`, and why the fix has to be per-REQUEST.
 *
 * (2x DRIFT vs the 1.x note: on 1.x `ALLOW_WRITES` is a module-LOAD const, so an
 * env flip there also needs a sidecar restart in both directions — a probe that
 * stops the brain, flips, replays and flips back wedges the voice lane if it
 * crashes mid-run. This port reads the env PER CALL via `allowWrites()`, so that
 * particular restart hazard is gone here. It does not rescue the approach: a
 * process-wide env flip is still process-WIDE, so a live family turn landing
 * during a replay window would get the refusal text, and the scorer objection
 * above is untouched.)
 *
 * THE MECHANISM — identical to the acting-identity envelope (src/request-identity.ts),
 * for the same reason: Flue's payload schema accepts only {message, images} and
 * silently drops every other body field, so a per-request flag must ride the turn
 * MESSAGE. The seam prepends a ` zoe-replay:1` line; the provider reads it, binds it
 * to the turn's AbortSignal in a WeakMap, and strips it before the model sees the
 * text. Tools read it back by their own `signal`, so concurrent turns are
 * independent and a live user's turn is never affected by a replay turn.
 *
 * WIRE ORDER — the replay line is FIRST, ahead of the identity line:
 *     " zoe-replay:1\n zoe-uid:<id>\n<blocks>\n<user message>"
 * Both parsers are `^`-anchored, so the provider strips the replay line first and
 * hands the remainder to the identity parser unchanged. That keeps
 * src/request-identity.ts completely untouched by this feature.
 *
 * TRUST — the marker is only ever set by trusted server code (the zoe-data seam).
 * It is NOT model-chosen and NOT a tool arg. The seam additionally SANITISES the
 * inbound user message, stripping any ` zoe-replay:` line a user typed, so a user
 * cannot forge the marker to silently void their own writes. See
 * services/zoe-data/zoe_flue_client.py `_strip_replay_envelope`.
 *
 * ABSENT MARKER = TODAY'S BEHAVIOUR, byte for byte. Nothing here changes the live
 * lane; the marker is only ever sent by the replay harness.
 *
 * Part of the live Zoe brain (flue-zoe-brain-2x.service, :3579).
 */

/**
 * Replay-isolation flag for a turn, keyed by that turn's AbortSignal — the one
 * object shared, race-free, between the model call (our provider) and the tool. A
 * WeakMap so a settled turn's entry is reclaimed with its signal.
 */
const replayBySignal = new WeakMap<AbortSignal, boolean>();

/**
 * Bind replay isolation for the turn identified by `signal`. Called by the provider
 * on every model round from the trusted message envelope. No-op when `signal` is
 * absent (non-agent path).
 */
export function bindTurnReplayMode(signal: AbortSignal | undefined, replay: boolean): void {
  if (!signal) return;
  replayBySignal.set(signal, replay === true);
}

/**
 * True when this turn is a REPLAY turn whose writes must not commit. Defaults to
 * false for any turn with no binding (live traffic, unit tests, non-HTTP paths), so
 * the fail direction is "writes behave exactly as they do today".
 */
export function isReplayTurn(signal: AbortSignal | undefined): boolean {
  if (!signal) return false;
  return replayBySignal.get(signal) === true;
}

/** Sentinel prefix that carries the trusted replay marker inside the turn message. */
const REPLAY_ENVELOPE_PREFIX = ' zoe-replay:';
const REPLAY_ENVELOPE_RE = /^ zoe-replay:([^\n]*)\n/;

/**
 * Wrap `message` with the replay envelope. Called by the zoe-data seam
 * (services/zoe-data/zoe_flue_client.py mirrors this format). `replay === false`
 * yields the message unchanged, so a live turn is byte-identical to today.
 *
 * Applied OUTSIDE the identity wrap so the replay line lands first on the wire.
 */
export function wrapMessageWithReplay(message: string, replay: boolean): string {
  if (!replay) return message;
  return `${REPLAY_ENVELOPE_PREFIX}1\n${message}`;
}

/**
 * True when the LAST user message carries a replay envelope. Pure read — does not
 * mutate the messages. Any non-empty value counts as "on"; the seam only ever
 * sends `1`.
 */
export function forwardedReplayFromMessages(
  messages: { role: string; content: unknown }[],
): boolean {
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i];
    if (msg.role !== 'user') continue;
    const m = firstText(msg.content).match(REPLAY_ENVELOPE_RE);
    return m ? (m[1] ?? '').trim() !== '' : false;
  }
  return false;
}

/**
 * Return a copy of `messages` with the replay envelope stripped from every user
 * message. Returns the same array reference when nothing changed, so the common
 * (live) path allocates nothing.
 */
export function stripReplayEnvelope<T extends { role: string; content: unknown }>(
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
  if (typeof content === 'string') return content.replace(REPLAY_ENVELOPE_RE, '');
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
        const next = orig.replace(REPLAY_ENVELOPE_RE, '');
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
