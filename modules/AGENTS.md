# modules/ — optional add-on modules

## Purpose

Self-contained optional modules that extend Zoe beyond the core assistant, served under `/modules/` by nginx.

## Ownership

- `zoe-music/` — music module. **Being replaced** by Music Assistant (see `docs/CANONICAL.md`); keep until the replacement is proven.
- `omnigent/` — remote-coding agent module.

> Retired 2026-06-24 (see `docs/CANONICAL.md`): `orbit/`, `agent-zero/`, `jag-board/`, `questionable-decisions/`. Do not re-add them.

## Local Contracts

- Modules are optional: core Zoe must run with any module absent.
- Module compose files are generated via `tools/generate_module_compose.py` into `docker-compose.generated-modules.yml`; do not hand-edit generated compose output. The generator rejects non-slug module names (no path traversal).
- Module routes are declared in `services/zoe-ui/nginx.conf`; adding a module route touches that critical file (see `services/AGENTS.md`).
- `zoe-music` state-changing `/tools/*` are gated by a shared service token (`ZOE_MUSIC_SERVICE_TOKEN`); the module fails closed (503) until it is set, and the in-cluster caller (`intents/handlers.py`) must send the same value as `X-Zoe-Service-Token`. Publish module ports on loopback only; in-cluster reach is via the `zoe-network` service name.
- **`omnigent`'s MCP config (`omnigent/.mcp.json`, bind-mounted over `/workspace/.mcp.json`) must never spawn a stdio Serena again.** Each container-spawned server was ~900 MB RSS on a 15.6 GB box shared with llama-server + Kokoro. It uses the host's shared `serena-mcp.service` over `zoe-codeintel` — an `internal` network with exactly one member, pinned at `172.28.0.2` — fronted by `scripts/setup/systemd/system/serena-bridge.{socket,service}`. The bridge's `IPAddressAllow=`, not the network, is the access control (a bridge GATEWAY is reachable from every container on the host; only container-to-container across networks is blocked). Keep the pinned address, the subnet, the MCP url and the socket unit in agreement — `tests/unit/modules/test_omnigent_mcp_config.py` fails if they drift. Adding a second member to `zoe-codeintel` widens whole-repo read access and is out of scope for any module change.

## Work Guidance

(empty)

## Verification

After enabling a module, verify its nginx route serves and core `/health` still passes.

## Child DOX Index

No child AGENTS.md files yet.
