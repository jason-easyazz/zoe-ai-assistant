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
| `kokoro-tts`   | `0` | `2G` | `4G` |
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
