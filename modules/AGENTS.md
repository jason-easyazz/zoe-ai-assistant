# modules/ — optional add-on modules

## Purpose

Self-contained optional modules that extend Zoe beyond the core assistant, served under `/modules/` by nginx.

## Ownership

- `agent-zero/` — Agent Zero integration module.
- `orbit/` — Orbit module (nginx route `^~ /modules/orbit`).
- `zoe-music/` — music module.
- `jag-board/` and `questionable-decisions/` content is per-deployment game data: excluded from git and from the knowledge graph; routes exist in nginx.

## Local Contracts

- Modules are optional: core Zoe must run with any module absent.
- Module compose files are generated via `tools/generate_module_compose.py` into `docker-compose.generated-modules.yml`; do not hand-edit generated compose output.
- Module routes are declared in `services/zoe-ui/nginx.conf`; adding a module route touches that critical file (see `services/AGENTS.md`).

## Work Guidance

(empty)

## Verification

After enabling a module, verify its nginx route serves and core `/health` still passes.

## Child DOX Index

No child AGENTS.md files yet.
