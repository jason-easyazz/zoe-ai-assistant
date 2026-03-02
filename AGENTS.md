# Agents Instructions

## Cursor Cloud specific instructions

### Project Overview

Zoe AI Assistant is a Docker-based multi-service architecture. The core services are Python/FastAPI microservices, with an nginx frontend, Redis cache, and various integrations.

### Services

| Service | Port | Description |
|---------|------|-------------|
| zoe-core | 8000 | Main FastAPI backend (chat, tasks, calendar, memory) |
| zoe-auth | 8002 | Authentication service (JWT, RBAC, sessions) |
| zoe-ui | 80/443 | Nginx reverse proxy + static web UI |
| zoe-redis | 6379 | Redis cache (internal) |
| zoe-mcp-server | 8003 | MCP tool-calling server |
| zoe-mem-agent | 11435 | Memory/vector search agent |

### Running Services

Start core services (no GPU required):
```bash
sudo docker compose up -d zoe-redis zoe-ui zoe-auth zoe-core zoe-mcp-server zoe-mem-agent
```

GPU-dependent services (`zoe-llamacpp`, `zoe-whisper`, `zoe-tts`, `livekit`, `zoe-voice-agent`) and optional integrations (`homeassistant`, `zoe-n8n`) cannot run in the cloud VM (no GPU/hardware).

### Gotchas

- **No `.env.example`**: A `.env` file must be created manually. See `docker-compose.yml` for required env vars. Most have defaults; `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` can be placeholder values for local dev.
- **SSL certs required**: nginx needs `ssl/zoe.crt` and `ssl/zoe.key`. Generate with: `openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout ssl/zoe.key -out ssl/zoe.crt -subj "/CN=zoe.local"`.
- **Database init**: zoe-core auto-creates tables in `data/zoe.db` on startup. The `data/schema/*.sql` files are mostly empty stubs.
- **Missing `models/` in zoe-auth**: The `services/zoe-auth/models/database.py` module was missing from the repo and had to be created. It defines `AuthDatabase`, `SessionType`, `AuthMethod`, `AuthSession`, `User`, `UserRole`, `Role`, `Permission`, `AuditLog`.
- **Dockerfile `setuptools` issue**: `services/zoe-core/Dockerfile` needs `setuptools<70` pinned before installing requirements, because `openai-whisper` uses `pkg_resources` which was removed from newer setuptools.
- **nginx upstream resolution**: The nginx config at `services/zoe-ui/nginx.conf` uses the resolver+variable pattern for optional services (tts, whisper, livekit, music). Without this, nginx crashes if those services aren't running.
- **First user setup**: There is no open registration endpoint. The first admin user must be inserted directly into the `auth_users` and `users` tables in `data/zoe.db`. Use bcrypt to hash the password.

### Running Tests

Unit tests (from repo root):
```bash
python3 -m pytest tests/unit/ -v --ignore=tests/unit/music
```

The `conftest.py` adds `services/zoe-core` to `sys.path`. Tests in `services/zoe-auth/tests/` use relative imports and must be run within that service's Docker container or with proper PYTHONPATH setup.

### Linting

No dedicated linter config (no `.flake8`, `ruff.toml`, `mypy.ini`). The `.cursorrules` file defines code quality guidelines.

### Key Paths

- Service code: `services/<service-name>/`
- Frontend: `services/zoe-ui/dist/`
- Database: `data/zoe.db`
- Tests: `tests/` (unit, integration, e2e)
- Schemas: `data/schema/`
