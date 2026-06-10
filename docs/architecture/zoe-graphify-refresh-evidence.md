# Zoe Graphify Refresh Evidence

## Purpose

This note records Graphify refresh evidence after Zoe harness foundation PRs so
the generated graph outputs have human-readable evidence attached to refresh
pull requests.

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
