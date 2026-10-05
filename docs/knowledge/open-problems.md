---
type: Ledger
title: Open problems — found, not yet fixed
description: The standing ledger of every problem found on the live system or in the repo that was not fixed on the spot. Rule (owner, 2026-10-05) — a problem is fixed, or it is written here the same turn; a problem that lives only in a chat reply is forgotten. One line per item; delete the line when it is fixed (git history keeps it).
tags: [ops, ledger, backlog, runbook]
timestamp: 2026-10-05T12:50:00+08:00
---

# Open problems

Format: **found** · where · what · evidence · owner · verified fixed when.
Add a line the moment a problem is found and not fixed. Remove it only with the fix merged or applied and
the "verified fixed when" check done. Operator items also go in the current morning/operator pack.

## Needs the owner's hands

- **2026-10-04** · GitHub branch protection · `voice-gate` is not a required check on `main`, so an armed PR can merge unprobed if its head moves (incident signatures 36, 40); the landing script only mitigates · `gh api repos/:owner/:repo/branches/main/protection/required_status_checks` lists `validate`, `secret-scan` · owner (the auto-mode classifier refuses the agent) · the listing shows `voice-gate`.
- **2026-10-04** · Docker · nine containers with unbounded json-file logs and no memory cap; Postgres and the HA bridge bound on all interfaces (#1727's loopback compose is merged, never applied) · `docker inspect` shows `max-size` empty and `Memory=0` for all but zoe-ui/zoe-auth · owner, Docker window (morning pack block 3) · `docker port zoe-database` shows `127.0.0.1:5432` only, every container has `max-size` and a cap.
- **2026-10-04** · Docker · `livekit` container `Exited (137)` for a week · `docker ps -a` · owner decision: remove, or keep for the room lane · the container is gone or running.
- **2026-10-04** · Home Assistant · one ESPHome entry keeps refusing port 6053 (a device on the Pi's address); the localtuya Tuya IoT Core trial expired · HA log + Settings → Devices · owner, HA UI · no 6053 refusals in the HA log; Tuya integration loads.
- **2026-10-04** · Pi 5 panel · idles at 76–82 °C under load, throttling in its history, no fan device registered · `vcgencmd measure_temp`, `vcgencmd get_throttled` · owner, hardware or `/boot/firmware/config.txt` fan overlay · idle under 60 °C, `get_throttled` 0x0.
- **2026-10-05** · Music Assistant · YouTube Music login expired (143 retries in 5 h) · MA log · owner, panel Music → Sources → Reconnect → QR · a music search returns results.
- **2026-10-04** · Pi panel · barge-in phase 1 (`BARGE_DUCK_ENABLED`) never run in the lab; flag dark · pack block 1 · owner at the panel · `BARGE_DECIDE` lines meet the bar (false commits ≤ 10 %, resumes on noise ≥ 90 %, commits ≤ 1.1 s).
- **2026-10-04** · Speaker gate · the shadow week needs the owner's re-enrolment (8 prompts × 3 conditions) and a labelled TV/radio negative set; today's targets are met only on pseudo-labels · `docs/knowledge/` speaker-gate note · owner + agent · shadow-week report on real labels.
- **2026-10-04** · zoe-auth · prints a usable first-run setup token to its log on every start · container log · owner decision (print only when no admin exists, never the value) then agent · the log shows a one-line "setup pending" notice at most.
- **2026-10-05** · Mac virtual panel (#1865) · the `[unverified]` items in `docs/knowledge/mac-virtual-panel.md` (ai-edge-litert wheel, TFLite shim, test clip format) were designed without a Mac · the doc · owner's first run · `preflight` passes on the Mac.
- **2026-10-05** · Wake word · no `.tflite` build of `hey_zoe` is known; the Mac panel falls back to `hey_jarvis` · doc §2 · owner (does a build exist?) or agent (train one) · the Mac daemon loads `hey_zoe.tflite`.

## Agent work, queued

- **2026-10-05** · Memory provenance · the Pi daemon's speaker-id verdict is not wired into `MemoryService.ingest(speaker_verified=…)`, so voice-lane self-facts never become `user_unverified` · #1868 docstring · agent, after #1868 merges · a voice turn with a failed speaker check stores `user_unverified`.
- **2026-10-05** · Memory authority · `memory_authority_backfill.py` has not run on the live palace (dry-run first); `ZOE_MEMORY_AUTHORITY` stays `enforce` until a night of `AUTHORITY_BLOCKED` logs says whether legitimate updates get parked (then `shadow`) · #1868 · agent · backfill report + one night's log reviewed.
- **2026-10-05** · Voice lane · lacks the correction, contacts-conversation and verify-on-challenge tiers the text lane got in #1853/#1854/#1860 · `fast_tiers.py` lane gating · agent (replay-gated) · bar scenarios pass on the voice channel.
- **2026-10-05** · Touch UI · `touch/home.html` posts chat without a session id (the server now reuses the owner's last session within 20 min, #1866); the one-line client fix is still open · #1866 notes · agent, separate touch PR · the page sends `session_id`.
- **2026-10-05** · Samantha bar · S21 ("a correction reaches the record", a declared target while `ZOE_CORRECTION_APPLY` is dark) reports ERROR, not FAIL: a harness setup error, not a product result · `~/.zoe/agent-tools/logs/final_bar2.log` · agent · the bar prints PASS or FAIL for S21, never ERROR.
- **2026-10-04** · Flag inventory · no pre-commit hook regenerates `docs/knowledge/flag-inventory.{md,json}`; cost four incidents in one day · `reference_flag_inventory_papercut` · agent · the hook exists and a stale inventory fails the commit.
- **2026-10-05** · Landing tooling · the merge/landing scripts live only in `~/.zoe/agent-tools/` on the Jetson, unversioned (`land_voice_pr.sh`, `docs_merge_chain.sh`, the hold/pending files) · the directory · agent · the scripts are tracked under the repo (secrets stay out) or documented in `docs/knowledge/`.
- **2026-10-05** · Review · #1870 (memory restore tool) merged through the docs chain before the planned Opus review ran; the tool is operator-only and its restore was dry-run-verified first · merge log 12:15 · agent · a retroactive review posted on the PR, findings fixed or declined with rationale.
- **2026-10-04** · Kokoro · logs a warning about its explicit repo id until its next restart (harmless) · unit log · agent · the warning is gone after the next planned restart.
