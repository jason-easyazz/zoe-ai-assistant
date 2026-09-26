# services/homeassistant-mcp-bridge/ — Home Assistant MCP bridge

## Purpose

Small containerized bridge exposing Home Assistant control to Zoe over MCP. Runtime code lives in `main.py`; service-local regression tests live under `tests/`.

## Local Contracts

- Smart-home concepts stay in skills and scopes; this bridge exposes tools, it must not push household assumptions into kernel code.
- HA credentials come from environment configuration, never hardcoded.
- The live Home Assistant runtime tree at `homeassistant/` (repo root) is data, not code — do not edit it from here.
- **HA LLM tool names are spelled ONLY through `ha_tool_names.py`.** HA 2026.9 domain-prefixes every LLM/Assist/MCP tool name (`HassTurnOn` → `intent__HassTurnOn`, `GetLiveContext` → `homeassistant__GetLiveContext`; intents take the REGISTERING domain — `light__HassLightSet`, `assist_satellite__HassBroadcast`) and 2027.3 rejects unprefixed names. Callers use base names + `ha_tool_name()` / `script_tool_name()`; inbound names in either scheme go through `normalize_tool_name(name, scheme=<detected>)`, which rejects unknown names rather than passing them through — the scheme argument is what lets a legacy HA's bare script names (`morning`, `_3am_check`) resolve, so always pass the detected one. The scheme is detected once from HA `/api/config` `version` (cached, logged once, lock-serialised so racing cold requests share one read) and served at `GET /tools/names`; `HA_TOOL_NAME_SCHEME=legacy|prefixed` pins it for an upgrade window. Never hard-code either spelling elsewhere. Runbook: `docs/knowledge/ha-2026-9-upgrade-runbook.md`.

## Work Guidance

(empty)

## Verification

- `python3 -m pytest services/homeassistant-mcp-bridge/tests/test_ha_bridge.py -q`
- `python3 -m pytest services/homeassistant-mcp-bridge/tests/test_ha_tool_names.py -q` (ci_safe; both files are enumerated in `validate.yml`'s bridge lane — a new test file here needs a YAML entry, unlike the marker-based lanes)
- Rebuild the container and exercise one HA tool call through Zoe chat for deployed changes.

## Child DOX Index

No child AGENTS.md files.
