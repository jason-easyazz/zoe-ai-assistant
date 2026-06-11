# Auto Research Run: Zoe System Improvements

Goal: continuously improve Zoe system quality for 4 hours while preserving tools and governance.

Editable asset allowlist:
- services/**/*
- scripts/**/*
- tests/**/*
- docs/**/*
- config/**/*
- skills/**/*
- .github/**/*

Locked exclusions:
- Do not edit run artifacts under data/autoresearch/jun11-system-4h after setup.
- Do not edit or remove tool/runtime integration definitions we rely on: MCP tool schemas/dispatch, Hermes/OpenClaw/GitHub/Greptile connector mechanics, .codex rules, or local app tool definitions.
- Do not perform destructive database operations.
- Do not change the scorer to improve the score.

Locked scoring command:
python3 data/autoresearch/jun11-system-4h/score.py

Metric: higher score is better. The scorer rewards passing validators/tests/compilation and penalizes broad exception debt in selected production code.

Loop:
1. Score baseline.
2. Make one focused improvement.
3. Commit it.
4. Score again.
5. Keep if score improves; revert if score worsens or crashes.
6. Append results.tsv.
7. Continue until 4 hours elapse or stopped.
