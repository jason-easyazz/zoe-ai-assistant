---
type: Runbook
title: Panel barge-in lab - ten scripted interruptions for the duck-decide-resume policy
description: Samantha lane E, experiment E2 (owner's panel hour). Ten scripted barges - false ones (a cough, "mm-hm", a chair scrape, TV music, a TV line, an aside across the room) and real ones ("stop", "Zoe, stop talking", a full sentence) - what to do, what the panel should do, how the lab is scored (at least 8 of 10 correct and no stuck mute), and what the offline simulation of the policy already predicts, including one gap it expects.
tags: [voice, barge-in, duck, panel, lab, samantha, lane-e, runbook]
timestamp: 2026-11-05T08:00:00+08:00
---

# Panel barge-in lab - ten scripted interruptions

Lane E, experiment E2 of the [Samantha Initiative](../research/samantha-initiative-2026-11-03.md). The policy is
built and flag-dark (`BARGE_DUCK_ENABLED`, [design](../research/barge-in-duck-decide-resume-2026-10-04.md),
mechanics in [voice-pipeline.md](voice-pipeline.md) "Phase 1 - duck, decide, resume") and has never run in a room. This
is the hour that runs it.

- **Pass:** at least 8 of the 10 barges behave correctly, **and** no stuck mute at any point.
- **Kill:** any stuck state (below).
- **Time:** about an hour (10 barges at 3 min each, plus the control and two repeats).

## Before the hour (offline, already done by the agent)

`scripts/perf/barge_policy_sim.py` runs the REAL `_BargeDetector` -> `_BargeEpisode` -> `_BargeDecider` ->
`_SinkInputDucker` on scripted probabilities with a PulseAudio volume model. Its numbers are in
[Predicted by the offline simulation](#predicted-by-the-offline-simulation). Re-run it any time:

```
python3 scripts/perf/barge_policy_sim.py --lab --controls --fuzz 3000
```

## Setup (owner, panel on)

1. Deploy the daemon from a worktree: `scripts/setup/deploy-pi-voice.sh` (see voice-pipeline.md, "Pi lab").
2. On `zoe-pi`, add `BARGE_DUCK_ENABLED=1` to `/home/pi/.zoe-voice/.env.voice` (nothing else; the grace, the 900 ms
   commit and the 400 ms resume stay at their defaults), then `systemctl --user restart zoe-voice`.
3. Open a second terminal on `zoe-pi` and keep it running: `journalctl --user -u zoe-voice -f | grep -E "BARGE_DECIDE|Barge-in"`.
4. Stand where you normally stand to talk to the panel. For the TV and aside barges, use the real TV / the real person
   at their usual place.
5. **Rollback at any point:** remove the flag line, `systemctl --user restart zoe-voice`. That is today's hard stop.

## The routine, for each barge

1. Ask for a long answer: "Zoe, tell me about the history of the moon landings." (about 20 s of speech).
2. Let her speak for **3 to 5 seconds**, then do the barge below. Not in the first second (the 800 ms onset grace
   deliberately ignores it) and not in the last few seconds (a reply that ends mid-decision is a different path).
3. Watch and listen: did she dip and carry on, or stop? Write the result on the sheet.
4. Let the reply finish (or say nothing and wait 20 s) before the next barge. Do not stack barges.

## The ten barges

"Should" is what the owner wants from the panel. Correct for a *false* barge means **a short dip, then the same reply
carries on from where it was** (no restart, no skipped sentence, no stop). Correct for a *real* barge means **she stops
and listens**.

| # | Do this | Should | Why this one |
|---|---|---|---|
| 1 | Cough once, normally. | carry on | a short loud burst |
| 2 | Say "mm-hm" at normal volume. | carry on | the classic backchannel |
| 3 | Say "uh-huh". | carry on | two-beat backchannel |
| 4 | Scrape a chair or knock the table once. | carry on | non-speech thump |
| 5 | Play 3 seconds of TV music or sound effects with no speech (TV at normal volume). | carry on | non-speech background |
| 6 | Play a TV line of two words, for example "oh really". | carry on | short speech from the wrong source |
| 7 | Have someone across the room say "yeah, okay" (or say it yourself, turned away from the panel, quietly). | carry on | an aside not meant for her |
| 8 | Say "stop", one word, firmly. | **stop** | the shortest real interruption |
| 9 | Say "Zoe, stop talking." | **stop** | a short real interruption |
| 10 | Say a full sentence over her, for example "Actually, can you play something else instead." | **stop** | a long real interruption |

Also run **control 0** once, first: ask for the long reply and say nothing at all, with the room quiet. The reply must
play to the end without a dip and without a `Barge-in detected` line. This is the 2026-09-28 self-interruption class; it is not
one of the ten but any dip here fails the lab.

## Scoring

Per barge: **correct** if the panel did what "Should" says (table above). A reply that stopped when it should have
carried on counts wrong; so does a reply that ducked and carried on when it should have stopped. A dip that you could
hear but that came back is *correct* for 1 to 7.

**Stuck mute (any one fails the lab):**
- a reply that stays quiet (ducked) after the barge ended, for more than about 1 second;
- the next, fresh reply after any barge starts quiet;
- during a reply that has carried on, `pactl list sink-inputs` shows the aplay stream below 100 % while the sink
  volume is unchanged;
- a reply that stops without a `BARGE_DECIDE outcome=commit` line, or a commit that leaves her still talking;
- a barge for which no `BARGE_DECIDE` line follows within about 2.5 s of the `Barge-in detected` line (no decision).

Aggregate cross-check from the log (counts only, no transcripts):

```
python3 scripts/setup/mac_panel/barge_lab_summary.py <(journalctl --user -u zoe-voice --since "<lab start>")
```

With ten barges done once each, expect `commit` to be 2 or 3 and `resume` plus "no detection" to make up the rest. If the
log's commits disagree with the sheet, trust neither until you find why. Also record, per commit, `ms=` (target
900 to 1100 ms from onset) and `heard_ms`.

**Result to report back:** correct count out of 10 (and which numbers were wrong), stuck-mute yes/no with the evidence,
the `summary` output, and a one-line note on how the dip *sounded* (too deep, too long, fine).

## Predicted by the offline simulation

The simulation scores each barge on a synthetic Silero shape (the shapes are assumptions: how Silero scores a real cough
is exactly what the lab measures). With the policy defaults it predicts **9 of 10, with one expected gap, #8**.

| # | Predicted | Note |
|---|---|---|
| 1 to 3, 6, 7 | ducks, then resumes | short bursts under the 900 ms commit |
| 4, 5 | does not fire at all, or ducks briefly | below the sustained-speech threshold |
| 8 | **ducks, then RESUMES (wrong)** | a lone "stop" is about 500 ms of speech; the commit needs 900 ms |
| 9, 10 | ducks, commits at about 960 ms | sustained speech |

**#8 is the expected finding, not a surprise.** Phase 1 decides on speech duration alone; the word is not read. A one-word
"stop" resumes. The fix is Phase 2 (a short STT look at short bursts, the lexicon "stop" / "wait" / "Zoe") or a lower
`BARGE_COMMIT_SPEECH_MS`, which the simulation shows commits the same shape at 400 ms but widens the false-commit
window for #2, #3, #6 and #7. So: run #8 three times (say it differently each time) and report how many stopped.
If #8 resumes every time, that is a *design gap with a known fix*, not a stuck state.

Real-Silero evidence and fuzz results are in the [E2 follow-up](#e2-offline-results). Limits: the shapes are synthetic;
the TV and aside cases depend on the room; the simulation proves the state machine cannot stick, not that Silero scores
the owner's cough the way the shape does.

## E2 offline results

Run 2026-11-05, `python3 scripts/perf/barge_policy_sim.py --lab --controls --fuzz 5000` (no model, no audio).

- **Scripted barges:** 9 of 10 predicted correct; the one gap is #8 (a lone "stop" resumes).
- **Stuck states:** 5000 random profiles (speech bursts, quiet, the VAD-failure sentinel, the reply ending mid-decision, a `pactl` restore that fails after the duck lands, no sink-input) produced **0 unexpected violations** and **50 occurrences of the one known edge** below. Outcomes: 2144 commit, 834 resume, 148 ceiling, 157 ended, 1895 never fired, 168 hard-stop fallback (no sink-input).
- **Negative controls:** each of the five checks (open episode, stuck mute, kill without commit, sentinel commit, multiple outcomes per episode) flags a deliberately broken policy and is silent on the real one.
- **Known edge, a REAL stuck mute (low likelihood):** if `pactl` succeeds on the duck but fails on the restore while the reply keeps playing, that reply stays at -15 dB until it ends; the daemon's heal only fixes the NEXT player. The simulator reports it separately (never folded into "clean") and the lab counts it as a stuck mute if it ever happens. A cheap daemon fix would be a retry of the restore; it is not made here (voice-path change).
- **Real Silero over the quarantine clips and the command clips** (`--corpus ~/.zoe-voice-samples --lock /tmp/zoe-voice-harness.lock`): NOT run yet - voice landings held the box for hours (the guard refuses while one runs). Run it in a quiet window and append the aggregates here.

## After the hour

- All ten correct, or only #8 wrong: leave the flag on for a week and count `BARGE_DECIDE` lines (voice-pipeline.md); plan
  Phase 2 for the lone-word case.
- Any stuck mute: remove the flag at once, keep the log, and send it. That is the kill rule.
- Write the outcome to `docs/PLANS.md` and the initiative record's experiment table (row 4).
