---
type: Runbook
title: Zoe-driven voice enrolment
description: How Zoe knows who has not enrolled their voice, offers it by voice at a natural moment, and runs the enrolment herself from the panel (no ssh, no command). Flag ZOE_ENROLL_OFFER (default off). Design, privacy, the exact operator deploy steps, the owner's 2026-10-12 failed run and its three causes.
tags: [voice, speaker-id, enrolment, biometrics, panel, operator]
timestamp: 2026-10-12T00:00:00Z
---

# Zoe-driven voice enrolment

Owner requirement (verbatim): *"if enrolment hasn't been done, zoe needs to know this, and prompt the user, if they decline that is fine but they aren't going to be running a piece of code."* Household members never run commands.

Contract and forbidden list: [services/zoe-data/AGENTS.md](../../services/zoe-data/AGENTS.md) (server) and [scripts/AGENTS.md](../../scripts/AGENTS.md) (daemon). Policy of record for what is stored: [biometric-retention-policy.md](biometric-retention-policy.md).

## The design

| Piece | Where | What it does |
|---|---|---|
| Knowledge | `enrollment_state.py`, migration `0046` (`enrollment_offers`) | Enrolment is READ from `speaker_profiles` / `face_profiles`; the new table holds only what Zoe offered and what the member answered. A failed read is "unknown", never "not enrolled". |
| Policy | `enrollment_state.decide_offer` (pure) | Offer only to a real, positively-adult member who is not enrolled, not declined (60 days), not offered in 24 h, outside quiet hours. Never guests, synthetic ids, unknown roles; children only with `ZOE_ENROLL_OFFER_KIDS`. |
| Moments | `routers/panel_auth.py` (after a correct PIN), `voice_enrollment.tier` (a thanks / "that's all" on a voice turn) | A PIN step-up is the best moment ("so you won't need your PIN next time"). A goodbye never carries an offer. |
| The talk | `voice_enrollment.py` + `lexicons_data/voice_enrollment_<lang>.json` | Spoken yes/no (the repo's confirm machinery + the language file), "learn my voice", "forget my voice", "do you know my voice?". |
| The hands | `scripts/setup/zoe_voice_enroll_mode.py` + the daemon's "Enrollment mode" section | The daemon enrols the member itself, in-process, without stopping. |
| Channel | the announcement queue (`voice_announce.enqueue_command`) | A claimed command has empty `text` + a `command` object: an old daemon says nothing, a new one acts. |

Three properties from review (Codex, PR #2003): (1) the `voice_enroll` command is **panel-scoped** and is only queued for a panel that holds a live device token, so another panel's daemon can never claim it; (2) the yes/no is **armed only when the daemon's played-ACK arrives** (`POST /api/voice/announcements/{id}/played` -> `on_announcement_played`), never when the offer is merely queued, so an unrelated "yes" cannot be consent; (3) a failed enqueue **gives the offer slot back** (`release_offer`), so it is not counted as an offer or an ignored offer. And the daemon reports WHY a run was cancelled: only `said_stop` / `tapped_stop` record a decline; a `shutdown` or restart does not.

Flow: a correct PIN (or a thanks) -> policy -> one spoken offer through the announcement queue ("I can learn your voice ... I'd keep a voice signature, not recordings, and you can delete it whenever you like. It takes about a minute. Want to?") -> the daemon speaks it and opens a listening window -> "yes" -> the server queues `voice_enroll` with the member's language script -> the daemon (main loop, wake stream closed) says the intro, then per phrase: prompt, beep, record, judge, retry with guidance -> only when all three are captured it uploads them to `POST /api/voice/enroll` with `consent: true` -> confirms aloud -> reports `POST /api/enrollment/result` -> back to wake-word listening. Bounded at 90 s; "stop" (heard in a capture), a tap (`POST :7777/enroll/cancel`) or a shutdown ends it with nothing stored.

On "no": stored with a 60-day quiet period (`ZOE_ENROLL_DECLINE_DAYS`); three offers nobody answered start the same quiet period. "Learn my voice" works any time and clears it.

## The phrases on the talk screen

Owner request 2026-10-15: *"it's sometimes hard to remember; you could probably just use the Zoe talk screen."* Zoe still SPEAKS every phrase; the panel also SHOWS it, large, so it can be read while saying it.

`EnrollmentRun(screen=...)` -> the daemon's `_enroll_screen` -> `POST /api/enrollment/screen` (device token; the server pushes a transient `voice_enroll` frame to the token's panel only) -> `touch-ui-executor.js` relays `zoe:voice-enroll` (only a frame naming this panel) -> `js/touch-voice-enroll.js` (loaded by `touch/home.html`) paints a full-screen overlay in the talk screen's type: `Say:` + the phrase + `2 / 5` + dots.

| State | On the wall |
|---|---|
| `phrase` | `Say:` + the phrase, counter and dots. Sent BEFORE Zoe starts speaking the phrase. |
| `recording` | `Recording...` with a pulsing dot, the phrase still up, until the capture ends. |
| `accepted` | a tick, that dot filled. |
| `reask` | the gentle `That one sounded different - let's try it again` + the phrase again (the ResNet34 leave-one-out re-ask). |
| `saving` | `Saving...` while the signature is uploaded. |
| `done` / `partial` / `failed` | the closing line, gone after 8 s. |
| `clear` | gone at once: the run ended any other way (stop, tap, timeout, shutdown, no voice, a crash). |

The wall never keeps a stale phrase: a `clear` removes it immediately, a closing line expires by itself, a 75 s watchdog (re-armed by every frame) takes it down if the daemon dies mid-run, and a tap hides it. Privacy: only the phrase text, the counter and a short localized line (`screen_*` in the language file) are sent - no member, no name, no audio; the page sets text with `textContent` and stores nothing in the browser. The daemon's post is short (3 s, no retries) and stops after two failures in a row, so an older server (no endpoint) costs two failed posts, never a slower or broken enrolment. Pinned by `tests/unit/test_voice_enroll_mode.py` (the `screen` group), `services/zoe-data/tests/test_enrollment_screen.py` and `services/zoe-ui/dist/test_touch_voice_enroll.js` (CI wrapper `test_touch_voice_enroll_harness.py`). Deploy: the daemon files (`deploy-pi-voice.sh`) AND the server/UI together; the screen needs both ends, the spoken flow works with either missing.

## Privacy

- The offer and the intro both say what is kept: **a voice signature (256 numbers), not recordings**, deletable on request.
- Recordings exist only in the daemon's memory during the run and are dropped; the server derives the embedding and discards the audio (unchanged).
- Nothing is recorded unless the spoken intro was heard (Zoe's voice unavailable -> the run ends).
- **Forget my voice** deletes the server row AND queues a `voice_profile_forget` command for every panel holding a device token; the daemon removes the profile from memory and the 0600 `speaker_profiles.json` at once instead of waiting up to an hour for the sync. The settings-page delete does the same. It also starts the quiet period so Zoe does not offer again tomorrow.
- **Children are an owner decision** (biometrics of minors): `ZOE_ENROLL_OFFER_KIDS` defaults OFF. With it off a child is never offered and "learn my voice" from a child is refused ("only switched on for grown-ups"); forgetting a voice is never gated.
- Residual risk, stated: identity for the unenrolled comes from the panel session / PIN, exactly as for "read my calendar". The PIN offer is the strongest path (just verified). A spoken request that arrives with a voice MISMATCH (`speaker_verified=False`) is refused. An already-enrolled member cannot re-enrol by voice (it would average another voice into their profile): "forget my voice" first.
- **The server decides whose voice the upload writes (2026-10-19).** Queuing the `voice_enroll` command also opens an enrolment GRANT for exactly that member at that panel (`biometric_scope.grant_enrolment`, in-process, command TTL + run + 60 s), and `/api/voice/enroll` from the device token writes only the granted member or the member with a fresh PIN sign-in at that panel; the daemon's `user_id` is checked, never trusted. The run's `/api/enrollment/result` closes the grant and records a "no" only for the granted member. A token used over Cloudflare or the relay never enrols. A zoe-data restart mid-run drops the grant, so that run's upload is refused (say "learn my voice" again).

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `ZOE_ENROLL_OFFER` | `off` | `off` = nothing (returns before any work); `shadow` = decide and log `ENROLL_OFFER ...` lines, say and do nothing; `on` = offers + spoken requests + commands. Per-call read. |
| `ZOE_ENROLL_OFFER_KIDS` | off | offer / accept requests from child members |
| `ZOE_ENROLL_DECLINE_DAYS` | 60 | quiet period after a no |
| `ZOE_ENROLL_OFFER_PIN_DELAY_S` | 20 | wait after a PIN so the resumed command's reply goes first |
| `ZOE_ENROLL_MODE_ENABLED` (Pi `.env.voice`) | true | daemon accepts enrol commands |
| `ZOE_ENROLL_RECORD_MAX_S` (Pi) | 8.0 | longest single capture |

The owner flips `ZOE_ENROLL_OFFER` (shadow first) AFTER the replay gate below.

## Operator steps (deploy order matters)

Nothing here is done by the agent; the daemon deploy is an operator step.

1. **Merge** the PR; the server deploy restarts zoe-data (run migration `0046`: the normal deploy path applies Alembic; check `alembic heads` shows `0046`). With the flag off nothing changes for anyone.
2. **Apply the library-path drop-in** (the real fix for the owner's 503 — see the findings below), in the deploy window, mind the voice-gate 2 GB headroom rule:
   ```
   cp scripts/setup/systemd/zoe-data.service.d/70-ld-library-path.conf ~/.config/systemd/user/zoe-data.service.d/
   systemctl --user daemon-reload && systemctl --user restart zoe-data   # then poll /readyz
   ```
   Verify enrolment's engine loads (no value printed): `tr '\0' '\n' < /proc/$(systemctl --user show -p MainPID --value zoe-data)/environ | grep -c '^LD_LIBRARY_PATH=.*aarch64-linux-gnu'` prints `0`. (The code-side fallback makes enrolment work without this step, at ~6 s per upload.)
3. **Deploy the daemon to the Pi BEFORE flipping the flag**, all files together:
   ```
   DRY_RUN=1 bash scripts/setup/deploy-pi-voice.sh        # shows the plan
   bash scripts/setup/deploy-pi-voice.sh                  # ships zoe_voice_daemon.py, zoe_voice_announce.py, zoe_voice_enroll_mode.py, zoe_enroll_flow.py, pi-requirements.txt; restarts; verifies active + /health + md5
   ```
   Rollback is printed by the script (`.bak-<ts>` copies). If only the daemon file is copied by hand, the missing `zoe_voice_enroll_mode.py` / stale `zoe_voice_announce.py` disable commands with one WARNING at startup and in the poll log; they never crash the daemon.
4. **Replay gate** (the daemon file is on the voice path): `flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py` against a checkout of the PR head; said-vs-did and per-stage speed must not regress. The server change adds one tier that returns before any work with the flag off; the harness is the proof.
5. **Shadow first**: set `ZOE_ENROLL_OFFER=shadow` in zoe-data's env, restart, watch `grep ENROLL_OFFER ~/.zoe-logs/zoe-data.stderr.log` for a day: `would_offer` lines show who WOULD be offered and when, nobody hears anything.
6. **Then `on`** (and `ZOE_ENROLL_OFFER_KIDS` only if the owner decides). Try it: sign in with a PIN on the panel, wait ~20 s: Zoe offers; say yes; follow the prompts. Check `GET /api/enrollment/state`.
7. Panel `.env.voice` needs no new keys (`DEVICE_TOKEN`, `ZOE_URL` already exist).

## The owner's failed run, 2026-10-12 19:11 AWST (`zoe_enroll_flow.py voice --user jason`) — three causes, all fixed here

1. **Every `POST /api/voice/enroll` returned 503 "resemblyzer not available"** (167-489 ms). Cause, reproduced read-only by running the venv python with the live process's `/proc/<pid>/environ`: the unit's `LD_LIBRARY_PATH=/home/zoe/.local/lib:/usr/local/cuda-12.6/lib64:/usr/lib/aarch64-linux-gnu` makes the linker prefer the distro `libopenblas` over the BLAS bundled in the torch wheel, and `import torch` (pulled in by resemblyzer) dies with `ImportError: .../torch/lib/libtorch_cpu.so: undefined symbol: sbgemm_`. It works from any shell or transient unit that lacks the variable (it works with the variable minus only the last entry, and with no variable at all). The route's 503 came from `_compute_resemblyzer_embedding` catching the `ImportError` and logging it at DEBUG, so nothing reached the log. **Fix:** the failure is now a WARNING with its exception type and message; `last_failure()` feeds the 503 body (which now names the cause and this section); an environment-shaped failure falls back to a child interpreter with the distro directories filtered out of `LD_LIBRARY_PATH` (verified against the live environment: a 1024-byte embedding); the drop-in `70-ld-library-path.conf` removes the cause. Pinned by `tests/test_speaker_engine_import_failure.py`.
2. **A robot voice for the first lines.** `zoe_enroll_flow.say()` uses the same `POST /api/voice/synthesize` waterfall as every reply (Kokoro -> local -> Edge -> espeak-ng). At 19:10:39 and 19:10:54 that endpoint answered **503** in ~1.7 s: the Kokoro sidecar was crash-looping on CUDA init (`... — retrying in 6s`, ready only at 19:11:05) and the Jetson has no server-side espeak-ng, so the waterfall was empty. The flow then fell to its CLIENT-side `espeak-ng` on the Pi on the first failure — the robot voice. **Fix:** `say()` retries the server voice (6 attempts, ~20 s) before espeak-ng ever speaks; the daemon's enrolment mode uses the daemon's normal TTS path with the same wait and never a local espeak (if Zoe's voice is unavailable the run ends before recording — consent is spoken). The Kokoro CUDA-init failure itself is on the open-problems ledger. Pinned by `tests/unit/test_enroll_flow.py`.
3. **No beep although the prompts say "After the beep".** The flow only played a chime `if os.path.exists(HERE/chime.wav)`, and the Pi has never had a `chime.wav`. **Fix:** the flow generates the daemon's two-tone chirp when no file is deployed; the daemon's enrolment mode plays `play_wake_beep()` (or the follow-up tone when `WAKE_BEEP_ENABLED=false`) before every recording. Pinned by `tests/unit/test_enroll_flow.py` and `tests/unit/test_voice_enroll_mode.py`.

## Not built (named honestly)

- A tap-to-stop on the enrolment screen: the overlay only hides itself on a tap; cancelling by tap is still the daemon-side `POST /enroll/cancel`.
- Barge-in DURING a prompt is not monitored (the monitor needs its own mic stream); cancel is heard in the next capture, by tap, or by the 90 s bound.
- Face enrolment is not Zoe-driven (voice only); the state API and `enrollment_offers.modality` already carry `face`.
- Multi-panel routing of the offer / enrol command uses the repo's claim-any default (one household speaker); set `ZOE_ANNOUNCE_STRICT_PANEL` for exact-panel claims when a second speaker exists.

## Verification

`pytest services/zoe-data/tests/test_voice_enrollment.py services/zoe-data/tests/test_speaker_engine_import_failure.py tests/unit/test_voice_enroll_mode.py tests/unit/test_enroll_flow.py tests/unit/test_flag_inventory.py` (all `ci_safe`; every group has a break-the-fix control recorded in the PR).
