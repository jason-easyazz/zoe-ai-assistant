---
type: Reference
title: Panel Pi log review — 2026-10-04 (barge-in phase 1 deploy check)
description: Read-only review of the zoe-pi voice daemon's logs for 2026-10-04 and the Pi's health at 21:44 AWST — full warning/error inventory split around the 19:54 barge-in phase 1 redeploy (flag off), the before/after verdict (startup, cadence, CPU and health unchanged; no live turn since the deploy), the two daemon fixes (a per-request InsecureRequestWarning flood that was 82 % of the unit's journal; no recovery line after announce-poll outages), and the operator items (passive-cooled Pi at 76–82 °C with throttling history, inert Nice=-5, no camera on the USB bus).
tags: [panel, pi, voice-daemon, log-review, barge-in, urllib3, journald, thermal, announce-poll, operator]
timestamp: 2026-10-04T22:00:00+08:00
---

# Panel Pi log review — 2026-10-04

**Scope.** `zoe-voice` on `zoe-pi` (Raspberry Pi 5, 8 GB): `~/.zoe-voice/voice.log` (full file), the
system journal from 17:00 to 21:44 AWST (the user journal is empty — `Storage=volatile`, and the
unit logs to the system journal), `dmesg`, and the Pi's health. **Read-only**: nothing on the Pi
was restarted, edited or deployed, and `.env.voice` was read for key names only.
The deployed `zoe_voice_daemon.py` and `zoe_voice_announce.py` are byte-identical (md5) to the repo at
`5127e861` (#1830) — what was reviewed is what main contains.

## 1. Pi health snapshot (21:44 AWST)

| Check | Result |
|---|---|
| Uptime / load | up 6 d 3 h (boot 2026-09-28 18:23); load 3.0 → 1.7 over the review |
| Memory | 8,063 MB total, 1,664 used, 6,399 available; swap 0 of 511 used |
| Disk `/` | 29 G, 52 % used, 14 G free |
| `vcgencmd get_throttled` | `0xe0000` — **history only**: ARM frequency capped (17), throttled (18), soft temperature limit (19) have each occurred since boot. Live bits 0–3 are clear. **No under-voltage bit (16)**; `EXT5V_V` 5.12 V; `dmesg` has no voltage message |
| Temperature | **81.8 °C → 76–77 °C** within a minute; `scaling` at 2.4 GHz at the time of the reading; `thermal_zone0` is the only thermal device — no fan cooling device is registered |
| `systemctl --failed` (system and `--user`) | 0 units both |
| Units | `zoe-voice`, `zoe-airplay` (shairport-sync 5.x), `pulseaudio`, `zoe-panel-agent`, `pw-headless3` (user); `nqptp`, `zoe-kiosk` (system) — all `active`, `NRestarts=0`; `zoe-kiosk-watchdog` oneshot fires every minute (243 clean runs) |
| Audio | one sink (the Jabra Speak 750), `SUSPENDED` (idle), 0 sink-inputs |
| USB | `lsusb`: only the Jabra (3-1). `dmesg -T` is readable with `sudo -n`: 590 lines, **none after boot** — no USB disconnect, reset, xhci error, OOM or thermal message |
| `/health` on :7777 | `status=ok`, `uptime_s` consistent with the 19:54 restart |

Two things were running on the Pi that are not the voice path and add heat and load: the
`pw-headless3` headless chromium (the UI-review instrument, started 19:49 and still up) and a
speaker-shadow embedding experiment (its log and `embeddings.npz` were being written at
21:43–21:47). Neither is a daemon fault.

## 2. Inventory — every warning / error / exception / restart class, 2026-10-04

The journal window is 17:00–21:44 (8,158 lines; class 1 is two lines per warning, ~6,690 lines).
`voice.log` counts are for the whole day. "pre" is before 19:54:22 (the stop of the old process),
"post" after. `voice.log` has **no** `ERROR`, `CRITICAL`,
`Traceback` or restart-without-signal line today (the last `ERROR` anywhere in the file is 2026-07-31).

| # | Class (redacted sample) | Source | Pre | Post | First – last | Code path |
|---|---|---|---|---|---|---|
| 1 | `InsecureRequestWarning: Unverified HTTPS request is being made to host '<jetson>'` + `warnings.warn(` (stderr → journal) | urllib3, every `requests` call with `verify=False` | 2,052 | 1,292 | 17:00:03 – 21:44:52, one per 5.1 s in both halves | `zoe_voice_daemon.py` `_fetch_announcements` (every poll); also `_api_post`, `_do_single_turn_stream`, `_sync_speaker_profiles` — all `verify=VERIFY_SSL` |
| 2 | `WARNING announce poll failed (502 Server Error: Bad Gateway for url: https://<jetson>/api/voice/announcements) — backing off up to 60s until the server returns` | `zoe_voice_announce.py:237` (first failure of a streak only) | 43 on the day | 7 | 02:26 – 21:41 | poller `run()`; fetch at `zoe_voice_daemon.py:3143`. Variants: `Read timed out (read timeout=10)` ×3, `Connection refused` ×1 (21:25:40) |
| 3 | `INFO wakeword near-miss: max_score=0.2x–0.39 (need 0.4500)` | `zoe_voice_daemon.py:3519` (12 s limiter) | 6 | 0 | 10:14 – 19:07 | main loop |
| 4 | ALSA/JACK startup noise (`Unknown PCM surround…`, `jack server is not running`, `Cannot open device /dev/dsp`, `dsnoop … unable to open slave`) | libasound/PortAudio during `pyaudio.PyAudio()` (`:3341`) | 0 | 83 lines, all at 19:54:24 | startup only | benign, present on every start |
| 5 | `onnxruntime … GPU device discovery failed … /sys/class/drm/car…` | onnxruntime at wake-model load | 0 | 1 | 19:54:23 | benign (CPU provider is used) |
| 6 | `INFO Empty transcript with no audio — retry chime.` | `zoe_voice_daemon.py:2719` | 1 | 0 | 14:06:04 | see §5 (orb tap with no speech) |
| 7 | chromium `registration_request.cc: DEPRECATED_ENDPOINT` (GCM) ×6, `SharedImageManager::ProduceMemory … non-existent mailbox` ×3 | the `pw-headless3` instrument browser (pids 299983/300011) | 5 | 4 | 19:49 – 21:43 | not the kiosk, not the daemon |
| 8 | `start-kiosk.sh … dbus/bus.cc: Failed to connect to the bus` ×3 | the kiosk chromium | 0 | 3 | 21:27:25 | benign (no session bus for the kiosk); coincides with the panel waking (`panel-agent mode idle -> day`) |
| 9 | `Shutdown signal received.` → `Voice daemon stopped.` → `Logging to …` | systemd stop/start (the deploy) | – | 1 restart | 19:54:22 | clean: stop took 21 ms; no `Failed`, no SIGKILL |

shairport-sync / nqptp / `zoe-airplay`: **zero** journal lines in the window (no errors, no
activity). Speaker-claim lines: one shadow-only line today (14:06, pre). Face-ID: the daemon has
no face-ID lines or code path; `face_profiles.json` was last written 2026-07-19. TTS (`aplay`/`pactl`):
no TTS ran since 14:06 — see §3.

## 3. Before / after the 19:54 deploy (barge-in phase 1, `BARGE_DUCK_ENABLED` unset)

**Verdict: no regression found; the flag-off turn path is not exercised live yet.**

Evidence that behaviour is unchanged:

- **Startup sequence.** The 19:54 start logs exactly the same set of 19 line classes as the
  2026-09-29 12:08 start (the last previous restart; a set diff is empty — the thread-start lines
  interleave in a slightly different order, which is a thread race, not a change): model load, `Barge-in VAD thread started.`,
  `Announce poll thread started (interval=5.0s)`, `Speaker profiles loaded from disk`,
  `Listening on panel=…`, `Follow-up config`, `Wake beep`, resemblyzer, `Silero VAD loaded.`,
  `Barge-in VAD thread started (threshold=0.75)`, `Speaker-ID pipeline warmed`. No new line class,
  no new `WARNING`/`ERROR`. (Barge-in with the old hard stop is `BARGE_IN_ENABLED`, default true,
  and unchanged; `BARGE_DUCK_ENABLED` is not in `.env.voice`, so duck/decide/resume is off.)
- **Poll cadence.** 2,052 stderr warnings in 174 min before, 1,292 in 110 min after: 5.09 s vs
  5.11 s per poll. The announce poller is untouched by the phase 1 diff.
- **CPU.** The old process consumed 13 h 49 min over its ~5-day life (≈ 11 %); the new one 12 min 21 s
  over 110 min (≈ 11.2 %). The barge VAD thread's idle cost did not move.
- **Health.** `/health` returns 200 and the wake phrase; no restarts, no crash, no `BARGE_DECIDE`
  or `Barge-in duck` line (expected with the flag off).
- **Static review of the #1830 hunks that touch flag-off code.** `_heal_duck_leak(proc)` runs per
  playing chunk but returns immediately while `_duck_leak is None`; `_PLAYOUT.reset()/note()` and
  the seed ring are all behind `BARGE_DUCK_ENABLED`; `_set_turn_response` only stores the response
  object; `_follow_up_source` with `seed=None` performs the same notify → beep → `_recording_active.set()`
  → listen sequence (the only visible difference is that the `Follow-up listening …` line is now
  logged before the beep rather than after it). The 49 barge-in tests pass, including the flag-off lock.

**Unverified, because it did not happen:** after 19:54 there was **no wake, no orb tap, no STT
upload, no TTS playback, no follow-up window and no speaker-claim line** on the panel. The only
turn of the day was the 14:06 orb tap (before the deploy), which was an empty recording. So wake
detection → record → `/api/voice/turn_stream` latency → `aplay`/`pactl` playback → follow-up
cadence on the new code is untested on the Pi. The first real utterance after the deploy should be
read against the 09-28/29 baseline (§6 lists the lines to expect).

## 4. Fixes in this PR

1. **`InsecureRequestWarning` flood — `zoe_voice_daemon.py` `_silence_insecure_request_warnings`.**
   The Pi runs `VERIFY_SSL=false` (self-signed Jetson cert). urllib3 registers `SecurityWarning` as
   "always", so each `requests` call wrote a two-line stderr traceback: **~3,340 warnings (two lines each) = ~6,690 of
   the 8,158 journal lines (82 %) in 4 h 45 min, ~17,000 warnings (~34,000 lines) a day**, every 5 s from the announce poll, interleaved with the real log lines and written into
   the volatile (RAM) journal. Now: when `VERIFY_SSL` is false the warning class is disabled
   process-wide and one `INFO TLS certificate verification is OFF …` line says so at startup.
   Verification itself is unchanged (still the operator's value in every `verify=` argument).
   Class sweep: every `requests` call in the daemon goes through `VERIFY_SSL`; `zoe_face_id.py`
   has one `requests.get` (hourly TTL) and the daemon never imports it, so it was left alone.
   Tests: `tests/unit/test_voice_daemon_tls_warning.py` (flag off silences and notices exactly once;
   flag on silences nothing and announces nothing — the negative control; the flag value is not
   altered; a broken urllib3 never costs the daemon). Break-the-fix: removing the
   `disable_warnings` call turns the first test red; making the guard unconditional turns the
   second red.
2. **No recovery line after announce-poll outages — `zoe_voice_announce.py` `AnnouncePoller`.**
   Only the first failure of a streak is logged, so the log counted 50 outages on 2026-10-04 but
   never showed one end. Now one `INFO announce poll recovered after N failed polls; poll blind ~Ts (…)` (first failure to first good poll, backoff included — an upper bound on the outage, not its length) closes each
   streak. While fixing it, a latent coupling: a *busy* cycle (live turn) returns `["busy"]` without
   fetching, yet it reset the failure counter — so a turn in the middle of an outage snapped the
   backoff from up to 60 s back to 5 s and would have logged a "recovery" for a server nobody had
   reached. Busy cycles now leave the outage state alone. Tests in
   `services/zoe-data/tests/test_voice_announce_daemon_logic.py` (recovery once with count and
   length; healthy run logs none; busy cycle neither recovers nor resets). Break-the-fix: forcing
   the old `reset on any outcome` turns the busy test red; removing the log turns two red.

Not changed on purpose: the 502 bursts themselves are Jetson-side (see §5).

## 5. Remaining items (not fixed here)

**Operator steps** (also in [incident-runbook.md](incident-runbook.md) §23):

- **Thermal.** Idle temperature 76–82 °C with throttling history (`0xe0000`) and no fan device
  registered. Fit a heatsink/Active Cooler (or confirm the cooler's overlay is loaded) and re-check
  `vcgencmd get_throttled` stays `0x0` under a voice turn. Meanwhile stop the review instrument when
  it is not in use: `systemctl --user stop pw-headless3` on zoe-pi (it has run since 19:49), and
  schedule Pi-side embedding experiments away from voice use.
- **Camera / USB power.** No camera is on the Pi's USB bus (`lsusb` shows only the Jabra) and none
  enumerated since the 09-28 boot, so the "TTS during video knocks the camera off" trap cannot be
  assessed from this boot. If a camera is expected on the panel, check it is plugged in; whenever it
  is attached, put it and the Jabra on a **powered hub**. There was no USB event or under-voltage
  in `dmesg` today.
- **Landing this PR.** Deploy both `zoe_voice_daemon.py` and `zoe_voice_announce.py` with
  `scripts/setup/deploy-pi-voice.sh` after the replay gate, then verify:
  `journalctl -u zoe-voice --since "<restart>" | grep -c InsecureRequestWarning` is `0` and exactly one
  `TLS certificate verification is OFF` line appears.
- `Nice=-5` in `zoe-voice.service` is inert (`NI 0`, `RLIMIT_NICE 0` for the user manager) — known;
  see `tests/unit/test_systemd_memory_protection.py`. No action; do not rely on it.
- Journal storage is `volatile`: a reboot loses `journalctl` history (`voice.log` and `dmesg`-since-boot
  are the only evidence that survive a restart of the unit; `dmesg` does not survive a reboot).

**Jetson-side finding (not a Pi bug).** The announce-poll 502 bursts follow Jetson restarts: the
last one (21:41:57) is four seconds after `zoe-data` entered `active` at 21:41:53; 21:25 matches
the nginx/`zoe-ui` restart (`Connection refused`). 43 outage streaks before 19:54 and 7 after
means many deploys/restarts in a day; the Pi handled every one without a crash. The two
`Read timed out (10 s)` events (13:57, 14:30) are the Jetson being slow, not down.

**Observations needing an owner decision:**

- **Wake near-misses.** 38 near-misses since 09-28; three scored 0.39–0.43 against the 0.45
  threshold (0.4038 and 0.4334 on 09-29, 0.3920 on 10-04 10:25), which look like missed real wakes.
  Today's other five (0.22–0.31) are room noise. Threshold changes go through the voice gate; not touched.
- **Orb tap with no speech (14:06).** The recording ended after 3.04 s: 20 chunks of pre-roll (1.6 s,
  audio from *before* the tap, since the orb path does not clear `_PREROLL`) plus the 1.5 s
  no-speech window, then the retry chime. A user who taps and pauses has ~1.2 s to start speaking
  (the wake chime overlaps it). Only one sample; worth a look if orb taps feel impatient.
- **Shadow speaker scoring on a no-speech clip.** That same empty recording was scored by the
  speaker-ID shadow (a nominal match at 0.52). The shadow log is one-row-per-turn by contract, so it
  was left alone; exclude `tail=no_speech` turns when the W5 analysis reads the metrics file.
- The orb-tap path logs no `Listening again …` line after the turn (the wake path does) — cosmetic.

## 6. What to read in the log after the next real utterance

For the first wake after the deploy, expect (and compare against 09-29): `Wake word detected!` →
`Recording command` → `Recorded command: …` → `Speaker ID (shadow): …` → `turn_stream TTFA=…s` →
`Listening again (cooldown …)` → `Follow-up listening (turn …)`. No `BARGE_DECIDE` / `Barge-in duck`
line should appear with the flag off. A `Barge-in detected during playback` line should only follow a
real interruption (see incident-runbook §12).
