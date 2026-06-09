# Zoe Hindsight Bake-Off

## Purpose

Hindsight remains an offline-only candidate for Zoe reflective memory: lessons, recurring failures, fixes, and experience summaries. It is not a replacement for MemPalace and it is not in the chat hot path.

This document records the current bake-off runner and the first Zoe-host availability check.

## Harness

Files:

- `services/zoe-data/hindsight_bakeoff.py`
- `scripts/maintenance/hindsight_bakeoff.py`
- `services/zoe-data/tests/test_hindsight_bakeoff.py`

The runner uses the synthetic Zoe memory events from `hindsight_bakeoff.py`, can optionally retain them into a configured Hindsight sidecar, then measures recall scores and p50/p95 latency across the evaluation queries.

Command:

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/hindsight_bakeoff.py --json
```

To write synthetic events, the operator must explicitly enable Hindsight and opt into writes:

```bash
HINDSIGHT_ENABLED=true PYTHONPATH=services/zoe-data python3 scripts/maintenance/hindsight_bakeoff.py --retain-synthetic --json
```

Zoe memory remains offline-only. `HindsightConfig` rejects public/cloud model providers unless an OpenAI-compatible provider points to a localhost/private base URL.

## Current Zoe-Host Check

Date: 2026-06-09

Environment:

- Host: Zoe Jetson service host.
- Expected sidecar URL: `http://127.0.0.1:8888`.
- Sidecar status: not running; `curl --max-time 2 http://127.0.0.1:8888/health` returned connection refused.
- Process/container status: no Hindsight process or container was running.
- Runner mode: default disabled config, no retain, no writes.

Measured disabled-run result:

| Metric | Value |
| --- | --- |
| Cases | 4 |
| Average score | 0.0 |
| Minimum score | 0.0 |
| p50 latency | 0.0024 ms |
| p95 latency | 0.0066 ms |
| Sidecar writes | 0 |

This is not a Hindsight acceptance result. It is an availability baseline and a fixed runner. The Hindsight bake-off remains incomplete until a local/offline sidecar is started and measured with synthetic retain/recall.

## Acceptance Use

Hindsight should not move into production recall until a measured sidecar run proves:

- recall p95 under 600 ms for normal low/mid budget use, or an explicit async-only designation;
- returned memories include evidence/source pointers;
- retain can run async without blocking chat;
- wrong, disputed, or superseded memories can be corrected;
- user/scope isolation passes;
- no cloud model provider is required;
- Jetson CPU/RAM impact is acceptable or the service is marked sidecar/remote-only.
