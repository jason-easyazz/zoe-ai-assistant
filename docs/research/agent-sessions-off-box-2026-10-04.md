---
type: research
title: Agent sessions off the box — where engineering runs so the Jetson can breathe (2026-10-04)
date: 2026-10-04
status: research-only — no code, flag, unit, container or live service changed by this document
description: Decision record for owner question Q4 ("each Claude Code or Codex session on the Jetson costs about 1 GB — keep as is, one at a time, or engineer from another machine and treat the Jetson as the deploy target only"; owner answer "move agent sessions off the box", with the doubt "maybe offload these to the touch-screen Pi … I still want Zoe to be able to self evolve, but we keep hitting RAM issues"). A read-only measurement of what agent tooling costs on the box tonight (Claude Code remote session, the shared Serena, codebase-memory scopes, the Omnigent container with a Codex cross-review in flight, Multica, the GitHub runner, 102 worktrees), set beside the 2026-10-03 profile; the recorded RAM incidents that are all agent-tooling incidents; an honest evaluation of A (one session at a time, lease + slice), B (engineer from the laptop / Claude Code cloud sessions, Jetson = deploy target — what must stay on the box, what moves, how the PR pipeline already decouples editing from deploying), C (the Pi 5 — verdict: no, and why it is the wrong kind of host) and D (keeping self-evolution: a capped builder lane that wakes for a ticket and never coexists with a brain window); comparison table, recommendation (B as the default + A's guard for whatever still runs on the box), the units involved, a one-week measurement plan with negative controls, four decisions for Jason and next steps.
---

# Agent sessions off the box — where engineering runs so the Jetson can breathe (2026-10-04)

Research date: 2026-10-04. The owner's question, verbatim: *"Agent sessions on the box. Each
Claude Code or Codex session on the Jetson costs about 1 GB (code-intel servers and their
caches). Options: keep as is; allow one session at a time; or run engineering sessions from
another machine and treat the Jetson as the deploy target only."* The answer: **"Move agent
sessions off the box"**, with the note: *"I'm just wondering if maybe we offload these to the
touch screen pi, i dont like the idea, because i still want zoe to be able to self evolve, but
we keep hitting ram issues."*

This record takes the note as the acceptance test: whatever is chosen must (1) stop the RAM
incidents and (2) keep Zoe's self-evolution loop (Omnigent / polly / the Multica executor, the
[self-building skills record](self-building-skills-2026-10-04.md)) working. It sits beside the
[W3 memory profile of 2026-10-03](../knowledge/memory-pressure-profile-2026-10-03.md), whose
reclaim list already names "keep agent sessions off the box" as item 1 and leaves "whether
engineering sessions may stay resident on the product box" as operator decision 6.

Evidence labels: **[live]** read-only observation on the box tonight (`ps`, `/proc`, cgroup
files, `systemctl show`, `docker ps`; nothing restarted, written or dispatched); **[src]** this
worktree's tree or tracked docs (file:line); **[doc]** an upstream page read today;
**[unverified]** not checked against a primary or not measured on our hardware; **[inf]** my
inference. No secrets, tokens or household data are quoted; command lines are truncated before
any argument that could carry one.

Hard constraints honoured: the rocks are untouched; the Pi was not touched; no MCP server was
started for this record (the measurement is `grep`/`Read` only); nothing below is built.

## 0. TL;DR

- **Measured tonight: the dev tooling on the box is ~1.3 GB resident + ~0.2 GB swap, in a
  2.5 GB set of cgroups** — one Claude Code remote session (0.65 GB RSS, 1.28 GB cgroup with
  its file cache), the shared Serena (0.25 GB), a Codex cross-review in flight inside the
  Omnigent container (0.17 GB on top of Omnigent's own 0.12 GB), Multica, the GitHub runner.
  That is **with the code-intel servers idle**: this session never called Serena or
  codebase-memory, and codebase-memory's two capped scopes sit at 1.7 MB each. On 2026-10-03,
  with code-intel in use, the same session shape measured **0.85–0.95 GB resident + 0.77 GB
  swap** — the "~1 GB per session" the question quotes is confirmed by two measurements, and it
  is the *floor*, not the ceiling (§1.3).
- **The box is roomier tonight than on 10-03, and not because of anything in this record:**
  MemAvailable 2.4–3.3 GB versus 0.42–0.56 GB, because the box was rebooted at 14:36 today and
  **zram is off** (`nvzramconfig` disabled, swap is the NVMe file only, 1.4 GB used) — the
  profile's reclaim item 5, executed [live, inf]. The W3 gate (≥ 2 GB with the voice stack
  resident, with *and without* a session) is now in reach, which makes the agent-session
  decision the swing factor rather than a rounding error.
- **Every recorded RAM incident on this box since July is an agent-tooling incident** (§1.4):
  19 `ccd-cli` processes + 3.59 GB swap (07-06), six per-session Serenas OOM-ing the host at
  65 MB free (07-16), the brain's CUDA crash loop from test lanes (07-19), 14 codebase-memory
  instances at 1.27 GB (08-02), 19 leaked Omnigent runners at 0–245 MB free (08-04). Each was
  fixed as a class (shared Serena, capped scopes, swap-denied voice stack, reaper timers). What
  none of those fixes bound is the thing the question asks about: **how many engineering
  sessions run on the product box at once, and whether any has to.**
- **Verdict on the Pi 5: no.** It is possible (every binary involved already runs on aarch64 —
  the Jetson proves it) and it would free the same ~1–1.7 GB on the Jetson, but the Pi is
  the *latency* surface: the wake/endpointing daemon (openWakeWord + Silero + speaker-ID), the
  barge-in monitor (≤ 300 ms fire time), the face embedder, the Chromium kiosk and the AirPlay-2
  receiver (`shairport-sync` + `nqptp`, a clock-sync daemon) all live there, with about 1.3 GB
  in use and a third of one core busy at rest. A Serena cold index pinned two Jetson cores at
  92 % / 58 %; the same on a Pi 5 is a wake-word miss and a drifting AirPlay clock. It would also
  mean a second device holding the Claude/Codex/gh credentials, and worktrees + indexes + swap on
  an SD card the infra audit already calls "weak". The owner's instinct ("I don't like the
  idea") is right; the reason is not RAM, it is that the Pi's job is to be idle.
- **Recommendation: B with A's guard.** Jason's interactive sessions move to his laptop /
  desktop (and Claude Code / Codex cloud sessions for PR-only work); the Jetson keeps exactly
  what cannot leave it — the self-hosted runner (deploy gate, tree-bound voice gate, restarts),
  the nightly voice probe, the landing scripts in `~/.zoe/agent-tools`, and read access to the
  live palace — behind SSH. Whatever *still* runs on the box (the builder lane, a landing
  script, an emergency session) runs inside one `zoe-agents.slice` with `MemoryMax` +
  `MemorySwapMax=0` and a non-blocking lease, so the worst case is **one** capped session, never
  a fleet. Self-evolution is preserved by design: the builder lane is a lease holder like any
  other, wakes only for a ticket, is capped, and excludes itself from the brain window with the
  same lock the voice probe uses (§3.4). RAM returned to the Jetson: **~1–1.7 GB for every hour
  nobody is editing**, plus the removal of the fleet failure class by construction.

## 1. Measured — what agent tooling costs on this box tonight (read-only)

Two samples, 20:05 and 20:11 AWST, 2026-10-04. Host up 5 h 35 m (rebooted 14:36). One Claude Code
remote session was live throughout (it took the measurement), a Codex cross-review dispatch was
running inside the Omnigent container, and a voice-PR landing chain (`land_voice_pr.sh 1827`) was
mid-flight, which restarted Kokoro and zoe-data between the samples — so the product numbers
are quoted as ranges and the dev numbers are the steadier of the two. Method = the 10-03
profile's: `VmRSS` / `VmSwap` from `/proc/<pid>/status`, `memory.current` / `memory.swap.current`
from the unit's or container's cgroup. On Tegra `VmRSS` includes NvMap (GPU) pages and the cgroup
figure does not, which is why the brain's RSS and cgroup differ.

### 1.1 Host

| | 2026-10-03 (profile) | **2026-10-04 20:05–20:11 [live]** |
|---|---|---|
| MemTotal | 15,655 MB | 15,655 MB |
| MemAvailable | **0.42–0.56 GB** | **2.38–3.29 GB** (`free` 2,378–2,507 MB; `/proc/meminfo` 3,293 MB during the Kokoro restart) |
| swap used | 2.1–2.7 GB of 53 GB (zram 8 × 244 MiB full + NVMe file) | **1.39–1.41 GB** of 51.2 GB, **NVMe `/swapfile` only** — `lsmod` shows no zram, `nvzramconfig.service` inactive/disabled, no `/sys/block/zram*` |
| Mlocked / Unevictable | 1.95 / 1.99 GB | 1.95 / 1.99 GB (the brain's GGUF) |
| swappiness | 10 | 10 |
| uptime | 53 d | 5 h 35 m |

### 1.2 Per group

RSS and swap are the sum of the group's processes; "cgroup" is the enclosing unit's
`memory.current` / `memory.swap.current` (includes page cache the processes touched).

| Group | What was running [live] | RSS | swap | cgroup |
|---|---|---|---|---|
| **Claude Code CLI** (one remote session) | 2 × `ccd-cli 2.1.286` (381 + 172 MB — the session and its sub-agent), the remote-control `srv --serve` bridge (56 MB) + `--bridge` (2 MB), a `gh pr view` (33 MB), shells | **0.65 GB** | 33 MB | `session-72.scope` **1.28 GB / 41 MB** (also holds the two sshd sessions and the landing-script shells) |
| **codebase-memory-mcp** | 2 host instances in capped `run-*.scope`s (one per CLI process), **idle** — this session never called them; 1 more inside the Omnigent container | 4 MB | 7 MB | 1.7 + 1.8 MB (on 10-03, *in use*: **506 MB RSS + 767 MB swap**, pinned at its 512M/768M cap) |
| **Serena** (shared, `serena-mcp.service` :9121) | `serena start-mcp-server` (204 MB) + `jedi-language-server` (46 MB); plus the root `serena_bridge_proxy.py` for the container (14 MB) | **0.26 GB** | 0 | **256 MB / 0**; caps in force `MemoryMax=2G`, `MemorySwapMax=0`, `MemoryLow=0`, 0 restarts since boot |
| **Codex** | none on the host. Inside `zoe-omnigent`: `codex app-server` (126 MB) + node wrapper (10 MB) + `codex-code-mode-host` (1 MB) + `omnigent.claude_native_bridge serve-mcp` (34 MB) — a polly/Codex cross-review kicked by `omnigent host` 11 min earlier | **0.17 GB** | 24 MB | (in the Omnigent container below) |
| **Language servers** | `jedi-language-server` only (counted under Serena). No pyright, no tsserver running | — | — | — |
| **node** | dev: the Codex node wrapper (10 MB). Product, for contrast: `flue-zoe-brain-2x` 114 MB, `flue-zoe-telegram` 107 MB, `ytmusic-potoken` 30 MB + 80 MB swap | 0.01 GB dev | — | — |
| **Omnigent** (`zoe-omnigent` container, uid 1000, **no memory cap**) | `omnigent server` (90 MB + 58 MB swap) + `omnigent host` (33 MB + 49 MB swap) + the Codex dispatch above | **0.12 GB** (+ 0.17 Codex) | 0.11 GB | **648 MB (291 MB anon) / 147 MB** — on 10-03, idle: 332 / 197 |
| **Multica** (board) | `zoe-multica-backend` + `-web` containers; 5 idle `postgres: zoe multica` backends (42 MB) | 0.04 GB | 0.04 GB | 15 + 10 MB / 5 + 31 MB |
| **GitHub runner** (`github-runner.service`) | `Runner.Listener` (68 MB + 16 MB swap) + `run.sh` / `run-helper.sh`; a job was active (the landing chain) | 0.07 GB | 0.02 GB | **266 MB / 24 MB**, no cap |
| **git worktrees** | `git worktree list` → **102** registered worktrees (the live checkout + agent worktrees + `~/.worktrees`) | — | — | disk, not RAM; each is a candidate stale `.mcp.json` (the 07-20 fleet trap) |
| **Dev tooling total** | | **≈ 1.3 GB** | **≈ 0.2 GB** | **≈ 2.5 GB** of cgroup memory |

Product side, for scale [live]: `llama-server` brain 6.45 GB RSS / 6.51 GB cgroup
(`MemoryLow=6G`, `MemorySwapMax=0`); Kokoro 2.08 GB RSS / 2.35 GB cgroup (`3G/4G/0`); zoe-data
1.38 GB RSS / 2.06 GB cgroup just after its restart (`2G/∞/0`); FunctionGemma router 0.65 GB
cgroup (`768M/1G/0`); Flue brain + Telegram sidecars 0.13 + 0.07 GB; Home Assistant 0.16 GB +
0.28 GB swap; Music Assistant 0.48 GB + 0.51 GB swap. The voice stack is swap-denied; the dev
tooling is not, except the capped codebase-memory scopes and the shared Serena.

### 1.3 What one session costs, honestly

| Session shape | Resident | Swap | Source |
|---|---|---|---|
| Claude Code remote session, code-intel **idle** (tonight) | 0.65 GB | 0.03 GB | §1.2 [live] |
| Claude Code session, code-intel **in use** (one `codebase-memory` at its cap) | 0.85–0.95 GB | 0.77 GB (zram then; NVMe now) | profile 10-03 `:220-233` |
| + the shared Serena when any client is attached | +0.23–0.26 GB | 0 | both |
| + a Serena that has leaked for an hour (it "leaks to >1 GB roughly hourly, 65 recycles in 3 days") | up to +1 GB before the cap recycles it | 0 | state review 09-25 `:156` |
| A second session | another 0.65–0.95 GB + its own codebase-memory (0.5 GB + 0.77 GB swap) | | by construction (stdio-only, one per client, `scripts/AGENTS.md:45`) |

So the honest per-session number is **0.65 GB idle → ~1.2 GB working → ~2 GB with a leaked
Serena**, and it multiplies by the number of sessions because codebase-memory cannot be
shared. The only fleet-wide bound today is Serena's `MemoryMax=2G`; nothing bounds the number
of `ccd-cli` or `codex` processes ("Residual honesty: nothing caps ccd-cli session *count*
itself", `samantha-evolution-plan.md:435`).

### 1.4 The incident ledger — "we keep hitting RAM issues", itemised

| Date | What | Numbers | Class fix that followed | What it did **not** bound |
|---|---|---|---|---|
| 2026-07-03 | Serena swap exhaustion | 2.1 GB of swap from one bloated language server | `MemorySwapMax` on the capped scope | session count |
| 2026-07-06 | `ccd-cli` fleet | **19** host-side Claude Code processes, ~1 GB RSS + **3.59 GB swap**, "the largest non-model memory owner on the box" (`memory-pressure-profile.md:135-138`) | drained by turnover; W3.1 closed 07-19 | session count |
| 2026-07-16 | per-session Serenas OOM the host | 6 agents × 2 GB cap = 12 GB; **65 MB free, 11 GB swap, live voice 2.2× slower**, two Serenas at 92 % / 58 % CPU re-indexing (`serena-mcp.service` header) | one shared `serena-mcp.service` (#1400) | the other per-session server |
| 2026-07-19 | brain CUDA-OOM crash loop | `NvMapMemAllocInternalTagged: error 12`; burst from the **test lanes**; "cgroup guards protect CPU pages only — they do nothing for NvMap/CUDA" (`incident-runbook.md:194-203`) | voice-stack `MemorySwapMax=0` + `MemoryLow` (#1409); brain-window lock | GPU-touching harnesses run by a session |
| 2026-07-20 | stale per-worktree stdio Serena configs | ~91 worktrees each able to spawn a ~1 GB Serena at connect time | swept; "recheck `.mcp.json` when resurrecting an old worktree" | the 102 worktrees that exist tonight |
| 2026-08-02 | codebase-memory fleet | **14** instances, 1.27 GB, one resident 2.3 days; same afternoon load 55, 45 GB swap (`scripts/AGENTS.md:45`) | `codebase_memory_capped.sh` (512M/768M/768M → equal high/max per A6) | instance count (one per client, by the tool's design) |
| 2026-08-04 | leaked Omnigent runners | **19** resident, box at **0–245 MB available**, host daemon refused to come online, two reviews killed (`omnigent-cross-review.md:149-152`) | synchronous `stop_session` + hourly reaper | the container itself (no cap) |
| 2026-09-30 → 10-03 | nightly replay gate | skips below 700 MB available; timed out four nights running | — | — |

Reading: the class fixes work (tonight's fleet is one session, one Serena, two idle scopes),
but each one bounded a *component*. The unbounded variable in every row is the same: more than
one engineering process on the product box at the same time, with nothing refusing the second.

### 1.5 Where sessions already run from

The session that took this measurement is **not a terminal on the Jetson**. It is a Claude Code
*remote* session: `~/.claude/remote/srv --serve` runs on the box and the UI is on Jason's
desktop app, with `ccd-cli` spawned by that bridge (not by a shell) [live]. Two `sshd` sessions
from the LAN were also present. So the *interface* is already off-box; only the *compute* is
on it. That matters for option A: a lease in a shell wrapper would never see these sessions,
and a Claude Code hook cannot take it for them either — by the time any hook runs, the bridge
has already spawned `ccd-cli` outside any slice (§3.1). The enforcement point has to be the
thing that *launches* the CLI.

No Tailscale is installed [live]; the LAN, SSH and the Cloudflare tunnel container
(`zoe-cloudflared`, used for Omnigent behind Access) are the paths in. The panel is reached from
the box by SSH tunnels (`127.0.0.1:9222/9223` for the browser-verify instrument).

## 2. What must stay on the box, whatever is chosen

The PR pipeline already separates *editing* from *deploying*; the box is only load-bearing at
the end of the chain.

| Stage | Where it runs [src] | Needs the Jetson? |
|---|---|---|
| Editing, tests (`validate.yml`, `pr-hygiene.yml`, `secret-scan`) | `ubuntu-latest` on GitHub | **No** — the `ci_safe` marker set runs in a slim venv with no network |
| Greptile gate (`greptile-gate.yml`), Codex PR review, polly cross-review | GitHub + the Omnigent container | Greptile/Codex: no. polly: wherever the Omnigent container lives (today: the Jetson, §3.4) |
| `voice-gate.yml` `scope` + `verdict` | `ubuntu-latest` | No |
| `voice-gate.yml` `replay-evidence` | `runs-on: self-hosted` (`:218`) — reads the replay artifact that only the box can produce; checks out the **base** SHA, never the PR head (`:233-240`) | **Yes** (read-only, 10-min timeout) |
| The replay probe itself (`voice_regression_probe.py`, nightly `zoe-voice-regression.timer`, or hand-run under `flock /tmp/zoe-voice-harness.lock`) | the box: Moonshine + the brain + Kokoro | **Yes** — this *is* the box |
| `deploy.yml` | `runs-on: self-hosted` (`:14`): memory-headroom gate `THRESHOLD_MB=250` (36 × 15 s), `flock /tmp/zoe-deploy.lock`, `voice_gate_check.py --expect-tree-of <sha>`, `git reset --hard`, Alembic, `docker compose up -d --build zoe-auth`, `systemctl --user restart zoe-data`, Flue sidecar rebuilds | **Yes** |
| `self-hosted-tests.yml` | self-hosted, nightly 16:30 UTC, reporting-only, 20-min cap | Yes, by design (it exists to exercise suites that import the live services) |
| Landing scripts `~/.zoe/agent-tools/{land_voice_pr.sh,land_queue.sh,queue_after.sh}` | the box (they run the probe, watch the PR, merge, watch the deploy) | **Yes** as long as they run the probe; the *watching* half could run anywhere with `gh` |
| Live palace reads (Postgres `zoe-database`, Chroma under `~/.zoe`) for an investigation | the box | Yes for direct reads; the HTTP API and `ssh zoe` cover it |
| Panel verification (`estate_browser_verify.py` over CDP to the Pi) | tunnels from the box today | No in principle — a laptop on the LAN can tunnel to `zoe-pi` directly |
| Code-intel (Serena, codebase-memory) | wherever the editing session is | **No** — a clone on the laptop indexes itself; the Jetson's shared Serena serves only on-box clients |

The merge-and-deploy doctrine is already "merged to `main` auto-deploys; the live checkout is
pinned to `main`" (`merge-and-deploy.md`), and the W3 profile records the deploy gate as the one
thing that refuses to restart the brain under pressure. Nothing in that chain needs the editor
to be on the box.

## 3. The options, in the owner's terms

### 3.1 A — one session at a time on the Jetson

**What it is.** Sessions keep running on the box, but the box refuses a second one and bounds
the first.

- **The lease — taken by the launcher, not by a hook.** A non-blocking `flock` on
  `/run/user/1000/zoe-agent-session.lock` (the pattern the voice harness, the deploy step and
  `cross_review.sh` already use [src]), held for the session's lifetime by whatever process
  **exec's the CLI**: a `zoe-agent` launcher script (`flock -n … systemd-run --user
  --slice=zoe-agents.slice --scope -- claude|codex …`) for shell-launched sessions, and a
  managed **user unit for the remote bridge** (`Slice=zoe-agents.slice`, `ExecStart=flock -n
  <lock> … srv --serve …`) so every `ccd-cli` it spawns inherits the slice and the held lock.
  Why not a Claude Code hook: per the hook reference, `SessionStart` is context-only — it can
  add context and warn but has no blocking decision — and by the time it runs the bridge has
  already spawned `ccd-cli` outside any slice; a `systemd-run` from the hook would only place a
  *new child* in the slice, not the session [doc, inf]. What a `SessionStart` hook **can** still
  do, and should: print an advisory ("another session holds the lease since 19:40") and tag
  the sampler (§5) so unlaunchered sessions are counted. A bridge launched by hand around the
  unit remains possible; it is the thing the sampler's "`ccd-cli` outside the slice" column
  exists to catch, and the slice's cap does not cover it.
- **The slice.** A `zoe-agents.slice` (user) with `MemoryHigh=MemoryMax` (the A6 lesson:
  a throttle band under a cap is permanent refault, `scripts/AGENTS.md:45`) and
  **`MemorySwapMax=0`** — "two out of three is not a cap" (`incident-runbook.md §6`). Processes
  enter it only at launch (the launcher's `--scope`, the bridge unit's `Slice=`); today the
  bridge is a plain process under the login scope [live], which is exactly why it needs the
  unit. The shared Serena stays in its own unit (already capped); the codebase-memory scopes are
  moved under the slice by the capping wrapper. Values: `MemoryMax=2G` fits one working session
  with a leaked Serena (§1.3); `3G` if two must briefly overlap for a hand-over.
- **The trap the memory notes name.** cgroup guards cover CPU pages only; a session that runs a
  GPU-loading harness (a `measure_voice.py --stt inprocess`, a second Kokoro) is the 07-19 crash.
  The answer is **not** to tie the session lease to the brain-window lock
  (`/tmp/zoe-voice-harness.lock`): an idle open session would then block the nightly replay
  gate and every voice-PR landing for as long as it stays open, turning RAM skips into lock
  skips. The two locks stay separate: the session lease counts sessions; the harness lock is
  taken **only around model-loading commands** (the probe, `measure_voice.py`, a Kokoro load —
  which already run under `flock /tmp/zoe-voice-harness.lock` by rule) and around the builder's
  whole run (§3.4). A session that cannot get the harness lock for such a command waits or
  skips that command, not the session.
- **RAM freed on the Jetson:** 0 at steady state while a session is on; it converts "N sessions"
  into "1 capped session" — the fleet failure class goes away, the per-session cost does not.
- **Self-evolution:** preserved; the builder lane is one more lease holder.
- **Effort / cost / risk:** one small PR (slice template, launcher, bridge unit, advisory hook,
  sampler); $0; risks: a bridge or CLI started by hand outside the launcher is uncounted and
  uncapped (the sampler flags it, nothing refuses it), a `MemoryMax` that OOM-kills a session
  mid-PR, and Jason waits when the builder holds the lease (or vice versa).

### 3.2 B — engineer from another machine; the Jetson is the deploy target

**What moves off:** the Claude Code / Codex CLIs, their Serena + codebase-memory (each ~1 GB,
trivial on a laptop), the worktrees (a clone on the laptop; the 102 Jetson worktrees become
cleanup), pushes and PR driving (`gh` runs anywhere), Greptile/Codex review loops, panel
verification (tunnel from the laptop to `zoe-pi`).

**What stays (from §2):** the self-hosted runner and both gates, the nightly probe, the landing
scripts (or their probe half), the live palace, the Omnigent container until §3.4 moves it.

**Three flavours, not one:**

| Flavour | Reach to the box | Good for | Not for |
|---|---|---|---|
| **Laptop / desktop on the LAN** (Claude Code + Codex locally, `ssh zoe` for inspection, `ssh zoe 'flock … voice_regression_probe.py'` to produce a replay artifact) | full, over SSH | everything Jason does today, including voice-path PRs (the probe is one SSH command away) and palace investigations | nothing — this is the default lane |
| **Claude Code cloud sessions** (claude.ai/code: the repo is cloned into an isolated VM, a branch is pushed for review; "available on Pro, Max and Team plans" [doc]) and **Codex cloud tasks** (a container per task, repo preloaded, PRs proposed; on paid ChatGPT plans [doc]) | **none** — no inbound path to the box; network limited by default | docs, unit-tested code, flag-dark backend work, the builder lane's L0–L1 skill tickets (§3.4), parallel PRs that keep running after the laptop closes | anything needing the replay probe, the palace, the panel, or a Jetson-only dependency (CUDA, Tegra numpy); voice-path PRs still need a box-produced artifact — which `deploy.yml` enforces regardless |
| **Remote bridge as today** (desktop UI, compute on the box) | full | an emergency session when the laptop is away | the default — it is exactly the 1 GB the question is about |

**Connectivity.** On the LAN: SSH, already in use [live]. Off the LAN: nothing today (no
Tailscale [live]); either Tailscale on the Jetson + laptop (operator, ~minutes, no repo change)
or Cloudflare Access for SSH on the existing tunnel. Latency is irrelevant for editing (local)
and milliseconds for the SSH-run gates. The landing scripts need one change: a `ZOE_HOST` /
`ssh zoe` wrapper so `land_voice_pr.sh` can be driven from the laptop while the probe step runs
on the box.

**RAM freed on the Jetson:** the whole §1.3 cost for every hour no session is on — **~0.65 GB
(idle) to ~1.2 GB (working) to ~2 GB (leaked Serena)**, plus Serena's 0.26 GB when no client is
attached, plus 0.77 GB of swap. The 10-03 profile's no-session estimate was MemAvailable
1.5–1.7 GB *before* zram-off; tonight's 2.4–3.3 GB *with* a session says a no-session box is
likely at **3–4 GB available** [inf, to be measured in §5].

**Self-evolution:** unchanged for the builder; it does not depend on where Jason's editor runs.

**Effort / cost / risk:** medium-low (a laptop runbook: clone, `.mcp.json` pointing at a local
Serena, `gh auth`, `ssh zoe`, the tunnel recipe to the Pi; the landing-script wrapper; the
102-worktree sweep); $0 marginal — the plans already paid for (Claude Max, ChatGPT) include the
cloud sessions [doc]; risks: two code-intel indexes that drift (harmless — each indexes its own
clone), voice PRs that forget the probe (the deploy gate catches them, fail-closed), and the
habit itself ("I'll just open one on the box").

### 3.3 C — the touch-screen Pi 5 (the owner's idea)

**What the Pi is [src, doc]:** Raspberry Pi 5, **8 GB**, Bookworm, hostname `zoe-touch`, user
`pi` (`runtime-topology.md:62`, `inference-speech-stack-2026-10-03.md:169`). Last read:
5.65 GB of 8 GB free (`samantha-evolution-plan.md:504`), 6.6 GB available on 2026-10-04
(`barge-in-duck-decide-resume-2026-10-04.md:304`), "roughly one third of ONE core of four" busy.
It runs:

- `zoe-voice.service` (`--user pi`): openWakeWord 0.6.0, **Silero VAD via `torch.hub` (torch
  2.11)**, the endpointer, the resemblyzer speaker-ID shadow score (540 ms median, holds one core
  per turn), the barge-in monitor (fire ≤ 300 ms, phase 1 duck→decide→resume landed today in
  #1830), the announcement poller;
- the face pipeline — "Panels detect, liveness-check, and embed faces locally; only the
  resulting vector is POSTed" (`biometric-retention-policy.md:43-44`), camera = the PanaCast
  that shares USB power with the Jabra;
- `zoe-kiosk.service` → `chromium-browser --kiosk` loading the 4,550-line estate page;
- `shairport-sync` + `nqptp` (the AirPlay-2 receiver and its PTP clock daemon; the panel is a
  live AirPlay-2 speaker since 2026-07-23);
- the provisioning server / wifi portal.

**Could it run the sessions?** Technically yes: Claude Code, Codex (`codex-linux-arm64`), Serena
(Python), `codebase-memory-mcp` (a static aarch64 ELF) and even `omp-linux-arm64` all run on
aarch64 — the Jetson is the proof [live, src]. RAM-wise a single session (~1.2 GB working) fits
in 6.6 GB. A worktree on the Pi could run the Jetson gates over SSH exactly as a laptop would.

**Why it is the wrong host anyway:**

1. **The Pi's product is latency, and sessions are bursty CPU.** A Serena cold index pinned two
   Jetson cores at 92 % / 58 % (`serena-mcp.service` header); codebase-memory indexing this repo
   drove ~2k major faults/s for its whole life under the old cap (`scripts/AGENTS.md:45`);
   `npm ci`, `pytest`, `git` on a 100-worktree repo are the same shape. On a four-core Pi 5
   that is a missed wake word (the daemon's VAD/wake loop is real-time), a late barge-in, a
   stuttering kiosk, and an AirPlay clock that `nqptp` cannot hold — the exact surfaces the
   last three months of panel work tuned. The 09-27 note says "any new model goes on the Pi"
   because its cores are *idle*; a builder burst spends that idleness on the wrong thing.
2. **It becomes a second thing to protect.** The Jetson's RAM discipline (swap-denied voice
   units, capped scopes, reapers, the brain-window lock) does not exist on the Pi; it would have
   to be rebuilt there, for a device whose daemon already loads torch.
3. **Credentials on a second device.** Claude/Codex OAuth, `gh`, the tunnel route — today's
   policy already flags the container's subscription OAuth as the top operational risk
   (`omnigent-container-config.md`); copying it to a wall panel in a public room is worse.
4. **Storage.** The infra audit calls the panel's SD card "weak" (`infra-data-config-2026-10-03.md:149`);
   worktrees, `node_modules`, two code-intel indexes and swap are write-heavy. Whether this Pi
   boots from SD or NVMe is **not recorded in the docs** — stated as unknown, not assumed.
   Thermal: a Pi 5 under sustained all-core load throttles without active cooling [unverified
   for this unit; the enclosure is not documented].
5. **It frees exactly what B frees, for more risk.** The Jetson gain is the same ~1–1.7 GB; the
   laptop has no product on it to break.

**Verdict: no.** Not for interactive sessions, not for the builder lane (the self-building
record reached the same "no" for the builder, §5 option B there). If a 24/7 off-Jetson host is
wanted so the builder does not depend on a laptop lid, the answer is a cheap dedicated box (a
used mini-PC, or the "DGX Spark" direction the self-building record notes), not the panel.

### 3.4 D — Zoe's self-evolution path under each option

**Where it runs today [live, src]:**

| Agent | Host | Cost tonight | State |
|---|---|---|---|
| **Omnigent** server + host (`zoe-omnigent`, uid 1000, mounts the live repo rw, no memory cap) | Jetson, Docker | 0.12 GB RSS + 0.11 GB swap idle; container 0.65 GB with a dispatch in flight | up 6 h; roster `claude`, `claude-sdk`, `codex`, `pi` (OpenRouter GLM-5.2), `cursor`; subscription OAuth, Claude refresh token due **2026-10-11** |
| **polly** cross-review (`cross_review.sh`, `claude-sdk` harness + a different-vendor sub-agent; one worker repo-wide under `flock`) | inside the container | +0.17 GB for the Codex side tonight; historically up to ~1 GB per leaked runner | kicked by the landing scripts; advisory tier |
| **Multica** board (`zoe-multica-backend/-web`) | Jetson, Docker | 0.03 GB | up; dispatch kill switch `~/.zoe/multica_dispatch_paused` armed since 2026-08-04 |
| **Executors**: `flue-executor.service` (template, inert), `multica_board_runner.py --loop` (hand-run), `omnigent_issue_executor.py` (`ZOE_USE_OMNIGENT_EXECUTOR=0`) | Jetson, user units / hand-run; the harness modules live *inside zoe-data's cgroup* (`MemorySwapMax=0`) | ≲ 10 MB resident; the burst (git/pytest/agent CLI) lands on the voice path's cgroup | not running |
| `pi` harness | OpenRouter (cloud tokens) | 0 local | tie-breaker only, capped |

**The design that keeps self-evolution working under A or B — the builder lane:**

- A user unit `zoe-builder.service` (the self-building record's PR 6 names it), **outside
  zoe-data's cgroup**, `Slice=zoe-agents.slice`, `MemoryHigh=MemoryMax=1500M`,
  `MemorySwapMax=0`, `Nice=10`, `OOMScoreAdjust=500` (the Serena unit's dev-tooling values).
- **Wakes only for a ticket:** a `zoe-builder.timer` every 10 min runs the single-lane runner
  once; it exits 0 immediately when the queue is empty (the existing dry/full dispatch and
  SINGLE LANE guards in `flue-executor.service` / `multica_board_runner.py`).
- **Locks in `ExecStart`, checks in `ExecCondition`.** The two locks are acquired by wrapping
  the runner itself — `ExecStart=flock -n /run/user/1000/zoe-agent-session.lock flock -n
  /tmp/zoe-voice-harness.lock <runner> --once` — so the lock file descriptors belong to the
  builder process and live exactly as long as it does. They cannot be taken in `ExecCondition`:
  that command exits before `ExecStart` runs and its `flock` descriptors close with it, so the
  builder would start with nothing held. `ExecCondition` keeps the **point-in-time** checks
  only: `MemAvailable ≥ 1.5 GB` (read from `/proc/meminfo`, the deploy gate's method) and not
  inside the nightly replay window (`zoe-serena-pregate-restart` at 04:15 → the probe). A
  builder that fails a condition or a non-blocking lock exits at once and logs
  `BUILDER_SKIP reason=`; the timer tries again — the zero-effect blind spot
  (`incident-runbook.md §7`) is covered by the sampler in §5 counting skips.
- **Never coexists with a brain window:** because the harness lock is held by the runner for
  its whole run, a hand-run probe, a landing script's probe step and the nightly gate exclude
  it, and it them — and because it holds the harness lock only while a ticket actually runs,
  an idle builder never blocks the probe.
- **The Omnigent container gets the same three-part cap** via compose (`mem_limit` +
  `memswap_limit` equal to `mem_limit`, which is Docker's "no swap"), ~1.5 GB, so a leaked
  runner is OOM-killed in the container rather than paging the box (the 08-04 class).
- **Under A:** the builder is one more lease holder on the Jetson; Jason's session and the
  builder take turns. The container stays. RAM freed: none beyond the cap.
- **Under B:** the builder's *tickets* that need nothing from the box (L0–L1 skills, docs,
  flag-dark backend) can run as cloud sessions or on the laptop's Omnigent (the self-building
  record's §5 option C: a compose re-point, the credential volumes, the Access route); the
  Jetson keeps a capped builder for tickets that need the probe or the palace. Sequence as that
  record says: Jetson-with-cap for the first proof skill, laptop/VM after two skills.
- **Under C:** no (§3.3).

What this costs the Jetson at steady state: the container's ~0.12 GB + 0.11 GB swap idle
(reclaim item 3 says idle-reap it for ~0.05 GB anon; not worth the cold start while reviews are
routine), and up to the 1.5 GB cap *only while a ticket runs*, which by `ExecCondition` is only
when the box can afford it.

## 4. Comparison and recommendation

| Option | RAM freed on the Jetson | Self-evolution preserved? | Operator effort | Monthly cost | Risk |
|---|---|---|---|---|---|
| **Keep as is** | 0 | yes | none | $0 | the §1.4 ledger repeats; nothing refuses a second session; the W3 gate stays hostage to habit |
| **A. One at a time (lease + slice)** | 0 while a session is on; caps the worst case at one session (~1–2 GB) instead of a fleet | yes (builder = lease holder) | low: 1 PR (slice, launcher, bridge unit, advisory hook, sampler) + `systemctl --user` install | $0 | a CLI or bridge started by hand bypasses the launcher (counted, not refused); OOM-kill mid-PR at the cap; serial waits |
| **B. Laptop / desktop + cloud sessions, Jetson = deploy target** | **~0.65–2 GB per avoided session** (+0.26 GB Serena, +0.77 GB swap) — i.e. the box runs at its no-session floor whenever nobody is editing | yes (builder stays capped on the box, moves later) | medium-low: laptop runbook, `ssh zoe` wrapper for the landing scripts, optional Tailscale, worktree sweep | $0 marginal (cloud sessions are in the existing Max / ChatGPT plans [doc]) | habit regression; voice PRs still need a box-run probe (deploy gate enforces); off-LAN access is an operator step |
| **C. The Pi 5** | same as B | technically | high: second device with credentials, no RAM discipline there, SD-card and thermal unknowns | $0 | the voice/kiosk/AirPlay latency surface takes the bursts; a second device to protect |
| **B + A (recommended)** | B's gain **and** a hard bound on whatever still runs on the box | yes, by design (§3.4) | A's PR + B's runbook | $0 | the union, each mitigated by the other |

**Recommendation: B as the default, A as the guard.** Jason's interactive engineering moves to
the laptop/desktop (LAN SSH to the box for the probe and the palace; cloud sessions for PR-only
work). The Jetson becomes the deploy target plus a *capped* builder lane. Everything that can
still run on the box — the builder, the landing scripts, an emergency remote session — runs in
`zoe-agents.slice` with the three-part cap and the lease, so "we keep hitting RAM issues" has a
structural answer rather than a habit: the box can hold **at most one** capped engineering
process at a time, and usually holds none. The Pi stays the panel.

This is also the cheapest route to the W3 gate as the 10-03 profile re-stated it (≥ 2 GB
available with the voice stack resident, measured with *and without* a session): tonight's
2.4–3.3 GB with a session suggests the no-session box is already there after zram-off, and B
makes "no session" the normal state rather than a measurement condition.

## 5. Flags, units and the measurement plan

**No `ZOE_*` product flag is involved** — this is operator tooling. The pieces (all templates,
all operator-installed, none auto-enabled, the `scripts/setup/systemd/README.md` rule):

| Piece | Kind | Does |
|---|---|---|
| `scripts/setup/systemd/zoe-agents.slice` | new user slice | `MemoryHigh=MemoryMax=2G`, `MemorySwapMax=0` — the fleet-wide bound for everything engineering-shaped on the box |
| `scripts/maintenance/zoe-agent` launcher | script | `flock -n /run/user/1000/zoe-agent-session.lock` then `systemd-run --user --slice=zoe-agents.slice --scope -- claude\|codex …` — the lock fd is held by the launcher for the session's lifetime; a second launch is refused with the holder's name. The session lease only; it never takes the harness lock |
| `scripts/setup/systemd/zoe-claude-bridge.service` | new user unit (inert) | the remote-control bridge (`srv --serve`) as a managed unit: `Slice=zoe-agents.slice`, `ExecStart=flock -n <session lock> … srv --serve`, so every `ccd-cli` it spawns inherits the slice and the held lease |
| `.claude/settings.json` `SessionStart` hook | config | **advisory only** (the hook has no blocking decision): prints who holds the lease, tags the session in the sampler's log; cannot refuse or move a session |
| `scripts/setup/systemd/zoe-builder.service` + `.timer` | new user unit (inert) | §3.4: single-lane ticket runner, `Slice=zoe-agents.slice`, 1.5 GB cap; **`ExecStart` wraps the runner in both `flock -n`s** (session lease + `/tmp/zoe-voice-harness.lock`, held for the run); `ExecCondition` only for the point-in-time checks (`MemAvailable ≥ 1.5 GB`, replay-window schedule) |
| `modules/omnigent/docker-compose.module.yml` `mem_limit` / `memswap_limit` | compose | the container's three-part cap (~1.5 GB, no swap) |
| `scripts/setup/systemd/zoe-agent-mem.timer` + `.service` | sampler | every 5 min append to `~/.zoe-logs/agent-mem.tsv`: `MemAvailable`, swap used, `zoe-agents.slice` `memory.current`/`swap.current`, count of `session-*.scope`s, `ccd-cli` / `codex` / `serena` / `codebase-memory-mcp` process counts, lease holder, `BUILDER_SKIP` count since last sample, deploy-gate waits (from the runner log) |
| `docs/knowledge/engineering-off-box.md` | runbook | laptop setup, `ssh zoe` recipes (probe, palace read, panel tunnel), the landing-script wrapper, cloud-session do/don't list, the worktree sweep |

**Measurement — one week, with negative controls** (the `feedback_verify_your_instruments`
rule: break the fix and the test must go red):

1. **Baseline week (now, nothing installed):** the sampler alone. Expected: sessions present
   most evenings; MemAvailable bimodal (session / no session). This also answers the open
   question tonight's numbers raise — what the no-session floor is after zram-off.
2. **Guard week (A installed, sessions still on the box):** same sampler. Pass = the slice's
   `memory.current` never exceeds its cap, zero `memory.events oom` outside the slice, no
   deploy-gate "waiting for headroom" loops attributable to a session.
   - *Negative control 1:* launch a second session through `zoe-agent` while one holds the lease
     → it must be refused with the holder's name; start the bridge unit while a launcher
     session holds the lease → the unit must fail to start (`flock -n` exit 1), and a `ccd-cli`
     started by hand outside both must appear in the sampler's "outside the slice" column.
   - *Negative control 1b:* leave a launcher session open and idle, then run the nightly probe
     by hand under `flock /tmp/zoe-voice-harness.lock` → it must run (the session lease and the
     harness lock are separate); a model-loading command *from* that session must wait on the
     harness lock, not the session.
   - *Negative control 2:* inside the slice, allocate 2.5 GB in a throwaway `python3 -c` →
     the OOM kill must land on *that* process (`memory.events` of the slice increments), the
     brain's `MemoryCurrent` unchanged, `/health` green across the event.
   - *Negative control 3:* take `/tmp/zoe-voice-harness.lock` by hand and fire the builder timer
     → `BUILDER_SKIP reason=brain_window` must be logged and nothing dispatched; then, with a
     builder run in flight, `flock -n /tmp/zoe-voice-harness.lock true` must fail for the whole
     run and succeed the moment the runner exits (proves the lock lives in `ExecStart`, not in
     an `ExecCondition` that already returned).
3. **Off-box week (B in use, A still installed):** Jason's sessions from the laptop. Pass =
   MemAvailable p50 during waking hours ≥ 2 GB **with the voice stack resident**, the nightly
   replay gate stops skipping on the 700 MB floor, `ccd-cli` count on the box is 0 for ≥ 80 % of
   samples, and one voice-path PR lands end-to-end from the laptop (probe over SSH → artifact →
   deploy gate green).
4. **Then** re-run the 10-03 profile method once *without* any session as the W3 DoD
   measurement the plan asks for, and record it in `docs/knowledge/`.

## 6. Go / no-go against the VISION principles

- **Local-first, the house keeps working:** improved — the product box stops hosting the
  engineering fleet; nothing about Zoe's runtime moves.
- **The rocks are untouched:** yes; no brain, STT or TTS flag changes. The one brain-side lever
  the profile names (`--cache-ram`) stays a separate operator decision.
- **Self-evolution stays possible:** yes by design (§3.4) — and better bounded than today,
  where the executor's bursts would land inside zoe-data's swap-denied cgroup.
- **Voice first on the panel; the panel stays a panel:** the Pi is explicitly kept out of it.
- **Nothing ships dark here because nothing ships:** this record builds nothing; the pieces in
  §5 are inert templates, a launcher and an advisory hook, each a reviewed PR.

**Go**, on the B + A shape, pending the four decisions below.

## 7. Decisions for Jason

1. **Your sessions: laptop/desktop by default, with the Jetson reached over SSH for the probe
   and the palace?** (Yes = B. If you want sessions to keep running when the laptop is closed,
   use Claude Code / Codex cloud sessions for PR-only work; they cannot reach the box.)
2. **The Pi stays a panel — closed as "no"?** The reasons are latency, a second device holding
   your credentials, and its storage; the RAM gain is the same as the laptop's.
3. **Hard cap or polite refusal for whatever still runs on the box?** A `MemoryMax=2G` slice
   can kill a session mid-work when it leaks; a launcher lease only refuses at start, and only
   for sessions started through the launcher or the bridge unit. The recommendation is both
   (cap at 2 GB, lease in front), accepting the rare mid-work kill over the box paging.
4. **Builder lane: capped on the Jetson for the first proof skill, then to the laptop (or a
   cheap dedicated box) after two skills?** This follows the self-building record's own order;
   say if you would rather the builder never run on the Jetson at all, which means it waits for
   the laptop/VM before the first skill.

## 8. Next steps if GO

1. **PR 1 — the guard + the sampler** (templates only, inert): `zoe-agents.slice`, the
   `zoe-agent` launcher, `zoe-claude-bridge.service`, the advisory `SessionStart` hook,
   `zoe-agent-mem.timer`, a `ci_safe` test that the slice carries all three memory keys, that
   the launcher and bridge unit take the session lease in the exec path, and that the builder
   unit's `ExecStart` (not `ExecCondition`) wraps the runner in both `flock`s. Operator installs
   the sampler first (baseline week), the slice, launcher and bridge unit a week later.
2. **PR 2 — the builder lane's shape**: `zoe-builder.service/.timer` with the locks in
   `ExecStart`, the point-in-time `ExecCondition`s and `BUILDER_SKIP` logging, plus the
   Omnigent compose `mem_limit`/`memswap_limit`.
   Inert until the self-building record's PR 6 wires tickets to it.
3. **PR 3 — the runbook**: `docs/knowledge/engineering-off-box.md` + a `ZOE_HOST`-aware
   `land_voice_pr.sh` (probe over `ssh zoe`, the rest local), and the sweep of the 102
   worktrees (list, prune the merged ones, re-check every surviving `.mcp.json` for a stdio
   Serena).
4. **Operator**: laptop clone + local Serena/codebase-memory + `gh auth` + `ssh zoe`; Tailscale
   or Access-SSH if off-LAN sessions are wanted; run the three negative controls; after the
   off-box week, re-run the profile with no session as the W3 DoD record.
5. **Later**: move the Omnigent container to the laptop/VM after two skills have landed
   (self-building record §8 step 9); decide `--cache-ram` on the 24 h occupancy read; if the
   sampler keeps showing `ccd-cli` outside the slice, decide whether a hand-started bridge is
   tolerated or the bridge binary is only ever reachable through the unit.

## 9. Sources

Repo (read in this worktree): `docs/knowledge/memory-pressure-profile-2026-10-03.md`,
`docs/knowledge/memory-pressure-profile.md`, `docs/knowledge/incident-runbook.md` (§4–§7),
`docs/knowledge/omnigent-cross-review.md`, `docs/knowledge/omnigent-container-config.md`,
`docs/knowledge/runtime-topology.md`, `docs/knowledge/merge-and-deploy.md`,
`docs/knowledge/state-of-zoe-review-2026-09-25.md`, `docs/knowledge/biometric-retention-policy.md`,
`docs/knowledge/feature-audit-2026-09-25.md`, `docs/research/self-building-skills-2026-10-04.md`
(§2.5, §5, §8), `docs/research/inference-speech-stack-2026-10-03.md` (§1.7),
`docs/research/barge-in-duck-decide-resume-2026-10-04.md` (§5),
`docs/research/speaker-gate-rebuild-2026-10-04.md` (§2.5),
`docs/research/infra-data-config-2026-10-03.md` (A5, A6),
`docs/architecture/samantha-evolution-plan.md` (W3, §6a), `scripts/AGENTS.md:45`,
`scripts/setup/systemd/{serena-mcp.service,flue-executor.service,zoe-omnigent-runner-reaper.timer,README.md}`,
`scripts/maintenance/codebase_memory_capped.sh`, `.github/workflows/{deploy,voice-gate,self-hosted-tests,greptile-gate}.yml`,
`scripts/setup/touchscreen/README.md`.

Live (read-only, 2026-10-04 20:05–20:11 AWST): `ps -eo pid,rss,etimes,comm,args --sort=-rss`,
`/proc/<pid>/{status,cmdline,cgroup}`, `/sys/fs/cgroup/**/memory.{current,swap.current,stat}`,
`systemctl --user show <unit> -p MemoryCurrent -p MemoryMax -p MemorySwapMax -p MemoryLow`,
`systemctl show nvzramconfig.service`, `lsmod`, `/proc/swaps`, `/proc/meminfo`, `free -m`,
`docker ps`, `git worktree list | wc -l`, `ls ~/.zoe/agent-tools`.

Upstream [doc]: Claude Code cloud sessions —
[Get started with Claude Code in the cloud](https://code.claude.com/docs/en/web-quickstart),
[Choose a sandbox environment](https://code.claude.com/docs/en/sandbox-environments),
[Claude Code on the web (announcement)](https://anthropic.com/news/claude-code-on-the-web),
[Claude Code hooks reference](https://code.claude.com/docs/en/hooks) (`SessionStart` carries no
blocking decision — context and warnings only);
Codex cloud — [Using Codex with your ChatGPT plan](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan),
[Codex cloud environments](https://developers.openai.com/codex/cloud/environments).
