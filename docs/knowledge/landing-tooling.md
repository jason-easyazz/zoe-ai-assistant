---
type: Runbook
title: Operator landing tooling (scripts/landing)
description: The scripts that land PRs on the box - the voice-gated lander, the voice queue loop, the docs merge chain, the overnight auto-lander, the event watcher, the paginated thread lister and the night pause rules - what each one refuses, the exit codes, the configurable paths, the tests that pin the rules, and the owner-run cutover from the old out-of-repo copies.
tags: [landing, merge, voice-gate, night-pause, operator, runbook]
timestamp: 2026-11-10T09:00:00+08:00
---

# Operator landing tooling

Moved into the repo on 2026-10-29 from `~/.zoe/agent-tools` (where it had no tests and no docs). Everything is in
`scripts/landing/`; run state stays outside the repo in `$ZOE_LANDING_HOME` (default `~/.zoe/agent-tools`).
Public repo: nothing here names a household member; the only identifiers are the GitHub owner/repo slug.

## The scripts

| script | job |
|---|---|
| `land_voice_pr.sh <pr> <wt> <branch> <title>` | voice-gated landing: update-branch, head-bound replay probe (Kokoro paused, brain lock + test lane + harness lock), gate rerun, arm auto-merge bound to the probed head, wait MERGED |
| `land_queue.sh <file>` | runs the lander over `pr worktree branch title` lines, one at a time; records every real exit code in `last-results.txt` |
| `after_voice_more.sh` | the rolling voice loop: batches `voice-pending.txt` into `land_queue.sh`, re-queues what is still open |
| `docs_merge_chain.sh <pr>...` | update-branch, arm when clean, wait MERGED, for non-voice PRs |
| `autoland.sh` | every ~10 min: open non-draft non-held PRs; voice-path diffs go to `voice-pending.txt`, the rest to the docs chain |
| `watchd.sh` | event watcher, appends NEW attention lines once to `logs/watchd-events.log` |
| `threads.sh [--count] <pr>...` | unresolved review threads, paging through ALL of them |
| `night_busy.sh voice\|deploy\|test\|deadline` | exit 0 = pause now, 1 = may run; `deadline` prints the epoch second a lander must be gone by |
| `landing_lib.sh` | sourced: paths, hold lists, thread count, probe-retry decision, head watcher, the voice hold, the probe-vs-chat locks |
| `quiet_for_probe.py turn\|wait\|status` | the CHAT DRIVERS' side of the probe-vs-chat locks (`from quiet_for_probe import chat_turn`); every QA/chat driver wraps each turn in it |

`resolve_inventory_conflict.sh` (the generated-inventory conflict resolver) is NOT moved yet; it is still looked up in
`$ZOE_LANDING_HOME` (override `ZOE_LANDING_RESOLVE_SCRIPT`).

## Rules the tests pin (`tests/unit/test_landing_*.py`, all `ci_safe`)

Fake `gh` and `systemctl` go first on PATH (`tests/unit/landing_fakes.py`); `ZOE_LANDING_NOW_EPOCH` injects the clock; TZ is UTC.

- **Never land a draft.** The lander exits 6 on a draft or an unreadable draft flag, no longer runs `gh pr ready` at all, and re-checks the draft flag right before arming. The docs chain skips drafts too. (2026-10-28: #2200 merged as a draft.)
- **Threads are counted over every page.** `threads.sh` pages 100 at a time until `hasNextPage` is false. A GraphQL response carrying an `errors` array counts as unreadable too. An unreadable API is exit 6 in the lander ("unknown, not safe") and "holding" in the chain - never zero. (2026-10-28: first:50 hid unresolved threads and left a PR BLOCKED with everything green.)
- **Hold lists bind the voice lander.** A PR number (first token of a line, `#` comments allowed) in `coord/hold.txt` or `chain-hold.txt` makes the lander exit 10 before it touches anything (no disarm, no update-branch); `land_queue.sh` does not even spawn a lander for it; the docs chain and autoland skip it.
- **Night pause.** voice: paused 07:00-22:00 unless `voice-day-ok` holds TODAY's date (exact `YYYY-MM-DD`) or the exact token `always` (standing mode, whitespace-trimmed; the household-quiet gate in the lander and every night pause still apply); paused from 01:55 until tonight's window finished with exit 0 (a refused window does not count) or 03:30; paused 04:00-04:55 (nightly regression owns the artifact slot) and while `zoe-night-window`/`zoe-voice-regression` are active. deploy: paused 02:45 until the window finished or 03:30. test: paused while the backup or the window runs.
- **Deadline.** `night_busy.sh deadline` = the next 02:40 (tomorrow's when already past it). The lander never starts a probe unless 1500 s remain.
- **A push mid-probe is caught after, never by killing.** There is no mid-probe watcher (removed by design: the probe runs under nested `timeout`/`flock`/`nice` process groups that a watcher cannot reliably terminate). The head is re-read after winning the lane and before Kokoro is stopped (exit 9, Kokoro untouched); the probe then runs to completion, bounded by `timeout`; arming requires the artifact revision to equal the PR head and the head to still be the probed one, so a push mid-probe means exit 5 (re-queue), never an arm.
- **Never armed behind the lander.** After arming, the wait polls state AND head (`landing_wait_merged`): a head change disables auto-merge at once (exit 5); no merge within the wait or the deadline disables it too (the 30 min base wait is EXTENDED, hard cap `ZOE_LANDING_WAIT_CAP_TRIES` = 450 steps = 75 min, only while the head is unchanged and a required check is still pending - a slow `validate` on the box's own runner cost #2253/#2270/#2269 a second probe each on 2026-11-03/04; a FAILED/cancelled required check, mergeState DIRTY/BEHIND, or the night deadline still disarm at once); an EXIT trap disarms any unmerged PR on every other exit path. `--match-head-commit` only binds the request, a later writer push would otherwise keep the arm.
- **The CI wait reads reality (2026-11-10).** `landing_required_state` reads the required checks of the CURRENT head: a context in `ZOE_LANDING_REQUIRED_CHECKS` (default `validate secret-scan`) that has not REPORTED yet is pending, and an empty/unreadable answer is unknown - both keep the extension alive (still only on a head positively read as unchanged, still under the 75-min cap). Measured to 2026-11-09: 26 of 55 disarms ended BLOCKED at ~30-37 min, i.e. the base wait expired without the extension firing. Before any disarm the state is read once more (7 of the 55 had in fact merged), and the reason is logged as `wait ended: ...`.
- **The voice hold: nothing overtakes a probed voice PR (2026-11-10).** 12 of those 55 disarms were BEHIND - another PR merged first under `strict` and the probe was wasted. `land_voice_pr.sh` takes `coord/voice-hold` (`<pr> <pid> <pid-starttime> <epoch>`) just before update-branch, under an EXIT trap that releases it on every way out. While it is active `docs_merge_chain.sh` neither updates nor arms any other PR and drops its own PR's auto-merge (re-arming once the hold clears), and `autoland.sh` starts no chain pass. Taking the hold - and again right before the voice PR is armed - drops every OTHER armed PR's auto-merge (merge-and-deploy.md "Landing a voice-path PR" step 1, automated) and records `<pr> <head>` in `coord/voice-hold-disarmed.txt`; the release re-arms each one bound to that same head (`--match-head-commit`), and leaves a PR whose head moved alone (logged). Bounded, never a freeze: the hold is STALE once its owner is gone (pid + kernel start time, so a reused pid does not count) or older than `ZOE_LANDING_VOICE_HOLD_MAX_S` (default 3 h); whoever reads a stale hold clears it, logs `VOICE HOLD STALE` (surfaced by `watchd.sh`) and re-arms its list. No cycle: the lander's own wait for a running docs chain happens BEFORE it takes the hold, and nothing the lander waits on waits on the hold. `ZOE_LANDING_VOICE_HOLD_SWEEP=0` keeps the hold without touching other PRs.
- **A passed-but-overtaken PR goes to the FRONT (2026-11-10).** When the probe PASSED but the PR did not merge - BEHIND/DIRTY after the probe, or the post-arm wait ended unmerged - the lander exits **12** and `after_voice_more.sh` puts it at the front of `voice-pending.txt` (ahead of anything autoland queued meanwhile; the batch dedupes on PR number, first line wins). It is probed again: the deploy gate binds evidence to the exact head/tree, and that binding is not weakened.
- **Chat drivers yield to the probe (2026-11-10).** False PROBE NOT PASS came from QA chat agents driving the live brain mid-probe (prewarm timeout, brain_ms 2.9x). After the household-quiet check and before Kokoro is stopped, `landing_probe_window` takes `/tmp/zoe-probe-gate.lock` EXCLUSIVE (no new turn starts) and then `/tmp/zoe-chat-turn.lock` EXCLUSIVE (waits up to `ZOE_PROBE_DRAIN_WAIT_S`, default 300 s, for in-flight turns), holds both until the probe ends, and closes them for the probe's children. A driver (`quiet_for_probe.py`) takes the gate SHARED only for an instant and the turn lock SHARED for the turn, so drivers can never starve the probe. Not drained in time = exit 9, re-queued, Kokoro untouched.
- **Lane first, then silence.** `landing_probe_window` takes the test-lane lock BEFORE Kokoro is stopped (flock is not FIFO; the old order left Zoe mute for an hour in the daytime while agents' test runs won the lane). A lane not won by the deadline = exit 9 with Kokoro never touched; Kokoro is always restarted and the lane released after.
- **Silence only a quiet household (2026-11-02).** After the lane and `landing_pre_stop`, `landing_household_quiet` reads zoe-data `/health` `last_voice_turn_age_s`: a voice turn within `ZOE_LANDING_QUIET_MIN` minutes (default 20) = exit 9, re-queued, Kokoro untouched (owner-allowed daytime landings had muted her for 214 min on 10-29 while people were talking to her). Fails CLOSED: an unreachable `/health` or a missing / malformed field is "not quiet". Tests point `ZOE_LANDING_HEALTH_URL` at a file (the rig defaults to quiet), never the live box.
- **Chain and autoland guards.** The docs chain re-reads the voice scope every pass (only `CLEAR` proceeds; VOICE or unreadable is left to the voice lander) and honours the measured deploy pause; autoland does not refill a head the voice loop parked (`probe-failed-heads.txt`); the lander re-checks the night gate and time budget after winning the lane and refuses a probe artifact older than its own attempt.
- **"Is a lander running" is read from /proc argv (`landing_running <script.sh>...` in `landing_lib.sh`), never `pgrep -f`.** A bare `pgrep -f land_voice_pr.sh` matches every agent waiter shell that merely mentions the name (and itself), which froze autoland on 2026-11-05. A match is a shell/script whose first non-option argument is the script; `bash -c <text>` never matches. Pinned by `tests/unit/test_landing_running.py`.
- **Bounded retry.** A probe NOT PASS (lander exit 4) used to be re-queued forever or dropped, depending on the loop. The lander now snapshots the probe artifact PER PR (`probe-artifacts/<pr>.json`, because the shared artifact slot belongs to whichever probe ran last), and `after_voice_more.sh` re-queues the PR ONCE per (pr, head) only when that snapshot is for the PR's current head and shows POSITIVE evidence of a pure speed fail (status fail, `reason` made only of `SPEED ...` clauses, no said-vs-did regressions, VAD pass, cold first turn pass/absent, `speed_comparison` `compared`; a cold-cache or cleanup failure parks), writing the mark to `voice-probe-retried.txt`; otherwise the PR is parked in `probe-failed.txt` and a PARKED line is logged. A new push earns one new retry. Every other exit code is re-queued as before; conflicting PRs still go to `needs-hand-merge.txt`.

- **The lander never writes the shared `FETCH_HEAD`.** Every `git fetch` in `land_voice_pr.sh` uses an explicit refspec into its own namespace (`+refs/heads/<branch>:refs/zoe-landing/<branch>`, `refs/zoe-landing/main`) with `--no-write-fetch-head`. Incident 2026-10-30: its plain `git fetch origin <branch>` overwrote `FETCH_HEAD` between the CD runner's fetch and rev-parse, and the runner deployed that unmerged branch commit (see merge-and-deploy.md; the runner no longer reads `FETCH_HEAD` either). Pinned, with an executed negative control, by `tests/unit/test_deploy_never_reads_fetch_head.py`.

Exit codes of the lander: 0 landed/nothing to do, 4 probe not pass, 5 re-queue, 6 not landable yet (draft/threads), 7 validate failed on this head, 8 hand merge, 9 night pause / no time, 10 held, 12 probe passed but not merged (front re-queue).

## Configuration

`ZOE_LANDING_HOME` (state), `ZOE_LANDING_REPO` (live checkout, default `/home/zoe/assistant`), `ZOE_LANDING_GH_REPO`, `ZOE_LANDING_HOLD_FILES`, `ZOE_VOICE_ARTIFACT`, `ZOE_LANDING_ARTIFACT_DIR`, `ZOE_LANDING_WORKTREES`, `ZOE_LANDING_PANEL_HOST` (empty = skip the panel-quiet wait) / `ZOE_LANDING_PANEL_LOG`, `ZOE_LANDING_PYTHON`, `ZOE_LANDING_ENV_FILE`, `ZOE_WATCHD_SKIP` / `coord/watchd-skip.txt`, `ZOE_WATCHD_VOICE_LOG`, `ZOE_LANDING_REQUIRED_CHECKS`, `ZOE_LANDING_VOICE_HOLD` / `ZOE_LANDING_VOICE_HOLD_DISARMED` / `ZOE_LANDING_VOICE_HOLD_MAX_S` / `ZOE_LANDING_VOICE_HOLD_SWEEP`, `ZOE_PROBE_GATE_LOCK` / `ZOE_CHAT_TURN_LOCK` / `ZOE_PROBE_GATE_WAIT_S` / `ZOE_PROBE_DRAIN_WAIT_S`. Test-only: `ZOE_LANDING_NOW_EPOCH`, `ZOE_LANDING_ONCE`, `ZOE_WATCHD_ONCE`, `ZOE_LANDING_NO_BUSY_WAIT`, `ZOE_LANDING_CHAIN_HOLD_S`, `ZOE_LANDING_CHAIN_STEP_S`, `ZOE_LANDING_CHAIN_PASSES`.

## Restarting the loops (owner-run; nothing in the repo does this)

The live loops have run from the repo since 2026-11-06 (the old `~/.zoe/agent-tools` copies are not removed yet - open-problems.md): `after_voice_more.sh`, `autoland.sh` and `watchd.sh` run from
`/home/zoe/assistant/scripts/landing/`. A running bash loop keeps the code it started with, while every script it CALLS
(`land_queue.sh`, `land_voice_pr.sh`, `docs_merge_chain.sh`) and `landing_lib.sh` as sourced by those children are re-read from
disk on each call - so after a merge + deploy the children are new and the loop bodies are old until restarted. Restart after any
change to a loop script:

1. After the deploy (live checkout on the merged `main`), pick a quiet moment: no lander probing (`pgrep -af land_voice_pr.sh` empty, `/tmp/zoe-brain-window.lock` free), outside 01:50-04:55.
2. Stop the loops, letting a running `land_queue.sh` / `docs_merge_chain.sh` finish first (`pgrep -af 'land_queue.sh|docs_merge_chain.sh'` empty):
   `pkill -f 'scripts/landing/after_voice_more.sh'; pkill -f 'scripts/landing/autoland.sh'; pkill -f 'scripts/landing/watchd.sh'`
3. Start them again (nohup, logs in `~/.zoe/agent-tools/logs/`):
   `nohup bash /home/zoe/assistant/scripts/landing/after_voice_more.sh >> ~/.zoe/agent-tools/logs/after_voice_more.log 2>&1 &`
   `STOP_AT=$(date -d '+7 days' +%s) nohup bash /home/zoe/assistant/scripts/landing/autoland.sh >> ~/.zoe/agent-tools/logs/autoland.log 2>&1 &`
   `nohup bash /home/zoe/assistant/scripts/landing/watchd.sh >> ~/.zoe/agent-tools/logs/watchd.log 2>&1 &`
4. Check the first cycle: `logs/watchd-events.log` stays quiet, `last-results.txt` appears after the first queue run.

State files are shared across versions (newer: `coord/voice-hold`, `coord/voice-hold-disarmed.txt`, `queue-voice-front.txt`).
