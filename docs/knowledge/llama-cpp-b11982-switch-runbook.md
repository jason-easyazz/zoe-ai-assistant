---
type: runbook
title: Switching the live brain from llama.cpp b11194 to b11982 - owner-run steps and one-command rollback
description: The exact live steps to move llama-server.service (Gemma 4 E4B QAT + MTP, :11434) from ~/llama.cpp-b11194 to ~/llama.cpp-b11982, with verification, the voice replay probe, and a single-command rollback. The repo PR only changes the tracked template; nothing here runs from CI.
tags: [llama-cpp, brain, runbook, voice, rollback]
timestamp: 2026-10-30T00:00:00+08:00
---

# llama.cpp b11982 switch - owner-run runbook

**Decision:** owner, 2026-10-30. **Evidence:** [llama-cpp-bump-b11982-ab.md](llama-cpp-bump-b11982-ab.md) (+6% prompt
processing, decode / warm TTFT / MTP acceptance / RSS equal, replay probe 18/18 held, no flag breakage).
**Scope of the repo PR:** the tracked template `scripts/setup/systemd/llama-server.service`, `docs/CANONICAL.md`, docs and
one test. The PR does NOT touch the live box. This is a voice-path change (the brain unit): do the live switch first, then
land the PR; do not arm the PR or add it to voice-pending before the switch is verified.

Live facts (read-only, 2026-10-30): the unit is the user unit `llama-server.service`
(`~/.config/systemd/user/llama-server.service`), listening on `127.0.0.1:11434`. Its base file carries `LD_LIBRARY_PATH` and an
`ExecStart=` naming `%h/llama.cpp-b11194/build-jetson/bin/...`; the host-local drop-in
`~/.config/systemd/user/llama-server.service.d/80-swa-full.conf` (not tracked) **resets and re-states `ExecStart=`**
(adds `--swa-full`, `--cache-ram 1024`), so it is the ExecStart that actually runs. The other drop-ins
(`50-cold-start.conf`, `memory.conf`, `priority.conf`) do not name the build and are not touched. A second `llama-server`
process on the box is the FunctionGemma router (a different, older build) - it is not part of this change; always
select the brain by port `11434`.

Never run any of this between 01:50 and 04:55 or while `~/.zoe/agent-tools/night_busy.sh voice` exits 0 (see
[merge-and-deploy.md](merge-and-deploy.md), "Landings and the night window"). The restart silences the brain for about a
minute and the probe silences Kokoro for ~15 minutes: do it when nobody is talking to Zoe.

## 0. Preconditions

```bash
~/.zoe/agent-tools/night_busy.sh voice; echo "night_busy rc=$? (1 = clear to run, 0 = wait)"
~/llama.cpp-b11982/build-jetson/bin/llama-server --version 2>&1 | grep -i commit   # -> commit 8a199ad
ls ~/llama.cpp-b11194/build-jetson/bin/llama-server                                 # rollback binary still on disk
free -m | awk '/Mem:/{print "MemAvailable MB:", $7}'                                 # want >= 2000
```

## 1. Back up, then repoint

```bash
D=~/.zoe/unit-backups/$(date +%F-%H%M%S)-llama-b11982
mkdir -p ~/.zoe/unit-backups && mkdir "$D" || { echo "backup dir exists - stop"; false; }   # never reuse a backup dir: a re-run must not overwrite the b11194 copies
cp -a ~/.config/systemd/user/llama-server.service "$D"/
cp -a ~/.config/systemd/user/llama-server.service.d/80-swa-full.conf "$D"/
# record the running command line (names/flags only) to compare after the switch
pid=$(systemctl --user show -p MainPID --value llama-server); tr '\0' ' ' < /proc/$pid/cmdline > "$D"/args.before

# edit BOTH files: path only, every other byte unchanged (LD_LIBRARY_PATH + ExecStart in each)
sed -i 's#llama\.cpp-b11194#llama.cpp-b11982#g' \
  ~/.config/systemd/user/llama-server.service \
  ~/.config/systemd/user/llama-server.service.d/80-swa-full.conf
grep -n 'llama\.cpp-b1' ~/.config/systemd/user/llama-server.service ~/.config/systemd/user/llama-server.service.d/80-swa-full.conf
#   expect: only b11982 hits (LD_LIBRARY_PATH + ExecStart in the base unit; ExecStart in the drop-in)
diff <(sed 's/b11982/b11194/g' ~/.config/systemd/user/llama-server.service) "$D"/llama-server.service && echo "base unit: path-only change"
diff <(sed 's/b11982/b11194/g' ~/.config/systemd/user/llama-server.service.d/80-swa-full.conf) "$D"/80-swa-full.conf && echo "drop-in: path-only change"
systemctl --user daemon-reload
systemctl --user show llama-server -p ExecStart | grep -o 'llama.cpp-b1[0-9]*' | sort -u   # -> llama.cpp-b11982 only
```

## 2. Restart in the brain window

Take the lock for the whole restart + probe so nothing else touches the brain. Stop Kokoro first only for the probe (step 4);
the restart itself needs no Kokoro stop.

```bash
exec 9>/tmp/zoe-brain-window.lock && flock -x 9      # held until this shell exits or you run: exec 9>&-
systemctl --user restart llama-server                # ExecStartPost waits up to 120 s for /health
```

## 3. Verify

```bash
pid=$(systemctl --user show -p MainPID --value llama-server)
readlink /proc/$pid/exe                                         # -> .../llama.cpp-b11982/build-jetson/bin/llama-server
# identical args, path aside:
diff <(tr '\0' ' ' < /proc/$pid/cmdline | sed 's/b11982/b11194/g') "$D"/args.before && echo "args identical"
curl -sf http://127.0.0.1:11434/health; echo                    # -> {"status":"ok"}
# one real completion (greedy, short):
curl -sf http://127.0.0.1:11434/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Reply with exactly: ok"}],"max_tokens":8,"temperature":0}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["choices"][0]["message"]["content"])'
# MTP active: the journal prints a draft-acceptance line after a completion; the metrics counter moves
journalctl --user -u llama-server --since "-3min" --no-pager | grep -i "draft acceptance" | tail -2
curl -s http://127.0.0.1:11434/metrics | grep -i 'n_drafted_total\|n_accepted_total\|draft' | head   # drafted > 0
```

If any check fails, run the ROLLBACK below at once.

## 4. Voice replay probe - the lander way (no `--update-baseline`)

The probe must run against a checkout of the merged (or, before merge, the PR head) code, under the harness lock, with Kokoro
stopped. Use the recipe in [merge-and-deploy.md](merge-and-deploy.md) ("Unwedge" / "Landing a voice-path PR"); the shape is:

```bash
W=~/.worktrees/voice-gate-llama-b11982
git -C /home/zoe/assistant worktree add --detach "$W" origin/main      # or the PR head sha before merge
cp -n /home/zoe/assistant/services/zoe-data/.env "$W"/services/zoe-data/.env && chmod 600 "$W"/services/zoe-data/.env
ZPY=$(bash "$W"/scripts/deploy/zoe_data_python.sh)                      # the zoe-data service interpreter
systemctl --user stop kokoro-tts
flock /tmp/zoe-voice-harness.lock "$ZPY" "$W"/scripts/maintenance/voice_regression_probe.py \
  --samples 20 --service-dir "$W"/services/zoe-data                     # NO --update-baseline
systemctl --user start kokoro-tts
curl -s http://localhost:10201/health      # pipeline_loaded true AND device cuda (else Zoe is mute / on CPU)
exec 9>&-                                  # release the brain-window lock
git -C /home/zoe/assistant worktree remove --force "$W"
```

Pass bar (from the A/B): said-vs-did 18/18 held, 0 failed; COLD first turn still a prefix-cache hit; per-stage medians no worse
than baseline. If the probe fails the said-vs-did bar, ROLLBACK.

Then land the repo PR the normal voice-path way ([merge-and-deploy.md](merge-and-deploy.md), "Landing a voice-path PR"); the
probe artifact for that PR head is a separate run against that head.

## ROLLBACK (one command)

Restores both host-local files from the backup directory and restarts the brain on b11194. Safe to run at any point after step 1; the leading `exec 9>&-` releases the step-2 lock first (a second `flock` on the same file from the same shell would otherwise wait on it forever)
(`$D` is the backup dir from step 1; if the shell is gone, `D=$(ls -dt ~/.zoe/unit-backups/*-llama-b11982 | head -1)`).

```bash
exec 9>&- ; cp -a "$D"/llama-server.service ~/.config/systemd/user/ && cp -a "$D"/80-swa-full.conf ~/.config/systemd/user/llama-server.service.d/ && systemctl --user daemon-reload && flock -x /tmp/zoe-brain-window.lock systemctl --user restart llama-server && curl -sf http://127.0.0.1:11434/health
```

After a rollback, `readlink /proc/$(systemctl --user show -p MainPID --value llama-server)/exe` must name `llama.cpp-b11194`.
The tracked template then disagrees with the live box; revert the PR (or hold it) so the repo does not claim b11982.

## Notes

- `~/llama.cpp-b11194` is kept on disk for rollback; delete it only after b11982 has run clean for a while.
- Not changed on purpose: `scripts/night/night_window.py` (`WINNER_BINARY`, the 12B night window's own sweep winner, still
  b11194 and still on disk) and the lab/benchmark scripts that name b11194 as their measured build.
- The P0 de-root templates (`scripts/setup/p0-deroot/`, `labs/p0-deroot/`) were repointed to the b11982 path so a later
  de-root provisions the build that will actually be live.
