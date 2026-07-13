#!/usr/bin/env python3
"""Q4 — prompt-token savings if a prefilter shortlists 2-3 tools instead of the
full tool block going into the Gemma brain's context.

Counts GEMMA tokens (the tokens that actually cost prefill time on :11434) via
llama-server's /tokenize endpoint — a pure tokenizer call, no generation, no
GPU-slot contention. Falls back to a chars/4 estimate with --no-server.

The "tool block" is Zoe's real 20-tool set (extract_tools.py) serialized the
way a JSON tool schema rides in a prompt.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LLAMA = "http://127.0.0.1:11434"


def gemma_tokens(text: str, use_server: bool) -> int:
    if use_server:
        req = urllib.request.Request(
            f"{LLAMA}/tokenize", data=json.dumps({"content": text}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return len(json.loads(r.read()).get("tokens", []))
    return max(1, len(text) // 4)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", default=str(HERE / "zoe_tools.json"))
    ap.add_argument("--no-server", action="store_true")
    ap.add_argument("--shortlist", type=int, default=3)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    tools = json.loads(Path(args.tools).read_text())
    use_server = not args.no_server
    per_tool = {t["name"]: gemma_tokens(json.dumps(t, separators=(",", ":")), use_server)
                for t in tools}
    full = gemma_tokens(json.dumps(tools, separators=(",", ":")), use_server)
    sizes = sorted(per_tool.values())
    k = args.shortlist
    # Shortlist cost range: k smallest .. k largest tool schemas (+ JSON glue).
    short_min, short_max = sum(sizes[:k]), sum(sizes[-k:])
    short_avg = round(k * (sum(sizes) / len(sizes)))
    out = {
        "tokenizer": "llama-server /tokenize (Gemma)" if use_server else "chars/4 estimate",
        "n_tools": len(tools),
        "full_block_tokens": full,
        "per_tool_tokens": per_tool,
        f"shortlist_{k}_tokens_avg": short_avg,
        f"shortlist_{k}_tokens_range": [short_min, short_max],
        "savings_tokens_avg": full - short_avg,
        "savings_pct_avg": round(100 * (full - short_avg) / full, 1),
    }
    Path(args.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "per_tool_tokens"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
