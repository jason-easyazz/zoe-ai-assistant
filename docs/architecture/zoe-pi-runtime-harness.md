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

Current Pi install/readiness facts from upstream docs:

- Pi is distributed as `@earendil-works/pi-coding-agent` and the safe npm install command is
  `npm install -g --ignore-scripts @earendil-works/pi-coding-agent`.
- Current Pi requires Node.js `>=22.19.0` based on the Pi 0.75.0 release notes.
- Zoe's probe may execute `node --version` and `npm --version` for readiness, but it does not
  execute `pi`, install packages, or run agent/model tasks.
- Installing Node/Pi remains a Multica-governed runtime change, not an automatic probe action.

Sources checked 2026-06-15: https://pi.dev/docs/latest/quickstart and https://pi.dev/news.

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
| `ZOE_PI_INTENT_SHADOW_ENABLED` | `false` | Collect Pi-vs-Zoe intent comparison records without executing Pi output. |
| `ZOE_PI_INTENT_SHADOW_PATH` | `~/.zoe/data/pi-intent-shadow.jsonl` | JSONL evidence path for shadow records. |
| `ZOE_PI_INTENT_SHADOW_MAX_WORDS` | `32` | Maximum utterance length eligible for shadow comparison. |
| `ZOE_PI_INTENT_SHADOW_INCLUDE_PREVIEW` | `true` | Store a short sanitized text preview alongside the text hash. |
| `ZOE_PI_INTENT_SHADOW_FORCE_ENABLED` | `true` | Force the classifier on inside shadow mode while keeping live routing unchanged. |
| `ZOE_PI_INTENT_MISS_EVIDENCE_ENABLED` | `false` | Write sanitized intent-miss candidate rows for later review/labeling. |
| `ZOE_PI_INTENT_MISS_EVIDENCE_PATH` | `~/.zoe/data/pi-intent-miss-evidence.jsonl` | JSONL path for structured intent-miss candidates. |
| `ZOE_PI_INTENT_AUTO_PROMOTE` | `false` | Report whether automatic Pi promotion was requested. Current runtime remains evidence-only and requires the guarded apply helper. |
| `ZOE_PI_INTENT_PROMOTED_GROUPS` | unset | Comma-separated low-risk intent groups promoted through Pi for live fallback execution and rollback reporting. Unknown or privileged groups are ignored. |

Readiness report fields:

- `tool_versions.node` / `tool_versions.npm`: read-only version snapshots from `--version`.
- `requirements.node.minimum`: current Pi Node minimum, currently `22.19.0`.
- `requirements.node.status`: `missing`, `unknown`, `too_old`, or `ok`.
- `install_plan.install_command`: the operator-reviewed npm install command.
- `install_plan.requires_multica_approval`: always `true` for runtime installation.

## Probe

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/pi_runtime_probe.py --json
```

The probe is safe to run in production-adjacent environments because it only
uses filesystem and `PATH` checks plus `node --version` and `npm --version`.
It does not execute `pi`, install packages, or run agent/model tasks.

## Intent Shadow Evidence

Pi intent shadow mode is disabled by default. When
`ZOE_PI_INTENT_SHADOW_ENABLED=true`, Zoe's current intent route remains
authoritative and Pi output is never executed by the shadow path. The shadow
producer records sanitized JSONL evidence containing Zoe intent, Pi intent,
route class, confidence, agreement, timeout, and latency fields.

Admin status is available at:

```bash
GET /api/system/pi-intent/shadow-status
```

The first runtime slice reports agreement and latency for all records. When a
record includes `outcome_label`, the admin report also converts it into the same
Pi promotion scoring contract used by `pi_promotion_eval.py`, including
`promotable_groups` and `rollback_groups`. Reviewed records may also set
`user_corrected=true` or `rollback_blocked=true`; those fields feed the same
rollback gates as synthetic promotion samples. The promotion report includes
`route_class_breakdown` for deterministic, fallback, and extraction_failed
baselines, `transport_breakdown` for print vs RPC latency and accuracy, plus
compact `failure_examples` with IDs, intents, latency, transport, and reason
flags, but not raw text or text previews. Unlabeled
records are never treated as accuracy evidence. Pi live execution remains
separately gated by
`ZOE_PI_INTENT_PROMOTED_GROUPS`, so enabling the classifier alone does not
promote all Pi classifications into executable Zoe routes. The report includes a read-only
`promotion_actions.env.ZOE_PI_INTENT_PROMOTED_GROUPS` recommendation for operator
review; Zoe does not rewrite env or auto-promote groups from shadow evidence.
`ZOE_PI_INTENT_AUTO_PROMOTE=true` is reported for visibility, but current runtime
still requires the guarded apply helper rather than silently editing configuration.
Operators can inspect or explicitly apply that single env update with:

```bash
scripts/maintenance/pi_promotion_eval.py --demo --min-samples 10 \
  | scripts/maintenance/pi_promotion_apply.py --env-file /path/to/.env
scripts/maintenance/pi_promotion_eval.py --demo --min-samples 10 \
  | scripts/maintenance/pi_promotion_apply.py --env-file /path/to/.env --apply --confirm APPLY_PI_PROMOTION
```

The apply helper only writes `ZOE_PI_INTENT_PROMOTED_GROUPS`; it rejects any other env key in the report.

Optional structured intent-miss evidence can be enabled with
`ZOE_PI_INTENT_MISS_EVIDENCE_ENABLED=true`. This writes sanitized candidate rows
to `ZOE_PI_INTENT_MISS_EVIDENCE_PATH` without changing the live route. These rows
start unlabeled and must be reviewed by an operator before adding an
`outcome_label` for promotion scoring.

## Evaluation Datasets

The promotion evaluator can load sanitized JSON or JSONL case files so Zoe can
grow evidence from intent misses, known failures, chat/voice-derived examples,
synthetic ambiguous phrasing, and negative casual-chat cases without editing
Python code. The seed dataset lives at:

```bash
data/eval/pi_intent_eval_cases.jsonl
```

Run the seed dataset without touching live routing:

```bash
scripts/maintenance/pi_promotion_eval.py \
  --cases-file data/eval/pi_intent_eval_cases.jsonl \
  --no-default-cases
```

Add `--run-pi --transport rpc` only when the local/offline Pi intent runtime is
configured. The RPC path keeps a warm worker but resets it on timeout or task
cancellation, and ignores response events whose request id does not match the
current prompt. The report still remains evidence-only;
`ZOE_PI_INTENT_PROMOTED_GROUPS` changes must pass the promotion actions and
guarded apply helper described above.

Sanitized JSONL evidence can be exported into the same eval-case format. For Pi
shadow records this uses `text_preview` plus a trusted `outcome_label`; unlabeled,
privileged, secret-looking, or overlong rows are skipped:

```bash
scripts/maintenance/pi_eval_export.py ~/.zoe/data/pi-intent-shadow.jsonl \
  --source intent_miss \
  --case-prefix shadow \
  --output /tmp/pi-intent-export.jsonl \
  --summary
scripts/maintenance/pi_promotion_eval.py \
  --cases-file /tmp/pi-intent-export.jsonl \
  --no-default-cases
```

## Review-Only Runtime Proposal

Pi adoption now has an inert proposal builder:

```bash
PYTHONPATH=services/zoe-data python3 scripts/maintenance/pi_runtime_proposal.py --legacy-row
```

The proposal builder runs the read-only probe, attaches Zoe's existing Pi
candidate score, records local/offline model prerequisites, and emits the legacy
`evolution_proposals` row shape with a validated Zoe proposal contract in
`target_patterns`. It does not write the database, install Node/npm/Pi, create
agents, or enable execution. On the current host the proposal remains blocked by
`offline:partial` and `score_below_threshold` until runtime and local-model
evidence are supplied and approved.

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
