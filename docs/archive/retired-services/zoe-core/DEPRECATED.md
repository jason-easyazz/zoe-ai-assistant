# zoe-core (retired)

This tree was the original Docker-packaged FastAPI “core” service. **Production now uses `services/zoe-data/`** (host-native uvicorn on port 8000) with the OpenClaw bridge, per `docker-compose.yml` header comments.

- Do **not** add new features here.
- Use **`services/zoe-data/`** for chat, system/OpenClaw APIs, intents, and MCP integration.
- This folder remains in the repository for historical reference and migration comparisons only.
