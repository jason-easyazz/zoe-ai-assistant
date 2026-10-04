---
type: Reference
title: Telegram voice notes in and out (stage 1, flag-dark)
description: How a Telegram voice note reaches Zoe and how she answers in kind — the lane handler, zoe-data's internal telegram_media router, the flags (all default OFF), identity and forwarding rules, the per-stage log lines that are the measurement, and the operator apply/rollback lines. Nothing in the panel voice path changes.
tags: [telegram, voice, moonshine, kokoro, ffmpeg, flags, operator]
timestamp: 2026-10-04T00:00:00Z
---

# Telegram voice notes in and out

Stage 1 of [telegram-calls-voice-photos-2026-10-04.md](../research/telegram-calls-voice-photos-2026-10-04.md)
(§3.1), built per the owner decision Q17 (2026-10-04): **voice notes reply in kind — voice to
voice, text to text; a talk button yes; photos wait; no phone number.** Shipped flag-dark: with
every flag unset the bot behaves byte for byte as before (a voice note is received by the poll
loop and ignored; `/talk` falls through to the brain as text).

## The path

| Step | Where | What |
|---|---|---|
| 1 | `labs/flue-zoe-telegram-2x/src/app.ts` | `bot.on(['message:voice','message:audio'])` registered ONLY under `ZOE_TELEGRAM_VOICE_NOTES=1` |
| 2 | `src/voice.ts` `handleVoiceNote` | identity first (same linked-member gate as text); then refusals **before** `getFile`: forwarded (unless `ZOE_TELEGRAM_ALLOW_FORWARDED`), duration > `ZOE_TELEGRAM_VOICE_MAX_S`, size > 8 MiB |
| 3 | `src/telegram.ts` `downloadTelegramFile` | `getFile` → `<api>/file/bot<token>/<path>`; the URL embeds the token and is never logged (errors carry sizes/statuses/names only; `redactToken` strips `bot<token>` from anything logged) |
| 4 | `src/brain.ts` `transcribeTelegramAudio` | `POST /api/system/telegram/transcribe` (raw OGG, internal token) |
| 5 | `services/zoe-data/routers/telegram_media.py` | ffmpeg `-ac 1 -ar 16000 -sample_fmt s16 -f wav` (clipped at max+1 s) → `routers.voice_tts._transcribe_audio(path, capture=False)` → `{ok, text, duration_s}` — **no `voice:transcript` broadcast, no panel corpus** |
| 6 | `src/brain.ts` `askZoeAs` | the SAME `/api/chat?stream=false` turn as text: `X-Zoe-User-Id` + `X-Internal-Token`, `channel: 'telegram'`, the chat's session id |
| 7 | `src/voice.ts` `deliverReply` | per `ZOE_TELEGRAM_VOICE_REPLY`: `voice` (default) → `POST /api/system/telegram/synthesize` (Kokoro WAV → ffmpeg `libopus 32k 48 kHz mono voip` → `audio/ogg`) → `sendVoice`; `both` adds the text; `text` sends text only. A 503 (Kokoro down, ffmpeg missing, router off) falls back to text — Zoe is never silent |
| `/talk` | `src/voice.ts` `handleTalk` | when `ZOE_BASE_URL` is set: one inline URL button "Talk to Zoe" → `<base>/voice.html`, the existing push-to-talk page (VISION principle 8, zero new infrastructure). Linked senders only |

Why not `/api/voice/transcribe` or `/livekit-audio`: the first broadcasts to every panel and
captures into the panel corpus; the second attributes the turn to the device token's principal,
skips the `telegram` channel profile and runs `brain_oneshot(voice_mode=True)` instead of
`/api/chat` — not the same Zoe (record §3.1 step 5).

**Voice path untouched.** No file in `VOICE_PATH_PATTERNS` changes; the router only *calls*
`voice_tts._transcribe_audio` and `tts_waterfall._synthesize_kokoro_sidecar`. Telegram STT
serialises behind the panel's on the single `_moonshine_infer_lock` (it queues; it never
pre-empts). Zero new resident memory; transient = one ffmpeg per stage + an ≤ 8 MiB buffer in the
bot (its unit is `MemoryMax=1G`).

## Flags (all default OFF / current behaviour)

| Flag | Process | Default | Meaning |
|---|---|---|---|
| `ZOE_TELEGRAM_VOICE_NOTES` | bot | off | register the voice/audio handler |
| `ZOE_TELEGRAM_VOICE_REPLY` | bot | `voice` | `voice` (in kind) / `text` / `both` |
| `ZOE_TELEGRAM_VOICE_MAX_S` | bot **and** zoe-data | 60 | longest note; the bot checks Telegram's declared duration before download, zoe-data re-checks the DECODED audio (413) — keep both equal |
| `ZOE_TELEGRAM_ALLOW_FORWARDED` | bot | off | forwarded notes are someone else's words; on = accepted, framed as a quote, never as the member's command |
| `ZOE_BASE_URL` | bot | unset | public base URL; set = `/talk` command + button to `voice.html` |
| `ZOE_TELEGRAM_MEDIA` | zoe-data | off | mount the `telegram_media` router (off = 404) |
| `ZOE_TELEGRAM_FFMPEG` | zoe-data | auto | ffmpeg path; auto = PATH → `~/.local/bin/ffmpeg` (the box's static libopus build; the unit's PATH does not include it) |
| `ZOE_TELEGRAM_FFMPEG_TIMEOUT_S` | zoe-data | 20 | per ffmpeg run; a timeout kills it and answers 504 |
| `ZOE_TELEGRAM_STT_CAPTURE_DIR` | zoe-data | unset | copy phone-mic clips + transcripts into a SEPARATE corpus. Never `~/.zoe-voice-samples` |

The bot reads its flags from `labs/flue-zoe-telegram-2x/.env` (the unit's `EnvironmentFile`);
zoe-data from its own env. Two processes, two files. The bot token lives in both files under
different names (`TELEGRAM_BOT_TOKEN`, `ZOE_TELEGRAM_BOT_TOKEN`) — names only here.

## Operator apply (zoe-data first, then the bot)

```sh
# 1. zoe-data: mount the media router. Append to zoe-data's env, then restart it
#    (standing authorisation; check headroom first — the restart reloads Moonshine).
echo 'ZOE_TELEGRAM_MEDIA=1' >> services/zoe-data/.env      # the live env file zoe-data.service reads
systemctl --user restart zoe-data.service
until curl -fsS http://127.0.0.1:8000/health >/dev/null; do sleep 2; done
# negative control: the route is gated, not open
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/api/system/telegram/synthesize \
  -H 'Content-Type: application/json' -d '{"text":""}'    # 400 from loopback (gate passed, empty text)

# 2. the bot: voice notes on, reply in kind, the talk button.
cat >> labs/flue-zoe-telegram-2x/.env <<'EOF'
ZOE_TELEGRAM_VOICE_NOTES=1
ZOE_TELEGRAM_VOICE_REPLY=voice
ZOE_BASE_URL=https://<the tunnel hostname>
EOF
systemctl --user restart flue-zoe-telegram.service
curl -fsS http://127.0.0.1:3582/health

# 3. prove it from a LINKED phone: send a 3–5 s voice note → a voice note comes back.
journalctl --user -u flue-zoe-telegram.service -n 20 | grep TELEGRAM_VOICE
grep -E 'TELEGRAM_(STT|TTS)' ~/.zoe-logs/*.log | tail -5   # zoe-data logs to ~/.zoe-logs/, not journald
```

Controls while live: a forwarded note gets a refusal and no `getFile`; an unlinked phone gets
the onboarding text; a 61 s note is refused before download; stop `kokoro-tts.service` → the
reply arrives as text (`delivered=text` in the log line).

## Rollback

Remove the lines added above from both env files and restart both units (zoe-data's router is
unmounted → 404; the bot ignores voice notes again). Code rollback is `git revert` of the PR —
`deploy.yml` rebuilds and restarts the bot on the next push to `main`.

## Measurement

Every note writes one line in the bot's journal:
`TELEGRAM_VOICE kind=voice file=<file_unique_id> dur=<s> mode=<m> delivered=<voice|text|both> download_ms= stt_ms= brain_ms= reply_ms= total_ms=`
and zoe-data writes `TELEGRAM_STT duration= decode_ms= stt_ms= chars=` and
`TELEGRAM_TTS chars= tts_ms= encode_ms= ogg_bytes=`. Record §4's budget for a 5 s note
(≈ 4–6 s text, ≈ 6–8 s with a voice reply) is the thing those lines confirm or refute. The
corpus-replay WER instrument of record §5.1 (`telegram_voice_replay.py`, parallel-port zoe-data)
is **not built yet** — the Opus round-trip cost is still an estimate.

## Verified on the box (2026-10-04, read-only, no live service touched)

- `~/.local/bin/ffmpeg` 7.0.2-static: a 24 kHz WAV → `encode_argv` → `OggS` file → `decode_argv` →
  16 kHz mono s16 WAV of the same length; the size-derived duration the route uses matched the
  header; a missing input returns rc 254; a `-re` run under a 0.5 s timeout is killed and returns
  `-1` in 0.51 s.
- `grammY` 1.44 uploads an `InputFile` under a generated multipart part named by
  `voice: attach://<name>` with an UNQUOTED `filename=` — the offline mock parses exactly that.
- A `/talk` command registered after `message:text` is swallowed by the text handler; it is
  registered before it, and `test/voice_note.test.ts` pins that.

## Not done / next

- A real Telegram round-trip (needs the operator's token; only one poller may hold it).
- Record §5.1 replay instrument (WER delta of the Opus round-trip) and §5.4 RAM before/after.
- Photos (stage 2, waits per Q17) and a dialable number (stage 3b, opt-in only).
