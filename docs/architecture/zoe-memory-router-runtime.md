# Zoe Memory Router Runtime Gate

Zoe's deterministic memory router can now be inspected at runtime without
changing chat behavior.

The helper lives in `services/zoe-data/zoe_memory_router_runtime.py`.

## Contract

- Default is disabled: `ZOE_MEMORY_ROUTER_RUNTIME_ENABLED=false`.
- Enabled mode is observe-only.
- Chat hot-path prompt injection remains disabled.
- Durable memory writes remain disabled.
- Runtime status shows sample route decisions only when the runtime flag is enabled
  or a test/operator call explicitly requests samples.
- Runtime calls can include optional observation trace packets for route
  decisions. These packets are returned to the caller only; they are not
  persisted and do not contain raw query text.
- This slice does not call MemPalace, Hindsight, Graphiti, Graphify, or Multica.

## Status Endpoint

`GET /api/system/memory-router/status`

The endpoint is admin-scoped through the existing system router and returns the
feature flag, runtime mode, safety booleans, and sample route decisions. Trace
packets remain an explicit helper option and are not included by the endpoint
by default.

## Next Runtime Step

The next PR may persist or forward observation traces through a governed
collector, still without injecting memory packets into prompts or writing
durable memory. Prompt-time recall should remain a later feature flag after
latency and safety evidence exist.
