# Root Legacy Archive (2026-04-21)

This folder contains root-level files moved out of the repository top-level to reduce root clutter.

Moved from root:

- `docker-compose.mem-agent.yml` (legacy mem-agent compose)
- `docker-compose.secure.yml` (legacy security hardening draft for retired zoe-core)
- `docker-compose.override.yml` (empty legacy override)
- `warmup_models.py` (legacy Ollama warmup helper)

These are retained for historical reference and rollback context, but are not part of the active runtime path.
