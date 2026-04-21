# Repository Layout

This file defines the active project layout so the repo stays easy to navigate.

## Active Runtime Areas

- `services/zoe-data` - primary backend API/runtime
- `services/zoe-ui` - web/touch UI assets and nginx-served frontend
- `services/zoe-auth` - authentication service
- `services/homeassistant-mcp-bridge` - Home Assistant bridge service
- `modules/orbit` - active module service

## Device/Deployment Setup

- `scripts/setup/touchscreen/` - Raspberry Pi touchscreen installer and templates
- `scripts/setup/` - deployment/service setup scripts
- `docs/guides/TOUCHSCREEN_DEVICE_STACK_192.168.1.61.md` - touchscreen device source of truth

## Archived/Retired Runtime Trees

Retired service code has been moved out of `services/` into:

- `docs/archive/retired-services/`

This keeps active paths clean while preserving legacy reference code in git history and in-repo archive paths.

## Rule of Thumb

If a service is not part of active runtime/deployment, archive it under `docs/archive/retired-services/` rather than leaving it in `services/`.
