---
name: zoe-board
description: Use Zoe's Multica board and evolution proposal tools to track durable engineering tasks, approvals, and self-improvement work.
version: 1.0.0
author: Zoe
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [zoe, multica, board, planning, issues, evolution]
    related_skills: [zoe-engineering]
---

# Zoe Board

Use Multica as the durable board for Zoe engineering and self-evolution tasks.

Default API:

```text
http://127.0.0.1:8080
```

Preferred local helper:

```text
/home/zoe/bin/zoe-tools-mcp.py
```

Do not print `MULTICA_API_TOKEN` or any other token.

## Workflow

1. Check existing issues before creating duplicates.
2. Store real follow-up work in Multica, not only in chat.
3. Use clear titles, phase labels, acceptance criteria, and verification notes.
4. For world-changing Zoe actions, preserve the proposal/approval path.
5. Hermes owns board repair by default. OpenClaw is fallback only if a browser/session workflow is required.

## Useful Concepts

- `ZOE-2869`: agentic engineering setup epic.
- `ZOE-2873`: Cursor plan triage and stale plan cleanup.
- `ZOE-2878`: future LLM person extractor and proactive suggestion detector.
- `ZOE-2879`: future Authentik retirement cleanup audit.

## Verification

When an item is completed, update the Multica issue with:

- What changed.
- Tests/checks run.
- Any remaining blocker.
- PR or commit reference if available.
