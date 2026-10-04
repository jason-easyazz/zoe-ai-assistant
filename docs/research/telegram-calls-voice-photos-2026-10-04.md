---
type: research
title: Telegram calls, voice notes and photos (2026-10-04)
date: 2026-10-04
status: research-only — no code, flag, unit or live service changed by this document
description: Deep-research record for the owner's ask "build out the ability to call using Telegram and be able to send voice recordings and pictures to Zoe". Field half — what a Telegram bot can and cannot do (Bot API 10.3 has no calls; calls need a user account over MTProto, a phone number, or a WebRTC page), how voice notes (OGG/Opus) and photos flow through the Bot API, and what Gemma 4 E4B vision would cost on the Jetson. Our-system half — the live Telegram lane (labs/flue-zoe-telegram-2x), the voice ingest routes, the LiveKit on-demand path and the identity rules, quoted file:line. Ends in a three-stage design behind default-OFF flags, a RAM/latency budget per stage, a corpus-replay measurement plan and a go/no-go against VISION.
---

# Telegram calls, voice notes and photos (2026-10-04)

Scope: one idea, two halves. **Field**: can a Telegram bot take a call, how do bots receive and send
voice notes and photos, and can the brain see pictures on this box. **Ours**: what the Telegram lane,
the voice ingest routes, LiveKit and the identity rules do today, read-only, with file:line. Then a
staged design, a budget, a measurement plan and a verdict. **The rocks are fixed**
([CANONICAL.md](../CANONICAL.md), [VISION.md](../VISION.md) principle 1): Gemma 4 E4B-QAT + MTP on
llama.cpp b11194, Moonshine v2 Medium in-process, Kokoro on `:10201`. Nothing here swaps them.

Method: read-only. Source files in this worktree, the tracked systemd units, the live process
command lines (`ps`), `/proc/meminfo`, the binaries on the box (`ffmpeg`, `sox`, `node`), one `soxi`
on a corpus file, and the cited web pages (the Bot API page was fetched with `curl` and grepped for
the verbatim limits). No live service was started, stopped or restarted, nothing was sent through
the live bot, no MCP code-intel tool was used (MemAvailable was 0.5–1.3 GB during the audit).
**[unverified]** marks a claim I could not confirm at a primary source or on the box. No token,
household datum or `.env` value appears in this record — the variable *names* do, the values never.

Prior research this builds on and does not repeat:
[inference-speech-stack-2026-10-03.md](inference-speech-stack-2026-10-03.md) (§2.5: Gemma 4 audio-in
needs a BF16 mmproj; `-DGGML_CUDA_NO_VMM=ON` for multimodal on the Orin iGPU),
[companion-field-vs-samantha-2026-10-03.md](companion-field-vs-samantha-2026-10-03.md) (§4 "Sight" is
parked on mmproj RAM; tracker B7.4),
[memory-pressure-profile-2026-10-03.md](../knowledge/memory-pressure-profile-2026-10-03.md),
[state-of-zoe-review-2026-09-25.md](../knowledge/state-of-zoe-review-2026-09-25.md) (the Node
Happy-Eyeballs fix), `labs/flue-zoe-telegram-2x/README.md` (the four zoe-data contracts).

## 0. TL;DR

1. **A Telegram bot cannot make or take a call. Full stop.** Bot API 10.3 (2026-08-24) has no call
   method of any kind; the only call-shaped things a bot ever sees are the `video_chat_started` /
   `video_chat_ended` service messages. Telegram calls are an MTProto *user-account* feature
   (`phone.requestCall` → `phone.acceptCall` → `phone.confirmCall`, E2E DH exchange). Every "bot
   that calls you" on the internet is either a **userbot** (a real account with a phone number,
   driven by Telethon/Pyrogram + `ntgcalls`), a **group voice chat** joined by that userbot, or a
   **cloud service** (CallMeBot) that owns such an account. Telegram Business "connected bots" read
   and reply in private chats; they do not call. Mini Apps are web pages — they can open a WebRTC
   page, which is the one call-like surface that needs no second account (§3.3).
2. **Voice notes are the cheap, honest win and the lane already receives them.** The live bot
   long-polls with `allowed_updates: ['message']` (`app.ts:132`) but registers only `message:text`
   (`app.ts:99`), so a voice note or photo sent to Zoe today is **silently dropped — no reply**.
   Telegram voice notes are OGG/Opus; bots fetch them with `getFile` (≤ 20 MB; a 1-minute note is
   ~250 KB) and must reply with OGG/Opus via `sendVoice` (≤ 50 MB). Zoe's STT loader is **WAV-only**
   (`moonshine_voice.utils.load_wav_file`, called at `voice_tts.py:2611-2615`), so a decode stage is
   required — and the box already has a static `ffmpeg` 7.0.2 with `libopus` encode + decode at
   `~/.local/bin/ffmpeg`. Kokoro returns 24 kHz WAV (`kokoro_sidecar.py:15,63`), so the reply needs
   one `libopus` encode. Budget for a 5 s note on the Jetson: ~4–6 s end to end, < 150 MB transient,
   no new resident memory (§5). **Stage 1 = GO.**
3. **Photos: the brain can see, but not on this box today.** llama.cpp serves Gemma 4 E4B vision
   with `--mmproj` and the OpenAI `image_url` content part; the Unsloth E4B projector is **990 MB
   (F16) / 992 MB (BF16)** and is offloaded to the GPU by default. The unit has no `--mmproj`
   (`llama-server.service:129-153`), no projector file exists under `~/models/gemma4-e4b-qat/`, the
   flag set is pinned by `tests/unit/test_llama_server_unit_flags.py`, the Flue model declares
   `input: ['text']` so an image becomes "(image omitted)" (`capped-completions.ts:560-561,579`), and
   multimodal on the Orin iGPU hits the open 32 GB VMM-reserve bug (#29142) that needs a
   `-DGGML_CUDA_NO_VMM=ON` rebuild — which would itself have to be replay-gated for the text path.
   With MemAvailable at 0.42–0.56 GB (profile) / 1.29 GB (read today with one agent session on the
   box), +1 GB resident is **not affordable**. Honest alternatives: a short-lived, cgroup-capped
   CPU captioner (SmolVLM-class, ~300–600 MB transient, seconds per image) whose caption rides the
   text turn, or waiting for the W3 RAM gate to load the real projector. **Stage 2 = design + a
   measured captioner spike; native mmproj is NO-GO until W3.**
4. **Calls: the route that fits VISION is the one we already have.** `voice.html` push-to-talk
   posts a browser recording to `POST /api/voice/livekit-audio` (multipart, `voice_livekit.py:1587`)
   and gets transcript + reply + audio back — plain HTTPS, so it works through the Cloudflare tunnel
   from a phone. A Telegram bot can hand Jason that page with one tap (a `web_app` / URL button,
   exactly the "QR on the panel, finish on the phone" shape of principle 8). That is "call Zoe from
   Telegram" with **zero new infrastructure, zero audio leaving the box beyond the HTTPS hop, and no
   second Telegram account**. It is half-duplex (press to talk), not a ringing call. A real dialable
   number is possible — `livekit/sip` (Apache-2.0) beside the on-demand LiveKit container plus a
   Twilio AU number (US$3/mo, US$0.01/min inbound) — but it needs public UDP ingress (5060 + an RTP
   range) and puts the audio through a carrier, so it is an explicit **opt-in operator decision**,
   not a default. A **Telegram userbot with ntgcalls is NO-GO**: a second phone number, LGPL native
   code with known SIGSEGVs, a full-duplex pipeline Zoe does not have, and an automation posture
   Telegram's API terms do not bless.
5. **Identity is already right and must stay the gate.** A Telegram sender reaches the brain only
   if their numeric `telegram_id` resolves to a linked member (`handler.ts:51-64`,
   `system.py:2961-2990`), linking needs a signed token minted in an authenticated Zoe session
   (`telegram_link.py:1-21`), and the turn runs *as that user* over the trusted internal path
   (`brain.ts:224-234`). A voice note adds no speaker verification — the Telegram account *is* the
   credential — so media turns inherit the text turn's trust exactly, and forwarded media (someone
   else's voice) must not be run as the member's command (§6).

## 1. Field: what a Telegram bot can do

### 1.1 Calls — three sub-answers

**(a) The Bot API has no calls.** Fetched 2026-10-04: the page is Bot API 10.3 (released
2026-08-24; 10.2 on 2026-07-14, 10.1 on 2026-06-11, 10.0 on 2026-05-08, 9.6 on 2026-04-03). It
contains no `phone.*`, `call`, `requestCall` or group-call *method*. The only call-related objects are
service messages a bot can *observe* in a group — `video_chat_scheduled`, `video_chat_started`,
`video_chat_ended`, `video_chat_participants_invited` — and the changelog back to 9.x adds no call
capability ([Bot API](https://core.telegram.org/bots/api),
[changelog](https://core.telegram.org/bots/api-changelog)). Telegram Business bots (9.0, 2025) run
"Secretary Mode": with `can_read_messages` / `can_reply` they read and answer *private chats* on the
account owner's behalf, limited to chats with activity in the last 24 h — messaging only
([features](https://core.telegram.org/bots/features)). Community answers agree: "regular bot accounts
cannot join voice chats, and bots cannot access VOIP calls"
([Latenode thread](https://community.latenode.com/t/can-a-telegram-bot-initiate-voice-calls/19748)).

**(b) Calls are a user-account (MTProto) feature.** A 1:1 call is `phone.requestCall` (caller sends
`g_a_hash`) → `phone.acceptCall` (callee sends `g_b`) → `phone.confirmCall` (caller sends `g_a` +
`key_fingerprint`), media over UDP peer-to-peer or via Telegram reflectors, E2E-encrypted with the
negotiated key ([voice-calls protocol](https://core.telegram.org/api/end-to-end/voice-calls)). Both
parties are accounts logged in with a phone number. So every route to "Zoe answers a Telegram call"
is one of:

| Route | What it is | Licence / cost | Privacy | Reliability / fit for Zoe |
|---|---|---|---|---|
| **Userbot + ntgcalls (private call)** | A second Telegram account ("Zoe", own SIM/number) driven by Telethon/Pyrogram; `ntgcalls` does the WebRTC/Opus media | `ntgcalls` 2.1.0 (2026-02-05), **LGPL-3.0**, prebuilt **aarch64 manylinux (glibc ≥ 2.28) wheels for CPython 3.11–3.14**, Node bindings on npm, "Group and private call support" ([repo](https://github.com/pytgcalls/ntgcalls), [PyPI](https://pypi.org/project/ntgcalls/)). A phone number for the account: free SIM or ~AU$10/mo [unverified] | Call media E2E to Telegram, decrypted *on the Jetson* by the library — audio stays local except for Telegram's relays | Native C++ with open crash reports (ntgcalls #61: SIGSEGV on channel removal); a userbot is an automated account — Telegram's API terms welcome third-party clients but forbid "actions on behalf of the user without the user's knowledge and consent" ([terms](https://core.telegram.org/api/terms)); bans of automated accounts are widely reported [unverified]. Needs a **full-duplex** audio loop Zoe does not have (§2.4). **NO-GO.** |
| **Userbot + py-tgcalls (group voice chat)** | Same account joins a *group* voice chat; Jason talks in the group | `py-tgcalls` 3.0.0 (2026-09-25), LGPL-3.0, Python ≥ 3.10, Pyrogram/Telethon/Hydrogram; its own feature list is "voice chats in channels and chats" — the group-call path is the well-trodden one, private calls are not in its README ([repo](https://github.com/pytgcalls/pytgcalls), [PyPI](https://pypi.org/project/py-tgcalls/)) | As above | Same userbot and full-duplex objections; UX is "open the group's voice chat", not a call. **NO-GO.** |
| **CallMeBot-style cloud** | A third party's userbot rings you and plays TTS ([CallMeBot](https://www.callmebot.com/telegram-call-api/)) | Free tier / paid | **Audio and text leave the box to a third party** | One-way announcements only; violates "nothing leaves the box unless opted in". **NO-GO.** |
| **Telegram Mini App / URL button → Zoe's own WebRTC/PTT page** | The bot sends a button opening `voice.html` on the phone; media goes to zoe-data over HTTPS (or LiveKit WebRTC on the LAN) | No new software; Mini Apps are plain web pages ([Bot API 8.0 features](https://core.telegram.org/bots/api-changelog)) | Only the existing HTTPS hop (Cloudflare Access) — same posture as the text lane | Half-duplex push-to-talk, not a ringing call; already works today on the LAN (§2.4). **GO** as stage 3a. |
| **LiveKit SIP + a DID** | `livekit/sip` bridge beside the on-demand `livekit-server`; a carrier number from Twilio/Telnyx; Jason dials a real number from any phone | `livekit/sip` **Apache-2.0**, Docker, needs **Redis** (retired here — `docker-compose.yml:300`), SIP **5060 UDP/TCP + RTP 10000–20000 UDP**, a public IP ([repo](https://github.com/livekit/sip), [inbound trunk](https://docs.livekit.io/telephony/accepting-calls/inbound-trunk/)); Twilio AU local number **US$3.00/mo**, inbound **US$0.0100/min**, SIP interface US$0.004/min ([Twilio AU](https://www.twilio.com/en-us/voice/pricing/au)) | PSTN audio is **not** E2E: carrier + trunk provider hear it | Carrier-grade once up, but UDP ingress through a home NAT (the Cloudflare tunnel carries no UDP) and a trunk SBC are real operational weight on a 16 GB box with < 1 GB free. **CONDITIONAL** (operator opt-in). |
| **Twilio Programmable Voice + Media Streams** | Twilio answers the number and opens a *WebSocket* to a public URL with 8 kHz µ-law audio both ways | Same Twilio prices; no SIP server on the box | Same as above (carrier + Twilio) | **The ingress is WSS, which the existing Cloudflare tunnel carries** — no UDP, no SIP container, no Redis. 8 kHz audio upsampled to 16 kHz for Moonshine [unverified quality]. The cheaper PSTN variant if a number is ever wanted. **CONDITIONAL.** |
| **WhatsApp Business Calling API** | Meta's calling API, WebRTC/SIP media, user-initiated calls free | Requires a WhatsApp *Business* number in good standing with a **2,000 business-initiated conversations / 24 h** messaging tier before calling can be enabled ([Meta pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/pricing), [guide](https://chakrahq.com/article/whatsapp-cloud-api-calling-feature-details)) | Meta sees the signalling, media via WebRTC | Built for brands, not a household. **NO-GO.** |
| **Signal** | No public bot or call API; `signal-cli` does messages only [unverified] | — | — | **NO-GO.** |
| **jambonz / FreeSWITCH** | Self-hosted CPaaS (drachtio + rtpengine + FreeSWITCH + MySQL + Redis + InfluxDB) ([docs](https://docs.jambonz.org/)) | **MIT**, free to self-host | Same PSTN caveat | Six services to host; only sane on a VPS. If a VPS is ever acceptable, the LiveKit-SIP or Twilio-WSS routes are lighter. **NO-GO on the Jetson.** |

**(c) What people actually build.** The `awesome-tgcalls` list is Python (`MarshalX/tgcalls`, the
older one; `pytgcalls`), Node (`tgcallsjs`) and Go (`gotgcalls`) — all userbot libraries for *group*
voice chats, dominated by music bots ([awesome-tgcalls](https://github.com/tgcalls/awesome-tgcalls)).
Nothing in the field is "a bot that picks up when you call it", because the platform does not allow
it.

### 1.2 Voice notes — how bots receive and send them

Verbatim from Bot API 10.3 (fetched 2026-10-04):

- `sendVoice`: "For this to work, your audio must be in an .OGG file encoded with OPUS, or in .MP3
  format, or in .M4A format (other formats may be sent as Audio or Document). … Bots can currently
  send voice messages of up to 50 MB in size, this limit may be changed in the future."
- `getFile`: "For the moment, bots can download files of up to 20MB in size. … The file can then be
  downloaded via the link `https://api.telegram.org/file/bot<token>/<file_path>` … It is guaranteed
  that the link will be valid for at least 1 hour."
- `Voice` object: `file_id`, `file_unique_id`, `duration` (seconds), optional `mime_type`
  (`audio/ogg` in practice), optional `file_size`.
- Bot API 10.2 added `InputMediaVoiceNote` (voice notes inside media groups / edits).
- A self-hosted Local Bot API server lifts the limits (download unlimited, upload 2000 MB) — not
  needed for voice notes.

The flow every open-source Telegram voice bot uses: `message.voice.file_id` → `getFile` → HTTPS
download of the OGG → decode to 16 kHz mono PCM → STT → text turn → TTS → encode OGG/Opus →
`sendVoice`. Home Assistant exposes incoming files as a single `telegram_attachment` event
(`file_mime_type`, `file_name`) and ships a `telegram_bot.send_voice` action; the community's voice
flows bolt Whisper on in n8n or a blueprint ([HA telegram_bot](https://www.home-assistant.io/integrations/telegram_bot/),
[Assist blueprint](https://community.home-assistant.io/t/blueprint-to-use-your-telegram-bot-for-assist-commands/951091),
[Voicy](https://community.home-assistant.io/t/voicy-voice-activated-telegram-bot-to-control-home-assistant/473182)).
The piece to borrow is the *shape* (download → `ffmpeg` → local STT → `sendVoice` with the text as
`caption`), not any of those stacks: Zoe's STT, brain and TTS are the rocks.

Decode and encode on this box: `~/.local/bin/ffmpeg` is a static 7.0.2 build with both `opus`
and `libopus` decoders and the `libopus` encoder; `/usr/bin/sox` is present (no Opus). No
`opusdec`/`opusenc` binaries. `ffmpeg -i note.ogg -ac 1 -ar 16000 -sample_fmt s16 note.wav` gives
exactly what `_prepare_audio_for_moonshine` expects (16 kHz mono, `voice_tts.py:2546`), and
`ffmpeg -i reply.wav -c:a libopus -b:a 32k -ar 48000 -ac 1 -application voip reply.ogg` produces a
Telegram-playable voice note. Note `zoe-data.service` runs `/usr/bin/python3` and `tts_waterfall.py`
finds `ffmpeg` with `shutil.which` (`tts_waterfall.py:147-154`), so a decode step *inside* zoe-data
must resolve the binary explicitly or carry `~/.local/bin` on PATH — a decode step in the bot
process (Node, `child_process`) has the same PATH question. Either way it is a configuration, not a
dependency.

### 1.3 Photos — can the brain see on this box?

- **Bot side is trivial.** `message.photo` is an array of `PhotoSize` (`width`, `height`,
  `file_id`, optional `file_size`); Telegram re-encodes photos to JPEG, largest side ≈ 1280 px
  [unverified exact], so the biggest size is a few hundred KB — well under `getFile`'s 20 MB.
  `sendPhoto` is limited to 10 MB and width + height ≤ 10000.
- **llama.cpp side exists.** `llama-server … --mmproj projector.gguf` enables vision; the projector
  is GPU-offloaded unless `--no-mmproj-offload`; requests use the OpenAI `image_url` content part
  (base64 `data:` URI works with no egress). Gemma 4 E2B/E4B are in the supported list
  ([multimodal.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)). Gemma 4's
  image token budget is configurable — 70, 140, 280, 560 or 1120 tokens per image via
  `--image-min-tokens` / `--image-max-tokens` ([HF Gemma 4 blog](https://huggingface.co/blog/gemma4),
  [dev.to note](https://dev.to/someoddcodeguy/a-quick-note-on-gemma-4-image-settings-in-llamacpp-39ng)).
  Two Gemma-4-on-llama.cpp facts that matter here: image tokens of E2B/E4B must be decoded
  *causally* (issue #28318, 2026-09-03, closed by #28335 — whether that fix is in b11194 is
  [unverified]; if not, image text-reading "has many errors on E4B"), and all image tokens must fit
  one `ubatch`.
- **What it costs.** The Unsloth E4B GGUF repo ships `mmproj-F16.gguf` **990 MB**, `mmproj-BF16.gguf`
  **992 MB**, `mmproj-F32.gguf` 1.91 GB ([HF tree](https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/tree/main)).
  On the Orin's unified memory a GPU-offloaded projector is ~1 GB more *resident*, plus the encoder's
  compute buffers at request time. The 12B trial on this box ran a q4_0 mmproj: image input took
  **8.68 s** and llama-server sat at ~8.7–9.1 GB RSS ([GEMMA4_12B_TRIAL_2026-06-08.md](../benchmarks/GEMMA4_12B_TRIAL_2026-06-08.md)).
- **What blocks it here.** (i) RAM: the profile measured MemAvailable **0.42–0.56 GB** with the
  voice stack resident ([memory-pressure-profile-2026-10-03.md:17](../knowledge/memory-pressure-profile-2026-10-03.md));
  `/proc/meminfo` read **1.29 GB** on 2026-10-04 with one agent session running and the profile's
  no-agent estimate is 1.5–1.7 GB (`:235`). +1 GB resident eats the whole quiet headroom the brain
  window policy demands (≥ 2 GB). (ii) The iGPU bug: multimodal requests crash in CLIP encoding
  because the CUDA pool hard-codes a 32 GB VMM reserve per pool, two pools for multimodal — open
  issue #29142 (2026-09-19); the workaround is a rebuild with `-DGGML_CUDA_NO_VMM=ON`
  ([issue](https://github.com/ggml-org/llama.cpp/issues/29142)), which changes the allocator for the
  *text* path too and therefore must pass the replay gate before it serves voice. (iii) The unit:
  `--mmproj` is absent (`scripts/setup/systemd/llama-server.service:129-153`), no projector file
  exists in `~/models/gemma4-e4b-qat/` (listing: model, MTP drafter, their pre-HF backups, a staging
  dir), and `tests/unit/test_llama_server_unit_flags.py` pins the flag set. (iv) The Flue lane:
  `zoeLocalModel()` declares `input: ['text']` so pi-ai replaces an image block with
  "(image omitted)" (`labs/flue-zoe-brain-2x/src/providers/capped-completions.ts:560-561,579`), and
  the context-window estimator bills an image block at a flat 4,800 tokens (`context-window.ts:102`)
  — more than half the 8,192 slot — which would have to become the real 70–1,120 budget. The Flue
  wire *does* accept images: the sidecar's payload schema is `{message, images}`
  (`services/zoe-data/zoe_flue_client.py:1474-1476`; `src/replay-mode.ts:37`), today unused.
- **Alternatives when the brain cannot take images cheaply.** A small captioner that turns the photo
  into text, so the *text* turn is unchanged: Moondream2 (1.9B) runs ~15 s/image on a Raspberry
  Pi 5 ([pristren](https://pristren.com/blog/moondream2-edge-vision-model/)); SmolVLM2-500M Q8 on
  llama.cpp CPU captioned at ~0.26 s/image on x86 in batch ([immich issue](https://github.com/sam-dumont/immich-video-memory-generator/issues/934))
  — on the Orin's CPU cores or the Pi 5 expect seconds, not sub-second [unverified]; the GLaDOS
  project runs FastVLM on an 8 GB SBC (already on the scouting board, companion audit §4). A
  SmolVLM-256M/500M GGUF + its own mmproj is ~300–600 MB and can run as a *short-lived* process
  inside a `systemd-run --scope -p MemoryMax=…` cap, so it costs nothing while idle — the same
  discipline the lab README uses for builds (`labs/flue-zoe-telegram-2x/README.md:134-139`).

## 2. Our system today (read-only)

### 2.1 The Telegram lane — `labs/flue-zoe-telegram-2x`

- **Deployed, not a lab.** `flue-zoe-telegram.service` runs `node dist/server.mjs` from this
  directory on `:3582` (`scripts/setup/systemd/flue-zoe-telegram.service:25-27`), and `deploy.yml`
  rebuilds + restarts the unit on any merged diff under the path (`.github/workflows/deploy.yml:424-476`;
  `labs/AGENTS.md:40-47`). A one-line change here reaches production on merge. The 1.x bot was
  removed 2026-09-25; rollback is `git revert` (CANONICAL "Retired").
- **Transport.** grammY long-polling — "the bot reaches OUT to Telegram, so nothing is exposed on
  the Jetson — no public ingress, no Cloudflare route" (`src/telegram.ts:4-6`). The token comes
  from `TELEGRAM_BOT_TOKEN` in the lab-local `.env` (`telegram.ts:45`; unit `EnvironmentFile`
  at `:35`; `.env.example` names exactly `TELEGRAM_BOT_TOKEN`, `PORT`, `ZOE_DATA_URL`,
  `ZOE_INTERNAL_TOKEN`). Node 22 Happy-Eyeballs: the unit sets
  `NODE_OPTIONS=--network-family-autoselection-attempt-timeout=1500` because the ~410 ms RTT to
  `api.telegram.org` exceeded Node's 250 ms per-address attempt and every connect aborted
  `ETIMEDOUT` (`flue-zoe-telegram.service:37-46`; recorded in
  [state-of-zoe-review-2026-09-25.md](../knowledge/state-of-zoe-review-2026-09-25.md)). Any new
  outbound call the media path adds (file download, `sendVoice` multipart) rides the same `fetch`
  and inherits this fix; a *separate* HTTP client would not.
- **Update types handled.** `bot.start({ allowed_updates: ['message'] })` (`src/app.ts:132`), so
  every message type — text, voice, photo, document, sticker — is delivered. Handlers: `/start`
  (`:70`), `/new` + `/reset` (`:84`), and `message:text` (`:99`). **There is no `message:voice` or
  `message:photo` handler: a voice note or photo is received and ignored, and the sender gets no
  reply.** `/health` is 200 only while polling (`:183-187`); the watchdog timer restarts the unit on
  503 (`flue-zoe-telegram-watchdog.service:1-10`).
- **How it talks to the brain.** Four plain-HTTP contracts to zoe-data (README `:52-57`):
  `GET /api/system/resolve-telegram/<id>` (`src/brain.ts:138-148`),
  `POST /api/system/telegram/consume-link-token` (`:157-175`),
  `POST /api/system/telegram/register-bot` (`:196-214`), and the turn itself —
  `POST /api/chat/?stream=false` with `X-Zoe-User-Id` + `X-Internal-Token` and body
  `{message, session_id, channel: 'telegram'}` (`:224-238`). The reply is `data.response`. Session id
  is `telegram-<chatId>[-e<epoch>]` (`:127-131`). The Flue agent in `src/agents/zoe.ts` is a
  placeholder that is never dispatched; **no model provider is registered in this process and the
  local Gemma is not wired here** (`app.ts:7-10`).
- **"Voice path untouched."** The phrase is stamped on `app.ts:33` and `telegram.ts:17`. It means:
  this lane calls `/api/chat` (the *text* pipeline) and nothing under the replay gate's
  `VOICE_PATH_PATTERNS` — `routers/voice_tts.py`, `zoe_core_client.py`, `fast_tiers.py`,
  `voice_delivery.py`, `tts_waterfall.py`, the dependency manifest, the models dir
  (`scripts/maintenance/voice_gate_check.py:75-89`). A change confined to the lane therefore
  deploys without the nightly replay gate standing in front of it, *and* cannot regress the panel's
  voice turn. Stage 1 must keep that property: media plumbing lives in the lane (or a new, separate
  zoe-data router), not in `voice_tts.py`.
- **Memory posture.** `MemorySwapMax=0`, `MemoryLow=256M`, `MemoryMax=1G`
  (`flue-zoe-telegram.service:89-91`) — pure userspace Node, so the cap is real; external Buffers
  (a downloaded OGG, a WAV in flight) are outside V8's heap and *can* hit the ceiling (`:69-71`).
  Media must stream to disk, not accumulate in memory.
- **Channel profile.** `fast_tiers.CHANNEL_PROFILES["telegram"] = {run_tier0: True, allow_writes:
  True}` (`services/zoe-data/fast_tiers.py:66`): a Telegram turn can write (reminders, lists) as the
  linked member. `chat.py:_resolve_channel` only honours known channels (`routers/chat.py:2674-2699`).

### 2.2 Identity — a Telegram chat must map to a member

- "Identity IS the gate (no static allow-list)": `handleIncoming` resolves the Telegram-verified
  sender id → Zoe `user_id`; unlinked senders get the onboarding text and **never reach the brain**
  (`src/handler.ts:5-9,51-64`). `/new` has the same gate with a negative control
  (`handler.ts:133-150`).
- The mapping lives in `user_preferences.prefs.telegram_id`, unique per Telegram id, last-writer-wins
  (`services/zoe-data/routers/system.py:2950-2990`, `:3038-3049`; id regex `^[1-9][0-9]{1,19}$` at
  `routers/user_profile.py:18`). Linking: a 10-minute HMAC-signed, single-use token minted in an
  authenticated session, shown as a `t.me/<bot>?start=<token>` deep link + QR, redeemed by the bot
  with the *Telegram-supplied* sender id (`services/zoe-data/telegram_link.py:1-21,46-62`). That is
  the "QR on the panel, finish on the phone" reference flow of VISION principle 8.
- The turn is trusted-forwarded: zoe-data honours `X-Zoe-User-Id` only from internal callers
  (loopback or `X-Internal-Token`; `brain.ts:219-223`), and `ZOE_INTENT_DISPATCH_REQUIRE_TOKEN`
  semantics are documented at `services/zoe-data/auth.py:375-400`.
- zoe-data also holds its *own* copy of the bot token as `ZOE_TELEGRAM_BOT_TOKEN` for the
  "send to my phone" hand-off (`auth_handoff.py:381-382,403-415`) — two processes, two env files, one
  secret; a rotation must touch both. The record notes the names only.
- **What identity does *not* do.** There is no speaker-ID on a Telegram voice note and no face-ID
  on a photo; the panel's enrolled voice/face identity (memory: Phase 1+2 live) is a *panel* feature.
  On Telegram the account is the credential, which is the same strength the text lane already
  accepts. Owner-only abilities (`fast_tiers` voice-scope gate, `:55`) remain owner-only by user id,
  not by channel.

### 2.3 Voice ingest routes in zoe-data — `routers/voice_tts.py`, `routers/voice_livekit.py`

| Route | Takes | Auth | Returns | Notes |
|---|---|---|---|---|
| `POST /api/voice/transcribe` (`voice_tts.py:2737`) | JSON `{audio_base64, panel_id}`; sniffs `RIFF`→`.wav`, `\xff\xfb`→`.mp3` (`:2752-2756`) | `_require_voice_auth` = **device token or non-guest session** (`:750-765`) | `{text…}` | **Broadcasts `voice:transcript` to `"all"` panels** unless the caller is a `replay-` instrument with device auth (`:2778-2800`). Captures into the STT corpus by default (`_transcribe_audio(capture=True)`, `:2683-2693`). Wrong endpoint for Telegram: it would put a phone whisper on the wall screen and seed the panel corpus with phone-mic audio. |
| `POST /api/voice/turn` (`:4849`) | JSON `{audio_base64, panel_id, identified_user_id}` | same | transcript + reply + TTS | The panel's one-shot; same broadcast + capture behaviour. |
| `POST /api/voice/synthesize` (`:2123`) | JSON `{text, …}` | same | **`audio/wav`** (Kokoro/espeak) or `audio/mpeg` (Edge) bytes (`:2141-2181`) | Kokoro sidecar: `POST /synthesize → audio/wav`, **24 kHz** (`scripts/setup/kokoro_sidecar.py:15,63`). A Telegram reply needs one `libopus` encode after this. |
| `POST /api/voice/livekit-audio` (`voice_livekit.py:1587`) | **multipart** `audio` + `session_id` | `_require_livekit_media_auth`: device token, non-guest session, or a validated guest session (`:1500-1522`) | `{transcript, response_text, audio_base64, content_type}` | The browser PTT path. It writes `.webm`/`.ogg`/`.wav` by `content_type` (`:1608-1612`) and hands the file straight to `_transcribe_audio` → `load_wav_file`, which "Supports 16-bit and 24-bit PCM WAV files" only (`moonshine_voice/utils.py:50-55`). **By reading, a non-WAV upload here raises and returns `"STT failed"` (`:1613-1617`)** [not executed]; `voice.html` must therefore be sending WAV today, and the same WAV-only truth applies to anything Telegram sends. |

STT is one in-process Moonshine singleton behind `_moonshine_infer_lock` (`voice_tts.py:2431-2437`):
Telegram transcriptions **serialise with the panel's**. A 10 s note at the nightly STT median
(568 ms for ~4 s samples) is ~1–1.5 s of lock time [unverified scaling]; acceptable for a chat lane,
but it is why Telegram STT must never run with a lower priority than, or concurrently against, a
live panel turn — it queues.

### 2.4 LiveKit on demand — the nearest thing to a call Zoe already has

- `livekit-server` runs as a Docker container (`docker-compose.yml:175-204`, config
  `services/livekit/config.yaml`: `port: 7880`, RTC `50000–50200`), **started on the first
  `/livekit-token` request and reaped after `ZOE_LIVEKIT_IDLE_TIMEOUT_S` (300 s) idle — "keeps the
  ~560MB WebRTC server out of memory except while a voice page is actually in use"**
  (`voice_livekit.py:174-199,260-290`). `LIVEKIT_API_KEY/SECRET` live in the env (`:429-436`); the
  join token is never anonymous (`_require_livekit_auth`, `voice_tts.py:768-830`).
- The agent loop joins room `zoe-voice`, runs energy VAD on PCM frames, optional barge-in and Smart
  Turn behind flags (`ZOE_VOICE_BARGE_IN`, `ZOE_SMART_TURN_ENABLED`, `:89-148`), and for each segment
  runs STT → brain → sentence-streamed TTS back into the room (`_run_pipeline`, `:580-735`;
  `_stream_sentence_audio`, `:500-545`). That is a **segment-at-a-time** conversation loop over a
  media room — the exact abstraction a SIP bridge or a phone-side WebRTC page would plug into.
- `voice.html` holds the push-to-talk button and today uses the HTTP upload path
  (`services/zoe-ui/dist/voice.html:688,838`: "HTTP upload path (POST /api/voice/livekit-audio) is
  the current fallback"), i.e. plain HTTPS multipart — tunnel-friendly. There is no SIP component,
  no Redis (retired, `docker-compose.yml:300`), and no public UDP ingress on the box.

### 2.5 The brain and the Flue sidecar — what an image would need

Live command line (read 2026-10-04): `~/llama.cpp-b11194/build-jetson/bin/llama-server --model
…gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf --model-draft …mtp-gemma-4-E4B-it.gguf --spec-type draft-mtp …
--ctx-size 8192 --parallel 1 --cache-type-k q8_0 --cache-type-v q8_0 --cache-ram 2048 --flash-attn
on … --jinja --reasoning off --load-mode mmap+mlock --metrics` — matches the tracked unit, no
`--mmproj`. llama-server at **7,629 MB** VmRSS, Kokoro **1,907 MB**, zoe-data ~1.1 GB
(profile `:94-95,157`). To pass a photo through the live lane, four seams change: (1) the unit gains
`--mmproj` (+ a projector file, + the `GGML_CUDA_NO_VMM` rebuild, + the flag-pin test, + a replay
PASS); (2) `zoe_flue_client.py` fills the `images` field it already knows the schema accepts
(`:1474-1476`); (3) `zoeLocalModel()` declares `input: ['text', 'image']` and the context estimator
bills the real image budget (`capped-completions.ts:579`, `context-window.ts:102`); (4) pi-ai's
`openai-completions` handler then emits the `image_url` part — pi-ai 0.83.0 is pinned
(`labs/flue-zoe-brain-2x/package.json:19`) and whether that handler emits base64 data URIs for
local providers is [unverified] (its `dist/` was not grepped — it is not vendored in this worktree).
The `/api/chat` body has no attachment field today (`routers/chat.py` references images only for
the research-screenshot feature, `:1311-1365`).

## 3. Design — three stages, all behind default-OFF flags

Principles applied: the lane stays "voice path untouched" (no edit to `voice_tts.py` or any
`VOICE_PATH_PATTERNS` file); media plumbing is the bot's job, intelligence stays in zoe-data; every
new zoe-data endpoint is internal-token gated like the existing four; nothing new is resident.

### 3.1 Stage 1 — voice notes in and out (Bot API only)

**Bot (`labs/flue-zoe-telegram-2x`).** Add `bot.on('message:voice', …)` beside `message:text`
(`app.ts:99`), reusing `handleIncoming`'s identity gate, extracted as `handleVoice` in `handler.ts`
with injected deps so it is testable offline like the others:

1. Gate: resolve sender → member (unchanged). Unlinked → the existing onboarding text.
   Forwarded media (`message.forward_origin` present) → polite refusal by default (§6).
2. Bounds: `voice.duration ≤ ZOE_TELEGRAM_VOICE_MAX_S` (default 120) and `file_size ≤ 2 MB`
   before `getFile`. Over → "that one's too long for me, try a shorter note".
3. `getFile` → stream the OGG to a `0600` temp file under the package `data/` dir (never into a
   Buffer; the cgroup is `MemoryMax=1G`). The download URL embeds the bot token — **never log it**.
4. Decode: `ffmpeg -i in.ogg -ac 1 -ar 16000 -sample_fmt s16 -f wav out.wav` via `child_process`
   with a 10 s timeout; `ZOE_TELEGRAM_FFMPEG` names the binary (default `ffmpeg` on PATH; on this box
   that is `~/.local/bin/ffmpeg`).
5. STT + turn: `POST /api/system/telegram/voice-turn` (new, internal-token gated, §3.1 zoe-data)
   with multipart `audio` (WAV) + `user_id` + `session_id`; returns `{transcript, response}`.
   Alternative that adds *no* zoe-data code: `POST /api/voice/livekit-audio` already does
   STT → brain → TTS from a WAV upload — but it needs a device token or session, attributes the turn
   to the token's principal (`voice-daemon`), bypasses the `telegram` channel profile and the
   `X-Zoe-User-Id` trusted path, and runs `brain_oneshot(..., voice_mode=True)`
   (`voice_livekit.py:1638`) instead of `/api/chat`. Not the same Zoe. Rejected.
6. Reply: text first (always — the chat stays searchable, and it is today's behaviour), then if
   `ZOE_TELEGRAM_VOICE_REPLY=voice` a voice note: `POST /api/system/telegram/speak` (new, internal)
   → WAV → `ffmpeg -c:a libopus -b:a 32k -ar 48000 -ac 1 -application voip` → `sendVoice` with
   `caption` = the reply text (≤ 1024 chars). Reply mode defaults to `text`; `both` is the likely
   setting for Jason.
7. Cleanup: unlink temp files in `finally`; per-stage timings to the log (download, decode, stt,
   brain, tts, encode, upload) — those timings *are* the measurement (§5).

**zoe-data (new router `routers/telegram_media.py`, mounted only when
`ZOE_TELEGRAM_MEDIA=1`).** Two internal endpoints under `/api/system/telegram/`, gated with the
same internal-token dependency the resolver uses (`require_intent_dispatch_auth` semantics,
`auth.py:375`):

- `voice-turn`: multipart WAV → `_transcribe_audio(path, capture=False)` (imported exactly as
  `voice_livekit.py:1612` does; **no `voice:transcript` broadcast, no corpus capture**) → the same
  internal chat call the bot makes today (`channel: 'telegram'`, `X-Zoe-User-Id`) → `{transcript,
  response}`. Rejects non-RIFF bodies and > 16 kHz × 120 s payloads.
- `speak`: `{text}` → `tts_waterfall` → WAV bytes. No broadcast.

Neither touches `voice_tts.py`. The replay gate therefore does not front this PR; the bot's own
offline suite does (§5).

**Flags (all default OFF / current behaviour):** `ZOE_TELEGRAM_VOICE_NOTES` (bot; off = voice
messages ignored as today), `ZOE_TELEGRAM_VOICE_REPLY=text|voice|both` (bot; default `text`),
`ZOE_TELEGRAM_VOICE_MAX_S` (bot; 120), `ZOE_TELEGRAM_ALLOW_FORWARDED` (bot; off),
`ZOE_TELEGRAM_FFMPEG` (bot; `ffmpeg`), `ZOE_TELEGRAM_MEDIA` (zoe-data; off = router not mounted,
404), `ZOE_TELEGRAM_STT_CAPTURE_DIR` (zoe-data; unset = no capture; if set, phone-mic clips go to a
*separate* corpus, never `~/.zoe-voice-samples`). Regenerate the flag inventory after adding readers
(memory: flag-inventory papercut).

### 3.2 Stage 2 — photos

Two sub-stages, because the honest one depends on RAM that does not exist today.

**2a — caption-to-text (measured spike first).** Bot: `message:photo` → largest `PhotoSize` ≤
`ZOE_TELEGRAM_PHOTO_MAX_BYTES` (2 MB) → `getFile` → temp file → `POST /api/system/telegram/photo-turn`
(internal) with the JPEG + the `caption` text the user typed. zoe-data runs a short-lived captioner
in a capped scope (`systemd-run --user --scope -p MemoryMax=800M -p MemorySwapMax=0 …`
`llama-mtmd-cli`-style one-shot with a SmolVLM-class GGUF + its mmproj, CPU only, `ZOE_PHOTO_CAPTIONER_CMD`),
and feeds the brain a text turn: `"[Photo from <member>: <caption>] <user's caption text>"`. No
resident cost; cost per photo = cold start + encode + decode on CPU, to be measured (§5). The
projector and model files are a download step for the operator, named in the flag, not in the repo.
Reject if the captioner takes > `ZOE_PHOTO_CAPTION_TIMEOUT_S` (20) or the scope OOMs; Zoe says she
couldn't see it. **Honest limit:** a caption is lossy; "what does this receipt say" will be worse
than native vision. That is the trade for zero resident RAM.

**2b — native Gemma 4 vision (W3 RAM gate).** When the brain-window policy's ≥ 2 GB quiet headroom
exists *after* a 1 GB projector (i.e. the inference audit's actions 1, 3, 4 have landed and been
measured), and only then: rebuild b11194+ with `-DGGML_CUDA_NO_VMM=ON` (or a fixed #29142), add
`--mmproj mmproj-BF16.gguf --image-max-tokens 280` to the unit, update the flag-pin test, replay-gate
the text path in a brain window, verify #28335 is in the build (image-text reading on E4B), then open
the Flue seams (§2.5 items 2–4) behind `ZOE_BRAIN_IMAGES=1` and the bot's `ZOE_TELEGRAM_PHOTOS=native`.
Budget an image at 280 tokens of the 8,192 slot and keep it in the *tail* of the prompt (the
prefix-cache rule, `llama-server.service:118-128`). Until that gate, 2b is **NO-GO**; 2a is the
product.

**Flags:** `ZOE_TELEGRAM_PHOTOS=off|caption|native` (bot; default `off` = photos ignored as
today), `ZOE_PHOTO_CAPTIONER_CMD`, `ZOE_PHOTO_CAPTION_TIMEOUT_S`, `ZOE_TELEGRAM_PHOTO_MAX_BYTES`,
`ZOE_BRAIN_IMAGES` (zoe-data + sidecar; default off).

### 3.3 Stage 3 — "call Zoe" with the chosen route

**3a — one tap from Telegram to Zoe's own talk page (chosen).** The bot answers `/talk` (and a
voice note that says "call me" is just a text turn) with an inline **URL button** to the voice page
at Zoe's tunnel hostname (`web_app` buttons need HTTPS and are the Mini-App form of the same thing;
start with a plain URL button, the "known member gets a Telegram link" case of VISION principle 8).
On the phone that page is `voice.html`'s push-to-talk over `POST /api/voice/livekit-audio` — HTTPS
multipart through Cloudflare Access, audio never leaves the box except for that hop, no SIP, no
UDP, no second account. Preconditions to measure, not assume: (i) the page must send WAV (or the
endpoint must gain the same `ffmpeg` decode as stage 1 — the `.webm`/`.ogg` branch at
`voice_livekit.py:1608-1612` is dead on arrival by reading); (ii) the phone browser must hold a
Zoe session that passes `_require_livekit_media_auth` (Cloudflare Access + `zoe-auth`); (iii) if
the LAN LiveKit WebRTC mode is wanted remotely it needs TURN/TCP — out of scope; the HTTP PTT path
is the deliverable. It is half-duplex and it is what the panel's own "Talk" button is; honest naming
in the UI ("Talk to Zoe"), not "Call".

**3b — a real number (conditional, operator opt-in).** If Jason wants "ring Zoe from any phone":
prefer **Twilio Programmable Voice + bidirectional Media Streams over WSS** (ingress through the
existing tunnel, 8 kHz µ-law ↔ 16 kHz PCM shim into the LiveKit agent loop's segment pipeline,
`voice_livekit.py:580`) over **`livekit/sip` + Redis + public UDP 5060/RTP** (heavier, but keeps the
media loop inside the room Zoe already runs). Costs: US$3/mo + US$0.01/min inbound (AU). The audio
transits a carrier and Twilio — an explicit, documented opt-in under VISION principle 2, flagged
`ZOE_PSTN_INBOUND=1`, never default. Design only in this record.

**3c — Telegram userbot calls: NO-GO** (§1.1): second phone number, LGPL native library with
crash history, automation posture outside the API terms' "with the user's knowledge and consent"
framing, and a full-duplex loop Zoe's turn-based pipeline does not provide. Revisit only if
Telegram ever gives *bots* a call API.

## 4. RAM and latency budget per stage (Jetson Orin NX 16 GB)

Resident today (profile 2026-10-03): llama-server 7.63 GB, Kokoro 1.91 GB, zoe-data ~1.1 GB,
router 0.38 GB; MemAvailable 0.42–0.56 GB with an agent session, 1.29 GB read today, ~1.5–1.7 GB
estimated with none. Every number below marked **est.** is an estimate to be replaced by §5's
measurements.

| Stage | Resident delta | Transient peak | Latency (5 s note / 1 photo) | Notes |
|---|---|---|---|---|
| 1 voice note | **0** | `ffmpeg` decode ~30–60 MB (**est.**) in the bot cgroup (1 GB cap); WAV for 120 s = 3.8 MB on disk; encode ~40–70 MB (**est.**) | download 0.4–1 s (one Telegram RTT ≈ 410 ms + body) · decode < 0.2 s (**est.**) · STT ~0.6–1.2 s (nightly median 568 ms for ~4 s; serialised behind the panel) · brain ~2.0 s (nightly median 2,030 ms) · Kokoro ~1 s for a 12 s reply (RTF 0.08) · encode < 0.3 s (**est.**) · `sendVoice` 0.5–1 s → **≈ 4–6 s text, ≈ 6–8 s with voice reply** | Zero new resident memory; nothing changes for the panel except STT lock contention (§2.3). |
| 2a caption | **0** | captioner scope ≤ 800 MB (cap), realistic 300–600 MB (**est.**) for a 256M–500M VLM on CPU | cold start 1–3 s + encode/decode 3–10 s on the Orin's A78AE cores (**est.** — Pi 5 Moondream2 is 15 s; SmolVLM2-500M is far lighter) + brain 2 s → **≈ 6–15 s** | Only runs while a photo is in flight; must refuse to start when MemAvailable < cap + 0.5 GB (read `/proc/meminfo` first — the brain-window rule in reverse). |
| 2b native | **≈ +1.0 GB** (BF16 projector, GPU-offloaded) + encoder compute buffers at request time (**est.** 100–300 MB) | — | image prefill at 280 tokens ≈ one extra `llama_decode` of ~280 tokens ≈ 0.3–0.6 s (**est.**, from the audit's 196 ms for 11–31 tokens scaling with batch) + CLIP encode (12B trial: 8.68 s whole request on q4_0) | **Unaffordable until W3**; also a text-path rebuild (NO_VMM) that must pass the replay gate. |
| 3a talk page | **0** (HTTP PTT) or +~560 MB *while in use* if LiveKit mode is used (on-demand container, `voice_livekit.py:176`) | as stage 1 | as the panel's PTT turn + tunnel RTT | Already the panel's path. |
| 3b number | `livekit/sip` container + Redis ≈ 150–250 MB (**est.**) resident if always-on, or on-demand like LiveKit; Twilio-WSS shim ≈ 0 (in zoe-data) | — | carrier setup 1–3 s + the room loop | Opt-in only. |

## 5. Measurement plan

Instrument-first, per the standing rule (memory: "verify your instruments"): every instrument below
has a negative control, and a skipped or timed-out check is a failure, not a pass.

1. **Corpus replay as Telegram voice notes (stage 1 acceptance).** `~/.zoe-voice-samples` holds
   1,311 WAVs (16 kHz mono 16-bit, e.g. `000438_185.wav` = 4.40 s). Script
   `scripts/maintenance/telegram_voice_replay.py` (new): for the gate's scoreable subset, encode each
   WAV to OGG/Opus at Telegram-like settings (`libopus`, 32 kbps, 48 kHz, `voip`), run it through the
   **same decode function the bot uses** (`ffmpeg` → 16 kHz WAV) and through
   `/api/system/telegram/voice-turn` on a **parallel-port** zoe-data (never the live one; the
   lane's own README mandates parallel ports, `:143-150`) with `ZOE_TELEGRAM_MEDIA=1`; compare each
   transcript with the WAV-path transcript from the nightly replay. Report the WER delta — that
   number *is* the cost of the Opus round-trip and should be ≈ 0; and the per-stage latencies.
   Negative controls: (a) a quarantined non-speech file → empty transcript; (b) break the decoder
   (`-ar 8000`) → the WER delta must go red; (c) `ZOE_TELEGRAM_MEDIA` unset → 404 on every call.
   Resource rule: the probe's own floor is 700 MB for `--stt remote` (`voice_regression_probe.py:925`);
   do not run while MemAvailable < 1 GB, and never during a replay gate (the Moonshine lock is shared).
2. **Bot offline suite.** Extend `test/helpers/mock-telegram.ts` with `getFile`, the
   `/file/bot<token>/…` download route and a multipart `sendVoice` sink; add `test/voice_note.test.ts`
   driving the REAL grammY handler: linked sender → one `voice-turn` call → one `sendMessage` (+ one
   `sendVoice` when `ZOE_TELEGRAM_VOICE_REPLY=both`); unlinked sender → onboarding text and **zero**
   zoe-data calls; forwarded note → refusal and zero calls; oversize → refusal before `getFile`;
   flag off → no handler registered (the message falls through untouched). `npm test` stays fully
   offline, no token.
3. **zoe-data unit tests** (co-located `ci_safe` marker, per the CI rule): the two internal endpoints
   with `_transcribe_audio` and the chat call stubbed; non-WAV body → 400; missing internal token →
   403; the router is absent when the flag is unset (negative control).
4. **RAM.** Before/after 20 notes: bot cgroup `memory.peak`, zoe-data `VmRSS`/`VmHWM`, host
   `MemAvailable`; `ffmpeg` peak via `/usr/bin/time -v` in the replay script. Pass = zoe-data RSS
   flat (± 50 MB) and bot peak < 300 MB.
5. **Stage 2a spike.** One capped scope run of the captioner over 20 photos of household-free test
   images (public domain), recording cold start, per-image latency, `memory.peak` and three human
   judgements per caption; stop condition: p50 > 15 s or any OOM in the scope → shelve 2a and wait
   for W3.
6. **Stage 3a.** From a phone on mobile data: `/talk` → page loads behind Access → one PTT turn
   → audio plays; log the `livekit-audio` content type actually sent by the phone browser (the
   WAV-only finding of §2.3 is the thing to confirm). No Jetson change needed to measure it.

## 6. Security

- **Tokens stay in env files.** `TELEGRAM_BOT_TOKEN` and `ZOE_INTERNAL_TOKEN` live in the lab's
  `.env` (`flue-zoe-telegram.service:30-35`); `ZOE_TELEGRAM_BOT_TOKEN` in zoe-data's env. This record
  names the variables and never their values; the `getFile` download URL **contains the bot token**
  and must never be logged, echoed in an error, or stored — log `file_unique_id` instead.
- **Bounded ingress.** Size and duration caps before download; temp files `0600`, unlinked in
  `finally`; decode with a timeout and `-nostdin`; never a shell string (`execFile`, argv array).
  `ffmpeg` is a large attack surface on hostile media; a Telegram file comes from a *linked member's*
  account only (unlinked senders never reach the download step), which bounds the threat to a
  compromised member phone.
- **Forwarded media is content, not command.** A forwarded voice note is someone else's speech; with
  `allow_writes: True` on the telegram profile it could create reminders or lists "as" the member.
  Default refuse (`ZOE_TELEGRAM_ALLOW_FORWARDED=0`); when allowed, prefix the transcript as quoted
  material the way `untrusted-content.ts` already wraps tool output in the brain lane.
- **No panel leakage.** The new endpoints do not emit `voice:transcript` or any push event; a phone
  whisper never appears on a wall screen. No capture into the panel corpus by default.
- **Identity unchanged.** Media turns run as the linked member via the same trusted-forward path;
  no new allow-list, no new principal. Owner-only abilities remain gated by user id.
- **Calls.** 3a adds no ingress (the page already exists behind Access). 3b would open a carrier
  path and public UDP; it is an opt-in with its own threat model (toll fraud on outbound, SIP
  scanners on 5060) and is explicitly *not* part of stages 1–2.

## 7. Go / no-go against VISION

| Item | Verdict | Why |
|---|---|---|
| Stage 1 voice notes in/out | **GO** | Local-first (decode, STT, brain, TTS all on the box; the only off-box hop is Telegram's own, which the text lane already takes); identity-gated by the existing link; zero resident RAM; "voice path untouched" preserved by construction; measurable with the corpus; ships behind flags default OFF. Principles 2, 3, 4, 8 satisfied. |
| Stage 2a photo → caption | **GO to spike, GO to ship if the spike passes §5.5** | Zero resident RAM, local, lossy by design — honest about what it is. Principle 6 ("borrow the piece"): a tiny captioner is the piece; its framework stays out. |
| Stage 2b native Gemma 4 vision | **NO-GO now; re-evaluate at the W3 RAM gate** | +1 GB resident on a box at 0.4–1.3 GB free, an allocator rebuild on the text path, an unverified fix in our build. Principle 1 (optimise around the rock, never risk it) and principle 3 (lab-prove first) both say wait. |
| Stage 3a "Talk to Zoe" button → own PTT page | **GO** | It is principle 8 verbatim (hand the phone a link), uses only what exists, and is private. Named honestly as talk, not call. |
| Stage 3b dialable number (LiveKit SIP or Twilio WSS) | **CONDITIONAL — operator opt-in only** | Audio leaves the box to a carrier: allowed only "if Jason opts in" (principle 2). Prefer the WSS variant for the tunnel; no default, no auto-enable. |
| Stage 3c Telegram userbot calls | **NO-GO** | Platform does not permit bots to call; the workaround is a second account under automation terms, a crash-prone native lib, and a duplex loop we do not have. Nothing in VISION is served by it that 3a does not serve more privately. |

**Sequencing.** Stage 1 (one PR: lane + the small new router + tests + replay script), then 3a (a
one-line button plus the phone-side WAV check — it may need stage 1's decode in `livekit-audio`),
then the 2a spike as a measured experiment. 2b and 3b wait on operator decisions that are recorded
here so they are not re-litigated.

## 8. Sources

Primary, fetched 2026-10-04 unless noted:

- Telegram Bot API 10.3 — https://core.telegram.org/bots/api (sendVoice, getFile, sendPhoto, Voice,
  PhotoSize, video_chat_* service messages, business_connection); changelog —
  https://core.telegram.org/bots/api-changelog; Bot features (Business bots, Local Bot API limits) —
  https://core.telegram.org/bots/features; Bot FAQ (20 MB / 50 MB) — https://core.telegram.org/bots/faq
- Telegram calls protocol — https://core.telegram.org/api/end-to-end/voice-calls; API Terms of
  Service — https://core.telegram.org/api/terms
- pytgcalls — https://github.com/pytgcalls/pytgcalls, https://pypi.org/project/py-tgcalls/ (3.0.0,
  2026-09-25); ntgcalls — https://github.com/pytgcalls/ntgcalls, https://pypi.org/project/ntgcalls/
  (2.1.0, 2026-02-05, aarch64 wheels cp311–cp314), crash report
  https://github.com/pytgcalls/ntgcalls/issues/61; awesome-tgcalls — https://github.com/tgcalls/awesome-tgcalls;
  CallMeBot — https://www.callmebot.com/telegram-call-api/
- Community: "Can a Telegram bot initiate voice calls?" —
  https://community.latenode.com/t/can-a-telegram-bot-initiate-voice-calls/19748
- LiveKit SIP bridge — https://github.com/livekit/sip; inbound trunk —
  https://docs.livekit.io/telephony/accepting-calls/inbound-trunk/; Twilio Voice pricing (AU) —
  https://www.twilio.com/en-us/voice/pricing/au
- WhatsApp Business Calling API pricing —
  https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/pricing;
  overview — https://chakrahq.com/article/whatsapp-cloud-api-calling-feature-details
- jambonz — https://docs.jambonz.org/ (MIT, component list)
- llama.cpp multimodal — https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md;
  Orin iGPU VMM issue #29142 — https://github.com/ggml-org/llama.cpp/issues/29142; Gemma 4 E2B/E4B
  causal image decode #28318 (closed by #28335) — https://github.com/ggml-org/llama.cpp/issues/28318
- Gemma 4 E4B GGUF + mmproj sizes — https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/tree/main;
  Gemma 4 image token budgets — https://huggingface.co/blog/gemma4,
  https://dev.to/someoddcodeguy/a-quick-note-on-gemma-4-image-settings-in-llamacpp-39ng
- Home Assistant telegram_bot — https://www.home-assistant.io/integrations/telegram_bot/; Assist
  blueprint — https://community.home-assistant.io/t/blueprint-to-use-your-telegram-bot-for-assist-commands/951091;
  Voicy — https://community.home-assistant.io/t/voicy-voice-activated-telegram-bot-to-control-home-assistant/473182
- Small captioners: Moondream2 on Pi 5 — https://pristren.com/blog/moondream2-edge-vision-model/;
  SmolVLM2-500M CPU timing — https://github.com/sam-dumont/immich-video-memory-generator/issues/934

On-box (read-only): `labs/flue-zoe-telegram-2x/src/{app,telegram,brain,handler,db}.ts`, its
`README.md` and `.env.example` (names only); `scripts/setup/systemd/{flue-zoe-telegram,
flue-zoe-telegram-watchdog,llama-server,kokoro-tts}.service`; `services/zoe-data/routers/{voice_tts,
voice_livekit,system,user_profile,chat}.py`, `telegram_link.py`, `auth_handoff.py`, `auth.py`,
`fast_tiers.py`, `zoe_flue_client.py`; `labs/flue-zoe-brain-2x/src/{providers/capped-completions,
context-window,replay-mode,request-identity}.ts`; `scripts/setup/kokoro_sidecar.py`;
`scripts/maintenance/{voice_gate_check,voice_regression_probe}.py`; `services/livekit/config.yaml`;
`docker-compose.yml`; `.github/workflows/deploy.yml`; `ps -o args= -C llama-server`; `/proc/meminfo`;
`ffmpeg -encoders/-decoders`; `soxi` on one corpus file; `ls ~/models/gemma4-e4b-qat`.
