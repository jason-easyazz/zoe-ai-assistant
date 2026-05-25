# Hermes Engineering Skills

Zoe uses Hermes as the default engineering, planning, review, and repair agent. The Zoe-specific Hermes skills live outside the repo at:

```text
~/.hermes/skills
```

These are operator-level Hermes skills. They are not Zoe runtime skills under `/home/zoe/assistant/skills`, and they should not be copied there unless the goal is to expose them as user-facing Zoe capabilities.

## Core Zoe Skills

`zoe-engineering`
: Default Zoe engineering workflow. It tells Hermes to use Graphify first, use `opensrc` for package source, keep work PR-sized, build the minimal feature first, run cleanup after the feature works, use Greptile, and verify with Zoe validators.

`source-code-context`
: Use when integrating packages, SDKs, MCP servers, browser tools, or open-source services. It tells Hermes to inspect local source under `~/.opensrc/repos` before guessing APIs.

`code-structure-cleanup`
: Use after a feature works. It guides Hermes to extract only repeated runtime mechanics into service-layer helpers while keeping domain policy in routes, actions, intents, and UI handlers.

`github-greptile-loop`
: Use for PR review and repair loops. It fetches live Greptile state, fixes real findings, verifies, pushes, and triggers re-review until the PR is clean or a human decision is needed.

`zoe-graphify`
: Use for broad Zoe architecture or cross-module work. It points Hermes at `graphify-out/GRAPH_REPORT.md` and the safe Graphify refresh commands.

`zoe-status-refresh`
: Use before and after substantial Zoe work. It checks health, validators, and agent knowledge sync.

## Skill Discovery

Zoe parses Hermes skills from `~/.hermes/skills` through:

```text
services/zoe-data/skill_discovery.py
```

`agent_sync.py` includes discovered skills in the federation skill snapshot. To refresh generated agent context after skill changes, use the existing Zoe sync path rather than editing generated files by hand.

## Maintenance Notes

- Keep the detailed skill instructions in `~/.hermes/skills`.
- Keep repo rules and docs concise; they should point to the skills, not duplicate every instruction.
- Do not vendor external source repos into Zoe. Use `opensrc` caches under `~/.opensrc/repos`.
- Do not expose operator workflow skills as Zoe runtime capabilities unless the product explicitly needs that.

## Verification

To confirm the skills are discoverable:

```bash
cd /home/zoe/assistant
python3 - <<'PY'
import sys
sys.path.insert(0, "services/zoe-data")
from skill_discovery import parse_hermes_skills
ids = {s["id"] for s in parse_hermes_skills()}
for expected in [
    "zoe-engineering",
    "source-code-context",
    "code-structure-cleanup",
    "github-greptile-loop",
]:
    print(expected, expected in ids)
PY
```
