# Flue Zoe — Telegram channel (lab-only)

> **LAB SPIKE. NOT wired into the voice path or any production service.**
> Increment 1 of "Zoe on Flue" (option B): a conversational Zoe over Telegram,
> bridged to Zoe's REAL brain (zoe-data's /api/chat).

## What it is

A small Flue app that lets you **text Zoe** from your phone, with Hermes out of
the loop:

```
Telegram ──(long-poll)──► bridge ──► zoe-data /api/chat ──► reply ──► Telegram
            no public ingress         (intent + tools + memory + persona)
```

- **Long-poll, not webhook** — the bot reaches out to Telegram; nothing is
  exposed on the Jetson (no Cloudflare route). Private + NAT-friendly.
- **Zoe's REAL brain** — each message is relayed to zoe-data's `/api/chat`
  (`src/brain.ts`) — the same pipeline voice + touch use (intent/semantic
  routing, her experts/tools, memory, real persona). This channel does NOT run a
  Flue LLM agent and does NOT touch the local voice Gemma on :11434.
  `src/agents/zoe.ts` is a build-required placeholder that is never dispatched.
- **Durable per-chat memory** — a stable `telegram-<chatId>` session id keeps
  zoe-data context per chat; `src/db.ts` (file-backed SQLite) persists Flue's
  own state across restarts.
- **Allow-listed** — only `TELEGRAM_ALLOWED_USERS` get through (fail-closed).
- **Proactive-ready** — `bot.api` is callable from anywhere (a cron can message
  you first); the scheduled-push increment is not wired yet.

## What it does NOT do yet

Conversation only. Zoe's domain tools (lists, calendar, timers, long-term
memory) are not wired in this increment — they come next as Flue tools / via
Zoe's MCP bus. The agent is told to say so rather than pretend.

## Voice safety

The voice fast-path is **not** routed through Flue and is untouched by this app.
This channel does not run a Flue LLM agent and never connects to the local voice
Gemma on `:11434`; it only calls zoe-data's `/api/chat` over HTTP. The added load
on zoe-data is the same shape as any other chat client — validate no voice
regression with `scripts/maintenance/zoe_latency_probe.py` while this runs before
trusting it live.

## Run (once `.env` has a token)

```sh
cd labs/flue-zoe-telegram
npm install
npm run build          # flue build --target node
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_USERS
node dist/server.mjs   # or `npm run dev` for watch mode
```

`/health` returns 200 only while long-polling is up; it returns **503** if
`deleteWebhook`/`bot.start` failed (so a supervisor can restart the process).

### Bot ownership — only ONE poller per token

A Telegram bot token allows exactly one `getUpdates` (long-poll) consumer at a
time. This app reuses **Hermes's** bot token, so **Hermes's poller must be
stopped first** — otherwise both fight over updates.

On start the app calls `deleteWebhook` then `bot.start`, which logs
`... polling (took the bot over)` on success. If you instead see a
**`Telegram 409 Conflict`** error in the log, another consumer (almost certainly
Hermes) is still polling that token. The app does **not** retry on 409 (a retry
storm just hammers Telegram); stop the other consumer (`systemctl --user stop
hermes-agent.service`, or whatever runs Hermes's bot) and restart this app.
