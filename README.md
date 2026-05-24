# Zoe AI Assistant

Privacy-first, self-hosted assistant for home automation, touch UI, and voice workflows.

## Current Architecture

The active stack is intentionally split:

- **Host-native services**
  - `zoe-data` FastAPI on `:8000`
  - Hermes agent gateway on `:8642`
  - OpenClaw gateway service
  - llama-server service
- **Docker services** (`docker-compose.yml`)
  - `zoe-database` (PostgreSQL + pgvector)
  - `zoe-ui` (nginx frontend)
  - `zoe-auth`
  - `homeassistant`
  - `homeassistant-mcp-bridge`
  - optional helpers in `docker-compose.modules.yml` (`zoe-orbit`, Music Assistant, Multica)

`zoe-core` and several older service trees are retired and archived under `docs/archive/retired-services/`.

## Quick Start

```bash
git clone https://github.com/jason-easyazz/zoe-ai-assistant.git
cd zoe-ai-assistant
cp .env.example .env
./scripts/setup/init_databases.sh
```

Start core Docker services:

```bash
docker compose up -d zoe-database zoe-auth zoe-ui homeassistant homeassistant-mcp-bridge
```

Then ensure host-native services are running (`zoe-data`, Hermes, OpenClaw gateway, llama-server, Kokoro TTS) per the runbook.

## Endpoints

- UI: `https://localhost` (or `http://localhost`)
- API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

## Touchscreen

Zoe supports a touch panel kiosk running on a separate device (previously a Raspberry Pi, now any Linux device running the kiosk stack).

- Device profile: `docs/guides/TOUCHSCREEN_DEVICE_STACK_192.168.1.61.md`
- Installer/templates: `scripts/setup/touchscreen/`

> **Note:** The Raspberry Pi 5 touchscreen target was retired April 2026. The installer scripts remain available for any compatible Linux host.

Example install command:

```bash
scripts/setup/touchscreen/install_touchscreen.sh \
  --host <TOUCH_PANEL_IP> \
  --user <USER> \
  --server-url https://192.168.1.218 \
  --panel-id zoe-touch-pi
```

## Repo Layout

- Active runtime: `services/zoe-data`, `services/zoe-ui`, `services/zoe-auth`, `services/homeassistant-mcp-bridge`
- Active docs: `docs/guides/`
- Historical docs/code: `docs/archive/`

See `docs/guides/REPO_LAYOUT.md` for details.

## Operational Docs

- `docs/guides/OPERATOR_RUNBOOK.md` - service start/stop order, troubleshooting
- `HARDWARE_COMPATIBILITY.md` - platform notes
- `CHANGELOG.md` - release history

## License

MIT - see `LICENSE`.
