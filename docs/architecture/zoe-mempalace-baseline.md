# Zoe MemPalace Baseline

## Purpose

MemPalace remains Zoe's offline baseline memory layer until another offline system beats it on Zoe-specific recall, latency, footprint, and governance tests. This document records the repeatable baseline harness and the first measured run from the Zoe host.

## Harness

Files:

- `services/zoe-data/mempalace_baseline.py`
- `scripts/maintenance/mempalace_baseline.py`
- `services/zoe-data/tests/test_mempalace_baseline.py`

The runner uses `MemoryService`, writes four synthetic benchmark memories for the synthetic user `zoe-mempalace-baseline`, runs scoped recall queries, scores expected terms, reports latency, and deletes the synthetic user rows by default.

Command:

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/mempalace_baseline.py --timeout 3.0
```

Use `--keep` only when intentionally inspecting benchmark rows.

## Baseline Run

Date: 2026-06-09

Environment:

- Host: Zoe Jetson service host.
- Memory path: `MemoryService` -> MemPalace.
- Models: offline/local memory stack; no cloud model provider required.
- Cleanup: default cleanup removed 4 synthetic rows.

Results:

| Metric | Value |
| --- | --- |
| Cases | 4 |
| Average score | 1.0 |
| Minimum score | 1.0 |
| p50 latency | 112.77 ms |
| p95 latency | 200.90 ms |
| Cleanup removed | 4 rows |

Case results:

| Case | Hit Count | Score | Latency |
| --- | --- | --- | --- |
| `weather_failure_fix` | 1 | 1.0 | 51.29 ms |
| `memory_preference` | 2 | 1.0 | 73.71 ms |
| `governance_approval` | 3 | 1.0 | 209.56 ms |
| `tool_capability` | 4 | 1.0 | 151.83 ms |

## Acceptance Use

This baseline does not prove MemPalace is perfect. It gives Zoe a repeatable local measurement before Hindsight and Graphiti comparisons. Future memory backends should be compared against the same cases plus expanded relational/supersession cases.

A replacement should not displace MemPalace unless it wins on the Zoe benchmark set while preserving:

- offline operation;
- user/scope isolation;
- compact cited recall packets;
- correction/supersession behavior;
- acceptable Jetson CPU/RAM use;
- normal chat latency regression under 10%.
