# Zoe Graphify Refresh Evidence

## Purpose

This note records the Graphify refresh after the Zoe harness foundation PRs so the generated graph outputs have human-readable evidence attached to the same pull request.

## Refresh

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
