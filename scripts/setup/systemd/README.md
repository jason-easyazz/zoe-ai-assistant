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
| `serena-mcp`   | `2G` | — | `2G` (dev tooling, deliberately yields) |

Measured on the live box 2026-07-18, **before** these directives existed:
llama-server had **1,457 MB** and kokoro-tts **1,489 MB** paged out — ~3 GB of the
voice path on disk. `kokoro-tts` had no memory directives at all (cgroup
`memory.low` was `0`), so the kernel reclaimed it first.

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

Headroom check (why this fits): brain + kokoro fully resident ≈ **9.6 GB** of
15.6 GB, leaving ~6 GB for zoe-data (~0.9 GB) and everything else.

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
