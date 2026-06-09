# Zoe Pi Runtime Harness

## Purpose

Pi is a candidate external runtime for Zoe capability reuse. Zoe should use Pi
where it provides proven agent/runtime/package leverage, but Pi execution must
enter through Zoe governance rather than bypass it.

This first slice is intentionally read-only:

- detect whether `node`, `npm`, and `pi` are present;
- discover project-local `.pi/agents/*.md` files;
- expose execution policy config;
- fail closed when execution is requested without a local/offline model path;
- avoid installing Pi, installing packages, or running Pi agent tasks.

## Current Zoe Host Result

Date: 2026-06-09

On the clean Zoe worktree:

- `node`: not found;
- `npm`: not found;
- `pi`: not found;
- `.pi/agents`: not present in the Zoe repo;
- execution status: disabled and acceptable;
- enabled execution status: blocked until runtime prerequisites and local model config exist.

## Configuration

Environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ZOE_PI_ENABLED` | `false` | Enable Pi runtime availability checks as a candidate execution surface. |
| `ZOE_PI_ALLOW_EXECUTION` | `false` | Allow Pi task execution after governance approval. |
| `ZOE_PI_OFFLINE_ONLY` | `true` | Require local/offline operation for Zoe-managed Pi execution. |
| `ZOE_PI_LOCAL_MODEL_REQUIRED` | `true` | Require an explicit local model signal before execution. |
| `ZOE_PI_LOCAL_MODEL_CONFIGURED` | `false` | Operator evidence that Pi is configured for a local/offline model. |
| `ZOE_PI_COMMAND` | `pi` | Pi CLI command name or path. |
| `ZOE_PI_CWD` | `/home/zoe/assistant` | Project directory for Pi runtime discovery. |
| `ZOE_PI_AGENT_DIR` | unset | Optional explicit `.pi/agents` directory. |
| `ZOE_PI_TIMEOUT_SECONDS` | `2.0` | Future runtime command timeout. |

## Probe

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/pi_runtime_probe.py --json
```

The probe is safe to run in production-adjacent environments because it only
uses filesystem and `PATH` checks. It does not execute `pi`.

## Adoption Rules

Pi remains experimental until all of these are true:

- Node/npm/Pi are installed or otherwise available on the intended execution host;
- Pi is configured for local/offline model use, or the task is explicitly not a
  Zoe offline/local-first path;
- candidate scoring passes license, security, footprint, activity, offline, and
  overlap checks;
- Multica approves any install, package adoption, or runtime behavior change;
- execution evidence includes tests, rollback, and PR/Grep loop evidence for
  code-producing changes.

## Why Not Execute Yet

Pi's official docs support CLI, SDK, RPC, project-local agents, and packages,
but the Zoe host does not currently have the Node/Pi runtime needed to run it.
Installing that runtime is a privileged environment change. The correct next
step is a scored, approved install/runtime proposal, not silent installation.
