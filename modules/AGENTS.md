# modules/ — optional add-on modules

## Purpose

Self-contained optional modules that extend Zoe beyond the core assistant, served under `/modules/` by nginx.

## Ownership

- `omnigent/` — remote-coding agent module. Currently the **only** module in this tree.

> Retired (see `docs/CANONICAL.md`): `zoe-music/` (2026-08-05), and `orbit/`, `agent-zero/`, `jag-board/`, `questionable-decisions/` (2026-06-24). Do not re-add them.

> **`zoe-music` is not the live music system, and never was.** The live one is
> `zoe-music-assistant` — the upstream Music Assistant container defined in the tracked
> `docker-compose.modules.yml` and proxied at `/modules/music-assistant/`. It is a
> different product that happens to share a name prefix; `zoe-music-assistant` did not
> evolve from `modules/zoe-music`. A `zoe-music` prefix grep matches both — distinguish
> them before acting on a hit. `test_no_zoe_music_module` in
> `services/zoe-data/tests/test_canonical_invariants.py` fails if the module returns.

## Local Contracts

- Modules are optional: core Zoe must run with any module absent.
- Module compose files are generated via `tools/generate_module_compose.py` into `docker-compose.generated-modules.yml`; do not hand-edit generated compose output. The generator rejects non-slug module names (no path traversal).
- Module routes are declared in `services/zoe-ui/nginx.conf`; adding a module route touches that critical file (see `services/AGENTS.md`).
- A module's state-changing `/tools/*` routes must be gated by a shared service token and fail closed (503) until it is set; the in-cluster caller sends the same value as `X-Zoe-Service-Token`. Publish module ports on loopback only; in-cluster reach is via the `zoe-network` service name.
- **`omnigent`'s MCP config (`omnigent/.mcp.json`, bind-mounted over `/workspace/.mcp.json`) must never spawn a stdio Serena again.** Each container-spawned server was ~900 MB RSS on a 15.6 GB box shared with llama-server + Kokoro. It uses the host's shared `serena-mcp.service` over `zoe-codeintel` — an `internal` network with exactly one member, pinned at `172.28.0.2` — fronted by `scripts/setup/systemd/system/serena-bridge.{socket,service}`. The bridge's `IPAddressAllow=`, not the network, is the access control (a bridge GATEWAY is reachable from every container on the host; only container-to-container across networks is blocked). Keep the pinned address, the subnet, the MCP url and the socket unit in agreement — `tests/unit/modules/test_omnigent_mcp_config.py` fails if they drift. Adding a second member to `zoe-codeintel` widens whole-repo read access and is out of scope for any module change.

## Work Guidance

- **A module's upstream version is PINNED, and bumping it is a deliberate procedure — never an incidental rebuild.** An unpinned install is Docker-layer-cached, so upgrades silently do NOT happen on rebuild, and an unpinned surprise upgrade has landed here before. Before changing a pin, diff the new release against the installed one on the surfaces this repo actually couples to, verify end-to-end (an actual agent/brain dispatch, not just that the binary starts), and record what you checked next to the pin. For `omnigent` those surfaces, and the full checklist, are in [`omnigent/README.md`](omnigent/README.md) → *Upgrading the pin*.
- **Assume an upstream bump can change API SHAPES, not just behaviour, and grep for our assumptions about them.** omnigent 0.7.0 dropped the type prefix from its ids (`conv_`/`ag_`/`host_` → bare hex); every consumer that pinned the prefix hard-failed, and the same assumption had been copied into three launchers. A green container and a 200 from the API prove neither — dispatch an agent end to end before calling a bump verified.
- **Any value interpolated into a shell string must be validated on CHARSET and LENGTH, and the validators must not drift.** `[A-Za-z0-9]` admits no metacharacter; anchor with `\Z` in Python (`$` also matches before a trailing newline) and floor the length, because `cross_review.sh`'s cleanup kills by SUBSTRING match on `/proc/<pid>/cmdline` — a degenerate short id sweeps unrelated processes, the omnigent server included. Sites: `services/zoe-data/omnigent_issue_executor.py`, `scripts/maintenance/cross_review.sh`, `labs/flue-executor/src/omnigent.ts`.
- **Check RAM before building a module image, and drop caches before restarting the brain afterwards.** A build alongside the ~6 GB `llama-server` can OOM it, and the cgroup guards do not cover CUDA/NvMap. Worse, a *finished* build leaves GBs in page cache: `MemAvailable` looks healthy while CUDA still fails to allocate, because NvMap needs physically free pages and will not force reclaim. `sync; echo 3 > /proc/sys/vm/drop_caches` first — see [`docs/knowledge/memory-pressure-profile.md`](../docs/knowledge/memory-pressure-profile.md).
- **Retire workarounds when upstream fixes the cause.** The vendored aarch64 `cel-expr-python` wheel, its `COPY`, and `UV_FIND_LINKS` were deleted once 0.7.0 moved to pure-Python `cel-python` — git history keeps the build recipe. Do not keep dead scaffolding "just in case".

## Verification

After enabling a module, verify its nginx route serves and core `/health` still passes.

## Child DOX Index

No child AGENTS.md files yet.
