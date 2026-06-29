# services/homeassistant-mcp-bridge/ — Home Assistant MCP bridge

## Purpose

Small containerized bridge exposing Home Assistant control to Zoe over MCP. Runtime code lives in `main.py`; service-local regression tests live under `tests/`.

## Local Contracts

- Smart-home concepts stay in skills and scopes; this bridge exposes tools, it must not push household assumptions into kernel code.
- HA credentials come from environment configuration, never hardcoded.
- The live Home Assistant runtime tree at `homeassistant/` (repo root) is data, not code — do not edit it from here.

## Work Guidance

(empty)

## Verification

- `python3 -m pytest services/homeassistant-mcp-bridge/tests/test_ha_bridge.py -q`
- Rebuild the container and exercise one HA tool call through Zoe chat for deployed changes.

## Child DOX Index

No child AGENTS.md files.
