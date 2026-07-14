# Zoe AI Assistant

Privacy-first, self-hosted AI life hub: home automation, a touch/voice UI, and a
local-LLM agent stack that runs entirely on your own hardware. No cloud account
required for the core experience.

> **Heads up — this is a real, hardware-specific deployment, not a one-command
> demo.** Zoe is developed and run on an **NVIDIA Jetson Orin** (aarch64 + CUDA)
> with a locally-built `llama.cpp`. It will run on other Linux hosts, but the
> LLM and voice steps assume Jetson/CUDA and will need adjustment elsewhere
> (see [HARDWARE_COMPATIBILITY.md](HARDWARE_COMPATIBILITY.md)). Read the whole
> Install section before starting.

## Architecture

Zoe is a split stack — Docker for the stateful/edge services, host-native
systemd user services for the latency-sensitive ones.

### Core spine

| Layer | Service | Port | How it runs |
|-------|---------|------|-------------|
| Data  | `zoe-database` (PostgreSQL + pgvector) | 5432  | Docker |
| Auth  | `zoe-auth`        | 8002  | Docker |
| UI    | `zoe-ui` (nginx)  | 80/443 | Docker |
| Home  | `homeassistant` + `homeassistant-mcp-bridge` | 8123 | Docker |
| Brain (LLM) | `llama-server` (Gemma 4 E4B-QAT + MTP, llama.cpp) | 11434 | systemd (host) |
| API   | `zoe-data` (primary backend: chat, memory, Skybridge) | 8000 | systemd (host) |
| STT   | Moonshine v2 Medium (in-process in `zoe-data`) | — | systemd (host) |
| TTS   | `kokoro-tts` (Kokoro → Edge TTS → espeak-ng waterfall) | 10201 | systemd (host) |

### Optional / opt-in

| Layer | Service | Port | Notes |
|-------|---------|------|-------|
| Brain sidecar | `flue-zoe-brain` | 3578 | Flue Pi-Agent brain, enabled via `ZOE_BRAIN_BACKEND=flue` |
| Router | `functiongemma-router` | 11436 | Two-stage router stage-2 decoder (FunctionGemma-270M) |
| Music | `zoe-music-assistant` (Music Assistant) | 8095 | Docker module (`docker-compose.modules.yml`), proxied at `/modules/music-assistant/` |

The brain, STT, and TTS models above are **locked** — Gemma 4 E4B-QAT+MTP
(brain), Moonshine v2 Medium (STT), Kokoro (TTS). See
[docs/CANONICAL.md](docs/CANONICAL.md). Boot order matters and is documented in
[docs/guides/OPERATOR_RUNBOOK.md](docs/guides/OPERATOR_RUNBOOK.md).

**Brain dispatch.** `services/zoe-data/brain_dispatch.py` selects the brain,
priority `flue > core` — both sharing the same Gemma 4 rock on `llama-server`.
`services/zoe-core` (the Pi agent) is the wired default `core` lane; `flue` is
the opt-in sidecar. The active layout is described in
[docs/guides/REPO_LAYOUT.md](docs/guides/REPO_LAYOUT.md).

## Prerequisites

- **Host:** Linux. Reference target is NVIDIA Jetson Orin (JetPack 6, CUDA 12.6).
- **Docker** + Docker Compose plugin.
- **Python 3.10+** with `pip` (host-native services run on system Python).
- **A GGUF chat model** for `llama-server` (~8 GB VRAM for the reference model).
- The repo is expected at `~/assistant`. Some setup scripts assume that path.

## Install

```bash
# 1. Clone to the expected location
git clone https://github.com/jason-easyazz/zoe-ai-assistant.git ~/assistant
cd ~/assistant

# 2. Configure secrets (DB password, tokens, API keys)
cp .env.example .env
cp services/zoe-data/.env.example services/zoe-data/.env
# Edit both .env files — generate strong values for POSTGRES_URL, tokens, etc.

# 3. Initialise database schemas
./scripts/setup/init_databases.sh

# 4. Download the local LLM model (or supply your own GGUF)
./scripts/setup/download_gguf_models.sh

# 5. Start the Docker layer
docker compose up -d zoe-database zoe-auth zoe-ui homeassistant homeassistant-mcp-bridge

# 6. Install + start the host-native services
#    Templates and full instructions: scripts/setup/systemd/README.md
cp scripts/setup/systemd/*.service ~/.config/systemd/user/
#    Edit llama-server.service for your binary + model path, then start the spine:
systemctl --user daemon-reload
systemctl --user enable --now llama-server zoe-data kokoro-tts
#    Optional sidecars — enable only if you need them:
#    flue-zoe-brain, functiongemma-router

# 7. (Optional) Start add-on modules, e.g. Music Assistant on :8095
docker compose -f docker-compose.modules.yml up -d music-assistant
```

See [scripts/setup/systemd/README.md](scripts/setup/systemd/README.md) for the
host-native services and [OPERATOR_RUNBOOK.md](docs/guides/OPERATOR_RUNBOOK.md)
for start/stop order and troubleshooting.

## Verify

```bash
curl -f http://localhost:8000/health   # zoe-data backend
curl -f http://localhost:8002/health   # zoe-auth
docker compose ps
```

- **UI:** `https://localhost` (or `http://localhost`)
- **API docs:** `http://localhost:8000/docs`
- **Home Assistant:** `http://localhost:8123`

## Day-to-day

```bash
# Restart backend after Python changes
systemctl --user restart zoe-data
# Restart UI after nginx/HTML changes
docker compose restart zoe-ui
```

`./RESTART_SERVICES.sh` restarts the common set. The full "restart after X"
matrix is in the [OPERATOR_RUNBOOK.md](docs/guides/OPERATOR_RUNBOOK.md).

## Touch panel (optional)

Zoe supports a kiosk touch panel on a separate Linux device.

- Installer + templates: `scripts/setup/touchscreen/`
- Guides: [docs/guides/](docs/guides/)

```bash
scripts/setup/touchscreen/install_touchscreen.sh \
  --host <TOUCH_PANEL_IP> \
  --user <USER> \
  --server-url https://<ZOE_SERVER_IP> \
  --panel-id zoe-touch-pi
```

## Documentation map

| Doc | What it covers |
|-----|----------------|
| [QUICK-START.md](QUICK-START.md) | Start/stop cheat sheet |
| [docs/guides/OPERATOR_RUNBOOK.md](docs/guides/OPERATOR_RUNBOOK.md) | Boot order, env vars, troubleshooting |
| [docs/guides/REPO_LAYOUT.md](docs/guides/REPO_LAYOUT.md) | Where things live |
| [HARDWARE_COMPATIBILITY.md](HARDWARE_COMPATIBILITY.md) | Platform notes (Jetson/Pi/x86) |
| [CAPABILITIES.md](CAPABILITIES.md) | Agent tiers + MCP tool inventory |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [AGENTS.md](AGENTS.md) | Conventions for AI coding agents in this repo |

## License

MIT — see [LICENSE](LICENSE).
