---
type: Runbook
title: Operator landing tooling (scripts/landing)
description: The scripts that land PRs on the box - the voice-gated lander, the voice queue loop, the docs merge chain, the overnight auto-lander, the event watcher, the paginated thread lister and the night pause rules - what each one refuses, the exit codes, the configurable paths, the tests that pin the rules, and the owner-run cutover from the old out-of-repo copies.
tags: [landing, merge, voice-gate, night-pause, operator, runbook]
timestamp: 2026-10-29T08:00:00+08:00
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
| `landing_lib.sh` | sourced: paths, hold lists, thread count, probe-retry decision, head watcher |

`resolve_inventory_conflict.sh` (the generated-inventory conflict resolver) is NOT moved yet; it is still looked up in
`$ZOE_LANDING_HOME` (override `ZOE_LANDING_RESOLVE_SCRIPT`).

## Rules the tests pin (`tests/unit/test_landing_*.py`, all `ci_safe`)

Fake `gh` and `systemctl` go first on PATH (`tests/unit/landing_fakes.py`); `ZOE_LANDING_NOW_EPOCH` injects the clock; TZ is UTC.

- **Never land a draft.** The lander exits 6 on a draft or an unreadable draft flag, no longer runs `gh pr ready` at all, and re-checks the draft flag right before arming. The docs chain skips drafts too. (2026-10-28: #2200 merged as a draft.)
- **Threads are counted over every page.** `threads.sh` pages 100 at a time until `hasNextPage` is false. A GraphQL response carrying an `errors` array counts as unreadable too. An unreadable API is exit 6 in the lander ("unknown, not safe") and "holding" in the chain - never zero. (2026-10-28: first:50 hid unresolved threads and left a PR BLOCKED with everything green.)
- **Hold lists bind the voice lander.** A PR number (first token of a line, `#` comments allowed) in `coord/hold.txt` or `chain-hold.txt` makes the lander exit 10 before it touches anything (no disarm, no update-branch); `land_queue.sh` does not even spawn a lander for it; the docs chain and autoland skip it.
- **Night pause.** voice: paused 07:00-22:00 unless `voice-day-ok` holds TODAY's date (exact `YYYY-MM-DD`); paused from 01:55 until tonight's window finished with exit 0 (a refused window does not count) or 03:30; paused 04:00-04:55 (nightly regression owns the artifact slot) and while `zoe-night-window`/`zoe-voice-regression` are active. deploy: paused 02:45 until the window finished or 03:30. test: paused while the backup or the window runs.
- **Deadline.** `night_busy.sh deadline` = the next 02:40 (tomorrow's when already past it). The lander never starts a probe unless 1500 s remain.
- **A push mid-probe is caught after, never by killing.** There is no mid-probe watcher (removed by design: the probe runs under nested `timeout`/`flock`/`nice` process groups that a watcher cannot reliably terminate). The head is re-read after winning the lane and before Kokoro is stopped (exit 9, Kokoro untouched); the probe then runs to completion, bounded by `timeout`; arming requires the artifact revision to equal the PR head and the head to still be the probed one, so a push mid-probe means exit 5 (re-queue), never an arm.
- **Never armed behind the lander.** After arming, the wait polls state AND head (`landing_wait_merged`): a head change disables auto-merge at once (exit 5); no merge within the wait or the deadline disables it too; an EXIT trap disarms any unmerged PR on every other exit path. `--match-head-commit` only binds the request, a later writer push would otherwise keep the arm.
- **Lane first, then silence.** `landing_probe_window` takes the test-lane lock BEFORE Kokoro is stopped (flock is not FIFO; the old order left Zoe mute for an hour in the daytime while agents' test runs won the lane). A lane not won by the deadline = exit 9 with Kokoro never touched; Kokoro is always restarted and the lane released after.
- **Chain and autoland guards.** The docs chain re-reads the voice scope every pass (only `CLEAR` proceeds; VOICE or unreadable is left to the voice lander) and honours the measured deploy pause; autoland does not refill a head the voice loop parked (`probe-failed-heads.txt`); the lander re-checks the night gate and time budget after winning the lane and refuses a probe artifact older than its own attempt.
- **Bounded retry.** A probe NOT PASS (lander exit 4) used to be re-queued forever or dropped, depending on the loop. The lander now snapshots the probe artifact PER PR (`probe-artifacts/<pr>.json`, because the shared artifact slot belongs to whichever probe ran last), and `after_voice_more.sh` re-queues the PR ONCE per (pr, head) only when that snapshot is for the PR's current head and shows POSITIVE evidence of a pure speed fail (status fail, `reason` made only of `SPEED ...` clauses, no said-vs-did regressions, VAD pass, cold first turn pass/absent, `speed_comparison` `compared`; a cold-cache or cleanup failure parks), writing the mark to `voice-probe-retried.txt`; otherwise the PR is parked in `probe-failed.txt` and a PARKED line is logged. A new push earns one new retry. Every other exit code is re-queued as before; conflicting PRs still go to `needs-hand-merge.txt`.

Exit codes of the lander: 0 landed/nothing to do, 4 probe not pass, 5 re-queue, 6 not landable yet (draft/threads), 7 validate failed on this head, 8 hand merge, 9 night pause / no time, 10 held.

## Configuration

`ZOE_LANDING_HOME` (state), `ZOE_LANDING_REPO` (live checkout, default `/home/zoe/assistant`), `ZOE_LANDING_GH_REPO`, `ZOE_LANDING_HOLD_FILES`, `ZOE_VOICE_ARTIFACT`, `ZOE_LANDING_ARTIFACT_DIR`, `ZOE_LANDING_WORKTREES`, `ZOE_LANDING_PANEL_HOST` (empty = skip the panel-quiet wait) / `ZOE_LANDING_PANEL_LOG`, `ZOE_LANDING_PYTHON`, `ZOE_LANDING_ENV_FILE`, `ZOE_WATCHD_SKIP` / `coord/watchd-skip.txt`, `ZOE_WATCHD_VOICE_LOG`. Test-only: `ZOE_LANDING_NOW_EPOCH`, `ZOE_LANDING_ONCE`, `ZOE_WATCHD_ONCE`, `ZOE_LANDING_NO_BUSY_WAIT`, `ZOE_LANDING_CHAIN_HOLD_S`, `ZOE_LANDING_CHAIN_STEP_S`.

## Cutover (owner-run; nothing in the repo does this)

The live loops still run the old copies from `~/.zoe/agent-tools`; the repo copies are inert until you switch. The night window pgreps `land_voice_pr.sh` by name, which both locations share, so never run old and new lander loops at once.

1. Merge this PR; sync `/home/zoe/assistant` with `git merge --ff-only origin/main`.
2. Pick a quiet moment: no lander probing (`pgrep -af land_voice_pr.sh` empty, `/tmp/zoe-brain-window.lock` free), outside 01:50-04:55.
3. Stop the loops: `pkill -f after_voice_more.sh; pkill -f coord/autoland.sh; pkill -f coord/watchd.sh` (let a running `land_queue.sh`/`docs_merge_chain.sh` finish first).
4. Seed the watcher skip list once: copy the PR numbers from the old `SKIP` regex in `~/.zoe/agent-tools/coord/watchd.sh` into `~/.zoe/agent-tools/coord/watchd-skip.txt` (one per line).
5. Restart from the repo path, same as before (nohup, logs in `~/.zoe/agent-tools/logs/`):
   `nohup bash /home/zoe/assistant/scripts/landing/after_voice_more.sh >> ~/.zoe/agent-tools/logs/after_voice_more.log 2>&1 &`
   `nohup bash /home/zoe/assistant/scripts/landing/autoland.sh >> ~/.zoe/agent-tools/logs/autoland.log 2>&1 &`
   `nohup bash /home/zoe/assistant/scripts/landing/watchd.sh >> ~/.zoe/agent-tools/logs/watchd.log 2>&1 &`
6. Point any hand-run (and the lab callers `labs/omi-firmware/build.sh`, `labs/credential-vault/run.sh`) `land_voice_pr.sh`, `docs_merge_chain.sh`, `threads.sh`, `night_busy.sh` invocation and the night-window/deploy callers of `night_busy.sh` at `scripts/landing/`; then retire the old files by removing them (keep `resolve_inventory_conflict.sh`).
7. Check the first cycle: `logs/watchd-events.log` stays quiet, `last-results.txt` appears after the first queue run.

Rollback: stop the repo-path loops and start the old ones; state files are shared and compatible (new files only: `last-results.txt`, `voice-probe-retried.txt`, `probe-failed.txt`, `probe-artifacts/`).
