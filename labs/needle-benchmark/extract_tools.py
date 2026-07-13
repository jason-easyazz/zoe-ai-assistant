#!/usr/bin/env python3
"""Extract Zoe's real tool set (names + descriptions + rough params) from the
live Flue brain's tool file (labs/flue-zoe-brain/src/tools/zoe-tools.ts) into
Needle's tools-JSON format.

LAB-ONLY. Parsing is regex-grade, good enough for a router benchmark: tool
NAMES and DESCRIPTIONS are exact (they drive Needle's tool choice); parameter
schemas are simplified to {name: {type: string}} — Needle's tool-choice head
conditions mostly on name+description, and this spike scores tool CHOICE, not
argument fidelity.

Usage: python3 extract_tools.py > zoe_tools.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TS = Path(__file__).resolve().parents[1] / "flue-zoe-brain" / "src" / "tools" / "zoe-tools.ts"

# The activator is progressive-disclosure plumbing, not a user-intent tool.
SKIP = {"activate_abilities"}


def _join_ts_string(block: str) -> str:
    """Join a TS concatenated string literal ("a" + 'b' + ...) into one str."""
    parts = re.findall(r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""", block)
    out = "".join(a or b for a, b in parts)
    return out.replace('\\"', '"').replace("\\'", "'").replace("\\n", " ")


def extract(ts_path: Path = TS) -> list[dict]:
    src = ts_path.read_text(encoding="utf-8")
    tools = []
    # Each tool: defineTool({ name: '<x>', description: <string concat>, [input: v.object({...})] ... })
    for m in re.finditer(r"name:\s*'([a-z_]+)',\s*description:\s*((?:[^,]|,(?!\s*\n\s*(?:input|run|//)))*?)(?=,\s*\n\s*(?:input|run|//))", src):
        name = m.group(1)
        if name in SKIP:
            continue
        desc = _join_ts_string(m.group(2))
        # Rough params: first v.object({...}) after this match, top-level keys.
        params: dict = {}
        tail = src[m.end():m.end() + 4000]
        im = re.search(r"input:\s*v\.object\(\{", tail)
        if im:
            body = tail[im.end():]
            depth = 1
            end = 0
            for i, ch in enumerate(body):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            for key in re.findall(r"^\s{4}([a-zA-Z_]+):", body[:end], re.M):
                params[key] = {"type": "string"}
        tools.append({"name": name, "description": desc, "parameters": params})
    if not tools:
        sys.exit(f"extract_tools: no tools parsed from {ts_path}")
    return tools


if __name__ == "__main__":
    print(json.dumps(extract(), indent=1))
