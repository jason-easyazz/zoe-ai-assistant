# Zoe AI Assistant - Quick Start

## First-Time Setup

For a new installation, initialize local schemas before starting services:

```bash
cd /home/zoe/assistant
./scripts/setup/init_databases.sh
```

Existing installations should keep their current databases.

## Start Zoe

```bash
cd /home/zoe/assistant
docker compose up -d zoe-database zoe-auth zoe-ui homeassistant homeassistant-mcp-bridge
XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user restart zoe-data.service
```

## Use Zoe

1. Open the web UI: `http://localhost:8090`
2. Check the data API: `http://localhost:8000/health`
3. Check auth: `http://localhost:8002/health`

## Stop Zoe

```bash
cd /home/zoe/assistant
XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user stop zoe-data.service
docker compose stop zoe-ui zoe-auth homeassistant homeassistant-mcp-bridge zoe-database
```

## Troubleshooting

```bash
curl -f http://localhost:8000/health
curl -f http://localhost:8002/health
docker compose ps
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
```

For operational details, see `docs/guides/OPERATOR_RUNBOOK.md`.
