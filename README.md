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

| Layer | Service | Port | How it runs |
|-------|---------|------|-------------|
| Data  | `zoe-database` (PostgreSQL + pgvector) | 5432  | Docker |
| Auth  | `zoe-auth`        | 8002  | Docker |
| UI    | `zoe-ui` (nginx)  | 80/443 | Docker |
| Home  | `homeassistant` + `homeassistant-mcp-bridge` | 8123 | Docker |
| LLM   | `llama-server` (Gemma 4 E4B, llama.cpp) | 11434 | systemd (host) |
| Agent | `hermes-agent`    | 8642  | systemd (host) |
| Agent | `openclaw-gateway` (fallback) | 18789 | systemd (host) |
| Voice | `kokoro-tts` (optional) | 10201 | systemd (host) |
| API   | `zoe-data` (primary backend) | 8000 | systemd (host) |

Boot order matters and is documented in
[docs/guides/OPERATOR_RUNBOOK.md](docs/guides/OPERATOR_RUNBOOK.md).

Retired services (`zoe-core` and older trees) are archived under
`docs/archive/`. The active layout is described in
[docs/guides/REPO_LAYOUT.md](docs/guides/REPO_LAYOUT.md).

## Prerequisites

- **Host:** Linux. Reference target is NVIDIA Jetson Orin (JetPack 6, CUDA 12.6).
- **Docker** + Docker Compose plugin.
- **Python 3.10+** with `pip` (host-native services run on system Python).
- **Node.js 22+** (for the optional OpenClaw agent gateway).
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
#    Edit llama-server.service for your binary + model path, then:
systemctl --user daemon-reload
systemctl --user enable --now llama-server hermes-agent openclaw-gateway kokoro-tts zoe-data
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
