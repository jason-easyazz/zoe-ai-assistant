#!/usr/bin/env python3
"""Regenerate ``scripts/perf/zmb/arms/mpa_snapshot.json`` from the INSTALLED MemPalace (run it through the bake-off venv):

    bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh scripts/perf/zmb/pilot/mpa_snapshot.py [--check]

The snapshot freezes what the MPA arm teaches the brain and offers it, as MemPalace 3.10.0 ships it: the agent-facing tool schemas
(``mempalace.mcp_server.TOOLS``), the five-rule ``PALACE_PROTOCOL`` and the AAAK spec that ``mempalace_status`` hands the model, the Stop hook's
save request, and the "Memory (recall + writing)" paragraph of ``instructions/shared_brain_rules.md``. The slim CI lane has no MemPalace, so it
reads the snapshot; ``--check`` (and ``test_snapshot_matches_installed_library``) fail when the installed package drifted from it, so a version
bump cannot silently change what the arm measures. Nothing here writes outside the snapshot file; nothing opens a palace.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "arms" / "mpa_snapshot.json"
#: the tools the brain is offered (decision recorded in docs/research/mempalace-agent-arm-2026-10-07.md section 3)
AGENT_TOOLS = ("mempalace_status", "mempalace_search", "mempalace_add_drawer", "mempalace_update_drawer", "mempalace_kg_query", "mempalace_kg_add",
               "mempalace_kg_invalidate", "mempalace_kg_supersede", "mempalace_kg_timeline", "mempalace_diary_write")


def build() -> dict:
    import mempalace
    from importlib import resources
    from mempalace import hooks_cli
    from mempalace.mcp_server import AAAK_SPEC, PALACE_PROTOCOL, TOOLS
    rules = resources.files("mempalace").joinpath("instructions/shared_brain_rules.md").read_text(encoding="utf-8")
    start = rules.index("Memory (recall + writing):")
    end = rules.index("Coordination (logstream):")
    return {
        "mempalace_version": getattr(mempalace, "__version__", "?"),
        "palace_protocol": PALACE_PROTOCOL,
        "aaak_spec": AAAK_SPEC,
        "stop_block_reason": hooks_cli.STOP_BLOCK_REASON,
        "save_interval": hooks_cli.SAVE_INTERVAL,
        "memory_rules": rules[start:end].strip(),
        "all_tool_names": sorted(TOOLS),
        "tools": {n: {"description": TOOLS[n]["description"], "input_schema": TOOLS[n]["input_schema"]} for n in AGENT_TOOLS},
    }


def main(argv: "list[str]") -> int:
    snap = build()
    if "--check" in argv:
        have = json.loads(OUT.read_text())
        if have != json.loads(json.dumps(snap)):
            print("DRIFT: the installed MemPalace differs from arms/mpa_snapshot.json", file=sys.stderr)
            return 1
        print("snapshot matches the installed library", snap["mempalace_version"])
        return 0
    OUT.write_text(json.dumps(snap, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes; mempalace {snap['mempalace_version']}; {len(snap['tools'])} agent tools of {len(snap['all_tool_names'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
