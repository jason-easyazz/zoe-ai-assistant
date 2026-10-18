# Lane B: voice, panel, identity and communications substrate

Read-only audit, 2026-10-18. Worktree `claude/zoe-architecture-review-2efbf6` (= origin/main `62f1e7e83`).
Live reads: `scripts/maintenance/zoe_ground_truth.sh`, `docker inspect/ps`, zoe-data and flue-zoe-telegram
`/proc/<pid>/environ` (flag names and values only, secrets redacted), and read-only `SELECT`s on the live Postgres.
Nothing was edited, restarted or run against the Pi. Paths are repo-relative unless absolute.

Labels: **[verified]** = I read the code or live state myself. **[inferred]** = it follows from verified facts but I did not
execute it. **[doc]** = a repo record says so and I did not re-check it.

---

## 0. Answer first

1. **Zoe has no calls, no messages between people and no intercom.** The comms primitives that exist all go one way,
   from Zoe to a person: a TTS announcement queue to the panel daemon, toasts and cards over the panel push WebSocket,
   VAPID web push, and Telegram chat with Zoe. No route, tool or table carries a message from one member to another, or
   audio from one panel to another. [verified: §1, §3, §9]
2. **LiveKit is not dead. It is dormant by design, and it is the only media server in the stack.** The container was
   started on demand at 2026-10-12 15:39:59Z and reaped at 15:45:50Z (5 min 51 s, which is the 300 s idle reap plus the
   30 s check interval plus the stop). `OOMKilled=false`. Exit 137 is `docker stop` escalating to SIGKILL, not the OOM
   killer. [verified: `docker inspect livekit`; `routers/voice_livekit.py:174-199,294-350`] It only does
   "talk to Zoe over WebRTC": one hard-coded room `zoe-voice` (`voice_livekit.py:69`), and a server agent that can
   subscribe to audio and send data but never publishes audio (`livekit_aiortc.py:1-20`). There is no TURN, so it works
   on the LAN only (`routers/voice_tts.py:7339-7340`).
3. **There is a latent party line.** Every `/livekit-token` grants the same room `zoe-voice` with `canPublish` and
   `canSubscribe` (`voice_tts.py:7310-7321`). The agent broadcasts its replies to every participant (`voice_livekit.py:472-477`).
   The desktop `voice.html` attaches any remote audio track it is given (`dist/voice.html:951-955`). So two people in
   LiveKit mode at once would, by reading, hear each other's microphones and each other's replies. [inferred, not
   executed] That is a privacy defect today. It is also the seed of an intercom, but without consent or targeting.
4. **The announce lane is a household speaker, but only one, and it speaks TTS text, not audio.** It claims any row
   by default, assuming a single household speaker (`voice_announce.py:27-37`, `_strict_panel` at `:98-103`). An
   "all panels" row is claimed once by whichever daemon polls first, so a PA to every panel is impossible today.
   [verified] It plays only text through `/api/voice/speak` (`scripts/setup/zoe_voice_daemon.py:4028-4055`).
5. **The brain cannot announce, message or call anyone.** The live Flue 2.x brain has 22 tools: lists, reminders,
   calendar, home, media, memory and so on. None of them is `panel_*`, announce, message or call
   (`labs/flue-zoe-brain-2x/src/tools`). [verified] The `panel_announce` MCP tool shows a toast and nothing else: it never
   enqueues the daemon lane (`services/zoe-data/mcp_server.py:2799-2811`). The kiosk's own TTS fetch from that toast
   returns 401 for the guest session (`voice_announce.py:3-9`; `dist/js/touch-ui-executor.js:1498-1536`).
6. **Live inventory is very small.** One real panel (`zoe-touch-pi`, room "Bedroom", default player a Sonos
   `RINCON_…`) plus three test rows (`p0-test-panel`, `bench-panel`, `_probe_scene`). One room. **Zero web-push
   subscriptions.** Since 2026-09-28 the voice_announcements table holds only one enrolment command, and only one row
   ever has `played_at`. [verified: live SQL]
7. **Outside the house is three half-built paths and no media path:**
   - the Cloudflare HTTP tunnel (live; terminates TLS at Cloudflare; carries no UDP);
   - Headscale (PR #2083 CLOSED unmerged; the bundle is only on branch `feature/headscale-bundle`);
   - the frp relay at the411 (F0 lab merged #2094; R1 box side is draft #2095; the runbook has never been run).

   None of the three carries WebRTC media. Backups exist only on the box (`~/.zoe-backups`). [verified]

---

## 1. Panel → box and box → panel channels

| # | Channel | What it is | Where | Live / dark | Who can call | Transport |
|---|---|---|---|---|---|---|
| 1 | Voice turn upload | Daemon posts WAV: `/api/voice/turn`, `/turn_stream` (sentence-streamed TTS back), `/transcribe`, `/stt_stream/chunk` (under-speech STT), `/wake`, `/ambient`, `/identify`, `/adapt`, `/profiles/sync` | `scripts/setup/zoe_voice_daemon.py:299,2003,2303,2387,2608,2683,3362,3629,1950` | **live** [verified ground truth: zoe-data healthy; daemon is the panel's voice] | Pi daemon with `X-Device-Token` | HTTPS `requests.post` per call, no keep-alive (`panel-anywhere.md:335-338` [doc]); `VERIFY_SSL=false` |
| 2 | Device tokens | `device_tokens` (id, panel_id, token_hash, role, scopes, expires_at, revoked) | `routers/panel_auth.py:502-563`; live table: 9 rows [verified SQL] | live | admin mints; daemon, BT agent and omi-sync present it | header `X-Device-Token` |
| 3 | Provisioning | 6-character code + QR on the panel, confirmed on the phone, mints a kiosk device token | `routers/panel_provision.py:127,246,326,353` | live | panel (public), signed-in member confirms | HTTPS |
| 4 | Kiosk credential | ticket → HttpOnly cookie so the kiosk browser proves it is the panel without its IP (#2082) | `routers/panel_kiosk.py:56,71`; `panel_kiosk_credential.py:56-153` | merged, flag-gated (`enabled()` `:56`); the cookie is still UNVERIFIED in real Chromium (`docs/knowledge/open-problems.md:484` [doc]) | kiosk | HTTPS cookie |
| 5 | Panel principal | is this request the panel (IP = `panels.ip_address`, or credential)? refuses any Cloudflare-tunnel request | `panel_principal.py:64-110`; `panel_kiosk_credential.via_public_tunnel` `:87` | live | server-side | — |
| 6 | Push WebSocket | `PushBroadcaster`, in-memory; channels `all` + `panel_<id>`; `connect_panel` subscribes to both | `services/zoe-data/push.py:26-90,153-167`; endpoint `main.py:2644-2700` | live; 1 subscriber on zoe-touch-pi [verified ground truth] | panel with a device token or a session bound to a panel; members by session | WSS `/ws/push`; single uvicorn worker (`scripts/setup/systemd/zoe-data.service:10`) so in-memory fan-out is coherent |
| 7 | UI actions | `ui_actions` table + ledger; `enqueue_ui_action` resolves the foreground panel when `panel_id` is empty and broadcasts to `panel_<id>`, else to `all` | `ui_orchestrator.py:71-200` (foreground lookup `:93`, broadcast `:198`); executor `dist/js/touch-ui-executor.js` (`panelMatches` `:280-286`) | live | brain/MCP tools, voice router, proactive engine | push WS + client ACK |
| 8 | Announcement queue | `voice_announcements` (TTL 120 s, atomic claim, played-ACK); command rows (`voice_enroll`, `voice_profile_forget`) on the same lane | `voice_announce.py:61-87,113-307`; `GET /api/voice/announcements` `routers/voice_tts.py:2457-2475`; `POST …/played` `:2478`; daemon poller every 5 s `zoe_voice_daemon.py:3986-4088` | live lane; spoken proactive delivery OFF (`ZOE_PROACTIVE_SPOKEN=0` [verified env]) | enqueuers: `proactive/engine.py:263,272` (spoken brief, flag-off), `voice_enrollment.py:244,297,351`. No HTTP route lets a person or the brain enqueue one [verified] | HTTP short poll (5 s) by the daemon |
| 9 | `/api/voice/speak` | text → TTS bytes (Kokoro waterfall) | `routers/voice_tts.py:2445` | live | device token or non-guest (`_require_voice_auth` `:830`) | HTTPS |
| 10 | Panel MCP tools | `panel_navigate/clear/show_fullscreen/browser_screenshot/announce/request_auth/check_auth/set_mode/show_smart_home/show_media/ssh_exec` | `mcp_server.py:730-930`, handlers `:2588-2830` | **legacy only**: no live Flue brain tool calls them [verified: no `panel_` in `labs/flue-zoe-brain-2x/src`]; `zoe_agent.py:236,3090-3130` references them | MCP clients, the legacy zoe_agent | → ui_actions |
| 11 | Enrolment screen | daemon → `POST /api/enrollment/screen` → server pushes to that token's panel | `zoe_voice_daemon.py:4127-4140` | live | daemon | HTTPS → push |
| 12 | BT agent | long-poll `/api/bluetooth/agent/poll` (wait 20 s), no inbound socket | `services/panel-bluetooth/panel_bt/zoe_link.py:47-52`; `config.py:97` | flag-dark (`PANEL_BT_ENABLED`) [doc AGENTS.md] | panel BT agent | HTTPS long-poll |
| 13 | ssh | `panel_ssh_exec` | `mcp_server.py:912-930` | legacy tool | admin agent | ssh |
| 14 | Daemon `/activate` | orb tap → `fetch('http://localhost:7777/activate')` from the kiosk | `dist/touch/home.html:3451`; daemon health server comment `zoe_voice_daemon.py:627` | live (now behind the #2082 panel firewall [doc `panel-remote-access.md:47-53`]) | the local kiosk | loopback HTTP |

**How many panels can Zoe address?** [verified]
- The `panels` registry has `panel_id, name, location, ip_address, panel_type, allow_guest, last_seen_at` plus `room_id`,
  `default_player` and `pinned` (`alembic/versions/0001_initial_schema.py:504-518`, `0026_zoe_rooms.py:89`,
  `0021_panel_config.py:54-55`).
- `rooms` and `room_devices` exist (`routers/rooms.py:183-410`), and `panel_user_bindings` exists.
- So the data model already supports N panels, each in a room.

**Can a message go to one panel, or to all of them?**
- To one panel: yes for UI actions (`broadcast_to_panel`).
- To all panels: only as a UI broadcast to `all`.
- **Targeting is enforced by the client, not the server.** `voice_tts.py` broadcasts to `"all"` **48 times** and calls
  `broadcast_to_panel` once [verified grep count]. Examples are `voice:transcript` at `voice_tts.py:3075,3988,5694,5954`
  and `ui_action` nav/card at `:1341-1342,1389-1390`. Each kiosk then filters with `panelMatches`, which returns *true*
  when an event has no `panel_id` or the panel's identity is unknown (`touch-ui-executor.js:280-286`).
- With a second panel, many voice-UI events from panel A would therefore render on panel B as well. [inferred] Panel
  identity also has a known alias mismatch: the browser id `panel_xxxx` vs the device id `zoe-touch-pi`
  (`voice_announce.py:27-35`).

---

## 2. The LiveKit lane

**What it implements** [verified]
- `services/livekit/config.yaml`: port 7880, RTC UDP 50000-50200, `use_external_ip: false`, no `turn:` block, keys come
  from env (`LIVEKIT_KEYS`), not the tracked file (#1644).
- Compose (`docker-compose.yml:194-231`):
  - `livekit/livekit-server:latest`, which is **unpinned** (the comments say v1.9.3 was verified);
  - `restart: unless-stopped`;
  - publishes `7880:7880` and `50000-50200/udp` on every interface;
  - **no memory cap**, `HostConfig.Memory=0` [verified inspect], also logged in `open-problems.md:274`.
- nginx proxies `/livekit/` to `host.docker.internal:7880` with the WebSocket upgrade (`services/zoe-ui/nginx.d/locations.inc:176-189`).
  There is no file named `nginx-livekit-proxy.conf`; the location lives in `locations.inc` [verified].
- Token: `GET /api/voice/livekit-token` (`routers/voice_tts.py:7244-7345`). It needs auth and is never anonymous
  (`_require_livekit_auth`). The identity is the server-resolved user (`:7305-7308`). It grants room `zoe-voice` with
  publish, subscribe and data for 1 h. `livekit_available` is false for non-private client IPs unless `LIVEKIT_TURN_URL`
  is set. That variable is **unset live** [verified env]. Live `LIVEKIT_URL=wss://zoe.the411.life/livekit` is used only
  as a fallback when no Host or Origin header is present (`:7284-7303`).
- Server agent (`routers/voice_livekit.py`):
  - joins `zoe-voice` as `zoe-agent` (`:1-30,69,429-452`);
  - runs a per-participant energy VAD, IDLE → LISTENING → PROCESSING → COOLDOWN (`:8-10,998-1135`);
  - supports a PTT fallback (`ptt_start/ptt_stop`, `:12-14`);
  - barge-in uses Silero (`ZOE_VOICE_BARGE_IN=1` **live**), and Smart Turn v3 is on (`ZOE_SMART_TURN_ENABLED=1` **live**) [verified env];
  - pipeline: STT (Moonshine) → brain → TTS, delivered as **base64 WAV over the data channel** (`:22-27,500-545,580-735`);
  - on-demand `docker start/stop` with a 300 s idle reap (`:174-199,260-370`; `ZOE_LIVEKIT_ONDEMAND=true`,
    `ZOE_LIVEKIT_IDLE_TIMEOUT_S=300` live);
  - the transport is the pure-Python aiortc client (`livekit_aiortc.py`, 563 lines; `ZOE_LK_USE_AIORTC=1` live), because
    livekit-ffi cannot open a PeerConnection on Tegra (`livekit_aiortc.py:1-20`).
- HTTP PTT: `POST /api/voice/livekit-audio` (multipart) (`voice_livekit.py:1601`), `/livekit-cancel` (`:1679`), and
  `/livekit-health` (`:1504`). Media auth is a device token, a non-guest session, or a validated guest session
  (`:1512-1600`). That path was hardened in #2034 and #2097 (open).

**Clients** [verified]
- `dist/voice.html` (desktop, 1088 lines):
  - `?mode=livekit` joins the room and attaches remote audio tracks (`:936-1037,951`);
  - otherwise it uses `/ws/voice/` (`:513`, endpoint `main.py:2991`) or the HTTP upload (`:838-857`).
- `dist/touch/voice.html` (987 lines) has its home set to `/touch/dashboard.html` (`:188`). Nothing in the estate links
  to it (only `orb-loader.js:11` and `auth.js` mention it), so it looks orphaned. [inferred]
- **The real live consumer is the estate Ask card** "Stay talking to Zoe" (`dist/touch/home.html:4460-4662`).
  - It lazy-loads the client, fetches a token, publishes the mic, and plays data-channel audio.
  - It does not attach remote tracks; it uses the DataReceived handler (`:4620`).
  - `docs/PLANS.md:71-78` records the decision: "LiveKit is transport, not a surface", and `voice.html` retirement waits on it.
  - The 2026-10-12 container start matches someone using this card. [inferred]

**Is any of it human↔human?**
- No, not by design. Only the server agent consumes audio.
- By construction, though, it is a shared room (point 3 of §0). [inferred party-line risk]

**Tests:**
- 15 LiveKit test files in `services/zoe-data/tests/`, among them `test_livekit_failure_paths.py` (5 tests),
  `test_livekit_media_authz.py` (25), `test_livekit_token_authz.py` (9), `test_voice_livekit_ondemand.py` and
  `test_livekit_vad_segmentation.py` (19).
- **The voice replay corpus never goes through the LiveKit lane** (`docs/knowledge/voice-pipeline.md:483-495`, pinned by
  `test_replay_corpus_does_not_traverse_the_livekit_lane`). A green voice gate says nothing about it.

**RAM:**
- "~560 MB" (`voice_livekit.py:176`; `docs/PLANS.md:72`) [doc, unmeasured]. No measurement under room load exists
  (`open-problems.md:274`).
- Box MemAvailable is **791 MiB** now [verified ground truth]. Starting LiveKit therefore uses most of the headroom.

**Why it "died":**
- It did not die. It was reaped by the idle reap [verified: State.StartedAt/FinishedAt, OOMKilled=false]
  (`memory-pressure-profile-2026-10-03.md:114` says the same).
- `log-review-docker-2026-10-04.md:131` calls it "stopped by hand" and `open-problems.md:50` asks the owner to "remove,
  or keep". Both misread the on-demand design. [verified docs vs live]

**What reviving it as the media server for calls would take** [inferred design facts]
1. **Rooms per call.** Today the room name is a constant (`voice_livekit.py:69`; `voice_tts.py:7316`). The token
   endpoint must mint a room per call or intercom session, and give the agent no access to it. The agent must not join
   human calls.
2. **Consent and targeting.** Who may ring which panel, auto-answer rules (intercom "drop-in" vs a ringing call), and
   per-member settings (the identity-presence engine's `guests.intercom` flag, `identity-presence-engine.md:407`, is
   design only).
3. **Panel answering.**
   - The kiosk already auto-grants mic and camera (`--use-fake-ui-for-media-stream`) and plays audio without a gesture
     (`--autoplay-policy=no-user-gesture-required`) (`scripts/setup/touchscreen/start-kiosk.sh:139-140`).
   - So an incoming-call page could auto-join, which means it could also open the mic silently.
   - It needs a visible, spoken call state (VISION 8).
4. **Daemon coexistence.**
   - Audio goes through PulseAudio 16.1 with `AUDIO_DEVICE=pulse`, so the mic is shared, not exclusive
     (`docs/knowledge/panel-as-ma-speaker-feasibility.md:30-58`) [doc].
   - Nothing suspends the daemon's wake word while a browser LiveKit session is live. The estate only guards the orb
     tap (`home.html:4740-4757`), and `PLANS.md:78` leaves "double-listening" open.
   - During a call, "hey Zoe" from either end would trigger the local daemon. [inferred]
   - The Jabra Speak 750 has a hardware echo canceller, which helps speakerphone calls.
5. **WAN.**
   - The house is behind signs of CGNAT (`panel-remote-access.md:84`) [doc], and the Cloudflare tunnel carries no UDP.
   - A phone or a remote panel needs TURN (TCP/TLS 443 fallback) on a public host. The natural place is the411
     (`panel-anywhere.md:68`, "one relay for every inbound need").
   - LiveKit has an embedded TURN option, but it must run where the public IP is, which means a LiveKit node or coturn
     at the411. Otherwise media would hairpin through the relay.
   - The frp relay is TCP SNI passthrough (`panel-anywhere.md:28-31`). It could carry LiveKit's TCP/TLS fallback, but
     not efficient UDP.
6. **RAM.** About 560 MB while in use against 0.8 GB free. On-demand works for calls too, but a ringing panel needs the
   server already up, or about 1-3 s of `docker start` before ring.
7. **Pin the image and set a memory cap.**

---

## 3. Telegram lane

| Item | State | Evidence |
|---|---|---|
| Bot process | live `flue-zoe-telegram` (:3582), grammY long-poll, no public ingress | ground truth ✓; `labs/flue-zoe-telegram-2x/src/telegram.ts:4-6` |
| Handlers | `/start`, `/new`, `/reset`, `/talk` (only when `ZOE_BASE_URL` is set), `message:text`, `message:voice` + `message:audio` (when `ZOE_TELEGRAM_VOICE_NOTES` is set) | `src/app.ts:80,94,110,127,149` [verified] |
| Voice notes | **live**: `ZOE_TELEGRAM_VOICE_NOTES=1`, `ZOE_TELEGRAM_VOICE_REPLY=voice` in the bot env; zoe-data `ZOE_TELEGRAM_MEDIA=1` mounts `/api/system/telegram/*` | bot `/proc` env [verified]; `routers/telegram_media.py:50,68-69` |
| Photos | **no handler**: dropped silently. Native vision is NO-GO until W3 | `app.ts` handler list; `docs/research/telegram-calls-voice-photos-2026-10-04.md:51-63` |
| `/talk` | sends a button to `${ZOE_BASE_URL}/voice.html`. Live `ZOE_BASE_URL=https://192.168.1.218`, a LAN IP with a self-signed cert, so it **works only on home Wi-Fi** | `src/voice.ts:58-61`; env [verified]; conclusion [inferred] |
| Calls | a bot cannot call (Bot API 10.3 has no call method); userbot is NO-GO | research `:37-46` [doc] |
| Identity | Telegram id → member through a signed link token; turns run as that member | research `:270-292`; `telegram_link.py` |
| Can zoe-data send a Telegram message? | **No, on the live box.** `auth_handoff._bot_token` reads `ZOE_TELEGRAM_BOT_TOKEN` (`auth_handoff.py:382`). The zoe-data env holds `TELEGRAM_BOT_TOKEN` (and `TELEGRAM_HOME_CHANNEL`) under the **wrong name**, and no zoe-data code reads those. So `telegram_available()` is false and "send to my phone" falls back to the QR. The bot exposes only `/health` and `/agents/zoe`: no `/internal/notify` (`app.ts:252-260`) | env + grep [verified] |
| Out-of-band owner alerts | `zoe_crash_loop_watch.py` reads the bot's env and calls the Bot API directly (timer live) | `scripts/maintenance/zoe_crash_loop_watch.py:111-112`; `systemctl --user list-timers` [verified] |

**"Message a household member" primitive:**
- Nothing for member to member [verified].
- The closest is `POST /api/proactive/schedule`, which lets an **admin** schedule a one-shot nudge to another user
  (`routers/proactive.py:39-63`, the target rule at `:48`). It is delivered by web push (0 subscribers) and by the
  `proactive_pending` pull. It is a reminder, not a message, and the recipient cannot reply.
- `routers/handoff.py` is the "finish on the phone" auth handoff, not messaging.

---

## 4. Notifications and the proactive lane: delivery surfaces

| Surface | Real? | Evidence |
|---|---|---|
| Panel/desktop toast + bell (`notification_created` over the push WS) | live. `notifications` has 247 rows, mostly `omi_sync` (205) | `routers/notifications.py:78-98`; SQL [verified] |
| Web push (VAPID, `pywebpush`) | code live, **0 subscriptions** in `push_subscriptions` | `routers/push.py:18-205`; SQL `0|0` [verified] |
| Spoken announcement (daemon) | lane live, delivery OFF (`ZOE_PROACTIVE_SPOKEN=0`; the owner does not want unprompted speech). 26 `morning_checkin` rows up to 2026-09-28, 1 ever `played_at` | `proactive/engine.py:210-219,223-290`; env + SQL [verified] |
| Pull queue / orb count / `[Today]` first turn | live (`ZOE_BRIEF_ON_FIRST_TURN=1`, `ZOE_PROACTIVE_SELECTOR=1`) | env [verified]; `routers/proactive.py:272-286` |
| Telegram from zoe-data | dead (wrong env name, §3) | [verified] |
| Email / SMS / WhatsApp / ntfy / Apprise | none; design P3 only | `communication-preferences.md:265-286` [doc] |

Small bug [verified]: `fire_notification` says "If no WebSocket subscribers received the push", but `subscribers_reached`
counts **web-push** subscriptions (`proactive/engine.py:163-169`). With 0 subscriptions, every reminder takes the in-app
fallback.

---

## 5. Audio output on the panel: is there a PA primitive?

- **Daemon TTS.**
  - `aplay`/`mpg123` subprocesses through PulseAudio to the Jabra Speak 750 (`zoe_voice_daemon.py:553-592`; `feasibility.md:30-58`).
  - The box can make a **chosen** panel speak **text** only by enqueuing a row with that `panel_id` and setting
    `ZOE_ANNOUNCE_STRICT_PANEL`, or by using a trigger in `PANEL_SCOPED_TRIGGERS` (`voice_announce.py:61`).
  - There is no arbitrary-clip primitive: the daemon always calls `/api/voice/speak` on `text` (`:4040`).
  - Latency is up to 5 s of poll plus TTS.
  - It is skipped while the daemon is busy (`_daemon_busy` `:4000-4009`). It expires after 120 s.
- **AirPlay "Zoe Panel".** shairport-sync 5.1 + nqptp on the Pi, exposed as an MA `universal_player`
  (`docs/knowledge/music-assistant-idle-reap.md:35`; memory note) [doc]. Through MA, the box *could* play any URL to the
  panel. **Zoe has no code that uses MA's announcement API.** A grep finds no `play_announcement`/`announce` in
  `music_service.py` or `routers/music.py` [verified].
- **MA itself is on demand.** `ZOE_MA_IDLE_REAP=1` is live [verified env]; container `zoe-music-assistant` is
  `Exited (0) 13 days ago` [verified docker ps]. Reaped, it does not advertise the panel player
  (`music-assistant-idle-reap.md:35`).
- **Ducking and priority.**
  - Only the daemon's own TTS ducks on barge-in (`BARGE_DUCK_ENABLED`, a PulseAudio sink-input duck,
    `zoe_voice_daemon.py:489-503,1412`).
  - Nothing ducks or pauses music for a reply or an announcement; a grep finds nothing [verified].
  - On the Pi, Pulse simply mixes music with TTS (`feasibility.md:53-58`).
- **Multi-room / sync.** MA bundles snapserver v0.34.0 but the snapcast provider is not enabled (`feasibility.md:88-99`)
  [doc]. MA groups (`can_group_with`) are surfaced (`music_service.py:579-655`; `routers/music.py:527`). Sonos, Chromecast
  (including Nest Hub and Home Mini) and Apple TVs are MA players [doc]. So whole-house *music* sync exists in MA; a
  whole-house *voice* PA does not.
- **Home Assistant TTS / notify.** Zoe calls no `tts.*`, `notify.*` or `mobile_app` service; a grep finds nothing [verified].

---

## 6. Identity at the panel (what a caller or callee identity can stand on)

| Piece | State | Evidence |
|---|---|---|
| Device token = the panel (guest-class) | live | `panel_auth.py:_resolve_device_token_user`; `panel-remote-access.md:86` [doc] |
| PIN session (`ui_panel_sessions.last_seen_at` within the idle window, 900 s default; `ZOE_PANEL_SESSION_TRUST_WINDOW_S=1800` live) | live | `identity-presence-engine.md:84` [doc]; env [verified] |
| Voice ID: dual Resemblyzer + WeSpeaker ResNet34 | **live in dual mode** (`ZOE_SPEAKER_MODEL=dual`, threshold 0.70) after #2024 merged | env + `git log` [verified] |
| Voice-verified session (#2025, `ZOE_VOICE_SESSION`) | **off**: not in the env | env [verified]; `voice_session.py` |
| Identity freshness (#2026: never a stale or owner fallback) | default on | `identity-presence-engine.md:91` [doc] |
| Face ID | server flag `ZOE_FACE_ID_ENABLED=true` live, but the Pi module is only called by the enrolment flow (cv2 `VideoCapture`) | env [verified]; `scripts/setup/zoe_face_id.py:6,43,369-380`; `identity-presence-engine.md:92` |
| Presence | `panel_presence_tier` (owner / bound_guest / absent, 900 s); no phone, BLE or HA presence; `voices_heard` is design only | `identity-presence-engine.md:94-97` [doc] |
| Roles | zoe-auth has 5 roles; zoe-data folds `family_member` and `child` into guest | `identity-presence-engine.md:86` [doc] |
| Callee directory | contacts with multi-value phone, email and IM fields (0053), plus the people graph | `alembic/versions/0053_contacts_data_model.py:53-160` [verified] |

**What calls would need on top:**
- a per-member **reachability** record: which panel or room they are near now, phone presence, a web-push endpoint, the
  linked Telegram id;
- a **caller identity** stamped on a call: verified member (voice or PIN) vs "the panel" (guest-class);
- call permissions by role (kids, guests);
- a "who is in the room" check before a call is announced aloud.

Today you can stand on: device token, PIN or voice session, Telegram link, contacts. Missing: presence routing,
reachability, call permissions.

---

## 7. Remote access and the411

**Cloudflare tunnel** ingress, live in `/home/zoe/assistant/config/cloudflared-config.yml` (untracked) [verified]:
- `zoe.the411.life` → `zoe-ui:80`;
- `ssh.the411.life` → `ssh://localhost:22`;
- `buildzoe.the411.life` → `zoe-omnigent:6767`;
- 404 for everything else.

The compose service uses profile `cloudflare` (`docker-compose.yml:283-298`); `zoe-cloudflared` has been up 4 days.
The Mac virtual panel reaches zoe-data through this tunnel with an Access service token
(`docs/knowledge/mac-virtual-panel.md:1-30`) [doc]. There is no `mac-dev` row in `panels` [verified SQL]. Cloudflare
terminates TLS, so voice is plaintext at the edge (`panel-remote-access.md:41-44`) [doc].

**Headscale:**
- PR #2083 is **CLOSED, not merged**. The bundle (`scripts/setup/remote-access/headscale/*`) exists only on branch
  `feature/headscale-bundle` [verified `gh pr list`, `git ls-tree`].
- Tailscale 1.104.1 is installed on both boxes but `NeedsLogin` (`panel-remote-access.md:47-48`) [doc].
- `panel-anywhere.md:73` still says "#2083, still open", which is now stale [verified].

**frp relay:**
- F0/R0 lab merged (#2094, `labs/relay/`).
- R1 box side is **draft** #2095 (`feature/relay-r1-ingress`).
- The `labs/relay/RUNBOOK-R1.md` header says "Nothing here has been run on the411" (second Reserved IP, `relay.the411.life`, frps container).
- The design requires mTLS and "relay never maps to guest" (`panel-anywhere.md:48-57`).

**the411:**
- `agent_sync.py:296-300` puts into every agent brief that "Zoe is the AI Assistant layer (component #16) of
  **the411.life** (the Living Map of Social Life) and its Nudge wedge".
- Concretely, the411 today is a DigitalOcean droplet with host nginx: the relay target and the Headscale host
  (`panel-anywhere.md`; RUNBOOK-R1 owner decisions D2, D4).
- VAPID claims already say `https://zoe.the411.life` (`routers/push.py:19`).

**Backups:**
- `zoe-backup.timer` runs nightly, the restore check weekly [verified `systemctl --user list-timers`].
- `scripts/maintenance/postgres-nightly-backup.sh` writes `~/.zoe-backups/postgres/<db>-<ts>.dump.gz`, 7 daily + 4 weekly
  (`:4-16`), plus `mempalace-nightly-backup.sh`.
- **There is no off-box copy.** A grep finds no restic, borg, rclone, s3 or scp [verified].
- The only off-site design is the credential-vault doc's age-encrypted archive "on the owner's existing server"
  (`docs/architecture/credential-vault.md:590-600`) [doc].

**What "link your Zoe to a the411 account for backups + relay" would need** [inferred]:
1. A household identity on the411: the frp **session master** device registry (`panel-anywhere.md:157-190`, section 9
   "one device registry").
2. An enrolment flow with QR on the panel and finish on the phone (VISION 8).
3. A box-side encrypted backup push: age or restic to the411 object storage, with the key off the box.
4. The relay listener plus mTLS (#2095).
5. TURN on the same IP for media.
6. A the411-side per-household quota and revocation.

None of it exists beyond the lab and the drafts.

---

## 8. Media and camera on the panel

- **Camera.** A USB camera is read by `cv2.VideoCapture(FACE_CAMERA_INDEX)` in `zoe_face_id.py:369-380`, used only by
  the enrolment flow. The PanaCast camera shares USB power with the Jabra (`identity-presence-engine.md:92`). No video
  path to the box exists beyond face embeddings [verified: no `video:true`/`facingMode` in `dist` outside vendored libs].
- **Kiosk browser.** Chromium (`chromium-browser || chromium`) is started by `start-kiosk.sh`.
  - Flags: `--kiosk --ignore-certificate-errors --no-sandbox --use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required`
    (`:52-66,139-140`).
  - So `getUserMedia` for mic **and camera** is auto-granted with no prompt.
  - The Chromium version is not recorded in the repo docs [verified grep: none]; it needs a live `chromium --version` on the Pi (not done: no Pi access).
  - WebRTC exists in the kiosk only through the LiveKit Ask card (audio only).
- **Mic ownership.**
  - The daemon holds one continuous PyAudio input stream on `pulse` (`zoe_voice_daemon.py:41,133,4585-4613`;
    `feasibility.md:41-50`).
  - PulseAudio arbitrates, so a browser `getUserMedia` can open the same source at the same time. There is no ALSA
    exclusive lock. [doc + inferred]
  - The real conflict is *behavioural*: two listeners (wake word + call) and no suspend handshake (§2, point 4).

---

## 9. Gaps: what does not exist

| Gap | Status |
|---|---|
| (a) Panel-to-panel intercom / PA | **None.** The announce lane is text-only, single-speaker claim-any (`voice_announce.py:27-37`); no live-audio path from box to panel (the daemon only polls); no "announce to room X" route or brain tool. The LiveKit room is shared but has no targeting, consent or ring |
| (b) Member messaging another member | **None.** No table, route or tool. Admin-only proactive nudge is the nearest (`proactive.py:48`). Telegram is chat-with-Zoe only |
| (c) Calls: panel↔panel, panel↔phone, household↔household | **None.** No signalling (ring, answer, decline), no per-call rooms, no TURN, no PSTN (Twilio/SIP is design-only, conditional: `telegram-calls…md:433-461`), no federation between households |
| (d) Presence for routing | **None beyond PIN/foreground freshness.** No phone/BLE/HA presence; `anyone_here` / `voices_heard` are design only (`identity-presence-engine.md:94-97,115`) |
| (e) Phone app | **None.** The desktop UI is a PWA (`dist/manifest.json`, `sw.js`, `js/push-notifications.js`) with web-push subscribe, but 0 subscriptions; "Zoe phone app later" is only mentioned (`panel-anywhere.md:68`) |
| Off-box backup | None (§7) |
| Arbitrary audio clip / chime to a panel | None. Text only; MA announcement API unused |
| Music ducking under speech | None |

---

## 10. Systems that do not work together (concrete seams)

1. **Three box→panel delivery lanes with different semantics.** Each has its own idea of panel identity (browser alias
   vs device id).
   - Push WS `ui_action`: the browser toast; at most once; delivery inferred from an ACK.
   - The `voice_announcements` queue: the daemon's 5 s poll; exactly once; claim-any.
   - BT long-poll.

   `panel_announce` (MCP) writes only to the first lane, so it is mute. `_speak_on_panel` writes to both, behind
   `ZOE_PROACTIVE_SPOKEN` (`proactive/engine.py:223-290`) [verified].
2. **Panel targeting by client-side filter.** 48 `broadcast("all")` calls in the voice router, plus `panelMatches` that
   says "act when unknown". This works with one panel and leaks with two [verified counts; inferred effect].
3. **Four voice ingest transports.** Each runs its own VAD and endpointing; all of them share the Moonshine lock.
   - The daemon's HTTP lane: wake word, Silero endpointing, `turn_stream`, the replay-gated canonical path.
   - `/ws/voice/` (desktop `voice.html` local mode, `main.py:2991`).
   - `/api/voice/livekit-audio` HTTP PTT.
   - The LiveKit room: the server energy/Silero VAD and Smart Turn in `voice_livekit.py`.

   Only the daemon lane is replay-gated (`voice-pipeline.md:483-495`). Barge-in is implemented twice: daemon
   `BARGE_DUCK` and LiveKit `ZOE_VOICE_BARGE_IN`.
4. **Two `voice.html` files plus the Ask card.** The desktop `dist/voice.html` (still the `/talk` target), the orphaned
   `dist/touch/voice.html` (home is the retired `dashboard.html`), and the estate Ask-card LiveKit client. `PLANS.md:71-73`
   says `voice.html` retires after the Ask card ships.
5. **Three remote-access paths, none merged end to end.**
   - The Cloudflare HTTP tunnel (live, plaintext at the edge, Mac panel).
   - Headscale (closed PR, bundle on a branch).
   - The frp relay (lab + draft).

   The docs disagree about which is next (`panel-anywhere.md:73` vs PR state). None of the three carries real-time
   media. The relay design is TCP SNI passthrough, so calls would still need TURN.
6. **Three notification designs.** The proactive engine (push + pending + spoken), notifications (bell), and the
   communication-preferences design (per-member channels, digest, `notify_transport`, design-only). The
   comm-prefs doc's "no bot token in zoe-data" is half right: the token is present under the name no code reads
   (`auth_handoff.py:382` vs live env).
7. **LiveKit's status is misread in the ops records.** `open-problems.md:50` and `log-review-docker-2026-10-04.md:131`
   treat Exited(137) as a dead or hand-stopped container. Live evidence shows the on-demand reap working. If someone
   "removes" it, the Ask-card conversation mode breaks.
8. **The daemon vs a browser LiveKit session on one mic.** Nothing suspends wake-word listening during a live session
   (`PLANS.md:78` open; `home.html:4740-4757` guards only the orb).
9. **The shared LiveKit room** (point 3 of §0): `zoe-voice` for every token, data broadcast to all.
10. **Brain toolset vs panel tools.** The rich `panel_*` MCP surface and the `zoe_agent.py` prompt that advertises it
    (`zoe_agent.py:236`) belong to the legacy agent. The live Flue 2.x brain has none of them, so "show X on the
    kitchen panel" or "announce dinner" cannot be done by voice today [verified tool list].

---

## 11. What a calls / messages / intercom layer would stand on (reuse list)

- **Registry:** `panels` (+ `room_id`, `default_player`), `rooms`, `panel_user_bindings`, `device_tokens` (scoped,
  expiring), the provisioning QR flow.
- **Per-panel addressing:** `PushBroadcaster.broadcast_to_panel` + `panel_push_status` (liveness),
  `voice_announce.PANEL_SCOPED_TRIGGERS` (exact panel claim), the played-ACK pattern.
- **Media:** the on-demand LiveKit SFU (rooms, JWT grants, vendored `livekit-client.umd.min.js`, nginx `/livekit/` proxy),
  the Jabra AEC speakerphone, PulseAudio mixing, kiosk auto-grant flags (needs a visible call UI).
- **Identity:** device token = panel; PIN and voice sessions; ResNet34 dual live; Telegram link; contacts (phones,
  emails); the identity-presence engine design (`guests.intercom`).
- **Off-box reach:** web push (VAPID; needs subscriptions), the Telegram bot (needs a loopback notify route or the
  right env name), the frp relay + the411 (needs R1 + TURN).
- **Policy:** VISION 8 (voice first, QR → phone), no unprompted speech (`ZOE_PROACTIVE_SPOKEN=0` holds: an intercom or a
  call is *member-initiated* speech, which is a different class, but the owner should rule on it explicitly).
