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
- Cached prompt packet preview is separately gated by
  `ZOE_MEMORY_PROMPT_PACKET_PREVIEW_ENABLED=false` by default.
- Prompt packet preview compiles compact cited lines only from caller-supplied
  cached rows; it does not call MemPalace, Hindsight, Graphiti, Graphify, or
  Multica.
- Prompt packet preview still returns `can_inject_prompt=false` and
  `can_write_memory=false`; a later chat integration must add a separate gate
  before prompt injection is possible.

## Status Endpoint

`GET /api/system/memory-router/status`

The endpoint is admin-scoped through the existing system router and returns the
feature flag, runtime mode, prompt packet preview flag, safety booleans, and
sample route decisions. Trace packets remain an explicit helper option and are
not included by the endpoint by default.

## Cached Prompt Packet Preview

`compile_cached_memory_prompt_packet()` is the first prompt packet contract, but
it is intentionally inert. It accepts already-cached candidate rows, filters out
rows without evidence, rejects cross-user personal/shared rows, suppresses
archived rows, prefers active/current rows over disputed or superseded rows, and
returns short lines with evidence IDs. It never fetches from a backend and never
authorizes prompt injection.

This is the safe follow-up to the Hindsight budget-matrix result: direct
Hindsight recall remained above the 600 ms hot-path target, so Zoe can measure
compact cached packets before considering any live chat path.

## Next Runtime Step

The next PR may add cached packet measurement from a real existing cache or
prompt-time recall in fallback-safe mode, still without writing durable memory
unless the memory admission contract and Multica approval gates are satisfied.
Prompt-time injection should remain behind a later feature flag after latency
and safety evidence exist.
