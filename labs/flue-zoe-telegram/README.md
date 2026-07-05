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
- **Identity-gated** — no allow-list. A sender reaches the brain only if their id
  resolves to a **linked** Zoe user; linking needs a signed token from an
  authenticated Zoe session. Unlinked senders are only guided to link.
- **Proactive-ready** — `bot.api` is callable from anywhere (a cron can message
  you first); the scheduled-push increment is not wired yet.

## Account linking (real identity + memory)

Each Telegram sender is mapped to their **real Zoe user**, so Zoe answers with
*that person's* memory and facts instead of everyone landing as `guest`.

```
Telegram msg ─► allow-list gate ─► resolve telegram_id → Zoe user_id ─► /api/chat AS that user
   (verified sender id)   (coarse)    GET /api/system/resolve-telegram/<id>   (X-Zoe-User-Id, trusted)
```

- **Linking is user-driven.** A user stores their **numeric Telegram id** in
  their Zoe profile: `PUT /api/user/profile/telegram` with body
  `{"telegram_id":"<id>"}`, session-authed as themselves (so they can only link
  *their own* account). `GET /api/user/profile/telegram` reads it back;
  `{"telegram_id": null}` unlinks. One telegram id maps to at most one Zoe user
  (last-writer-wins: re-linking an id elsewhere clears the prior owner).
- **The bot resolves, then forwards.** On each allowed message it calls the
  internal resolver for the sender's id. Resolved → it forwards the turn to
  `/api/chat` with `X-Zoe-User-Id: <user_id>` over zoe-data's **trusted internal
  path** (`X-Internal-Token` / loopback), so the turn runs as the real user.
  Not linked → it replies with the sender's numeric id and asks them to set it
  in Zoe settings; **an unlinked sender never reaches the brain as a real user.**
- **Security.** zoe-data honours `X-Zoe-User-Id` **only** for internal callers
  (loopback or valid `X-Internal-Token`, the same boundary as the intent-dispatch
  endpoint). A public/browser request that sets that header is ignored — it
  cannot impersonate a user. The resolver is likewise internal-only.

## What it does NOT do yet

Conversation + real per-user identity/memory. Zoe's domain tools (lists,
calendar, timers) are not wired in this increment — they come next as Flue tools
/ via Zoe's MCP bus. The agent is told to say so rather than pretend.

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
cp .env.example .env   # fill TELEGRAM_BOT_TOKEN (+ ZOE_INTERNAL_TOKEN if off-box)
node dist/server.mjs   # or `npm run dev` for watch mode
```

`/health` returns 200 only while long-polling is up; it returns **503** if
`deleteWebhook`/`bot.start` failed (so a supervisor can restart the process).

### Optional: run under systemd (operator opt-in)

A user-unit **template** ships at `scripts/setup/systemd/flue-zoe-telegram.service`.
It is **never auto-installed or auto-enabled** — installing it is a deliberate
operator action (the bot token is a secret you hold). Build + create `.env` first
(above), then:

```sh
mkdir -p ~/.config/systemd/user
cp ~/assistant/scripts/setup/systemd/flue-zoe-telegram.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now flue-zoe-telegram.service   # start when YOU choose
```

### Operator: end-to-end round-trip check

The true Telegram round-trip needs your real bot token, so it is an operator
step (agents can't do it):

1. In Zoe settings (or via API), link your account:
   `PUT /api/user/profile/telegram` `{"telegram_id":"<your numeric id>"}`
   (session-authed as yourself). Your numeric id: message the bot once — if
   unlinked it replies with `set your Telegram ID (<id>)`.
2. Verify the mapping: `curl -s http://127.0.0.1:8000/api/system/resolve-telegram/<id>`
   → `{"user_id":"<you>"}`.
3. Message the bot again — the reply should now reflect **your** memory/facts,
   not a generic guest answer.

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
