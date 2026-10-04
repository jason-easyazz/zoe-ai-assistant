/**
 * Voice notes in and out + the "Talk to Zoe" hand-off, as pure decisions with
 * injected deps (the same shape as src/handler.ts), so every gate has an
 * offline test and a negative control.
 *
 * Owner decision Q17 (2026-10-04): reply IN KIND — a voice note gets a voice
 * note back, text gets text; a talk button yes; photos wait; no phone number.
 * Design: docs/research/telegram-calls-voice-photos-2026-10-04.md §3.1.
 *
 * FLAG-DARK. `ZOE_TELEGRAM_VOICE_NOTES` unset = today's behaviour byte for
 * byte: a voice note is received by the poll loop and ignored (no handler is
 * registered, see app.ts). The media plumbing — OGG/Opus decode, Moonshine,
 * Kokoro, OGG/Opus encode — lives in zoe-data's internal `telegram_media`
 * router (`ZOE_TELEGRAM_MEDIA`); this process only moves bytes and decides.
 *
 * VOICE PATH UNTOUCHED: the brain turn is the SAME `/api/chat` call the text
 * handler makes, as the same linked member. Nothing in the panel/Pi voice
 * path is involved.
 *
 * SECURITY (record §6): identity is resolved BEFORE any download; forwarded
 * media is refused by default (someone else's speech must not run as the
 * member's command — the telegram channel profile allows writes); duration +
 * size are checked BEFORE `getFile`; the download URL embeds the bot token and
 * is never logged (we log `file_unique_id`).
 */
import { unlinkedMessage } from './handler.ts';

export type VoiceReplyMode = 'text' | 'voice' | 'both';

/** Hard ingress bound per note, agreeing with zoe-data's MAX_UPLOAD_BYTES. */
export const MAX_NOTE_BYTES = 8 * 1024 * 1024;

const truthy = (v: string | undefined) => ['1', 'true', 'yes', 'on'].includes((v ?? '').trim().toLowerCase());

/** `ZOE_TELEGRAM_VOICE_NOTES` — default OFF. */
export function voiceNotesEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return truthy(env.ZOE_TELEGRAM_VOICE_NOTES);
}

/** `ZOE_TELEGRAM_VOICE_REPLY` = text | voice | both — default `voice` (in kind). */
export function voiceReplyMode(env: NodeJS.ProcessEnv = process.env): VoiceReplyMode {
  const raw = (env.ZOE_TELEGRAM_VOICE_REPLY ?? '').trim().toLowerCase();
  return raw === 'text' || raw === 'both' ? raw : 'voice';
}

/** `ZOE_TELEGRAM_VOICE_MAX_S` — default 60 (zoe-data reads the same name). */
export function voiceMaxSeconds(env: NodeJS.ProcessEnv = process.env): number {
  const n = Number.parseInt(env.ZOE_TELEGRAM_VOICE_MAX_S ?? '', 10);
  return Number.isFinite(n) && n > 0 ? n : 60;
}

/** `ZOE_TELEGRAM_ALLOW_FORWARDED` — default OFF (forwarded media refused). */
export function allowForwarded(env: NodeJS.ProcessEnv = process.env): boolean {
  return truthy(env.ZOE_TELEGRAM_ALLOW_FORWARDED);
}

/** `ZOE_BASE_URL` → the existing push-to-talk page, or null (no /talk). */
export function talkPageUrl(env: NodeJS.ProcessEnv = process.env): string | null {
  const base = (env.ZOE_BASE_URL ?? '').trim().replace(/\/$/, '');
  return base ? `${base}/voice.html` : null;
}

export interface VoiceNote {
  kind: 'voice' | 'audio';
  fileId: string;
  /** Stable, token-free id — the one that may be logged. */
  fileUniqueId: string;
  /** Seconds, as Telegram reports it (checked before download). */
  duration: number;
  fileSize?: number;
  /** `message.forward_origin` present. */
  forwarded: boolean;
}

export interface VoiceOptions {
  mode: VoiceReplyMode;
  maxSeconds: number;
  allowForwarded: boolean;
}

export function optionsFromEnv(env: NodeJS.ProcessEnv = process.env): VoiceOptions {
  return { mode: voiceReplyMode(env), maxSeconds: voiceMaxSeconds(env), allowForwarded: allowForwarded(env) };
}

export interface VoiceDeps {
  resolve: (telegramId: number) => Promise<string | null>;
  session: (chatId: number) => string;
  /** The SAME brain call the text handler makes (`/api/chat`, as the member). */
  ask: (text: string, sessionId: string, userId: string) => Promise<string>;
  /** `getFile` + download, bounded by MAX_NOTE_BYTES. */
  download: (fileId: string) => Promise<Uint8Array>;
  /** zoe-data `telegram/transcribe`; '' means no speech was found. */
  transcribe: (audio: Uint8Array) => Promise<string>;
  /** zoe-data `telegram/synthesize`; null when voice is unavailable (fall back to text). */
  synthesize: (text: string) => Promise<Uint8Array | null>;
  reply: (text: string) => Promise<unknown>;
  replyVoice: (ogg: Uint8Array) => Promise<unknown>;
  /** Per-stage timings sink (the measurement). Defaults to console.log. */
  log?: (line: string) => void;
}

/** Error text safe to log: a Bot API URL embeds the token, so strip `bot<token>` wherever it appears. */
export function redactToken(err: unknown): string {
  const text = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
  return text.replace(/bot\d+:[A-Za-z0-9_-]+/g, 'bot<redacted>');
}

/** Why a note is refused BEFORE download, or null when it may proceed. */
export function refusalFor(note: VoiceNote, opts: VoiceOptions): string | null {
  if (note.forwarded && !opts.allowForwarded) {
    return "I only act on voice notes you record yourself — forwarded ones I'll leave alone.";
  }
  if (!Number.isFinite(note.duration) || note.duration > opts.maxSeconds) {
    return `That one's too long for me — keep voice notes under ${opts.maxSeconds} seconds and I'll listen.`;
  }
  if (note.fileSize !== undefined && note.fileSize > MAX_NOTE_BYTES) {
    return "That audio file is too big for me to take in. Try a shorter voice note.";
  }
  return null;
}

/** A forwarded note (when allowed) is quoted material, never the member's own command. */
export function framedTranscript(text: string, note: VoiceNote): string {
  return note.forwarded ? `(Forwarded voice note from someone else — quoted, not my instruction) "${text}"` : text;
}

/** Deliver `answer` in kind. Voice falls back to text when synthesis is unavailable. */
export async function deliverReply(answer: string, deps: VoiceDeps, mode: VoiceReplyMode): Promise<'text' | 'voice' | 'both'> {
  if (mode === 'text') {
    await deps.reply(answer);
    return 'text';
  }
  const ogg = await deps.synthesize(answer).catch((err) => {
    console.warn('telegram voice synth failed (falling back to text):', err);
    return null;
  });
  if (!ogg) {
    await deps.reply(answer);
    return 'text';
  }
  await deps.replyVoice(ogg);
  if (mode === 'both') {
    await deps.reply(answer);
    return 'both';
  }
  return 'voice';
}

/**
 * Handle one voice note / audio message. Order is load-bearing:
 * identity → refusals (no download yet) → download → transcribe → brain → reply in kind.
 */
export async function handleVoiceNote(
  telegramId: number,
  chatId: number,
  note: VoiceNote,
  deps: VoiceDeps,
  opts: VoiceOptions,
): Promise<void> {
  const log = deps.log ?? ((line: string) => console.log(line));
  const userId = await deps.resolve(telegramId);
  if (!userId) {
    await deps.reply(unlinkedMessage(telegramId));
    return;
  }
  const refusal = refusalFor(note, opts);
  if (refusal) {
    await deps.reply(refusal);
    return;
  }

  const t: Record<string, number> = {};
  const started = Date.now();
  let mark = started;
  const lap = (stage: string) => {
    const now = Date.now();
    t[stage] = now - mark;
    mark = now;
  };

  let transcript: string;
  try {
    const audio = await deps.download(note.fileId);
    lap('download_ms');
    transcript = (await deps.transcribe(audio)).trim();
    lap('stt_ms');
  } catch (err) {
    console.error(`telegram voice note ${note.fileUniqueId} failed before the brain: ${redactToken(err)}`);
    await deps.reply("I couldn't take in that voice note just now. Try again in a moment, or type it.");
    return;
  }
  if (!transcript) {
    await deps.reply("I couldn't make out any words in that one — want to try again?");
    return;
  }

  const answer = await deps.ask(framedTranscript(transcript, note), deps.session(chatId), userId);
  lap('brain_ms');
  const delivered = await deliverReply(answer, deps, opts.mode);
  lap('reply_ms');
  log(
    `TELEGRAM_VOICE kind=${note.kind} file=${note.fileUniqueId} dur=${note.duration}s mode=${opts.mode} ` +
      `delivered=${delivered} download_ms=${t.download_ms} stt_ms=${t.stt_ms} brain_ms=${t.brain_ms} ` +
      `reply_ms=${t.reply_ms} total_ms=${Date.now() - started}`,
  );
}

export interface TalkDeps {
  resolve: (telegramId: number) => Promise<string | null>;
  /** Send the text with a single URL button labelled `label`. */
  replyWithLink: (text: string, label: string, url: string) => Promise<unknown>;
  reply: (text: string) => Promise<unknown>;
}

/**
 * `/talk` — hand the phone the EXISTING push-to-talk page (`voice.html` at
 * `ZOE_BASE_URL`): VISION principle 8 verbatim, zero new infrastructure. It is
 * half-duplex press-to-talk, named honestly ("talk", not "call"). Linked
 * senders only, like `/new`.
 */
export async function handleTalk(telegramId: number, url: string, deps: TalkDeps): Promise<void> {
  const userId = await deps.resolve(telegramId);
  if (!userId) {
    await deps.reply('Please link your Zoe account first (Zoe settings → Telegram).');
    return;
  }
  await deps.replyWithLink('Tap below, then press and hold to talk to me from your phone.', 'Talk to Zoe', url);
}
