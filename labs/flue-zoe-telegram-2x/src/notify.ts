/**
 * `POST /internal/notify` — zoe-data asks THIS process to message a linked member
 * (A9, 2026-10-18). A bot can only message someone who started THAT bot, and the
 * bot the family links with is this one, so zoe-data's "send to my phone" hands
 * the message over loopback and we send it with our own token.
 *
 * Gate, all fail-closed: a loopback socket (app.ts, when the adapter exposes it);
 * `Authorization: Bearer <ZOE_INTERNAL_TOKEN>` (the secret zoe-data already shares;
 * unset = 503, wrong = 401, constant-time); a LINKED recipient only (zoe-data's
 * resolver), so it cannot message arbitrary chats; bounded text, http(s) URL only.
 * Pure decision with injected deps, like handler.ts. Nothing here is logged.
 */
import { createHash, timingSafeEqual } from 'node:crypto';

export const MAX_NOTIFY_TEXT = 1000;
const MAX_URL = 2000;
const MAX_LABEL = 64;

export interface NotifyDeps {
  /** The shared secret (ZOE_INTERNAL_TOKEN); empty/undefined = route disabled. */
  token: string | undefined;
  resolve: (telegramId: number) => Promise<string | null>;
  /** Send `text` to `chatId`, with a single URL button when `link` is given. */
  send: (chatId: number, text: string, link?: { label: string; url: string }) => Promise<void>;
}

export interface NotifyResult {
  status: 200 | 400 | 401 | 403 | 502 | 503;
  body: { ok: boolean; error?: string };
}

function digest(s: string): Buffer {
  return createHash('sha256').update(s, 'utf8').digest();
}

/** Constant-time `Bearer <token>` check (hashing first makes the lengths equal). */
export function bearerMatches(header: string | undefined, token: string): boolean {
  return timingSafeEqual(digest(header ?? ''), digest(`Bearer ${token}`));
}

/** 127.0.0.0/8, ::1 and their IPv4-mapped forms. */
export function isLoopbackAddress(addr: string): boolean {
  const a = addr.trim().toLowerCase().replace(/^::ffff:/, '');
  return a === '::1' || /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(a);
}

function parseUrl(raw: unknown): string | null {
  if (typeof raw !== 'string' || !raw || raw.length > MAX_URL) return null;
  try {
    const u = new URL(raw);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.toString() : null;
  } catch {
    return null;
  }
}

export async function handleNotify(
  authorization: string | undefined,
  body: unknown,
  deps: NotifyDeps,
): Promise<NotifyResult> {
  const token = (deps.token ?? '').trim();
  if (!token) return { status: 503, body: { ok: false, error: 'notify is not configured' } };
  if (!bearerMatches(authorization, token)) return { status: 401, body: { ok: false, error: 'unauthorized' } };

  const b = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const idText = String(b.telegram_id ?? '').trim();
  const text = typeof b.text === 'string' ? b.text.trim() : '';
  if (!/^\d{1,20}$/.test(idText) || !text || text.length > MAX_NOTIFY_TEXT) {
    return { status: 400, body: { ok: false, error: 'telegram_id (digits) and text (1-1000 chars) are required' } };
  }
  let link: { label: string; url: string } | undefined;
  if (b.url !== undefined && b.url !== null && b.url !== '') {
    const url = parseUrl(b.url);
    if (!url) return { status: 400, body: { ok: false, error: 'url must be http(s)' } };
    const label = typeof b.label === 'string' && b.label.trim() ? b.label.trim().slice(0, MAX_LABEL) : 'Open';
    link = { label, url };
  }

  const telegramId = Number(idText);
  let member: string | null;
  try {
    member = await deps.resolve(telegramId);
  } catch {
    return { status: 502, body: { ok: false, error: 'could not resolve the member' } };
  }
  if (!member) return { status: 403, body: { ok: false, error: 'not a linked member' } };

  try {
    await deps.send(telegramId, text, link);
  } catch {
    return { status: 502, body: { ok: false, error: 'telegram refused the message' } };
  }
  return { status: 200, body: { ok: true } };
}
