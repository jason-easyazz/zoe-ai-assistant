---
type: Runbook
title: Mac virtual panel (test the panel from a laptop, away from home)
description: How to run the panel voice daemon natively on an Apple-silicon Mac as a second panel ("mac-dev") that reaches zoe-data through the Cloudflare tunnel - install, the Access/device-token wiring and its least-privilege option, what it can and cannot test (including the barge-in phase-1 lab), and what is unverified because it was built with no Mac in reach.
tags: [voice, panel, macos, cloudflare-access, tunnel, barge-in, runbook]
timestamp: 2026-10-05T09:00:00+08:00
---

# Mac virtual panel

The real panel is a Pi 5 (`zoe-pi`) running `scripts/setup/zoe_voice_daemon.py` beside a kiosk
Chromium. This kit runs **the same daemon file** on a Mac, as a different panel id (`mac-dev`),
so the panel experience can be tested from anywhere. Native venv, not Docker: Docker Desktop
cannot pass a Mac's microphone or speakers into a container.

Evidence labels: **[src]** checked in this tree · **[doc]** fetched from the cited vendor page
2026-10-05 · **[unverified]** no Mac was available - designed from code and documentation,
proven only against fakes (§9).

## 1. What it tests, and what it does not

| Tests | Does NOT test |
|---|---|
| openWakeWord ONNX wake word (`hey_jarvis`, or `hey_zoe` if you supply the model) | the real duck: `pactl set-sink-input-volume` on a PulseAudio sink-input (the Mac ducks an in-process gain instead, §6) |
| mic capture, Silero endpointing, WAV upload to `/api/voice/*` (STT) | face ID (`zoe_face_id.py` is a separate Pi service; nothing here runs it) |
| the reply stream, sentence-gapless TTS playback, follow-up listening, conversation mode | AirPlay-2 "Zoe Panel" output (shairport-sync + nqptp are Pi services) |
| the announce poller (`/api/voice/announcements`, played-ACK) | the Jabra/PanaCast USB-power trap (a Mac has none of that topology) |
| the barge-in **decide** logic: detector, grace, `_BargeDecider`, ledger, seed capture (§7) | Pi thermals, Pi CPU/RAM contention, ALSA/Pulse latency (aplay adds ~70-90 ms before first sound [src: `zoe_voice_daemon.py` barge comment]) |
| the kiosk **UI** in a browser tab through the tunnel (`/touch/home.html?panel_id=mac-dev&kiosk=1`) | the orb-tap-to-daemon path: the Pi's on-box agent POSTs `127.0.0.1:7777/activate`; a browser tab cannot, so on the Mac use the wake word |
| the Access + device-token plumbing a remote panel needs | the screen-wake agent (`127.0.0.1:8765`, skipped on the Mac) |

## 2. How the code is arranged

`PANEL_PLATFORM=pi|mac|auto` (default **pi**; `auto` = Darwin -> mac, anything else -> pi; an unknown
value is logged and means pi). The daemon routes every OS-level thing through one object, `_PLATFORM`:

| Concern | `pi` (live panel - unchanged) | `mac` (`scripts/setup/mac_panel/mac_backend.py`) |
|---|---|---|
| reply stream player | `aplay -t raw ... ` subprocess on stdin | `MacPlayer`: a Popen-lookalike feeding a PyAudio (PortAudio) output stream, mono -> stereo, 20 ms blocks |
| file / chime player | `aplay` (`mpg123` for mp3) | `MacPlayer` for WAV; `afplay` for mp3 only (never requested today) |
| the duck (phase 1) | `_SinkInputDucker`: `pactl` on the player's sink-input, found by pid | `GainDucker`: a gain on the player's own stream (`BARGE_DUCK_DB`, optional ramp) |
| local TTS fallback | `espeak-ng` | `say` |
| screen-wake agent | POST `127.0.0.1:8765/wake` | none |
| `/health` + `/activate` bind | every interface | `127.0.0.1` (`HEALTH_BIND` overrides) - `/activate` is unauthenticated and starts a recording, fine on a home LAN, not on a laptop at a cafe |
| face ID, `vcgencmd`, `/proc`/`/sys` probes | not in the daemon at all [src: grep] | not applicable |

**Why a gain and not `osascript` or `afplay -v`** (the two obvious macOS options):
`afplay` takes `-v` at launch only and has no device option [doc: ss64 afplay]; `osascript -e 'set volume
output volume N'` is the *system* volume - it ducks every app, costs a process spawn per step, and if the
daemon dies while ducked the laptop stays quiet. The in-process gain keeps `_BargeEpisode`,
`_BargeDecider`, `_PlayoutLedger` **byte-identical** to the Pi (only the actuator differs), dies with the
player (no `module-stream-restore`-style leak), and is unit-tested end to end: scenarios duck -> commit,
duck -> resume, ceiling, VAD-failure sentinel, no-duck fallback, and the samples reaching the output stream
are scaled while ducked and untouched after restore (`tests/unit/test_voice_daemon_platform.py`).

**The Pi is untouched by default** (pinned): with `PANEL_PLATFORM` unset the daemon builds the same argv for
all seven player call sites, the same request headers, the same `_SinkInputDucker`, and binds `""`. This was
checked against the pre-change file (7 call sites x {`default`, `hw:2,0`}: identical), and the unit test holds
the golden values. The Mac module is loaded by path only when asked for, so `deploy-pi-voice.sh` ships nothing
new (`SHIPPED_FILES` unchanged).

## 3. Install and run - the eight steps

Prerequisites: Apple-silicon Mac, [Homebrew](https://brew.sh), a checkout of the Zoe repo, and from the
operator (§5): the `DEVICE_TOKEN` for `mac-dev` and the Cloudflare Access service-token pair.

1. `bash scripts/setup/mac_virtual_panel.sh install` - installs `portaudio` + `python@3.12` with brew, makes the
   venv, installs `mac-requirements.txt`, downloads the wake-word and Silero models, writes `.env.voice`.
   Everything lands in `~/.zoe-virtual-panel/`. Idempotent: re-running skips what is done.
2. `bash scripts/setup/mac_virtual_panel.sh configure` - silent prompts for `DEVICE_TOKEN`,
   `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` (nothing in shell history; file mode 600).
3. `bash scripts/setup/mac_virtual_panel.sh devices`, then edit `AUDIO_DEVICE` / `AUDIO_OUTPUT_DEVICE` in
   `~/.zoe-virtual-panel/.env.voice` if the macOS defaults are not what you want (index or name substring).
4. `bash scripts/setup/mac_virtual_panel.sh preflight` - macOS asks to let your terminal use the microphone:
   allow it (System Settings > Privacy & Security > Microphone [doc: Apple Support]; a denied permission reads as
   digital silence and preflight says so). It then plays a tone, and does one authenticated `/api/voice/speak`
   round trip and plays Zoe's reply. Every failure prints which layer (Access, token, origin) said no.
5. `bash scripts/setup/mac_virtual_panel.sh run` - preflight (server only) then the daemon in the foreground
   (`caffeinate -i` keeps an idle Mac awake; closing the lid still sleeps it). Log: `~/.zoe-virtual-panel/voice.log`.
6. `bash scripts/setup/mac_virtual_panel.sh ui` - opens `https://zoe.the411.life/touch/home.html?panel_id=mac-dev&kiosk=1`
   (the estate UI, same URL shape as the Pi kiosk [src: `scripts/setup/touchscreen/start-kiosk.sh`]). Sign in through Access
   as yourself; the page boots as the kiosk guest and shows the who+PIN card as on the Pi.
7. Say the wake word (default model: **"Hey Jarvis"**; for "Hey Zoe" copy the Pi's model next to the daemon:
   `HEY_ZOE_ONNX=/path/hey_zoe.onnx bash scripts/setup/mac_virtual_panel.sh install` - the daemon loads
   `scripts/setup/hey_zoe.onnx` if present; the file is untracked).
8. Rollback: `bash scripts/setup/mac_virtual_panel.sh uninstall --yes` deletes `~/.zoe-virtual-panel/` (venv, Silero
   cache via `TORCH_HOME`, `.env.voice`, log). Homebrew packages stay (`brew uninstall portaudio` if you want).

Commands: `install | configure | devices | preflight | run [--skip-preflight] | ui | lab-summary | uninstall --yes | env-template`.

## 4. Reaching zoe-data through the tunnel

**Where it lives [src].** `config/cloudflared-config.yml` (not git-tracked; read from the live checkout, hostnames only -
no credentials) routes `zoe.the411.life` -> `http://zoe-ui:80`; `ssh.the411.life` -> sshd; `buildzoe.the411.life` ->
Omnigent (documented as Access-gated in `modules/omnigent/README.md`). `zoe-ui`'s nginx (`services/zoe-ui/nginx.d/locations.inc`)
sends `/api/` to zoe-data with `proxy_buffering off`, `gzip off`, `client_max_body_size 25m`, `proxy_read_timeout 900s`, and
passes `X-Device-Token` through (it strips only `X-Internal-Token` / `X-Zoe-User-Id`). `/ws/` is proxied for the UI's push socket.

**Cloudflare Access sits in front of that hostname.** `skills/touch-panel/SKILL.md`: "NEVER use `zoe.the411.life` from or for
the panel - Cloudflare Access blocks it with HTTP 302". An API client with no Access credential gets a redirect to the login page
(which `requests` follows and then fails to parse as JSON) - that is the failure `preflight` classifies.

**Endpoints the daemon calls [src: grep of `zoe_voice_daemon.py`]** - all under `/api/voice/`, all behind the same `X-Device-Token`:
`wake`, `turn_stream`, `turn_stream/speculation`, `turn`, `transcribe`, `speak`, `ambient`, `identify`, `profiles/sync`,
`announcements`, `announcements/{id}/played`.

**Does the tunnel expose them today?** Yes: they ride `/api/` through `zoe-ui`; **no `cloudflared` ingress change is needed**.
The missing piece is on the Access side only: a way for a *non-browser* client to pass Access. Nothing else in the daemon is
reachable-or-not by tunnel: the HA bridge (`HA_BRIDGE_URL`, unused here) and the on-box agent are local.

### Access options (least privilege first)

* **A - recommended: a path-scoped Access application with a Service Auth policy.**
  Zero Trust > Access > Service credentials > create a service token `zoe-mac-virtual-panel` (choose a duration; tokens expire
  and can be renewed [doc: Cloudflare service tokens]). Add a **self-hosted application** with destination
  `zoe.the411.life/api/voice/*` and two policies: **Service Auth** including only that token, and **Allow** for the owner's
  e-mail (so the browser touch UI, which also calls `/api/voice/*`, keeps working). Access picks the more specific path rule over
  the parent app [doc: Cloudflare application paths], so nothing else on the host changes. The daemon sends the pair as
  `CF-Access-Client-Id` / `CF-Access-Client-Secret` [doc: Cloudflare service tokens]. Blast radius of a leaked pair: the
  voice endpoints only, and each still demands the panel device token. Revoke = delete the token.
  *Risk [unverified]:* two Access apps on one hostname issue separate session cookies; if the browser UI shows CORS errors on
  `/api/voice/*` after the owner's first login, re-visit once or fall back to B.
* **B - fallback: add the Service Auth policy to the existing `zoe.the411.life` application.** Simpler, no overlap, but the
  token then passes Access for the whole host (still gated by device token / sessions where endpoints require auth, but several
  endpoints are intentionally unauthenticated for the guest kiosk, e.g. `GET /api/panels/{id}/config`).
* **C - rejected: a Bypass policy on the path.** Opens the voice endpoints to the internet with only the device token between.

Env keys (daemon): `ZOE_URL=https://zoe.the411.life`, `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` (both or neither - a lone half
is ignored with a WARNING; unset, the Pi's headers are untouched), `DEVICE_TOKEN`, `PANEL_ID=mac-dev`, `VERIFY_SSL=true`.
**VERIFY_SSL:** the Pi sets it false for the Jetson's self-signed LAN cert; the tunnel host serves a public-CA certificate through
Cloudflare, so keep `true` here. **Do not** put the Access pair in an `.env.voice` whose `ZOE_URL` is a LAN address - the headers would be
sent to that host.

**Announce poller.** Unchanged code; it polls `GET /api/voice/announcements` with the same headers. The template sets
`ZOE_ANNOUNCE_POLL_S=15` (the Pi uses 5 s on the LAN). Announcements are claimed per panel id, so `mac-dev` only receives ones
addressed to it.

**Streaming caveats [unverified].** `turn_stream` is NDJSON; Cloudflare must pass it unbuffered. The cloudflared config comment
already notes SSE drops on long replies are cured by turning HTTP/3 (QUIC) off at the edge. Cloudflare also closes a proxied request
that sends no bytes for 100 s (HTTP 524) - the streaming turn starts emitting at once, the non-stream `/turn` fallback does not.

## 5. Operator steps (Jetson / Cloudflare side)

1. **Cloudflare**: create the service token and Access application of option A (§4). Hand the Client ID/Secret to the owner by a
   private channel; the secret is shown once.
2. **Register the panel** (admin session, on the LAN or via the owner's signed-in browser; `$Z` = zoe-data base, `$SID` = an admin `X-Session-ID`):
   ```
   curl -sX POST $Z/api/panels/register -H "X-Session-ID: $SID" -H 'Content-Type: application/json' \
     -d '{"panel_id":"mac-dev","name":"Mac virtual panel","location":"Owner laptop","allow_guest":false}'
   ```
3. **Bind a user** (an unbound device token resolves to **guest** - fail-closed - `routers/panel_auth.py:_resolve_device_token_user`):
   ```
   curl -sX PUT $Z/api/panels/mac-dev/bindings -H "X-Session-ID: $SID" -H 'Content-Type: application/json' \
     -d '{"default_user_id":"<owner-or-test-user>","allowed_user_ids":[]}'
   ```
   Turns on the virtual panel are **real turns** (memory writes, tool calls). Bind a test user if you do not want them in the owner's record.
4. **Issue the device token** (returned once; `expires_at` must be timezone-aware ISO - a naive value makes `lookup_device_token` raise):
   ```
   curl -sX POST $Z/api/panels/mac-dev/token -H "X-Session-ID: $SID" -H 'Content-Type: application/json' \
     -d '{"name":"mac-virtual-panel","role":"voice-daemon","scopes":["voice"],"expires_at":"2027-01-05T00:00:00+00:00"}'
   ```
   Revoke any time: `DELETE /api/panels/mac-dev/token/<token_id>`.
5. Hand `token` to the owner (private channel). **Do not** use the first-boot pairing flow (`/api/panels/provision/*` + `touch/pair.html`)
   for this: its pickup poll lives under `/api/panels/`, outside the Access path of option A, and it issues a `kiosk`-role token.

## 6. The duck on a Mac - what is and is not the same

Same: the detector (`_BargeDetector`: playback-anchored window, 800 ms grace, 3-of-6 / fast path), the decider (900 ms speech -> commit,
400 ms quiet -> resume, 2 s ceiling, `-1.0` sentinel never commits), the playout ledger, the seed capture, the log lines.
Different: the actuator (a gain on the player's stream, applied on the next 20 ms block, so the dip lands within roughly one block plus the
device buffer - the pactl path is a mixer change and also ducks audio already queued in the stream; here queued audio is limited to the
64 kB stream buffer plus the device's own buffer), and the output latency the ledger assumes (`BARGE_PLAYOUT_LATENCY_MS` 100 is a Pi/aplay
number). **Cannot be tested on a Mac:** the `pactl list sink-inputs` parse and pid match, the `module-stream-restore` leak and its
heal (`_duck_leak`), the "duck unavailable -> hard stop" fallback *as triggered by a missing sink-input* (the Mac triggers it only for
`afplay`/mp3), and Pulse mixer behaviour on the shared sink with shairport-sync.

## 7. The barge-in lab, run from the Mac

Purpose: exercise phase 1 (`docs/research/barge-in-duck-decide-resume-2026-10-04.md` §6.3, `voice-pipeline.md` -> "Panel barge-in") with real
Silero on a real mic and real replies through the tunnel, before the Pi lab.

1. In `.env.voice`: `BARGE_DUCK_ENABLED="true"` (leave `BARGE_IN_THRESHOLD="0.75"`, the live value). Restart `run`.
2. **Use headphones** (or a low speaker volume): PortAudio gives the daemon plain input, and a Mac has no Jabra-style echo canceller in this path
   [unverified], so loud laptop speakers reproduce the 2026-09-28 self-interruption class and swamp the result.
3. Ask for a reply that lasts 20 s+ ("Hey Jarvis, tell me a long story about a lighthouse"). During playback, one trial per reply: (a) a backchannel
   ("mm-hmm", "yeah"), (b) a real interruption ("wait, stop - what about ..."), (c) non-speech (clap, keyboard, a cough). Aim for 10 of each.
4. `bash scripts/setup/mac_virtual_panel.sh lab-summary` - aggregates `BARGE_DECIDE outcome=... ms=... speech_ms=... heard_ms=...` lines (counts
   and medians only; no transcripts). Read per outcome: commit / resume / ceiling, plus `duck unavailable` count.

**Evidence that counts** (against the research doc's first-bar targets, labelled "Mac mic, headphones"): false-commit on backchannel/noise <= 10 %,
real-interruption commit <= 1.1 s from onset (`ms=` on commit lines), resume on noise >= 90 %, zero self-interruptions with the room quiet. These
validate the *logic and its thresholds against a real VAD*, and catch regressions in the decider/ledger/seed. **What it cannot prove:** that the
duck sounds right on the Pi's mixer or at the Jabra's volume; the echo behaviour of the Jabra speakerphone; Pi CPU/thermal effects on Silero
cadence; `heard_ms` accuracy (different output latency); that the Mac mic's score distribution matches the Pi's (the 0.75 threshold was tuned on the
Jabra). So Mac results are a **pre-screen**, never the go/no-go - that stays the operator's Pi lab. Also note the replay gate does not see any of
this (it stops before TTS).

## 8. Replay gate

`zoe_voice_daemon.py` is on the voice path, so this PR needs the voice replay gate (`scripts/maintenance/voice_regression_probe.py` PASS,
`measure_voice.py` medians unchanged) before the Pi daemon is redeployed with it. Be exact about what that proves: the gate replays saved
recordings through STT/brain and stops before TTS, so a PASS shows the Silero loader and STT/brain path are unchanged (they are - this PR does not touch
them); it is not evidence about playback. The Pi-default byte-identity tests are the evidence for playback. Note `voice_gate_check.py`'s
`VOICE_PATH_PATTERNS` does not list the daemon file, so it will not auto-classify this diff - request the gate explicitly.

## 9. Unverified (no Mac was available)

Everything in `mac_virtual_panel.sh`, `preflight.py` and `mac_backend.py` has been run only against fakes and on Linux (`bash -n`, shellcheck, unit tests).
Not exercised on macOS: the brew formulae and the `CFLAGS`/`LDFLAGS` PyAudio build (PyAudio's install page says `brew install portaudio` then
`pip install pyaudio`, building from source [doc]); `openwakeword` on macOS (its setup lists `speexdsp-ns` for Linux only [doc: openWakeWord setup.py]; if pip
complains, `pip install --no-deps openwakeword` plus its listed deps is the fallback); `torch` arm64 wheel; Silero via `torch.hub` (hubconf needs only `torch`
[doc]); CoreAudio accepting 16 kHz/24 kHz mono input/output through PortAudio (the player falls back to the device rate with linear resampling and logs it);
the microphone permission prompt being attributed to your terminal app; the actual gain-change latency; `caffeinate`/`open`; whether `hey_zoe.onnx` lives at
`/home/pi/.zoe-voice/hey_zoe.onnx` on the Pi (the deploy script's default daemon dir - confirm before copying); the Access overlapping-app cookie behaviour (§4 A);
NDJSON passing through Cloudflare unbuffered (§4).

## 10. Citations

* PyAudio macOS install: https://people.csail.mit.edu/hubert/pyaudio/ - `brew install portaudio`, `pip install pyaudio` (builds from source).
* `afplay`: https://ss64.com/mac/afplay.html - `-v` volume at launch, no device option, nothing on mid-playback changes.
* Microphone permission: https://support.apple.com/guide/mac-help/control-access-to-your-microphone-on-mac-mchla1b1e1fe/mac.
* Access service tokens: https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/ - `CF-Access-Client-Id`/`CF-Access-Client-Secret`, Service Auth action, token expiry.
* Access application paths and precedence: https://developers.cloudflare.com/cloudflare-one/access-controls/policies/app-paths/.
* openWakeWord: https://github.com/dscripka/openWakeWord (`setup.py` markers, `utils.download_models`). Silero VAD hub: https://github.com/snakers4/silero-vad (`hubconf.py`).
* In this repo: `scripts/setup/zoe_voice_daemon.py`, `scripts/setup/mac_panel/`, `services/zoe-data/routers/panel_auth.py`, `routers/voice_tts.py`, `services/zoe-ui/nginx.d/locations.inc`,
  `config/cloudflared-config.yml`, `skills/touch-panel/SKILL.md`, `docs/research/barge-in-duck-decide-resume-2026-10-04.md`, `docs/knowledge/voice-pipeline.md`.
