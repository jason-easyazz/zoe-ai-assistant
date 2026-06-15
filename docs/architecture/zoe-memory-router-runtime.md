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

## Cached Packet Measurement

`scripts/maintenance/memory_prompt_packet_measure.py` measures the existing
`MemoryService.load_for_prompt()` read path plus cached packet compilation. It
can seed synthetic evidence-backed rows for the synthetic user
`zoe-prompt-packet-measure`, measures three representative packet queries, and
deletes the synthetic rows by default. The CLI refuses `--seed-synthetic` for non-synthetic user IDs before loading `MemoryService`, so cleanup can only delete synthetic measurement users.

Command:

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/memory_prompt_packet_measure.py --seed-synthetic
```

Live Zoe-host result from 2026-06-11:

| Metric | Value |
| --- | --- |
| Synthetic rows seeded | 7 |
| Cases | 3 |
| Min accepted memories | 3 |
| All packets cited | `true` |
| All packets safe | `true` |
| p50 `load_for_prompt` latency | 17.50 ms |
| p95 `load_for_prompt` latency | 21.84 ms |
| p50 packet compile latency | 2.25 ms |
| p95 packet compile latency | 2.71 ms |
| p50 total latency | 20.26 ms |
| p95 total latency | 22.53 ms |
| Cleanup removed | 7 rows |

The packet compiler itself clears the 0-50 ms cached-packet budget on this
measurement. The full read-plus-compile path also cleared 50 ms for the
synthetic MemPalace prompt-read fixture, but this is still measurement evidence
only: prompt injection remains disabled until a separate chat integration PR
adds explicit timeout, fallback, and user-correction guards.

## Next Runtime Step

The next PR may add a chat-integration proposal for cached packet use in
fallback-safe mode, still without writing durable memory unless the memory
admission contract and Multica approval gates are satisfied. Prompt-time
injection should remain behind a later feature flag with explicit latency
timeouts, empty-packet fallback, user-correction precedence, and observation
traces.
