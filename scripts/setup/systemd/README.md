# Host-native systemd units

Zoe runs as a split stack: the database, auth, UI and Home Assistant run in
Docker (`docker-compose.yml`), while the latency-sensitive services run as
**user** systemd units directly on the host.

These are **templates**. They use `%h` (your home directory) so they work
without editing on most setups, but paths marked platform-specific
(llama-server binary, GGUF model, CUDA libs) must be adjusted for your machine.
Secrets are never inlined — they are read from `.env` files.

| Unit | Port | Purpose |
|------|------|---------|
| `llama-server.service`     | 11434 | Local LLM (Gemma 4 E4B-QAT+MTP via llama.cpp) — **platform-specific paths** |
| `kokoro-tts.service`       | 10201 | Local neural TTS sidecar |
| `zoe-data.service`         | 8000  | Primary backend API |
| `functiongemma-router.service` | 11436 | Two-stage router stage-2 decoder (FunctionGemma-270M r2, CPU) — **platform-specific paths**; optional |
| `flue-zoe-brain.service`   | 3578  | Flue Zoe-brain sidecar (optional, operator opt-in) |
| `flue-executor.service`    | —     | Multica queue consumer (executor migration Phase 2) — **optional, operator opt-in; ships inert (not enabled, dispatch defaults dry) — see below** |
| `serena-mcp.service`       | 9121  | Shared Serena MCP code-intelligence server (dev tooling, one per HOST — see below) |

Everything in this directory is a **user** unit. The `system/` subdirectory holds
the few that must run as **root** (`/etc/systemd/system/`) because they use
directives the user manager cannot enforce — today just
`serena-bridge.{socket,service}`. They are kept out of this directory precisely
so the install glob below can never drop one into the user manager, where its
access-control directives would be silently ignored.

| System unit | Port | Purpose |
|-------------|------|---------|
| `system/serena-bridge.socket` + `.service` | 9121 on `172.28.0.1` | Scoped proxy letting ONLY the `zoe-omnigent` container use the shared Serena — see below |

## Install

```bash
mkdir -p ~/.config/systemd/user
cp scripts/setup/systemd/*.service ~/.config/systemd/user/

# Edit llama-server.service for your binary + model path first:
#   ${EDITOR:-nano} ~/.config/systemd/user/llama-server.service

systemctl --user daemon-reload
systemctl --user enable --now llama-server zoe-data kokoro-tts
```

## Shared Serena MCP server (`serena-mcp.service`)

Dev tooling, not part of the voice stack — enable it on hosts where the agent
fleet runs. It replaces the old per-agent stdio spawn in `.mcp.json`: **one**
server, **one** index, and its `MemoryHigh=1G`/`MemoryMax=2G` now bound the
whole fleet instead of each member (6 agents × 2G = 12G was the 2026-07-16 OOM).

```bash
cp scripts/setup/systemd/serena-mcp.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now serena-mcp

# Verify — a listening socket is NOT enough; this drives a real MCP handshake:
scripts/maintenance/serena_mcp_health.sh          # -> HEALTHY ... (exit 0)
systemctl --user status serena-mcp
journalctl --user -u serena-mcp -f
```

Port **9121** is deliberate: Serena's `--port` **defaults to 8000, which is
zoe-data's production port**. Never run it on the default. It binds `127.0.0.1`
only — this server can read the whole repo and must never reach the LAN.

Claude Code does **not** auto-start a URL-based MCP server: if this unit is
down, every agent silently loses code intelligence. `Restart=always` covers
crashes; the health check covers the rest.

### Letting the omnigent container use the shared server (`system/serena-bridge.*`)

`zoe-omnigent` used to spawn its **own** Serena per agent session (a stdio entry
in `modules/omnigent/.mcp.json`), ~900 MB RSS each — pressure that starved the
deploy gate and contributed to llama-server CUDA-OOM crashes. It now points at
the shared server. Serena itself does **not** change: it stays on `127.0.0.1`.
Two root units bridge the gap:

- `system/serena-bridge.socket` — listens on `172.28.0.1:9121`, the gateway of
  `zoe-codeintel` (an `internal` Docker network declared in
  `modules/omnigent/docker-compose.module.yml`, one member: `zoe-omnigent`,
  pinned at `172.28.0.2`), and carries the access list
  `IPAddressDeny=any` / `IPAddressAllow=172.28.0.2/32`.
- `system/serena-bridge.service` — socket-activated
  `scripts/maintenance/serena_bridge_proxy.py`, forwarding to `127.0.0.1:9121`.

**It is not an L4 proxy, and cannot be.** The bridge originally ran
`systemd-socket-proxyd`. The TCP path was fine and every request still came back
`421 Misdirected Request` / `Invalid Host header`: Serena builds its `FastMCP`
without passing `transport_security=`, so the MCP SDK auto-enables DNS-rebinding
protection for its loopback bind (`allowed_hosts = 127.0.0.1:*`, `localhost:*`,
`[::1]:*`) and the container's requests carry `Host: 172.28.0.1:9121`. A byte
shuffler cannot rewrite a header. `serena_bridge_proxy.py` (stdlib asyncio, no
new dependency — socat is not installed here either) changes exactly that one
header and relays everything else untouched. Two properties are load-bearing and
pinned by `tests/unit/test_serena_bridge_proxy.py`: it must **stream** both
directions (MCP replies are open-ended `text/event-stream`, so a
read-to-EOF-then-forward proxy hangs forever), and it must rewrite **every**
request on a kept-alive connection, not just the first. Both shortcuts are
wrong: binding Serena non-loopback breaks the loopback-only rule **and** turns
the SDK's protection off entirely, and overriding `localhost` in the container's
`/etc/hosts` breaks container-local loopback.

The proxy is **socket-activated and never binds for itself** — it refuses to
start without `LISTEN_FDS` and has no `--listen` option. The socket unit must
keep owning the bind: `FreeBind` and the `IPAddressAllow` list live there, and
for a socket-activated service it is the SOCKET unit's access list that covers
the passed-in listening socket.

Because the service runs with `ProtectHome=yes`, `/home` is invisible to it, so
the script is **installed** outside the checkout rather than run from it.
Re-install it whenever the repo copy changes — same rule as the units.

**Root, not user**: a `--user` unit logs `unit configures an IP firewall, but
not running as root` and then starts with **no filtering**. And the access list
is not decoration — the dedicated network alone does not protect a
gateway-bound port, because host-local delivery goes through INPUT while
Docker's isolation rules live in FORWARD (measured: a `zoe-network` container
reached a listener on a separate internal bridge's gateway).

```bash
# The proxy script FIRST — ProtectHome=yes hides /home from the unit, so
# ExecStart cannot point into the checkout.
sudo install -D -m 0755 scripts/maintenance/serena_bridge_proxy.py \
        /usr/local/lib/zoe/serena_bridge_proxy.py
sudo cp scripts/setup/systemd/system/serena-bridge.socket \
        scripts/setup/systemd/system/serena-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now serena-bridge.socket   # the SOCKET, not the service
# On an upgrade the SERVICE also has to be picked up (the socket stays bound):
sudo systemctl restart serena-bridge.service 2>/dev/null || true

# Bring the container onto zoe-codeintel (this RECREATES it):
cd modules/omnigent && docker compose --env-file ../../.env \
  -f docker-compose.module.yml up -d && cd -

# MANDATORY negative control — systemd fails OPEN if it cannot install the BPF
# filter, so prove the boundary rather than assuming it:
docker run --rm --network zoe-network alpine:latest \
  wget -q -T 5 -O - http://172.28.0.1:9121/mcp     # MUST fail / time out
curl -sS --max-time 5 http://172.28.0.1:9121/mcp   # the HOST too: MUST fail

# Positive control — a real MCP handshake, not just a TCP connect. It must NOT
# come back "Invalid Host header":
docker exec zoe-omnigent curl -sS -i --max-time 30 \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}' \
  http://172.28.0.1:9121/mcp | head -20      # expect 200 + "serverInfo"
```

If either negative control succeeds, the filter is not in force: `sudo systemctl
disable --now serena-bridge.socket` and do not leave the bridge running.

A freshly (re)started `serena-mcp.service` can take **many minutes** to answer
its first request — it walks every `.gitignore` under the checkout, and the
~120 agent worktrees under `.claude/worktrees/` made that 15 minutes on
2026-07-22. A hanging handshake right after a restart is warm-up, not the
bridge; check `systemctl --user status serena-mcp` before debugging.

`flue-zoe-brain.service` is deliberately NOT in that enable line: it supervises
the sidecar behind zoe-data's default-OFF `ZOE_BRAIN_BACKEND=flue` seam.
Enable it only when running the Flue brain — build + env steps are in
[labs/flue-zoe-brain/README.md](../../../labs/flue-zoe-brain/README.md).

## Opt-in Multica queue consumer (`flue-executor.service`)

Also deliberately NOT in the enable line. `install-jetson.sh` copies its
template into `~/.config/systemd/user/` (the `*.service` glob) but never enables
it — `install-jetson.sh` will `die` if a future edit ever slips it into the
auto-enabled `SYSTEMD_SPINE`. It is the single-lane consumer of Multica's real
`agent_task_queue` for the executor migration (Phase 2 of
[docs/architecture/multica-executor-migration.md](../../../docs/architecture/multica-executor-migration.md)).

**The installer is side-effect-free with respect to dispatch control.** It
installs the inert unit and stops there — it never enables it and it **never
touches the shared Multica dispatch pause sentinel**
(`~/.zoe/multica_dispatch_paused`). That is deliberate: the same sentinel is read
every cycle by the already-live zoe-data poll loop and `multica_board_runner`, so
recreating it on a reinstall of an established host would **halt all live
engineering dispatch**. Pausing is an operator go-live decision, not a
provisioning side effect.

Two gates ship ON by construction, and one is an operator tool:

- **Dry dispatch (default)** — `ZOE_EXECUTOR_DISPATCH` defaults to `dry`: the
  runner polls and logs what it WOULD claim and **mutates nothing**
  (`labs/flue-executor/src/config.ts`). This is the gate that makes a freshly
  enabled unit safe on its own. Set `full` in `.env` only when taking it live.
- **Single lane** — at most one task in flight per runtime.
- **Kill switch (operator-armed)** — while `~/.zoe/multica_dispatch_paused`
  exists the runner idles and claims nothing (the runtime only checks that the
  file exists — `labs/flue-executor/src/live-runner.ts`). The installer does NOT
  create it; **you arm it yourself** when you want to hold dispatch. Because it
  is shared with the live board, arm the same file only when you intend to pause
  the whole board.

> **Non-default kill-switch path?** The runner watches the path from
> `ZOE_MULTICA_KILL_SWITCH` in `labs/flue-executor/.env` (else the default
> `~/.zoe/multica_dispatch_paused`). If you set an override, arm THAT path when
> you want to pause, and confirm it matches the runner's `kill-switch=<path>`
> startup log.

Bring it up (all operator steps — the installer does none of these):

```bash
cd ~/assistant/labs/flue-executor && npm install
# register the executor identity in Multica (idempotent):
python3 ~/assistant/scripts/maintenance/verify_executor_queue_backend.py
# create the env file from the template IF NOT ALREADY PRESENT (stays dry by
# default) and adjust. -n (no-clobber) PRESERVES a .env you pre-created with a
# custom ZOE_MULTICA_KILL_SWITCH, so it is not silently reset to the template:
cp -n ~/assistant/labs/flue-executor/.env.example ~/assistant/labs/flue-executor/.env
# OPTIONAL — hold dispatch before you enable by arming the pause yourself. Note
# this sentinel is SHARED with the live board, so it pauses live dispatch too;
# arm it only when you mean to pause everything. Dry dispatch (above) already
# keeps a freshly enabled unit from mutating anything, so this is belt-and-braces.
# Arm the path the runner actually checks — resolve it the same way the go-live
# steps below do (from labs/flue-executor/.env; a RELATIVE override lives under
# WorkingDirectory=labs/flue-executor, NOT your CWD) so armed == checked:
#   cd ~/assistant/labs/flue-executor   # so a relative override resolves like the unit
#   mkdir -p "$(dirname "<your ZOE_MULTICA_KILL_SWITCH>")" && touch "<your ZOE_MULTICA_KILL_SWITCH>"
systemctl --user daemon-reload
systemctl --user enable --now flue-executor   # starts DRY (mutates nothing)
journalctl --user -u flue-executor -f
```

**Go live.** The pre-live gate is **dry wiring verification, not a ticket
count.** Dry dispatch cannot claim or advance a ticket — it only logs the next
queued id — so a "≥3 real tickets" bar is physically unreachable until dispatch
is live. Verify the wiring first, flip the go-live gate, and only then validate
real tickets.

**Step 1 — FORCE and VERIFY dry BEFORE touching the switch.** This is the gate
that makes every switch manipulation below safe. On a host whose `.env` was
previously `full` (e.g. a re-provisioned box), removing the switch while dispatch
is still `full` would claim REAL work while you believe you are read-only. So pin
`dry` first and PROVE it from the startup log — do not proceed until you see
`dispatch=dry`:

```bash
# pin dry in the env file (dry is the default, but a previously-live host may
# carry ZOE_EXECUTOR_DISPATCH=full):
env_file=~/assistant/labs/flue-executor/.env
if grep -qE '^[[:space:]]*ZOE_EXECUTOR_DISPATCH=' "$env_file"; then
  sed -i 's/^[[:space:]]*ZOE_EXECUTOR_DISPATCH=.*/ZOE_EXECUTOR_DISPATCH=dry/' "$env_file"
else
  printf 'ZOE_EXECUTOR_DISPATCH=dry\n' >> "$env_file"
fi
systemctl --user restart flue-executor
journalctl --user -u flue-executor -n 20
# REQUIRED evidence before continuing — the startup line MUST show dispatch=dry:
#   [live] runtime "<name>" (<uuid>) dispatch=dry poll=<n>ms kill-switch=<path>
#   [live] DRY dispatch — will report what it WOULD claim, mutating nothing.
# If it reads dispatch=full, STOP — fix the .env and restart before going on.
```

**Step 2 — (only if the switch is armed) confirm the paused idle branch.** While
the switch is present the runner takes the idle branch and **never reaches the
dry-preview**, so `would claim` cannot appear yet — that is expected, not a
fault. Skip this step entirely if you did not arm the switch:

```bash
journalctl --user -u flue-executor -f
# expect a steady idle with NO restart loop:
#   [live] kill switch present — idling (claiming nothing).
# Confirm <path> from the startup line is the file you armed — the runner watches
# exactly this path (armed file == runner's checked path).
```

**Step 3 — dry-preview of the queue wiring (STILL dry, dispatch=dry already
verified in Step 1).** With dry proven, ensure the switch is absent so the runner
reaches the dry preview. It runs a read-only preview SELECT — claims and mutates
nothing. If you armed the switch in Step 2, remove it here and re-arm after:

```bash
# Resolve the kill-switch path EXACTLY as flue-executor.service does. The runner
# reads ZOE_MULTICA_KILL_SWITCH from its EnvironmentFile (labs/flue-executor/.env,
# NOT your shell — config.ts), defaults to ~/.zoe/multica_dispatch_paused, and
# checks a RELATIVE override under WorkingDirectory=labs/flue-executor (the unit's
# cwd, which existsSync() resolves against — live-runner.ts). So parse the .env,
# default to that same path, and resolve a relative value under that dir:
wd=~/assistant/labs/flue-executor
line="$(grep -E '^[[:space:]]*ZOE_MULTICA_KILL_SWITCH[[:space:]]*=' "$wd/.env" 2>/dev/null | tail -n1)"
val="${line#*=}"; val="${val#"${val%%[![:space:]]*}"}"; val="${val%"${val##*[![:space:]]}"}"
[[ "$val" == \"*\" || "$val" == \'*\' ]] && val="${val:1:${#val}-2}"
val="${val:-$HOME/.zoe/multica_dispatch_paused}"
case "$val" in /*) KILL_SWITCH="$val" ;; *) KILL_SWITCH="$wd/$val" ;; esac

rm -f "$KILL_SWITCH"          # dispatch is already dry (Step 1), so nothing is claimed/mutated
journalctl --user -u flue-executor -f
# expect, per tick (this is the queued-id evidence):
#   [live] dry tick: would claim <id>     # or: would claim - (queue empty / lane busy)
# If you armed the switch in Step 2, re-arm it now to stay paused until go-live:
#   : > "$KILL_SWITCH"
```

**Step 4 — go live.** Flip dispatch to `full`, then (if you armed the switch)
remove it:

```bash
# 1) arm real dispatch in the env file:
#      ZOE_EXECUTOR_DISPATCH=full   (in labs/flue-executor/.env), then restart:
systemctl --user restart flue-executor
# 2) unpause (only if you armed the switch) — the go-live action. Resolve the
#    path EXACTLY as the service does (read from labs/flue-executor/.env, NOT your
#    shell; default ~/.zoe/multica_dispatch_paused; a relative override resolves
#    under WorkingDirectory=labs/flue-executor) so you remove the path armed:
wd=~/assistant/labs/flue-executor
line="$(grep -E '^[[:space:]]*ZOE_MULTICA_KILL_SWITCH[[:space:]]*=' "$wd/.env" 2>/dev/null | tail -n1)"
val="${line#*=}"; val="${val#"${val%%[![:space:]]*}"}"; val="${val%"${val##*[![:space:]]}"}"
[[ "$val" == \"*\" || "$val" == \'*\' ]] && val="${val:1:${#val}-2}"
val="${val:-$HOME/.zoe/multica_dispatch_paused}"
case "$val" in /*) KILL_SWITCH="$val" ;; *) KILL_SWITCH="$wd/$val" ;; esac
rm -f "$KILL_SWITCH"          # the go-live action (no-op if you never armed it)
```

**Only now — dispatch `full`** (and unpaused, if you had armed the switch) — can
real tickets flow. Validate **≥3 real end-to-end tickets here**, watching
`journalctl`, before treating Phase 2 as proven. To pause in a hurry, arm the
switch again — re-resolve `KILL_SWITCH` as above in a fresh shell, then
`: > "$KILL_SWITCH"` (this pauses the whole board).

Start order matters — see [OPERATOR_RUNBOOK.md](../../../docs/guides/OPERATOR_RUNBOOK.md).

## Memory protection — the voice stack must never swap

The Orin has 15.6 GB of **unified** memory (CPU+GPU share it). The latency-critical
services must stay resident: a swapped brain or TTS engine does not fail, it just
gets slow in a way that reads as a product bug ("voice in pieces", a long first
reply after idle) rather than a resource one.

| Unit | `MemorySwapMax` | `MemoryLow` | `MemoryMax` |
|------|-----------------|-------------|-------------|
| `llama-server` | `0` | `6G` | *(none — see below)* |
| `kokoro-tts`   | `0` | `3G` | `4G` |
| `flue-zoe-brain` | `0` | `512M` | `1G` |
| `flue-zoe-telegram` | `0` | `256M` | `768M` |
| `serena-mcp`   | `2G` | — | `2G` (dev tooling, deliberately yields) |

The set above is pinned by `tests/unit/test_systemd_memory_protection.py`. It
enforces the *doctrine* (swap denied, a floor set, and a ceiling paired with the
denial) rather than the exact numbers, so retuning a cap is fine and dropping one
is not. Add a new latency-critical user unit to `NO_SWAP_UNITS` there.

Measured on the live box 2026-07-18, **before** these directives existed:
llama-server had **1,457 MB** and kokoro-tts **1,489 MB** paged out — ~3 GB of the
voice path on disk. `kokoro-tts` had no memory directives at all (cgroup
`memory.low` was `0`), so the kernel reclaimed it first.

The same thing was true of both `flue-*` sidecars until 2026-08-03 — that pass
fixed the units it knew about and nothing enforced the class. `flue-zoe-brain` is
the **top** brain lane under `ZOE_BRAIN_BACKEND=flue` (flue > core > legacy) and
was 87% paged out; `flue-zoe-telegram` had no directives at all. Measurements,
sizing rationale and the operator apply/rollback sequence:
[`docs/knowledge/memory-pressure-profile.md`](../../../docs/knowledge/memory-pressure-profile.md)
(2026-08-03 section). **Applying these live means a drop-in, not a template copy**
— the installed units carry host-specific edits (llama-server's binary and model
paths), so `cp`-ing a template over one clobbers them.

Two things worth knowing before changing these:

- **`--mlock` is not sufficient on Tegra.** llama-server sets `--mlock` with
  `LimitMEMLOCK=infinity`, yet `VmLck` held only 1.95 GB of a 5.6 GB RSS — mlock
  covers the mapped model, not every CUDA/unified allocation around it.
  `MemorySwapMax=0` is what closes the gap. `MemoryLow` is *soft* (reclaim
  resistance, not swap immunity) and alone did not stop the eviction.
- **Size `MemoryLow` from measurement, not from the doc comment.** kokoro's note
  says "~2.3 GB CUDA-resident"; the live cgroup after 20 voice turns read
  `memory.current` 2,309 MB and `VmHWM` 2,465 MB. A 2G floor would leave part of
  the working set outside the protected zone — unswappable, but still
  reclaimable, and on unified memory those are GPU-accessible pages.
- **llama-server has no `MemoryMax` on purpose.** A hard ceiling *plus* no swap
  turns a transient spike into an OOM kill. Kokoro can take one because it is
  bounded (~2.3 GB CUDA-resident, does not grow with load).

- **A ceiling is only meaningful where cgroup accounting is complete.** Both
  `flue-*` sidecars are pure userspace Node (verified 0 CUDA/NvMap mappings in
  `/proc/<pid>/maps`), so `MemoryMax` genuinely bounds them. On Tegra, a process
  allocating through NvMap does *not* have its GPU/unified pages fully accounted
  to the cgroup — which is the other half of why llama-server gets no ceiling.

Headroom check (why this fits): brain + kokoro fully resident ≈ **9.6 GB** of
15.6 GB, plus the flue floors (768 MB combined) ≈ **10.4 GB**, leaving ~5 GB for
zoe-data (~0.9 GB) and everything else. `MemoryLow` is a protection *ceiling*,
not a reservation — an unused floor costs nothing.

**Do not add `Nice=-N` or `OOMScoreAdjust=-N` to user units.** A `--user` unit
cannot raise priority (`ulimit -e` is 0 here). systemd accepts the directive, the
service starts, status is success — and the value is **silently dropped**
(verified: `Nice=-5` applied as `0`, `OOMScoreAdjust=-500` applied as `0`, while
`Nice=10` applied correctly). It documents a guarantee that does not exist.
Priority ordering is achieved in reverse: dev tooling de-prioritises *itself*
(`serena-mcp.service` runs `Nice=10` / `OOMScoreAdjust=500`), which works because
positive values need no privilege.

## Verify

```bash
systemctl --user status zoe-data
curl -f http://localhost:8000/health
journalctl --user -u zoe-data -f   # tail logs
```
