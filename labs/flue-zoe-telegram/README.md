# Flue Zoe — Telegram channel (lab-only)

> **LAB SPIKE. NOT wired into the voice path or any production service.**
> Increment 1 of "Zoe on Flue" (option B): a conversational Zoe over Telegram,
> running on Zoe's own local Gemma brain.

## What it is

A small Flue app that lets you **text Zoe** from your phone, with Hermes out of
the loop:

```
Telegram ──(long-poll)──► Flue Zoe-agent (Gemma :11434) ──► reply ──► Telegram
            no public ingress      durable per-chat session
```

- **Long-poll, not webhook** — the bot reaches out to Telegram; nothing is
  exposed on the Jetson (no Cloudflare route). Private + NAT-friendly.
- **Zoe's own brain** — the agent runs on the local llama-server (Gemma E4B) via
  the `zoe` provider, never a cloud model.
- **Durable per-chat memory** — `src/db.ts` (file-backed SQLite) keeps each
  chat's context across restarts.
- **Allow-listed** — only `TELEGRAM_ALLOWED_USERS` get through (fail-closed).
- **Proactive-ready** — `bot.api` is callable from anywhere (a cron can message
  you first); the scheduled-push increment is not wired yet.

## What it does NOT do yet

Conversation only. Zoe's domain tools (lists, calendar, timers, long-term
memory) are not wired in this increment — they come next as Flue tools / via
Zoe's MCP bus. The agent is told to say so rather than pretend.

## Voice safety

The voice fast-path is **not** routed through Flue and is untouched by this app.
The only shared resource is the Gemma GPU on `:11434`; validate no regression
with `scripts/maintenance/zoe_latency_probe.py` while this runs before trusting
it live.

## Run (once `.env` has a token)

```sh
cd labs/flue-zoe-telegram
npm install
npm run build          # flue build --target node
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_USERS
node dist/server.mjs   # or `npm run dev` for watch mode
```

Reuses Hermes's bot token (Hermes is paused). On start it `deleteWebhook` +
long-polls, so it cleanly takes the bot over.
