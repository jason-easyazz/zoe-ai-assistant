/**
 * Nothing this process logs may carry the bot token (security fix 2026-10-19).
 *
 * Every Bot API URL embeds the token (`<root>/bot<token>/<method>`), and a
 * transport failure's error quotes that URL: grammY's `HttpError` wraps the
 * fetch error in `.error`, and printing the error object (`console.error('…', err)`)
 * walks into it, so any network failure would print the token. Two layers, both
 * required:
 *
 *   1. `describeError(err)` — the one way a log line names an error here: name +
 *      message + code down the `.error` / `.cause` chain, redacted, no stack and
 *      no object dump.
 *   2. `installConsoleRedaction()` — called once at module load (src/telegram.ts,
 *      before the bot exists). Every `console.*` call is formatted exactly as Node
 *      would (`util.format`) and then redacted, so a future `console.error(err)`
 *      that forgets layer 1 still cannot print the token.
 *
 * Redaction removes the `bot<id>:<secret>` shape, any bare `<id>:<secret>` token
 * shape, and the literal configured token value wherever it appears.
 */
import { format } from 'node:util';

const BOT_URL_TOKEN = /bot\d+:[A-Za-z0-9_-]+/g;
const BARE_TOKEN = /\b\d{6,}:[A-Za-z0-9_-]{30,}\b/g;
export const REDACTED = '<redacted>';

/** `text` with every bot-token shape, and the configured token itself, removed. */
export function redactSecrets(text: string, token: string | undefined = process.env.TELEGRAM_BOT_TOKEN): string {
  let out = String(text);
  if (token && token.length >= 10) out = out.split(token).join(REDACTED);
  return out.replace(BOT_URL_TOKEN, `bot${REDACTED}`).replace(BARE_TOKEN, REDACTED);
}

/** A loggable one-line description of `err`: name, message and code along the wrapped-error chain, redacted. */
export function describeError(err: unknown): string {
  const parts: string[] = [];
  let cur: unknown = err;
  for (let depth = 0; depth < 4 && cur !== undefined && cur !== null; depth++) {
    if (cur instanceof Error) {
      const code = (cur as { code?: unknown }).code;
      parts.push(`${cur.name}: ${cur.message}${code !== undefined ? ` (${String(code)})` : ''}`);
      const inner = (cur as { error?: unknown }).error ?? (cur as { cause?: unknown }).cause;
      cur = inner === cur ? undefined : inner;
    } else {
      parts.push(typeof cur === 'string' ? cur : format('%o', cur).slice(0, 300));
      cur = undefined;
    }
  }
  return redactSecrets(parts.join(' <- ') || String(err));
}

const METHODS = ['log', 'info', 'warn', 'error', 'debug', 'trace'] as const;
let installed = false;

/** Route every console method through `redactSecrets`. Idempotent. */
export function installConsoleRedaction(): void {
  if (installed) return;
  installed = true;
  for (const m of METHODS) {
    const orig = console[m].bind(console) as (...a: unknown[]) => void;
    (console as unknown as Record<string, (...a: unknown[]) => void>)[m] = (...args: unknown[]) => {
      if (args.length === 0) return orig();
      orig(redactSecrets(format(...(args as [unknown, ...unknown[]]))));
    };
  }
}
