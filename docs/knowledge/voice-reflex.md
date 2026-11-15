---
type: Reference
title: The panel reflex - a soft "Mm." at the committed close, in Zoe's own voice
description: What the panel's "I heard you" reflex is, its truthfulness rules (never before the commit, non-lexical only, silent at night at first, one voice), the handshake with the server's spacers, the ship bar (post-close talk <= 3 per 100 turns from the instrument first; the owner's blind ear test), deploy and rollback, and what is NOT built yet. Pi flag REFLEX_ENABLED (default off), server flag ZOE_VOICE_REFLEX_AWARE (default on, inert without a reflex block).
tags: [voice-path, panel, first-sound, kokoro, flag-dark]
timestamp: 2026-11-14T00:00:00+08:00
---

# The panel reflex

## What it is

Today the first sound comes about 2.7 s after a person stops talking, and only 8.5% of turns get any sound inside 1 s. The
reflex is ONE soft, non-lexical take ("Mm.", "Mm-hm.") that the panel plays itself, from a pre-rendered bank in Zoe's own
Kokoro voice, at the moment the daemon commits the close of the recording. It says "I heard sound that stopped", which is true
at that moment, and nothing more. Lexical lines ("Checking your calendar.") stay with the server's spacers, which know what is
true.

A close-triggered reflex lands about 0.8-1.1 s after true speech end (the recording closes 720-880 ms after speech on the live
640/800 tail). It moves the share of turns under 1 s; it cannot reach under 0.6 s. That needs a shorter tail, not this.

## Truthfulness rules (binding)

- **Never before the commit.** The Jabra mutes its own mic while it plays: a sound during capture would delete the words of a
  person still talking. The reflex fires only once the endpointer has closed the recording.
- **Non-lexical only.** "Mm." / "Mm-hm.", low and falling. Never "Sure", "Of course", "Got it", "Okay" (dismissive after bad
  news), "Let me check" (no tool known), laughter, sighs, breath (Kokoro cannot render breath; a recorded breath is another
  voice), or "Hm?" (claims not understanding).
- **Silent at night at first.** 22:00-07:00 is silent in S1 (`REFLEX_NIGHT`). A -6 dB night bank is a later owner decision.
- **One voice.** The bank's manifest voice must equal the household's current voice; an unknown voice counts as a mismatch.
  Any mismatch, tampered file or wrong format is no bank: silence, never the wrong voice.
- **Silent when in doubt.** Each rule is one fixed skip reason, checked in this order (the first that applies is reported):
  `disabled`; `error` (a numeric input that is not a finite real number, or any exception inside `decide`); `not_committed`
  (the daemon must pass `committed=True`, exactly: the truthfulness rule in code); `not_wake` (S1
  is wake turns only; a barge turn never); `capped` (hard cap / max_duration / unknown rule); `short` (no_speech, or under
  480 ms of speech); `muted` (the Jabra ate audio); `busy` (another player, or unknown); `media` (music or video playing through the panel,
  True OR unknown); `noisy` (pre-roll above the floor; provisional -40 dBFS until S0 data sets it); `night`; `idle_greeting`
  (first turn after 4 h idle); `guest`; `low_confidence`; `prev_question` (windows only); `budget` (one per 30 s; the chain
  rule for windows, flag-dark); `no_bank`; `voice_mismatch`.
- **Unknown is silence.** `committed`, `guest`, `media_active` and `speaker_conf` default to unknown, and unknown is silence:
  the daemon has to state the safe value. Every numeric input (`speech_ms`, `muted_ms`, `noise_dbfs`, `local_hour`, `idle_h`,
  `since_last_s`, `chain_turns_since`) must be a finite real number (no bool, string, NaN or infinity; durations and counts
  >= 0; `local_hour` a whole hour 0..23), else `error`. `decide` never raises.
- **Guests (decided 2026-11-14, conservative).** The design called the reflex identity-neutral ("mm" is fine for a guest); the
  review asked for an explicit guard. Rule: a guest OR an unknown speaker (`guest` not exactly False) is silent unless
  `REFLEX_FOR_GUESTS=true` (default false). The daemon passes `guest=False` only when its local speaker claim names a household
  member. `REFLEX_FOR_GUESTS=true` lifts this guard only; a guest still needs a speaker score (below). The claim gates only this
  local sound, never what the server does.
- **Speaker confidence.** A MISSING score (`speaker_conf=None`) is always `low_confidence`, and so is a score under
  `REFLEX_MIN_SPEAKER_CONF` (0.70, the live claim threshold) or one that is not a finite number. There is no setting that
  lets an unknown speaker through: until a pre-close speaker identity exists, the reflex stays silent.
- **Not a tic.** 6-8 vetted takes; never the same take twice running; never one of the last two.
- **Never registered as playback.** The reflex player is NOT `_register_tts_process`: a registered player would start the barge
  monitor's 800 ms grace before the reply's onset and bring back self-interruption.

## Pieces

| Piece | Where | State |
|---|---|---|
| Decision logic (gate, rotation, budget, handshake block, H3 ordering wait, bank loader, the daemon's trim rule) | `scripts/setup/zoe_voice_reflex.py` (stdlib only) | built, tested (`tests/unit/test_voice_reflex.py`), not imported by the daemon |
| Server side of the handshake | `services/zoe-data/voice_spacer.py` (`SpacerTurn.reflex`, `note_reflex`, `parse_reflex_block`, veto rules) | built, tested (`services/zoe-data/tests/test_voice_spacer_reflex.py`), not wired into `routers/voice_tts.py` |
| Bank renderer | `scripts/setup/render_reflex_bank.py` (run on the Orin by the owner) | built, tested on a synthetic voice (`tests/unit/test_voice_reflex_bank_render.py`); never run against the live sidecar |

### The handshake

The daemon puts one block on the `/api/voice/turn_stream` payload: `{"kind": "heard", "take": "mm_03", "bank": "<sha8>",
"voice": "<id>", "at_ms": <close to reflex write>, "dur_ms": <clip ms>}`, or `{"kind": "none", "skip": "<enum>"}`. Never text,
never audio. The server parses it strictly (unknown keys dropped, enums checked, ints clamped) and, while
`ZOE_VOICE_REFLEX_AWARE` holds, a heard reflex vetoes:

| Mechanism | After a heard reflex |
|---|---|
| timer filler | `already_spoken:reflex` until the wait reaches `REFLEX_FILLER_HOLD_S` (5.0 s); past it a truthful wait line is kinder than silence |
| tool filler | `already_spoken:reflex` (a generic line after a receipt is noise) |
| tool ack | allowed only when corroborated AND from the domain's own pool ("Checking your calendar."); else `already_spoken:reflex` |
| processing-ack audio | suppressed (`processing_ack_audio_allowed()`); the text frame for the screen stays |
| greeting | unchanged (content); the panel itself skips the reflex after 4 h idle |

The reflex does not take the turn's one spacer claim (`spoken` is untouched). With no block, or `kind: none`, nothing changes.

**Ordering (H3), holds even against an old server:** the stream loop waits `ordering_wait_ms` before its first server PCM write,
so the reply starts at least `REFLEX_SEAM_MS` (120 ms) after the reflex ends. The clip counts for at most `REFLEX_MAX_MS`
(600 ms), so the wait is at most 720 ms; on brain turns (first audio >= ~1.5 s after the close) it is 0.

### The bank

`~/.zoe-voice/reflex/<voice>/<bank_sha8>/{mm_01..}.wav` plus `manifest.json` (voice, style_len or null, speeds, kokoro and
misaki versions when discoverable, sidecar code sha, render date, per-take sha256 / dur_ms / rms_db). 24 kHz mono S16, the
format of the server's chunks. The sha8 covers the voice, the style row and every take's sha256 (not the date), so the same
inputs give the same bank. Takes are trimmed with the daemon's own rule, faded 10 ms, RMS-matched to the reference reply speech
minus 3 dB (`REFLEX_GAIN_DB`), peak at most -1 dBFS.

Render (owner, on the Orin, after the W1 sidecar `style_len` change if wanted):
```
python3 scripts/setup/render_reflex_bank.py --voice <voice id> --render-date <YYYY-MM-DD> --dry-run
python3 scripts/setup/render_reflex_bank.py --voice <voice id> --render-date <YYYY-MM-DD> --out ~/reflex-candidates
# listen, keep 6-8 takes, then re-render only those (Kokoro is deterministic, the audio is identical)
python3 scripts/setup/render_reflex_bank.py --voice <voice id> --render-date <YYYY-MM-DD> --keep mm_02,mm_05,... --out ~/reflex-bank
```
Without sidecar `style_pin` support in `/health` the renderer omits `style_len`, writes `"style_pin": false` and
`"style_len": null`, and says so.

## The ship bar

1. **The instrument first (S0, log-only).** One week of `FIRST_SOUND` lines from the daemon; post-close talk (`still_talking`)
   must be **<= 3 per 100 turns** before any sound ships, because a close-triggered reflex makes today's false closes audible.
2. **The owner's blind ear test (15 min).** 20 scripted wake turns (a task, a chat, a sad sentence, a long pause mid-thought),
   arm A silence vs arm B reflex, blind-ordered. Pass: >= 60% prefer B and **0 "talked over me"**.
3. **Bank seam test (S2).** Per-take RMS within +-1 dB of target; reflex-then-reply, the owner cannot hear a voice change in
   >= 16/20.
4. Live after: first ANY sound p50 on eligible wake turns ~0.8-0.9 s; server first-audio p50 unchanged within 50 ms; false
   starts <= 3 per 100 reflex turns.

## Deploy and rollback

The recipe is the existing one in [voice-pipeline.md](voice-pipeline.md) "Operator deploy": server first through the voice-path
deploy gate, then from a clean worktree at the merged commit `DRY_RUN=1 bash scripts/setup/deploy-pi-voice.sh`, then the real
run (rsync with `.bak-<ts>`, restart `zoe-voice`, verify active, `/health` and md5). The bank needs a separate DIR step (not
built). Pi flags go in `/home/pi/.zoe-voice/.env.voice`. Mixed versions are safe both ways (additive keys).

Rollback, cheapest first:
- `REFLEX_ENABLED=false` in `/home/pi/.zoe-voice/.env.voice`, then `systemctl --user restart zoe-voice` on the panel;
- `ZOE_VOICE_REFLEX_AWARE=0` on the Jetson, restart zoe-data (the server stops honouring the block);
- restore the `.bak-<ts>` daemon as the deploy script printed.

## NOT built yet

- **Daemon wiring** (`scripts/setup/zoe_voice_daemon.py`): decide at the close in `record_command`, an unregistered
  `_ReflexPlayer`, the payload block, the H3 wait before the first `_feed_pcm_chunk`, caching the voice from the done frame,
  and adding `zoe_voice_reflex.py` to `deploy-pi-voice.sh` `SHIPPED_FILES`. What it passes for the explicit inputs:
  `committed=True` only on the branch where `endpointer.push(...)` returned True (stop reason `silence`), never on the
  max-duration path; `guest=False` only when the local voice claim names a household member, `True` for a named guest, `None`
  when no claim exists; `speaker_conf` = that claim's raw score, or `None`. CAUTION: today the claim is scored AFTER
  `record_command` returns (`_speaker_claim_to_attach(wav)` in `_do_single_turn_stream`), and in shadow mode
  (`SPEAKER_ID_SHADOW`, default on) it returns None at once. So at the close the identity is unknown, and with the defaults the
  `guest` and `low_confidence` guards silence EVERY turn. The reflex cannot play until the claim is scored before the reflex
  (costs its latency; not possible in shadow mode, so shadow mode must end first). `REFLEX_FOR_GUESTS` does not help: a
  missing score is silence on its own; `media_active` from the panel's own media state (the AirPlay / shairport and Music Assistant player state, which the
  daemon does not read today: until it does, pass `None`, which is silence).
- **The two-line server wiring** in `services/zoe-data/routers/voice_tts.py`: `_spacer.note_reflex(payload.get("reflex"))`
  after the `SpacerTurn` is built in `voice_turn_stream`, `waited_s=` on the timer filler's `timer_line`, and the
  processing-ack audio check; plus the `reflex=` fields on VOICE TIMING and the done frame's `voice`.
- **The DIR deploy step** for the bank (rsync of `reflex/<voice>/<sha8>/` plus a manifest md5 check, owner-run).
- The S0 instrument, the class frame (S3), windows (S4), the night bank (S5).
