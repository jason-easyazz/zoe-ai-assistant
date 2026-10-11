---
type: runbook
title: Brain cold start under NvMap page-cache pressure
description: Why the 4B llama-server fails to start after a stop on the Orin (NvMap error 12 at ~6 GB MemAvailable), the bounded drop-cache helper + systemd drop-in that fix it, and the exact operator install and verification steps.
tags: [brain, llama-server, nvmap, systemd, runbook, night-window]
timestamp: 2026-10-12T00:00:00+08:00
---

# Brain cold start under NvMap page-cache pressure

## The failure (2026-10-10, twice in one day)

After a brain-stop window, `systemctl --user start llama-server` failed for **14.5 min** with

```
NvMapMemAllocInternalTagged ... error 12
cudaMalloc ... 2493 MiB failed
```

while `MemAvailable` said about 6 GB. Tegra's NvMap allocator does **not** reclaim page cache. Loading the 4.2 GB GGUF fills the cache faster than the big weight buffer allocation lands, so the allocation fails even though the kernel would happily give the cache back to anyone else. `Restart=on-failure` retried into the same wall. A single `drop_caches` before the start was not enough. What recovered it was `echo 1 > /proc/sys/vm/drop_caches` **every ~0.3 s while the start ran** (the loop `scripts/perf/engine_bench.py` carries for its own windows). A second 4B load for a bench died the same way, and a 09:00 cold restart the same morning needed 2 min 31 s of systemd retries.

Every planned brain stop hits this: the nightly 12B window (`scripts/night/night_window.py`), deploys, and OOM recovery.

## The fix

| Piece | What it is |
|---|---|
| `scripts/maintenance/brain_cold_start.sh` | Drops the page cache once (synchronously), then detaches a bounded loop that keeps dropping every 0.3 s until `http://127.0.0.1:11434/health` answers or **180 s** pass. Exits 0 on every path. |
| `scripts/setup/systemd/llama-server.service.d/50-cold-start.conf` | The tracked **drop-in**: `ExecStartPre=-/bin/bash %h/assistant/scripts/maintenance/brain_cold_start.sh` (after an `ExecStartPre=` reset) and `RestartSec=10`. The unit's `ExecStart` is not touched. |
| `scripts/setup/systemd/llama-server.service` | **Deliberately unchanged.** The template is voice-path scope (`voice_gate_check.py` `VOICE_PATH_PATTERNS`), so editing it would make this a replay-gated landing for a change that alters no brain flag. `RestartSec=10` in the drop-in equals the template's, so `unit_drift_check.py` reports no `differs`/`missing` for the installed unit; the extra `ExecStartPre` shows only as a `live_only` note (`--strict`). Folding the line into the template is a later, voice-gated step once the drop-in has proved itself live. |
| `scripts/night/night_window.py` `cold_start_guard()` | The wake path starts the brain with `systemctl start`. If the installed unit already runs the helper as `ExecStartPre`, the window does **nothing extra** (no double work). On a host without the drop-in it runs the same helper itself just before `systemctl start`. The window's own compaction (`drop_caches 3` + `compact_memory`, for fragmentation) is separate and unchanged. |

### Why an ExecStartPre and not a wrapper ExecStart

`ExecStartPre` must exit before the server starts, so it cannot *be* the loop. It spawns the loop detached (`setsid`, own session) and returns. The alternatives were worse: a wrapper `ExecStart` would change the unit's main process (signal handling, `MainPID`, the `ExecStartPost` probe, `unit_drift_check.py`'s flag parsing, the night window's clone generator that reads the unit's `ExecStart`), and an `ExecStartPost` loop starts too late (the server is already allocating). The helper is therefore a **side process in the unit's cgroup** that stops itself; `systemctl stop` kills it with the unit (`KillMode=control-group`).

### Why `RestartSec=10`

The detached loop keeps dropping the cache **across** the restart gap (it runs up to 180 s, independent of the unit's state), and each retry's Pre step supersedes it with a fresh 180 s loop plus a fresh synchronous drop. So a retry already meets a clean cache; a longer wait buys nothing and 10 s keeps a failed start's recovery short (the 09:00 incident needed 2 min 31 s of retries). The drop-in states the template's value explicitly so a host-local shorter value cannot start a retry before the loop has run.

### Properties (all pinned by `tests/unit/test_brain_cold_start.py`)

- **Never blocks the start**: the Pre step returns after one drop; the loop's stdout/stderr go to a log file, so a caller that captures output (the night window) is not held until the loop ends.
- **Never fails the unit**: the `-` prefix plus `exit 0` on every path. No passwordless `sudo -n` = one log line (`SKIPPED ...`, with the manual command) and the start proceeds exactly as before. A broken sudo mid-loop ends the loop after 5 consecutive failures.
- **Bounded**: a wall-clock bound (`BRAIN_COLD_START_MAX_S`, default 180, equal to the unit's `TimeoutStartSec`), not an iteration count; and it stops the moment `/health` answers.
- **One loop at a time**: a retry's Pre step supersedes the previous loop (pidfile take-over, verified against `/proc/<pid>/cmdline` so a recycled pid is never killed).
- **Privilege**: only `sudo -n sh -c 'echo 1 > /proc/sys/vm/drop_caches'`. Level 1 drops clean page cache only; nothing is lost, files are re-read from disk.
- **Knobs** (environment, none is a `ZOE_*` flag): `BRAIN_COLD_START_DISABLE=1`, `_HEALTH_URL`, `_MAX_S`, `_INTERVAL_S`, `_STATE_DIR` (default `$XDG_RUNTIME_DIR/brain-cold-start`: `loop.pid`, `loop.log`).

## Operator: install (does not restart anything)

From the live checkout `~/assistant` (the drop-in's script path is `%h/assistant/scripts/maintenance/brain_cold_start.sh`, so the PR must be merged and the checkout synced first):

```bash
mkdir -p ~/.config/systemd/user/llama-server.service.d
cp scripts/setup/systemd/llama-server.service.d/50-cold-start.conf ~/.config/systemd/user/llama-server.service.d/
systemctl --user daemon-reload
```

The installed unit is left as it is (no template copy: it carries host edits and untracked drop-ins). The change takes effect on the **next** start of the unit. Precondition: `sudo -n true` succeeds for the `zoe` user.

Check what systemd now sees (read-only):

```bash
systemctl --user cat llama-server | grep -n -B1 -A1 "ExecStartPre\|RestartSec"
python3 scripts/maintenance/unit_drift_check.py --units llama-server
```

Expect one `ExecStartPre=-/bin/bash .../brain_cold_start.sh` line and `RestartSec=10`; the drift check exits 0.

## Operator: verify (a cold start with a FULL page cache comes up first try)

This stops the brain, so it is a brain window: hold the lock, and run it when Zoe may be silent for about a minute (not during a night window, a deploy or the voice gate).

```bash
flock /tmp/zoe-brain-window.lock bash -c '
  systemctl --user stop llama-server
  cat ~/models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf ~/models/gemma4-e4b-qat/mtp-gemma-4-E4B-it.gguf > /dev/null
  grep -E "^(MemFree|MemAvailable|Cached):" /proc/meminfo          # Cached should now be several GB: the failing condition
  systemctl --user start llama-server                              # returns when the unit'"'"'s ExecStartPost sees /health ok
  curl -sf http://127.0.0.1:11434/health
'
```

If the start fails anyway the brain stays down; the unit retries every 10 s by itself (each retry re-arms the loop), and the manual recovery is the 2026-10-10 one: `while true; do sudo sh -c 'echo 1 > /proc/sys/vm/drop_caches'; sleep 0.3; done` in a second shell until `systemctl --user is-active llama-server` says `active` (then stop the loop).

While it loads, in a second shell, the loop must be alive (this is the check that systemd leaves the Pre step's detached child running):

```bash
pgrep -af "brain_cold_start.sh --loop"
```

After it:

```bash
systemctl --user show -p NRestarts -p ActiveState llama-server      # NRestarts=0, ActiveState=active: first try
journalctl --user -u llama-server --since "-5min" | grep -c "error 12"   # 0
journalctl --user -t brain-cold-start --since "-5min"
```

The last command should show, in order: `page cache dropped before the start`, `loop detached`, `loop started: dropping the page cache every 0.3s ...`, and `stopped: brain healthy after N ms (K drops)`. A line `SKIPPED: passwordless sudo is not available` means the precondition failed. If you see `loop started` but never a `stopped:` line and `pgrep` finds nothing, systemd reaped the Pre step's child: record that in `open-problems.md` and switch the detach to a transient `systemd-run --user` unit (follow-up; the single synchronous drop is all that ran).

Then tick the two `open-problems.md` entries (2026-10-10 "Brain restart" and "Brain") fixed and remove them, citing the verification output. A comparison *without* the drop-in is not recommended on a live brain: the 2026-10-10 incident is that control, and it cost 14.5 minutes.

## Rollback

```bash
rm ~/.config/systemd/user/llama-server.service.d/50-cold-start.conf
systemctl --user daemon-reload
```

The next start behaves as before (one start, `Restart=on-failure`, `RestartSec=10` as in the template). The night window then runs the helper itself before its brain start (`cold_start_guard`), so the nightly path stays protected even without the drop-in.

## Not covered

- Fragmentation of high-order free blocks (the 12B's problem, `night-window.md` sections 8 and 12): the window compacts memory for that; this helper only keeps the page cache from starving NvMap while the 4B loads.
- Other brain starts outside systemd (a bench's transient server, `scripts/perf/zmb/bakeoff.py`'s restore) are not wired to this helper. `scripts/perf/engine_bench.py` keeps its own loop for its windows.
