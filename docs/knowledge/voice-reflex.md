---
type: Reference
title: The panel reflex - a soft "Mm." at the committed close, in Zoe's own voice
description: What the panel's "I heard you" reflex is, which safety rules the code enforces and which it cannot, the single REFLEX_MODE switch (off, shadow, on; default off), the bank and how it is built, the exact switch-on steps (deploy, shadow week, numbers that must hold, on, rollback), and what is not built.
tags: [voice-path, panel, first-sound, kokoro, flag-dark]
timestamp: 2026-11-15T00:00:00+08:00
---

# The panel reflex

Labels: **[code]** read in the source, **[lab]** run on the lab, **[live]** read on the box, **[inferred]** reasoned, not measured.

## What it is

First sound after a person stops speaking is p50 2.73 s, p90 4.47 s, 8.5% of turns under 1 s (felt scorecard, 47 turns, one
week to 2026-11-14) [live]. The reflex is ONE soft, non-lexical take ("Mm." or "Mm...") that the PANEL plays itself, from a
pre-rendered bank in Zoe's own Kokoro voice, the moment the daemon commits the close of a wake recording and its rules all
pass. It says "I heard sound that stopped", true at that moment, and nothing more. It cannot reach under 0.6 s: the endpoint
closes no sooner than its 640 ms floor [code]. Whether it lands under 1 s depends on the close rule (a 640 ms close leaves
about 360 ms for the decision and the player, an 800 ms close about 200 ms) [inferred; `labs/voice-reflex-sim`].

## The switch

ONE panel switch, read once at start: `REFLEX_MODE` in the panel's `.env.voice` = `off` (default; anything else is `off`),
`shadow`, or `on`. The module's own `REFLEX_ENABLED` is ignored by the daemon (the mode forces it per call), so there is no
second switch to flip by accident.

| mode | what runs |
|---|---|
| `off` | nothing new. The line's `skip=` is the older post-hoc shadow (decide() at the turn's end, no bank, no still-talking rule) |
| `shadow` | on every eligible turn the REAL decision is made, with the real bank, on a short thread, and logged as `REFLEX_DECISION`. No sound, no player, no extra write |
| `on` | the same, and a "heard" decision plays one take through its own player |

Also needed for `on` (each missing one is a named silent skip, never a guess): `SPEAKER_ID_SHADOW=0` and
`ZOE_SPEAKER_MODEL=resnet34` (else `spk=-1`, skip `guest`), `MEDIA_WATCH_ENABLED=true` (else `media`), `BARGE_IN_ENABLED`
(the post-close mic window; else `talking`), `REFLEX_VOICE=<voice id>` (else `no_bank`/`voice_mismatch`), a bank under
`~/.zoe-voice/reflex/<voice>/<sha8>/`, and `zoe_voice_reflex.py` deployed. Optional: `REFLEX_SILENT_MEMBERS=<ids>` (comma
separated; those members are treated as guests, so silent), `REFLEX_SCORE_DEADLINE_MS` (default 250).

## Safety rules: what the code enforces and what it cannot

| Rule | State | How |
|---|---|---|
| Never before the close is committed | **enforced** [code] | `committed` must be exactly True (the endpointer's own commit, not the max-duration path); skip `not_committed` |
| Never talk over the person | **enforced, measured by the shadow** [code] | skip `talking`: the post-close mic window (the barge monitor's counters) must have covered 160 ms, be fresh, and hold NO speech chunk; speech, or nobody listening, is silence. Not proof: the monitor opens its mic after the close, so a person who resumes later is not yet visible. The ship bar below measures it (`still_talking` on turns where `reflex=heard`) |
| Reply never clipped or delayed | **enforced** [code, lab] | no wait, ever. Reply first audio while the reflex plays: the reflex is CUT (terminate; `reflex_cut=1`). Reply first audio before the player starts: skip `reply_first`. The reply's first PCM write is identical with the reflex off, shadow and on (`test_the_replys_first_write_does_not_move`) |
| Skippable late | **enforced** [code] | a decision slower than `REFLEX_SCORE_DEADLINE_MS` after the close is `late`; the player re-checks for a reply after spawning and kills itself unwritten |
| Unverified speaker / guest | **enforced** [code] | skip `guest` / `low_confidence`: the speaker model's OWN accept at the stricter FAR 0.1 % bar (0.309 / 0.351), exactly True, for a known model id; a scored no-match, a closer named guest, shadow mode or no claim is silence |
| A child speaking | **enforced only by an explicit list** [code] | the panel knows no age or role. An unenrolled child is a guest (silent). An ENROLLED child is a member and would be heard UNLESS the owner lists them in `REFLEX_SILENT_MEMBERS` |
| Music / media playing | **enforced if the watcher runs** [code] | skip `media`: True OR unknown (watcher off, stale, dead) is silence; `busy` for another daemon player |
| Never on a mute-eaten, capped, wake-only or very short turn | **enforced** [code] | `muted`, `capped`, `short` (under 480 ms of speech) |
| Night, first turn after 4 h idle, one per 30 s, never the last two takes | **enforced** [code] | `night` (22-07), `idle_greeting`, `budget`, take rotation |
| Never the wrong voice | **enforced** [code] | the bank's manifest voice must equal the household voice (`REFLEX_VOICE`, or a done frame's `voice` when the server sends one); unknown is `voice_mismatch` |
| Never during a crisis or medical turn | **NOT ENFORCEABLE at the close** | the panel has no transcript until the server's first frame, about 0.4 s after the POST, which is later than the reflex would play. The only safe-by-construction lever is the sound itself: non-lexical, soft, no promise. The shadow week MEASURES the exposure (`labs/voice-reflex-sim --markers`): would-play turns with a `DISTRESS_HANDOFF`, `MEDICAL_SAFETY` or `CHILD_SAFETY_FLOOR` marker within 45 s. A server-pushed "hold the reflex for the next turn" is not built and could only cover the SECOND turn of a crisis |
| Never before a command that will be refused | **NOT ENFORCEABLE at the close** | same reason. "Mm." makes no promise (no "sure", no "okay"), which is why the default bank has no "Mm-hm." |

**Owner decision needed before `on`:** the two NOT ENFORCEABLE rows. The measurement above gives the number; the call is yours.

## Pieces

| Piece | Where | State |
|---|---|---|
| Decision logic: gate (one named skip per rule), take rotation, bank loader, handshake block, the daemon's trim rule | `scripts/setup/zoe_voice_reflex.py` (stdlib only) | merged (#2470); model-aware speaker guard and `late` (#2571); `talking` here |
| Media watch (`MEDIA_WATCH_ENABLED`) | `zoe_voice_daemon.py` `_MediaWatch` | draft #2572, log-only until this wiring reads it |
| At-close speaker verdict, post-hoc shadow `skip=` | `zoe_voice_daemon.py` `_spk_verdict`, `_shadow_reflex_skip` | draft #2573 |
| The wiring: `REFLEX_MODE`, `_ReflexTurn` (decide, then maybe play), `_TALK.peek`, the reply hooks, the bank load | `zoe_voice_daemon.py` | this PR |
| Bank renderer (owner-run on the Orin) | `scripts/setup/render_reflex_bank.py` | this PR; run against a stub in tests, NOT yet against the live sidecar |
| Bank deploy | `scripts/setup/deploy-pi-voice.sh` with `REFLEX_BANK_DIR=<root>` | this PR |
| Projection lab and shadow-week reader | `labs/voice-reflex-sim/` | this PR |
| Server side of the handshake (the server drops its own filler after a heard reflex) | `services/zoe-data/voice_spacer.py` | NOT merged (the unmerged `reflex-stack4` branch). The panel sends no `reflex` block; an old server is unaffected |

### Noise input (decided here, pending measurement)

`noise_dbfs` used to be the RMS of the whole pre-roll, which holds the wake word, so `noisy` would have fired on almost every
wake turn. It is now the QUIETEST 80 ms chunk of the pre-roll (the pause around the wake word): steady music or TV keeps even
that chunk high, a quiet room does not. The bar stays the provisional -40 dBFS. `REFLEX_DECISION noise_db=` logs the value so
the shadow week sets the bar. Supersedes the "open decision" in [voice-reflex-noise-input.md](voice-reflex-noise-input.md).

### Logs

`REFLEX_DECISION mode=<shadow|on> kind=<heard|none> skip=<enum or -> take=<id or -> decided_ms=<n> talk_obs_ms=<n>
noise_db=<n> spk=<1|0|-1> media=<1|0|-1> win=<enum> bank=<sha8 or ->` - one per eligible turn, written when the decision is
made. In `shadow`, `kind=heard skip=-` means "would have played". `FIRST_SOUND` keeps its shape: `reflex=heard`,
`reflex_at_ms`, `reflex_dur_ms`, `reflex_cut`, `gap_ms` are filled only when a take was really written; `skip=` is the
controller's decision (or a runtime skip: `reply_first`, `player_failed`). `REFLEX_BANK result=<enum> voice= bank= takes=` once
at start. Integers and fixed enums only; no id, score, text or audio.

## The bank

`~/.zoe-voice/reflex/<voice>/<bank_sha8>/{mm_NN.wav,manifest.json}`: 24 kHz mono S16 (the format of the server's chunks), each
take trimmed with the daemon's own rule, faded 10 ms, RMS-matched to the reference reply speech minus 3 dB, peak at most
-1 dBFS; the manifest holds voice, style row, speeds, render date and per-take sha256, duration and level. The panel loader
refuses a tampered file, a wrong format, a take over 600 ms or fewer than 3 takes (fail silent, never the wrong voice).

**Words, proposed:** two neutral non-lexical texts, "Mm." and "Mm...", across three speeds and three style rows (18
candidates; keep 6 to 8 by ear). Nothing that agrees or promises ("sure", "okay", "got it", and not "Mm-hm.", which reads as
a yes, so it is only an opt-in through `--texts`). No breath: Kokoro cannot render one, and another voice breaks the one-voice
rule. Whether "Mm." phonemises as a hum is UNHEARD: the renderer's `--texts` takes a phoneme-marked form if it does not.

**Not committed.** `.gitignore` excludes `*.wav`, and the files are household-voice output of one box's sidecar build. They are
built once by the owner on the Orin, listened to, shipped with `REFLEX_BANK_DIR`, and verified on the panel by the loader's
sha256 check. The expected `bank` sha8 can be recorded in the landing note.

## Switch-on steps (landing session and owner)

Preconditions: the four PRs are merged (#2571, #2572, #2573, this one). Nothing here changes the server. A panel deploy is
the owner's step. Daytime only; none of it inside the night window.

1. **Panel deploy, dry.** From a clean worktree at the merged commit (never the live checkout):
   `DRY_RUN=1 bash scripts/setup/deploy-pi-voice.sh`. Read the plan; it must list `zoe_voice_reflex.py`.
2. **Read the panel's current state** (read-only): `ssh zoe-pi "grep -E '^(SPEAKER_ID_SHADOW|ZOE_SPEAKER_MODEL|BARGE_IN_ENABLED|MEDIA_WATCH_ENABLED|REFLEX)' /home/pi/.zoe-voice/.env.voice"`
   (names and values of these switches only). Needed: `SPEAKER_ID_SHADOW=0`, `ZOE_SPEAKER_MODEL=resnet34`, barge-in on.
3. **Render the bank on the Orin** (the sidecar is shared with the live voice: do it when no one is talking, under
   `flock /tmp/zoe-voice-harness.lock`): `python3 scripts/setup/render_reflex_bank.py --voice <voice> --render-date <YYYY-MM-DD> --dry-run`,
   then the same without `--dry-run`, with `--out ~/reflex-candidates`. Listen to all 18. Keep 6 to 8 that sound like a person
   listening and not like a word: re-render only those with `--keep mm_02,mm_05,... --out ~/reflex-bank`. Record the printed
   `bank <sha8>`.
4. **Panel deploy with the bank:** `REFLEX_BANK_DIR=~/reflex-bank bash scripts/setup/deploy-pi-voice.sh` (rsync `--checksum`,
   never `--delete`). Rollback of this step: delete `/home/pi/.zoe-voice/reflex/<voice>/<sha8>`.
5. **Shadow.** In the panel's `.env.voice` set `REFLEX_MODE=shadow`, `REFLEX_VOICE=<voice>`, `MEDIA_WATCH_ENABLED=true`
   (and `REFLEX_SILENT_MEMBERS=` for the children); `systemctl --user restart zoe-voice`. Expect, within a minute,
   `grep REFLEX_BANK` to say `result=ok ... takes=6..8`. Anything else: fix before going on.
6. **Read the shadow for at least 7 days AND 100 turns** (whichever is longer):
   `ssh zoe-pi "grep -E 'REFLEX_DECISION|FIRST_SOUND' /home/pi/.zoe-voice/voice.log" > decisions.txt`, then
   `python3 labs/voice-reflex-sim/sim.py --decisions decisions.txt` (the per-rule distribution) and, with the server's
   allowlisted marker extract, `--markers markers.txt`:
   `grep -E 'DISTRESS_HANDOFF|MEDICAL_SAFETY |CHILD_SAFETY_FLOOR' ~/.zoe-logs/zoe-data.app.log | sed -E 's/^([0-9-]+ [0-9:]+).*(DISTRESS_HANDOFF|MEDICAL_SAFETY|CHILD_SAFETY_FLOOR).*/\1 \2/' > markers.txt`.
7. **Numbers that must hold to go on** (any miss: stay in shadow, fix, repeat):
   - `FIRST_SOUND` `server_first_ms` p50 within 50 ms of the same panel before shadow, and `post_ms` p50/p95 unchanged
     (the controller is off the POST path; this proves it);
   - `REFLEX_DECISION decided_ms` p95 at or under 250; `skip=late` under 10% of turns that passed every earlier rule;
   - `skip=talking` not dominant: if most turns are `talking` the mic window never reports (barge-in off or mic opens too late),
     and the reflex cannot be on;
   - would-play share is reported per rule; `noise_db` shows where the floor really sits, set `REFLEX_NOISE_FLOOR_DBFS` from it
     only after an owner look;
   - would-play turns with a safety marker within 45 s: **0 is the bar for the owner's rule; any count is the owner's decision**
     (see the NOT ENFORCEABLE rows);
   - `spk=1` on the household's own wake turns most of the time; if `spk=-1` dominates, speaker shadow is still on.
8. **Rehearsal, owner present, daytime, `REFLEX_MODE=on` for the session only.** Ten ordinary wake turns (a real play on most,
   each inside 1 s of speech end by the owner's ear); music through the panel then five turns (silence all five, `media=1`); a
   non-enrolled adult and a TV clip five turns each (silence, `spk` not 1); "stop"/"yes" style short commands (`short`); stop
   the media watcher (`media=-1`, silence); a very fast canned reply (`reflex_cut=1` or `reply_first`, the reply never moved).
   Pass: zero reflexes on turns that should be silent; `server_first_ms` within 50 ms.
9. **On.** `REFLEX_MODE=on`, restart. Watch for a week: false starts (`reflex=heard` with `still_talking=1`) at most 3 per 100
   reflex turns; the `reflex_cut=1` rate (each is an audible truncated "mm"; many means the gate admits fast replies);
   `reflex=heard` with `server_first_ms=-1` (a play on a turn that ended in silence); and the owner's blind ear test
   (20 scripted wake turns, silence vs reflex, blind order: pass at least 60% prefer the reflex and zero "talked over me").
10. **Rollback, cheapest first:** `REFLEX_MODE=off` (or delete the line) in `.env.voice` and `systemctl --user restart zoe-voice`;
    then delete the bank directory; then restore the daemon `.bak-<ts>` the deploy printed. Nothing server-side changed.

## What is not built or not proven

- Not heard: no bank has been rendered or listened to; whether "Mm." sounds like a hum, and the seam to the reply, are
  unproven. The renderer has only run against a stub voice.
- The post-close mic window opens after the close; its real open time, the decision latency on the panel Pi with the daemon
  running, the player start latency and the media watcher's cost are all unmeasured. `labs/voice-reflex-sim` shows how the
  first-sound projection moves with them.
- No done-frame `voice`, so a server-side voice change is not noticed by the panel: re-render the bank and update
  `REFLEX_VOICE` together.
- The server handshake (dropping a redundant server filler after a heard reflex) is unmerged; stacking "Mm." with a generic
  server filler is possible until it lands.
- The rule for crisis and medical turns and for commands that will be refused cannot be enforced at the close (above).
- Windows (follow-up / conversation turns) never get a reflex: wake turns only.
