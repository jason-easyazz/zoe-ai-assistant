# Zoe AI Assistant - Quick Start

## First-Time Setup

Zoe's live database is PostgreSQL. On a new installation, start the database
container and run Alembic migrations before starting the host-native backend:

```bash
cd /home/zoe/assistant

docker compose up -d zoe-database
bash scripts/deploy/migrate.sh
```

Existing installations normally only need migrations during deploy.

## Start Zoe

```bash
cd /home/zoe/assistant
docker compose up -d
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
systemctl --user restart zoe-data.service
```

Optional host-native services are managed separately with user services:

```bash
systemctl --user restart openclaw.service llama-server.service hermes.service kokoro-tts.service
```

## Use Zoe

1. Open the UI: `http://localhost:8090` or the configured `zoe.local` address.
2. Backend API health: `http://localhost:8000/health`.
3. Auth API health: `http://localhost:8002/health`.
4. Touch panels use the `/touch/` pages and authenticate through `zoe-auth`.

## Stop Zoe

```bash
cd /home/zoe/assistant
docker compose down
systemctl --user stop zoe-data.service
```

## If Something's Wrong

Check services:

```bash
curl -sf http://localhost:8000/health
curl -sf http://localhost:8002/health
curl -sf http://localhost:8090/health
systemctl --user --no-pager status zoe-data.service
docker compose ps
```

Check logs:

```bash
journalctl --user -u zoe-data.service -n 100 --no-pager
docker compose logs --tail=100 zoe-auth zoe-ui zoe-database
```

Restart the active stack:

```bash
bash RESTART_SERVICES.sh
```

## File Structure

- `services/zoe-data/` - host-native FastAPI backend on port 8000.
- `services/zoe-auth/` - auth container on port 8002.
- `services/zoe-ui/dist/` - static UI served by nginx on port 8090/443.
- `services/zoe-data/alembic/` - PostgreSQL schema migrations.
- `docker-compose.yml` - core containers: UI, auth, database, HA bridge, LiveKit.

## Developer Checks

```bash
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
PYTHONPATH=services/zoe-data python3 -m pytest services/zoe-data/tests -q
```
