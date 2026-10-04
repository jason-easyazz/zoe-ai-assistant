---
type: Runbook
title: Engineering off the box — PR 1 install, negative controls, rollback (zoe-agents.slice + session lease + sampler)
description: Operator recipe for the inert PR-1 artefacts of the "agent sessions off the box" decision (record 2026-10-04) - the zoe-agents.slice aggregate cap, the zoe-agent launcher and the managed bridge unit that take the single session lease, the Serena and codebase-memory re-parenting, the advisory SessionStart hook example, and the 5-minute sampler. Ordered install, negative controls 1, 1b, 2 and 4 with what red and green look like, how to read the JSONL, and the rollback.
tags: [memory, cgroup, systemd, slice, lease, sampler, serena, codebase-memory, claude-code, codex, runbook]
timestamp: 2026-10-04T00:00:00Z
---

# Engineering off the box - PR 1 install, controls, rollback

Decision record: [agent-sessions-off-box-2026-10-04](../research/agent-sessions-off-box-2026-10-04.md)
(section 3.1 = option A, the guard; section 5 = the pieces and the measurement plan). This runbook
covers **PR 1 only**: everything here is a template, **nothing is installed by merging it**, and
the box behaves exactly as before until an operator runs the steps below. The builder unit and the
Omnigent `mem_limit` are PR 2; the laptop runbook and `ZOE_HOST`-aware landing script are PR 3.

Naming note: the record's section 5 calls the sampler `zoe-agent-mem.timer` and puts the launcher in
`scripts/maintenance/`; this PR ships them as `zoe-agents-sampler.{service,timer}` and
`scripts/agents/zoe-agent`. The behaviour is the record's.

## What ships

| Artefact | Does |
|---|---|
| `scripts/setup/systemd/zoe-agents.slice` | The aggregate: `MemoryHigh=MemoryMax=3G`, `MemorySwapMax=0`, `CPUWeight=IOWeight=50`. All three memory keys together (no throttle band, no swap escape). CPU/IO weight 50 is this PR's choice - the record gives none. |
| `scripts/setup/systemd/system/user@.service.d/delegate.conf` | **ROOT, operator-only** template of `/etc/systemd/system/user@.service.d/delegate.conf` (`Delegate=pids memory cpu io`). It is live on this host but was untracked; without `cpu`/`io` delegated, the slice's `CPUWeight`/`IOWeight` are silently inert on a rebuilt box. |
| `scripts/setup/systemd/serena-mcp.service.d/70-agents-slice.conf` | `Slice=zoe-agents.slice` on the shared Serena. Its own 2G / swap-0 member cap is unchanged. |
| `scripts/maintenance/codebase_memory_capped.sh` | `systemd-run --scope` now passes `--slice=zoe-agents.slice`. 768M member cap unchanged. **This one is live on merge** (agent configs run it from the live checkout) - harmless before the slice unit exists, see "Order matters". |
| `scripts/agents/zoe-agent` | The launcher: `flock -n -E 75 <lease>`, then `systemd-run --user --slice=zoe-agents.slice --scope -- claude\|codex "$@"`. Exit 75 prints "another engineering session holds the lease (pid, since)". |
| `scripts/setup/systemd/zoe-claude-bridge.service` + `scripts/agents/zoe-claude-bridge-exec` | The remote-control bridge as a managed unit: `Slice=zoe-agents.slice`, `ExecStart=flock -n -E 75 <same lease> …`. No `[Install]` - see the stage-gate. |
| `scripts/maintenance/zoe_agents_sampler.py` + `scripts/setup/systemd/zoe-agents-sampler.{service,timer}` | Every 5 min, one JSON line to `~/.zoe-logs/agents-sampler.jsonl`. |

Two locks, never one: the **session lease** is `$XDG_RUNTIME_DIR/zoe-agent-session.lock`
(`/run/user/1000/…`); the **brain-window lock** is `/tmp/zoe-voice-harness.lock`, taken only around
model-loading commands (`voice_regression_probe.py`, `measure_voice.py`, a Kokoro load). Nothing in
this PR takes the harness lock - an idle open session must not block the nightly replay gate.

## Install - ordered

Run as the `zoe` user. Install the sampler first and let it run a baseline week; the rest a week later.

```bash
cd ~/assistant            # the live checkout, already at the merged main (git merge --ff-only; no stash)
mkdir -p ~/.config/systemd/user ~/.config/systemd/user/serena-mcp.service.d

# 1 - baseline week: the sampler alone
cp scripts/setup/systemd/zoe-agents-sampler.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now zoe-agents-sampler.timer

# 2 - a week later: the slice. NOTE: by now the slice already EXISTS as an implicit, unlimited one
#     (codebase_memory_capped.sh has named it since merge) - this step turns it into a capped one.
#     (root, only on a rebuilt box lacking it: the delegate.conf template, see "Controller delegation")
cp scripts/setup/systemd/zoe-agents.slice ~/.config/systemd/user/ && systemctl --user daemon-reload
S=/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service
cat $S/cgroup.controllers                    # must list: cpu io memory pids   (delegation)
cat $S/zoe-agents.slice/memory.max           # 3221225472   (was "max" while implicit)
cat $S/zoe-agents.slice/memory.swap.max      # 0
cat $S/zoe-agents.slice/cpu.weight           # 50           (absent/100 = cpu not delegated -> weight inert)
cat $S/zoe-agents.slice/io.weight            # "default 50"; file ABSENT on this Orin (scheduler none) = IOWeight inert, expected
python3 scripts/maintenance/zoe_agents_sampler.py --stdout | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r["slice"]["state"], r["in_slice_bounded"])'   # capped True

# 3 - re-parent Serena (a restart; first request can take MANY minutes of warm-up - not a fault)
cp scripts/setup/systemd/serena-mcp.service.d/70-agents-slice.conf ~/.config/systemd/user/serena-mcp.service.d/
systemctl --user daemon-reload && systemctl --user restart serena-mcp && scripts/maintenance/serena_mcp_health.sh

# 4 - the launcher on PATH (start every shell session through it from now on)
ln -sf ~/assistant/scripts/agents/zoe-agent ~/.local/bin/zoe-agent

# 5 - the bridge unit: STAGE-GATED, see below.  6 - the SessionStart hook example, below.
```

If `memory.max` does not read `3221225472` after step 2, the slice file did not apply: the implicit
slice that `codebase_memory_capped.sh` created is the **normal** starting state of step 2, not an edge
case, and it may need `systemctl --user daemon-reload` twice, or
`systemctl --user set-property zoe-agents.slice MemoryMax=3G MemoryHigh=3G MemorySwapMax=0`
[unverified which is needed on this systemd]. **Do not proceed to control 2 until it reads 3G and the
sampler says `capped`.**

### Controller delegation (root, rebuilt boxes only)

A user slice enforces only the controllers the user manager was delegated. This host has
`/etc/systemd/system/user@.service.d/delegate.conf` (`Delegate=pids memory cpu io`) - it was untracked;
the template is `scripts/setup/systemd/system/user@.service.d/delegate.conf` (install line in its header).
Without it the slice's `CPUWeight` (and `IOWeight`) are accepted and silently do nothing, which is why
step 2 reads `cpu.weight` back. Separately, `io.weight` needs a block scheduler that exposes it (BFQ /
io.cost): on this Orin the NVMe scheduler is `none`, `io.weight` does not exist anywhere in the user
manager's tree, and `IOWeight=50` is **inert here regardless of delegation**. The memory keys - the cap -
depend on neither.

**Order matters.** `systemd-run --slice=zoe-agents.slice` against a slice with no unit file silently
creates an *implicit slice with no limits*. That is why the wrapper change is safe to merge first (it
degrades to "scope in an uncapped slice named zoe-agents.slice") and why every cap-bearing step reads
the value back before relying on it. It also means the **baseline week is not fully clean**: from merge
day, codebase-memory scopes are parented under the implicit slice, so the sampler counts them `in_slice`
while `slice.state` is `implicit slice (no memory.max)` and `in_slice_bounded` is `false`. Read
`in_slice` as "parented" and only `in_slice_bounded: true` as "bounded"; serena, jedi and the sessions are
the baseline's honest outside-the-bound population.

### The bridge unit - stage-gate [unverified]

The desktop app starts its **own** `~/.claude/remote/srv/<sha1>/server --serve --socket
~/.claude/remote/run/<hash>/rpc.sock --token-file …` (observed 2026-10-04: the binary is `…/srv/<sha>/server`
- one directory per app release - **not** a file called `srv`; the `<hash>` run directory carries
`daemon.lock`/`daemon.token`). Whether the app **attaches** to a server it did not start was not tested,
and a unit-started bridge the app ignores is just an unused process. So:

1. `cp scripts/setup/systemd/zoe-claude-bridge.service ~/.config/systemd/user/ && systemctl --user daemon-reload`
2. With **no** session open: `systemctl --user start zoe-claude-bridge` and read
   `journalctl --user -u zoe-claude-bridge -n 20` (the helper logs `release <sha> chosen by running --serve process N` or `by newest server file mtime` - never directory mtime, which goes stale on in-place rewrites and app rollbacks), `cat /proc/$(systemctl --user show -p MainPID --value zoe-claude-bridge)/cgroup`
   (must end `…/zoe-agents.slice/zoe-claude-bridge.service`).
3. From the desktop app, open a new remote session. **Pass** = the new `ccd-cli` is a child of that unit
   (its `/proc/<pid>/cgroup` is under the slice). **Fail** = the app launched its own bridge: `systemctl
   --user stop zoe-claude-bridge; rm` the unit and rely on the launcher + the sampler's `ccd-cli` outside-slice
   column. Do not `enable` it.

Stopping the unit ends every session its bridge spawned (`KillMode=control-group`).

## The advisory SessionStart hook (example only - not in this repo's `.claude/`)

A Claude Code `SessionStart` hook is context-only: it cannot refuse a session and runs after the
process is already outside any slice. It is useful for exactly two things - telling you who holds the
lease, and tagging unlauncher'd sessions. Put it in the **user** config (`~/.claude/settings.json`), not
in the repo:

```json
{ "hooks": { "SessionStart": [ { "hooks": [
  { "type": "command", "command": "~/.claude/hooks/zoe-lease-advisory.sh" } ] } ] } }
```

```bash
#!/usr/bin/env bash
# ~/.claude/hooks/zoe-lease-advisory.sh - stdout is added to the session context. Advisory only.
rt="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"; hf="$rt/zoe-agent-session.holder"
cg="$(sed -n 's/^0:://p' /proc/$$/cgroup)"
case "/$cg/" in */zoe-agents.slice/*) in_slice=true ;; *) in_slice=false ;; esac
if [ "$in_slice" = false ]; then
  echo "zoe-lease: this session is OUTSIDE zoe-agents.slice (not started via zoe-agent or the bridge unit) - uncapped, lease not held."
fi
if [ -r "$hf" ]; then
  pid="$(sed -n 's/^pid=//p' "$hf")"; since="$(sed -n 's/^since=//p' "$hf")"
  if [ -n "$pid" ] && [ "$pid" != "$PPID" ] && kill -0 "$pid" 2>/dev/null; then
    echo "zoe-lease: another engineering session holds the lease (pid $pid, since $(date -d "@$since" '+%H:%M' 2>/dev/null))."
  fi
fi
mkdir -p ~/.zoe-logs
printf '{"ts":"%s","event":"session_start","in_slice":%s}\n' "$(date -u +%FT%TZ)" "$in_slice" >> ~/.zoe-logs/agent-session-tags.jsonl
exit 0
```

The tag file is a join key by timestamp; the sampler counts processes by itself and does not read it.

## Reading the sampler

```bash
tail -n 1 ~/.zoe-logs/agents-sampler.jsonl | python3 -m json.tool
# MemAvailable (MB) and engineering processes outside the bound, per sample:
python3 - <<'PY'
import json, os
for l in open(os.path.expanduser("~/.zoe-logs/agents-sampler.jsonl")):
    r = json.loads(l); print(r["ts"], (r["mem_available_kb"] or 0)//1024, "MB avail,",
          r["outside_slice_total"], "outside;", "slice", r["slice"].get("memory_current"))
PY
```

Fields: `mem_available_kb`, `swap_used_kb`; `slice` (`present`, `memory_current`, `swap_current`,
`memory_max`, `swap_max`, `events.{high,max,oom,oom_kill}`, `members[]`); `omnigent` (container cgroup,
found through the `omnigent server` process - no docker CLI); `procs.<kind>.{total,in_slice,outside_slice,container}`
for `serena`, `jedi-language-server`, `codebase-memory-mcp`, `ccd-cli`, `codex`, `claude`;
`slice.state` (`absent`, `implicit slice (no memory.max)`, `capped`), `slice.cpu_weight` / `io_weight` (null
when the kernel does not expose them), top-level `in_slice_bounded`; `outside_slice_total`; `lease` (`held`, `lock_pid` from `/proc/locks`, `holder_file`). Container processes
share the host uid and appear in the host `/proc`; they are bucketed `container` and **never** counted
as outside. Before the PR merges the slice does not exist (`state: absent`, everything reads outside).
After merge and before step 2 it exists **implicitly**: `slice.present=true`, `memory_max=null`,
`state: implicit slice (no memory.max)`, `in_slice_bounded: false`, and any codebase-memory scope counts
`in_slice` (parented, not bounded). That is the baseline; do not read it as a cap.

## Negative controls (the record's 1, 1b, 2, 4 - control 3 belongs to PR 2)

A control that cannot go red proves nothing; each lists what *red* looks like.

**Control 1 - the lease refuses a second session.** With one `zoe-agent claude` open:
`zoe-agent claude` in a second shell must exit **75** and print "another engineering session holds the
lease (pid …, since …)". With the bridge unit running: `zoe-agent claude` exits 75 too (holder pid = the
bridge); and with a launcher session open, `systemctl --user start zoe-claude-bridge` must **fail** with
status 75 (`systemctl --user show -p ExecMainStatus zoe-claude-bridge` = 75; it is not restarted). A `ccd-cli`
started by hand outside both must show in the next sample as `procs.ccd-cli.outside_slice >= 1`.
*Red* = the second start succeeds or the hand-started process is not counted.

**Control 1b - the lease is not the harness lock.** Leave a launcher session open and idle, then in
another shell: `flock -n /tmp/zoe-voice-harness.lock true && echo ok` must print `ok` (and so the nightly
probe under that lock runs). *Red* = it fails while only a session is open.

**Control 2 - the slice kills its own, not the brain.** Only after `memory.max` reads 3221225472 and
`memory.swap.max` reads 0, with MemAvailable > 5 GB and no replay in flight (a wrong cap here would be the
2026-07-19 crash). Watch `curl -s localhost:8000/health` in another shell, then:

```bash
systemd-run --user --slice=zoe-agents.slice --scope --quiet --collect \
  python3 -c 'b=[]; [b.append(bytearray(b"x")*(64<<20)) for _ in range(56)]'   # 3.5 GB touched
```

Pass = that python is OOM-killed, `memory.events` of the slice shows `oom_kill` incremented, the brain's
`MemoryCurrent` is unchanged, `/health` stays green throughout. *Red* = it completes (no cap) or anything
outside the slice is killed.

**Control 4 - Serena, jedi and codebase-memory are inside the slice.** From a `zoe-agent` session run one
Serena-heavy query and one codebase-memory call, then:

```bash
python3 ~/assistant/scripts/maintenance/zoe_agents_sampler.py --stdout | python3 -c \
 'import json,sys; r=json.load(sys.stdin); print({k:v["outside_slice"] for k,v in r["procs"].items()})'
for p in $(pgrep -f 'serena start-mcp-server|jedi-language-server|codebase-memory-mcp'); do
  echo "$p $(sed -n 's/^0:://p' /proc/$p/cgroup)"; done
```

Pass = `serena`, `jedi-language-server`, `codebase-memory-mcp` all `outside_slice: 0`, every path contains
`/zoe-agents.slice/`, **and** `slice.state` is `capped` (`in_slice_bounded: true`) - a process in the implicit
slice is parented but unbounded, so membership alone proves nothing; the slice's `memory.current` rises when
they allocate. **Serena and jedi must read red before step 3** (they sit in `app.slice` tonight: the
sampler read 6 engineering processes outside on 2026-10-04 - serena, jedi, two codebase-memory, two
ccd-cli). **codebase-memory reads in-slice from merge day** (the wrapper, not an install step, moves it),
so for it the red-before signal is `in_slice_bounded: false` until step 2 - if serena/jedi read green on an
unmodified box, or `in_slice_bounded` is true before step 2, the instrument is broken.

## Rollback

```bash
systemctl --user stop zoe-claude-bridge 2>/dev/null; rm -f ~/.config/systemd/user/zoe-claude-bridge.service
rm -f ~/.config/systemd/user/serena-mcp.service.d/70-agents-slice.conf
rm -f ~/.config/systemd/user/zoe-agents.slice ~/.local/bin/zoe-agent
systemctl --user daemon-reload && systemctl --user restart serena-mcp     # back in app.slice
systemctl --user disable --now zoe-agents-sampler.timer                    # optional: keep it, it is read-only
```

`codebase_memory_capped.sh` needs no rollback step: with no slice unit it only names an implicit,
uncapped slice; `git revert` the one line to remove even that. Sessions started before a rollback keep
their cgroup until they exit. The lease files live in `/run/user/1000` and vanish at logout/reboot.

## [unverified]

- That the desktop app attaches to a unit-started bridge (stage-gate above), and the `--token-file`
  format the app expects. The observed command line is the only source for the flags.
- Whether `daemon-reload` re-applies slice limits to an already-implicit `zoe-agents.slice` (fallback given).
- `CPUWeight=IOWeight=50` is a choice, not a measurement; `IOWeight` is inert on this host (no `io.weight`).
- Behaviour of `/proc/locks` pid attribution under a pid-namespaced reader (the sampler runs on the host;
  fine there).
