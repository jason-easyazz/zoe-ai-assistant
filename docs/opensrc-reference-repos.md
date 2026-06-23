---
type: reference
title: opensrc reference repos + internal component source map
updated: 2026-06-24
---

# opensrc Reference Repos & Internal Component Source Map

`opensrc` caches third-party source at `~/.opensrc/repos/github.com/<owner>/<repo>/<ref>/`
so agents read **real source instead of guessing**. Fetch with
`opensrc fetch <owner/repo>` (or `pypi:<pkg>` / `<npm-pkg>`); resolve a path with
`opensrc path <owner/repo>` (fetches on cache miss); list everything with `opensrc list`.

> **Reminder:** when you need to understand a third-party library Zoe depends on,
> run `opensrc path <owner/repo>` and read the cached source — do not guess APIs.

## Public reference repos (in the opensrc cache)

| Repo | Public/Internal | opensrc path | Status |
| --- | --- | --- | --- |
| ggml-org/llama.cpp (our brain server) | Public | `~/.opensrc/repos/github.com/ggml-org/llama.cpp/master` | added |
| oraios/serena (code-intel MCP) | Public | `~/.opensrc/repos/github.com/oraios/serena/main` | added |
| GoogleCloudPlatform/knowledge-catalog (OKF — Open Knowledge Format, see `okf/`) | Public | `~/.opensrc/repos/github.com/GoogleCloudPlatform/knowledge-catalog/main` | added |
| DeusData/codebase-memory-mcp (codebase-memory MCP) | Public | `~/.opensrc/repos/github.com/DeusData/codebase-memory-mcp/main` | added |
| ag-ui-protocol/ag-ui (AG-UI protocol) | Public | `~/.opensrc/repos/github.com/ag-ui-protocol/ag-ui/main` | present |
| MemPalace/mempalace (we benchmark memory against it) | Public | `~/.opensrc/repos/github.com/MemPalace/mempalace/develop` | present |

### Notes on the OKF and codebase-memory resolutions
- **OKF** = the **Open Knowledge Format**. The canonical public repo is
  `GoogleCloudPlatform/knowledge-catalog` (Apache-2.0); the spec + reference code
  live under its `okf/` subdirectory. No standalone `okf` repo exists, so the cache
  holds the parent repo.
- **codebase-memory** = `DeusData/codebase-memory-mcp`, the tree-sitter knowledge-graph
  code-intelligence MCP server (matches the `mcp__codebase-memory__*` tool surface Zoe uses).

## Internal components (NOT public GitHub repos — read local source)

These were named alongside the public repos but are Zoe-internal / local. Do **not**
clone guesses for them; read the local source on this machine.

| Component | Public/Internal | Local source path | Status |
| --- | --- | --- | --- |
| Skybridge (voice/touch action-loop) | Internal | `services/zoe-data/skybridge_service.py`, `services/zoe-data/routers/skybridge.py`, `services/zoe-ui/dist/touch/js/skybridge*.js`; docs in `docs/architecture/skybridge-*.md` | present (local) |
| Pi agent (Zoe's brain / reasoning core) | Internal | `services/zoe-core/` (Pi on local Gemma 4; see `services/zoe-core/README.md`) | present (local) |
| hermes agent (PR-processing pipeline) | Internal | `~/.hermes/` (runtime: `~/.hermes/bin`, `~/.hermes/hermes-agent`); service wiring in `services/zoe-data/hermes_http.py`, `services/zoe-data/hermes_model_profiles.py` | present (local) |
| openclaw (local Gemma agent runner) | Internal | `~/.openclaw/` (runtime config) + binary `~/.local/bin/openclaw`; service wiring in `services/zoe-data/openclaw_manager.py`, `openclaw_ws.py`, `routers/openclaw.py` | present (local) |

> The `github.com/openclaw/openclaw`, `github.com/NousResearch/hermes-agent`, and
> `github.com/multica-ai/multica` entries already in the opensrc cache are upstream
> projects we learn from — they are **not** Zoe's running Skybridge/Pi/hermes/openclaw
> components. For Zoe's actual behaviour, read the **local** paths above.

## Still needs operator action

None. All Task 1 public repos resolved and were fetched/confirmed:

```
opensrc fetch ggml-org/llama.cpp
opensrc fetch oraios/serena
opensrc fetch GoogleCloudPlatform/knowledge-catalog
opensrc fetch DeusData/codebase-memory-mcp
# already present:
opensrc path ag-ui-protocol/ag-ui
opensrc path MemPalace/mempalace
```

No repo was left **unavailable** — every named public repo resolved to a verified
canonical URL.
