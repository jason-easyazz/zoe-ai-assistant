---
type: Reference
title: Zoe Runtime Topology
description: The live runtime — host, services, ports, where each thing is served from and logs to, the touch panel, and the memory-tight envelope. Orientation for any agent before touching the running system.
tags: [topology, runtime, services, deployment, orientation]
timestamp: 2026-06-26T00:00:00Z
---

# Zoe Runtime Topology

Where Zoe actually runs, verified live 2026-06-26. For the *locked* model/voice choices see
[CANONICAL.md](../CANONICAL.md) (the rocks); for the tool layer see [zoe-tool-stack.md](zoe-tool-stack.md).
Point-in-time facts (ports, paths) — verify with `docker ps` / `ss -ltnp` / `systemctl --user` before relying on them.

## The host

A **Jetson Orin NX 16 GB** at **192.168.1.218** (LAN). Memory is **unified** (CPU + GPU share
the 16 GB) and the box runs **memory-tight** — only a few hundred MB free steady-state, and the
brain alone `mlock`s ~5.2 GB. **Do not load large models speculatively**; startup warmups already
skip themselves under memory pressure.

## Live services (host-native, NOT containers)

These run as **user systemd** units (`systemctl --user`), straight on the host:

| Service | Unit | Port | Serves | Logs |
|---|---|---|---|---|
| API | `zoe-data.service` | `0.0.0.0:8000` | FastAPI/uvicorn (`main:app`) — voice + chat path, memory router, Skybridge; Prometheus at `/metrics` | `~/.zoe-logs/zoe-data.{stdout,stderr}.log` (append; **not** journald) |
| Brain | `llama-server.service` | `0.0.0.0:11434` | host-native llama.cpp serving the Gemma 4 E4B-QAT + MTP rock (~5.2 GB mlock) | journald |
| TTS | `kokoro-tts.service` | `127.0.0.1:10201` | Kokoro voice sidecar (localhost-only) | journald |

- `zoe-data` runs from the **live checkout** `WorkingDirectory=/home/zoe/assistant/services/zoe-data`,
  with `MALLOC_ARENA_MAX=2` set in the unit (caps glibc arena bloat — see [zoe-tool-stack.md] note;
  without it RSS ballooned to ~3.2 GB). An `ExecStartPre` runs `scripts/maintenance/sync_zoe_self.sh`.
- **Moonshine v2 Medium (STT)** runs **in-process inside zoe-data on CPU** (onnxruntime GPU discovery
  fails on Tegra); no separate port. See [voice-pipeline.md](voice-pipeline.md).

## Docker containers (everything else)

`zoe-auth`, `zoe-database` (Postgres), `zoe-ui` (+nginx), `zoe-music-assistant`,
`zoe-multica-{backend,web}`, `zoe-omnigent` (`:6767`, remote-coding meta-harness), `homeassistant`
(+`homeassistant-mcp-bridge`), `zoe-cloudflared` (edge tunnel), `zoe-smb-drop`. The brain, TTS, and
zoe-data are **host-native, not containers** — don't look for them in `docker ps`.

## The touch panel

A **separate Raspberry Pi**, hostname `zoe-touch` at **192.168.1.61**, user `pi`. `zoe-kiosk.service`
→ `/opt/TouchKio/start-kiosk.sh` (chromium kiosk) → loads `https://192.168.1.218/touch/skybridge.html`.
Tracked config under `scripts/setup/touchscreen/` (SSH access to the panel needs operator OK).

## Deploy (important)

There is **no deploy pipeline.** The live uvicorn runs from the `/home/zoe/assistant` checkout, which
is usually on a **feature branch, not `main`** — so **landing a PR on `main` does NOT make it live.**
Deploying = get the change into that checkout + `systemctl --user restart zoe-data.service`. Full
detail and merge discipline in [merge-and-deploy.md](merge-and-deploy.md).
