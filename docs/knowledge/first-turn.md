---
type: spec
title: The first turn - what I say to Zoe after a while works as well as the second
description: Acceptance spec + root-cause record for the panel's first-turn failures. The wake chime made the speakerphone mute its mic under the first words; the first-turn numbers (garbled transcripts, "I'm Zoe." answers, cold replies) and what was ruled out.
tags: [voice, panel, wake, chime, stt, first-turn, spec]
timestamp: 2026-11-09T00:00:00Z
---

# The first turn

## Acceptance — true when done

Avery is the owner. Each line is checkable on the panel or from `voice.log`.

1. The first thing I say to Zoe after a while works as well as the second: I say "Hey Zoe, turn on the lights" in one breath
   and she hears all of it, not "...the lights".
2. Zoe never answers a first request with "I'm Zoe." or "What's on your mind today?" because the start of it went missing.
3. I do not have to say it twice. A first request that needed a repeat is a miss.
4. Waiting for a beep is not part of the job. I can start talking the moment I say her name.
5. It is quick: the first reply is not noticeably slower than the later ones. **(not yet:)** see "Slower first reply".
6. When something does eat the start of a clip, the panel log says so (`muted=<ms>` on the `Recorded command` line) so it is
   found in a day, not in a week.

## What was wrong (2026-11-09, 10 days of panel logs, the owner only)

Evidence is private; this is the shape. Conversations are split at each wake; the owner's first turns are compared with his
later turns.

| measure | first turn | later turns |
|---|---|---|
| speaker verified as the owner | 39 of 42 | 109 of 127 |
| brain first-token time, median | 2.9 s | 1.8 s |
| first token of 2.5 s or more | 16 of 28 (57%) | 14 of 91 (15%) |
| prefix-cache miss logged (`PANEL_KV_MISS`) | 15 of 28 | 25 of 91 |
| transcript was a fragment / not what he meant | about 13 of 20 sessions | a handful of about 70 |
| Zoe's reply was an identity line ("I'm Zoe.", "What's on your mind today?") | 7 of 20 | none |
| he repeated the request on turn 2 | most of the failed ones | - |

Most failed first transcripts were the start of a command with the verb gone ("Turn on the lights" -> "the lot", "the
wire", "Shown the lot"). The reply to a clipped first turn has no context to lean on, so it introduced herself.

**Root cause.** On wake the daemon played a two-tone chime through the Jabra speakerphone. The Jabra mutes its own mic
while it plays anything (exact digital zeros in the saved clips, 0.25-0.8 s). The chime lands about 0.5 s after "Zoe",
which is when people start the command, and people also start the instant they hear it. Everything said under the mute is
gone before STT. All five saved first-turn clips (the corpus keeps two days) have the chime tone followed by a zero run,
and the speech after the mute begins mid-word; 1 of 24 follow-up clips has a zero run inside it (follow-ups have no chime).
Only the first turn of a conversation is a wake turn, so only it paid.

## The fix

- `WAKE_CHIME_ON_WAKE` (Pi `.env.voice`, default **off**): the wake chime no longer plays. The screen wake and the orb are the
  cue. `WAKE_BEEP_ENABLED` still governs the other beeps (enrolment cue, retry cue). Rollback: `WAKE_CHIME_ON_WAKE=true` and
  restart `zoe-voice`, no redeploy.
- `Recorded command` now ends with `muted=<ms>`: milliseconds of exact digital silence after the pre-roll. A clean first turn
  reads `muted=0ms`. Anything above ~100 ms means something played under the mic again.
- The post-wake gate (`_PostWakeGate`) needed no change: with no chime `_wake_chime_done` stays set.

Daemon-only. Needs a Pi deploy (`scripts/setup/deploy-pi-voice.sh`, then `systemctl --user restart zoe-voice`).
Verify after: say "Hey Zoe, turn on the lights" in one breath five times with a long gap between; each `Recorded command`
line shows `muted=0ms` and the transcript keeps the verb.

## Ruled out, with numbers

- **Speaker ID needs more audio than a first command** - no. First clips are longer (median 4.1 s vs 2.3 s) and verify better
  (39/42 vs 109/127); the three unverified first turns were two empty clips and a guest.
- **Wake prewarm times out** - no. Two `failed:timeout` of 128 warms in the window, both at night, none on a first turn.
- **Recall injection skipped on turn 1** - not first-turn specific: `RECALL_EVERY_TURN` runs in shadow mode on every turn
  (`injected=0`), and `skipped=warming` was one turn straight after a restart.

## Slower first reply (not fixed here)

The prewarm loads the shared prefix (~2.9k tokens) but the member's per-turn blocks (today block, manner, user model, ~1.1k
tokens) are only known once the speaker is. `PANEL_KV_MISS kind=first` follows. A turn straight after a deploy restart is also
cold (`skipped=warming`). Both belong to the prefix-cache work, not to this change.

## Not built

- A clipped first transcript still gets answered as if it were whole. A "say that again?" for fragments needs an STT
  confidence signal; `muted=` is the first honest input to one.
- The Moonshine confusion of "lights" is independent of position (it shows in follow-ups too) and is the STT rock.
