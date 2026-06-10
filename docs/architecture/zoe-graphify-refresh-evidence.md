# Zoe Graphify Refresh Evidence

## Purpose

This note records Graphify refresh evidence after Zoe harness foundation PRs so
the generated graph outputs have human-readable evidence attached to refresh
pull requests.

## Local/Offline Probe Lane

Zoe now has an observe-only local Graphify probe:

```bash
python3 scripts/maintenance/graphify_local_probe.py --mode smoke --timeout-sec 60 --status-json /tmp/zoe-graphify-local-smoke-status.json
python3 scripts/maintenance/graphify_local_probe.py --mode repo --timeout-sec 1800 --status-json /tmp/zoe-graphify-local-repo-status.json
python3 scripts/maintenance/graphify_local_probe.py --mode scope --include-path services/zoe-data --timeout-sec 600 --status-json /tmp/zoe-graphify-local-scope-status.json
python3 scripts/maintenance/graphify_local_model_matrix.py --mode scope --include-path services/zoe-data --model gemma-4-E2B-it-Q4_K_M.gguf --model gemma-4-e4b-it-Q4_K_M.gguf --model gemma-4-12B-it-Q4_K_M.gguf --timeout-sec 600 --allow-partial --status-json /tmp/zoe-graphify-local-model-matrix.json
python3 scripts/maintenance/graphify_local_shard_matrix.py --model gemma-4-e4b-it-Q4_K_M.gguf --timeout-sec 600 --allow-partial --status-json /tmp/zoe-graphify-local-shard-matrix.json
```

The probe uses Graphify's `ollama` backend against Zoe's localhost llama.cpp
OpenAI-compatible endpoint, removes cloud API keys from the subprocess
environment, records local model-file evidence, captures extract/cluster duration
and child-process max RSS evidence, and runs in a temporary fixture, scoped path
copy, or detached git snapshot. It never syncs generated `graphify-out` artifacts
back into the repo. The model matrix wrapper runs the same fail-closed probe
across local model candidates and records compact accepted/rejected, latency,
RSS, model-file, invalid-JSON, truncation, and context-split evidence so Zoe can
compare Gemma 4 E2B, E4B, and 12B before changing the sync-capable refresh lane. The shard matrix wrapper then runs the selected local model across named bounded repo slices, records blocked shards, accepted-shard latency, and max observed RSS across all shards, and keeps every shard observe-only until a later merge/sync strategy is proven. Default shards avoid `.zoe` operator state and docs-only directories; those require explicit `--shard` opt-in after separate safety/behavior evidence.
Scoped mode is observe-only evidence for subsystem-sized local model runs; the
sync-capable refresh wrapper still requires full repo mode with clustering.
After the first default matrix run, the default shard set was narrowed to the
accepted local slices only: `data-core` and `operators`. The UI tree remains an
explicit `--shard ui=services/zoe-ui` probe until Graphify can index Zoe's HTML,
CSS, and JavaScript assets without producing empty or malformed graph output.

`graphify_local_refresh.py` is the sync-capable local wrapper. It runs the repo
probe with clustering and `keep_workdir`, then syncs `graphify-out` back to the
repo only when the status is accepted, blockers are empty, `graph.json` is
non-empty, and `GRAPH_REPORT.md` exists. Rejected runs write
`graphify-out/.last_refresh_error` and leave committed graph artifacts untouched.

`graphify_shard_sync_plan.py` is the no-write bridge between accepted shard
evidence and a future sharded sync implementation. It consumes a
`graphify_local_shard_matrix` status JSON, rejects partial/blocked/nonlocal or
malformed shard evidence, and distinguishes `artifact_capture_ready` from
`artifact_sync_ready` so copied per-shard `graphify-out` artifacts do not imply
that deterministic graph merge handling, merged cluster/report generation, and
inventory reconciliation are already proven.

Acceptance is fail-closed. The status JSON is rejected when Graphify times out,
exits nonzero, omits `graphify-out/graph.json`, writes an empty graph, emits
invalid JSON chunks, emits truncated chunks, or surfaces cloud quota errors.
Context splits are recorded as warnings because they may recover, but they remain
important evidence for tuning local chunk budgets. Command evidence intentionally
omits raw logs from the compact JSON packet; operators should use metrics,
blockers, duration, RSS, and model-fit fields first, then inspect full terminal
logs only when a run needs repair.

Current evidence:

- Smoke mode against `gemma-4-E2B-it-Q4_K_M.gguf` on `http://127.0.0.1:11434/v1`
  accepted a one-file fixture with 2 nodes and 1 edge.
- Full-repo local extraction after `015a529` reached AST extraction for 601 code
  files and 257 docs, then was stopped after repeated context splitting and
  three invalid JSON chunks. That run was not accepted and no partial graph was
  committed.
- Repo-mode probe after `5c9ef0f` scanned 603 code files and 257 docs, completed
  AST extraction, and rejected the run after a 300 second timeout, 11 context
  split warnings, three invalid JSON chunks, and one truncated chunk. Temporary
  graph output existed inside the probe snapshot but was not accepted or
  committed.
- Local refresh wrapper short-timeout dry run rejected current main with
  `graphify_timed_out` and `graphify_exit_nonzero`, produced status JSON with a
  scrubbed workdir, and did not sync graph artifacts.
- Timeout cleanup now runs Graphify in its own process group and terminates the
  group on timeout; focused tests cover child-process cleanup, bytes output from
  timeout exceptions, dry-run marker preservation, and rsync timeout reporting.
- Local probe status now includes compact command evidence for extract/cluster
  duration and child max RSS, plus local model-file evidence so Gemma 4 E2B, E4B,
  and 12B runs can be compared without enabling a cloud backend.
- Local model matrix smoke mode accepted Gemma 4 E2B, E4B, and 12B against the
  one-file fixture with no invalid JSON, truncation, or context splits; E4B was
  the fastest accepted model at 146 ms.
- Local model matrix scoped mode accepted Gemma 4 E2B, E4B, and 12B for
  `services/zoe-data` with 290 code files and 10 docs, no invalid JSON,
  truncation, or context splits; E4B was the fastest accepted model at 47.087 s,
  with 5,089 nodes, 11,144 edges, and max child RSS about 86 MB.
- Full-repo local refresh with E4B after `3dd3218` rejected cleanly after the
  1800 second timeout. AST extraction completed over 617 code files and 257
  docs, and temporary `graph.json` output existed, but the run was blocked by
  `graphify_timed_out`, `graphify_exit_nonzero`, 26 invalid JSON chunks, 6
  truncated chunks, and 58 context split warnings. The refresh wrapper did not
  sync `graphify-out`, so the committed stale graph remains untouched.
- Shard matrix smoke run for `scripts/maintenance` accepted with E4B in 148.736 s
  over 45 code files and 1 doc, producing 306 nodes and 685 edges with no invalid
  JSON, truncation, or context splits. This proves the sharded lane can capture
  bounded local Graphify evidence after the full-repo rejection.
- Default shard matrix against `origin/main` at `911c1f6` accepted two of three
  shards with `--allow-partial`: `data-core` accepted in 103.591 s over 289 code
  files and 10 docs, producing 5,087 nodes and 11,145 edges; `operators`
  accepted in 64.320 s over 176 code files and 9 docs, producing 1,640 nodes and
  3,088 edges. Both accepted shards had zero invalid JSON chunks, truncation, or
  context splits.
- The same default shard matrix rejected `ui` in 85.404 s because the
  `services/zoe-ui` shard found 0 code files, 3 docs, 0 graph nodes, and 1
  invalid JSON chunk. This does not disprove local Gemma for Graphify; it shows
  the UI shard definition is not yet a reliable accepted source slice. The next
  default-shard PR should either point `ui` at indexable source assets or remove
  it from the default matrix until a focused UI probe is accepted.
- Corrected default shard matrix after removing `ui` accepted 2/2 default shards
  without `--allow-partial`: `data-core` accepted in 43.770 s over 289 code
  files and 10 docs, producing 5,087 nodes and 11,145 edges; `operators`
  accepted in 49.967 s over 176 code files and 9 docs, producing 1,638 nodes and
  3,075 edges. The run had no blocked shards, no invalid JSON chunks, no
  truncation, no context splits, median accepted duration 46.869 s, and max
  observed child RSS about 86 MB.
- Current default shard matrix against `origin/main` at `2032b40` accepted 2/2
  default shards without `--allow-partial`: `data-core` accepted in 43.524 s
  over 291 code files and 10 docs, producing 5,181 nodes and 11,355 edges;
  `operators` accepted in 49.883 s over 181 code files and 9 docs, producing
  1,652 nodes and 3,121 edges. The run had no blocked shards, no invalid JSON
  chunks, no truncation, no context splits, median accepted duration 46.704 s,
  and max observed child RSS 86,408 KB. This keeps the default local shard lane
  accepted on current main, but it does not make the committed full
  `graphify-out` map current.
- No-write shard sync plan against the same current default shard evidence returned
  `ready_for_artifact_merge_design` with no blockers or warnings, while keeping
  `artifact_sync_ready=false`. Required next steps are artifact-preserving shard
  runs, per-shard graph/report validation, deterministic graph JSON merge,
  merged cluster/report generation, and inventory comparison before any
  committed `graphify-out` replacement.
- Artifact-preserving default shard run against `origin/main` copied accepted
  per-shard outputs to `/tmp/zoe-graphify-shard-artifacts` without syncing the
  repo: `data-core` accepted in 43.797 s over 293 code files and 10 docs,
  producing 5,200 nodes, 11,394 edges, and a 5,034,475 byte `graph.json`;
  `operators` accepted in 49.826 s over 182 code files and 9 docs, producing
  1,660 nodes, 3,141 edges, and a 1,457,056 byte `graph.json`. The planner over
  this artifact evidence returned `artifact_capture_ready=true`,
  `artifact_report_ready=false`, and `artifact_sync_ready=false` because the run
  was non-clustered and no merged graph/report has been produced.

## Refresh 2026-06-09 Foundation Pass

Date: 2026-06-09

Source commit: `d228902dff4ec3c039f0d0a7644fa772b8e5b857`

Commands:

Note: `graphifyy` is the current uv tool install directory on the Zoe host; the executable inside that environment is still named `graphify`.

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify query "How does Zoe evaluate capability candidates?" --budget 800
```

Evidence:

- extract scanned 530 code files and 251 docs;
- `graphify-out/graph.json` wrote 7,411 nodes, 12,651 edges, and 523 communities;
- cluster-only regenerated 525 communities;
- `graphify-out/GRAPH_REPORT.md` now records built-from commit `d228902d`;
- query smoke test returned traversal results for the candidate scoring question.

## Refresh 2026-06-09 Final Harness Pass

Source commit: `0ee19f0333bd4e25d9dd3d2556aebd114a98cda5`

Commands:

```bash
WORKTREE_ROOT=/home/zoe/.worktrees/zoe-harness-final-refresh  # adjust to the clean worktree being refreshed
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) \
  ZOE_ASSISTANT_ROOT="$WORKTREE_ROOT" \
  scripts/maintenance/refresh_graphify.sh --force
```

Evidence:

- extract scanned 547 code files and 256 docs;
- `graphify-out/graph.json` wrote 7,646 nodes, 13,144 edges, and 545 communities;
- cluster-only regenerated 543 communities;
- `graphify-out/GRAPH_REPORT.md` records built-from commit `0ee19f03`;
- estimated Graphify extraction cost was `$0.1934`;
- refresh ran in a clean worktree because canonical `/home/zoe/assistant` had unrelated dirty files.

## Refresh 2026-06-09 Offline Embedding Guard Pass

Source commit: `3e9d1f2f3b561e1dc49421a8f437049859831edc`

Commands:

```bash
WORKTREE_ROOT=/home/zoe/.worktrees/zoe-graphify-post-267-refresh
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) \
  ZOE_ASSISTANT_ROOT="$WORKTREE_ROOT" \
  scripts/maintenance/refresh_graphify.sh --force
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify query "How does Zoe enforce offline-only memory for Hindsight embeddings?" --budget 800
```

Evidence:

- extract scanned 547 code files and 256 docs;
- `graphify-out/graph.json` wrote 7,688 nodes, 13,183 edges, and 593 communities;
- cluster-only regenerated 589 communities;
- `graphify-out/GRAPH_REPORT.md` records built-from commit `3e9d1f2f`;
- estimated Graphify extraction cost was `$0.1962`;
- query smoke test returned traversal results for `HindsightConfig`, `_validate_embeddings_offline_policy()`, and `_embeddings_base_url_from_env()`;
- refresh ran in a clean worktree after PR #267 merged the Hindsight offline embedding guard.

## Refresh 2026-06-10 Hindsight Latency Hardening Pass

Trigger commit: `0146caad86aa4d1c4c64d5f6895df6cbc7038033`

Commands:

Note: these are literal Zoe-host local operator paths used for this refresh;
`graphifyy` is the current uv tool install directory on the Zoe host.

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 tools/audit/validate_offline_memory.py
git diff --check
```

Evidence:

- Graphify was refreshed in PR #351 after PR #349 merged;
- `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` contain the generated graph metrics for the final refresh;
- structure, critical-file, offline-memory, and diff whitespace validators passed;
- refresh ran after PR #349 hardened Hindsight recall latency summaries so missing enabled latency remains unmeasured instead of becoming `0ms`.

## Refresh 2026-06-10 Pipeline And Worktree Policy Pass

Trigger commit: `e93a8e3605b28e2a7c5b8745ae68ee0d335a752d`

Commands:

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 tools/audit/validate_offline_memory.py
git diff --check
```

Evidence:

- Graphify was refreshed after PR #346, PR #352, and PR #353 merged;
- the full refresh recovered from one transient OpenAI rate-limit retry and completed successfully;
- `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` contain the generated graph metrics for the final refresh;
- structure, critical-file, offline-memory, and diff whitespace validators passed;
- refresh ran after the pipeline duplicate-phase regression fix, trunk/worktree prune policy, and prune-script hardening landed on main.

## Refresh 2026-06-10 Shopping Card Contract Pass

Trigger commits:

- `096288e12cbd4598de26a79e7b9bfb1ec1d6e6fc` (PR #201)
- `15f17b0bf3b8d462033ece301aa644654bcb5611` (PR #357)

Commands:

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 tools/audit/validate_offline_memory.py
git diff --check
```

Evidence:

- Graphify was refreshed after PR #201 added shopping/list card builders and PR #357 normalized shopping card item payloads;
- the source change closed the shopping/list card contract loop through `card_service.py`, `routers/chat.py`, action-form payloads, and focused tests;
- `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` contain the generated graph metrics for the final refresh;
- structure, critical-file, offline-memory, and diff whitespace validators passed.

## Refresh 2026-06-10 Intent Gap Budget And Follow-Up Dedup Pass

Trigger commit: `dc72d877f85db6173648633c3189ece700500091` (PR #356)

Commands:

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 tools/audit/validate_offline_memory.py
git diff --check
```

Evidence:

- Graphify was refreshed after PR #356 added the intent-gap pre-edit budget guard and cross-status blocker follow-up dedup;
- focused verification for PR #356 covered `test_kanban_adapter.py`, `test_main_multica_poll.py`, and Python compilation of touched files;
- `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` contain the generated graph metrics for the final refresh;
- structure, critical-file, offline-memory, and diff whitespace validators passed.

## Refresh 2026-06-10 Zoe Identity Utility Naming Pass

Trigger commit: `c766e2b11ff0a0260db86bbe62c06ce5abdf7927` (PR #360)

Commands:

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 tools/audit/validate_offline_memory.py
git diff --check
```

Evidence:

- Graphify was refreshed after PR #360 renamed utility intelligence checks from concrete Samantha naming to Zoe identity and continuity naming;
- focused verification for PR #360 covered Python compilation of the touched utility scripts, `git grep -n -i "samantha" -- scripts/utilities`, and the standard Zoe validators;
- `docs/reports/` is ignored so generated utility reports do not become accidental committed output;
- `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` contain the generated graph metrics for the final refresh;
- structure, critical-file, offline-memory, and diff whitespace validators passed.

## Refresh 2026-06-10 Memory Router Context Terms Pass

Trigger commit: `ae49ed8a36a85ba7bda677e5fb3b85d121dafb21` (PR #362)

Commands:

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 tools/audit/validate_offline_memory.py
git diff --check
```

Evidence:

- Graphify was refreshed after PR #362 hardened Zoe memory router code-context terms so casual service language stays on the fast MemPalace path;
- focused verification for PR #362 covered `test_zoe_memory_router.py`, Python compilation of `zoe_memory_router.py`, and the standard Zoe validators;
- the PR kept router matching deterministic while adding regression coverage for casual service false positives and explicit architecture service queries;
- `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` contain the generated graph metrics for the final refresh;
- structure, critical-file, offline-memory, and diff whitespace validators passed.

## Refresh 2026-06-10 Hindsight Bake-Off User Parameterization Pass

Trigger commit: `f05471cf1032a7d986d44f40211186b8a257c0d2` (PR #364)

Commands:

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 tools/audit/validate_offline_memory.py
git diff --check
```

Evidence:

- Graphify was refreshed after PR #364 parameterized Hindsight bake-off synthetic events and eval queries for multi-user runs;
- focused verification for PR #364 covered `test_hindsight_bakeoff.py`, Python compilation of the bake-off module and runner, a disabled/offline `--user-id casey` JSON runner smoke test, and a blank `--user-id` argparse error smoke test;
- the PR preserved default fixture behavior while allowing explicit user overrides without production chat wiring;
- `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` contain the generated graph metrics for the final refresh;
- structure, critical-file, offline-memory, and diff whitespace validators passed.

## Refresh 2026-06-10 Say-Exactly Intent Gap Helper Pass

Trigger commit: `91762d3e12d6994db53dcc16b17b1f2b8ab0d4d5` (PR #366)

Commands:

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 tools/audit/validate_offline_memory.py
git diff --check
```

Evidence:

- Graphify was refreshed after PR #366 added the deterministic say-exactly intent-gap helper and narrowed the Hermes hint guard to the exact ticket title pattern;
- extract scanned 588 code files and 257 docs;
- extract recovered from one transient OpenAI rate-limit message and completed successfully;
- `graphify-out/graph.json` wrote 8,256 nodes, 14,729 edges, and 528 extract-time communities;
- cluster-only regenerated 537 communities;
- `graphify-out/GRAPH_REPORT.md` records built-from commit `91762d3e`;
- estimated Graphify extraction cost was `$0.1713`;
- structure, critical-file, offline-memory, and diff whitespace validators passed.

## Refresh 2026-06-10 Active Zoe Identity Wording Pass

Trigger commit: `796705a88d3af8684dad1d072005d713d7f9ef82` (PR #368)

Commands:

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 tools/audit/validate_offline_memory.py
git diff --check
```

Evidence:

- Graphify was refreshed after PR #368 replaced active Samantha-inspired runtime/UI wording with Zoe-specific identity wording while leaving archived/research inspiration and OS voice-name matching untouched;
- extract scanned 588 code files and 257 docs;
- `graphify-out/graph.json` wrote 8,272 nodes, 14,760 edges, and 562 extract-time communities;
- cluster-only regenerated 564 communities;
- `graphify-out/GRAPH_REPORT.md` records built-from commit `796705a8`;
- estimated Graphify extraction cost was `$0.1951`;
- structure, critical-file, offline-memory, and diff whitespace validators passed.

## Refresh 2026-06-10 Worktree Read Guard Pass

Trigger commit: `75f5345d4c5886e103ae2c65548a42bad6f76390` (PR #369)

Commands:

Note: these are literal Zoe-host local operator paths used for this refresh;
`graphifyy` is the current uv tool install directory on the Zoe host.

```bash
OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" /home/zoe/assistant/.env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
/home/zoe/.local/share/uv/tools/graphifyy/bin/graphify cluster-only . --no-viz
python3 tools/audit/validate_structure.py
python3 tools/audit/validate_critical_files.py
python3 tools/audit/validate_offline_memory.py
git diff --check
```

Evidence:

- Graphify was refreshed after PR #369 added the pinned-worktree read guard and updated the say-exactly helper prompt to require the task `workspace_path`;
- extract scanned 588 code files and 257 docs;
- `graphify-out/graph.json` wrote 8,289 nodes, 14,760 edges, and 571 extract-time communities;
- cluster-only regenerated 566 communities;
- `graphify-out/GRAPH_REPORT.md` records built-from commit `75f5345d`;
- estimated Graphify extraction cost was `$0.1978`;
- structure, critical-file, offline-memory, and diff whitespace validators passed.
