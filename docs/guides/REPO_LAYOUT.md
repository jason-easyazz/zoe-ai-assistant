# Repository Layout

This file defines the active project layout so the repo stays easy to navigate.

## Active Runtime Areas

- `services/zoe-data` - primary backend API/runtime
- `services/zoe-ui` - web/touch UI assets and nginx-served frontend
- `services/zoe-auth` - authentication service
- `services/homeassistant-mcp-bridge` - Home Assistant bridge service
- `services/zoe-core` - TypeScript brain service (abilities/, bench/, extensions/)
- `services/livekit` - LiveKit voice transport
- `modules/omnigent`, `modules/zoe-music` - active module services served under `/modules/`

## Device/Deployment Setup

- `scripts/setup/touchscreen/` - Raspberry Pi touchscreen installer and templates
- `scripts/setup/` - deployment/service setup scripts
- `docs/guides/TOUCHSCREEN_DEVICE_STACK.md` - touchscreen device source of truth

## Retired Runtime Trees

Retired service code is removed from the working tree and kept in git history:

- `git log -- docs/archive/retired-services/`

Legacy root-level operational artifacts are also retained in git history:

- `git log -- docs/archive/root-legacy-2026-04-21/`

This keeps active paths clean while preserving legacy reference code in git history.

## Rule of Thumb

If a service is not part of active runtime/deployment, remove it from the working tree rather than leaving it in `services/`; git history keeps the old bytes.
