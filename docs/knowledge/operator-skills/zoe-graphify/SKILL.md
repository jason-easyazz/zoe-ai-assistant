---
name: zoe-graphify
description: Use Zoe's Graphify knowledge graph before broad Zoe architecture, dependency, routing, or cross-module codebase work.
version: 1.0.0
author: Zoe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [zoe, graphify, codebase, architecture, knowledge-graph]
    related_skills: [zoe-engineering]
---

# Zoe Graphify

Zoe's code graph lives at:

```text
/home/zoe/assistant/graphify-out/
```

Before broad code search, read:

```text
/home/zoe/assistant/graphify-out/GRAPH_REPORT.md
```

Use Graphify for:

- Architecture questions.
- Finding god nodes and fragile modules.
- Understanding how files or concepts relate.
- Planning refactors across Zoe subsystems.

## Local Search Fallback

If MCP graph tools are unavailable, search the generated report and graph:

```bash
cd /home/zoe/assistant
rg -i "<concept>" graphify-out/GRAPH_REPORT.md graphify-out/graph.json
```

## Safe Refresh

Use only the full extract command:

```bash
cd /home/zoe/assistant && OPENAI_API_KEY=$(grep "^OPENAI_API_KEY=" .env | cut -d= -f2-) /home/zoe/.local/share/uv/tools/graphifyy/bin/graphify extract . --backend openai
```

Do not use:

```bash
graphify update .
graphify hook install
```

Both have inflated this repo's graph by ignoring exclusions.

## Sanity Check

After refresh:

```bash
cd /home/zoe/assistant && python3 - <<'PY'
import json
from pathlib import Path
data = json.loads(Path("graphify-out/graph.json").read_text())
print("nodes", len(data.get("nodes", [])), "edges", len(data.get("links", data.get("edges", []))))
PY
```
