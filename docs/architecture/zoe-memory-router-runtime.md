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
  decisions. These packets pass through the governed non-persistent collector
  and are returned to the caller with a collection summary; they are not
  persisted and do not contain raw query text.
- This slice does not call MemPalace, Hindsight, Graphiti, Graphify, or Multica.

## Status Endpoint

`GET /api/system/memory-router/status`

The endpoint is admin-scoped through the existing system router and returns the
feature flag, runtime mode, safety booleans, and sample route decisions. Trace
packets remain an explicit helper option and are not included by the endpoint
by default.

## Next Runtime Step

The next PR may add a durable trace store proposal or prompt-time recall in
fallback-safe mode, still without writing durable memory unless the memory
admission contract and Multica approval gates are satisfied. Prompt-time recall
should remain behind a later feature flag after latency and safety evidence
exist.
