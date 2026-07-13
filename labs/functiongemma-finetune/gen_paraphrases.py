#!/usr/bin/env python3
"""Paraphrase a sample of the template dataset via the live brain (:11434).

GENTLE by design: serial requests, one at a time, short max_tokens, and a
default budget small enough to finish in ~15 min without pressuring the box.
The live brain serves real traffic — never parallelize this.

Arg safety: a paraphrase is kept only if every "anchor" string-arg value
(item, name, fact, …) still appears verbatim (case-insensitive) in the
rewrite, so the canonical args stay correct without re-labeling.

Writes data/paraphrases.jsonl; build_dataset.py merges it on its next run.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRAIN = "http://127.0.0.1:11434/v1/chat/completions"
# args whose value must survive the rewrite verbatim for the label to hold
ANCHOR_KEYS = {"item", "name", "fact", "label", "title", "content", "query",
               "location", "list_type", "room", "moment", "notes"}

PROMPT = (
    "Rewrite the voice command below as a different casual spoken phrasing. "
    "Keep the meaning and every specific detail (names, items, numbers, times, "
    "places) EXACTLY the same. Reply with ONLY the rewritten command, no "
    "quotes, no explanation.\n\nCommand: {text}"
)


def brain_rewrite(text: str, timeout: float = 60.0) -> str | None:
    payload = {
        "messages": [{"role": "user", "content": PROMPT.format(text=text)}],
        "temperature": 0.9,
        "max_tokens": 60,
    }
    req = urllib.request.Request(
        BRAIN, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
    out = out.strip().strip('"').strip()
    out = out.splitlines()[0].strip() if out else ""
    return out or None


def anchors_ok(args: dict, rewrite: str) -> bool:
    low = rewrite.lower()
    for k, v in args.items():
        if k in ANCHOR_KEYS and isinstance(v, str) and len(v) > 2:
            if v.lower() not in low:
                return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-tool", type=int, default=12)
    ap.add_argument("--chat", type=int, default=30)
    ap.add_argument("--pause", type=float, default=1.0,
                    help="seconds between brain calls (be gentle)")
    ap.add_argument("--train", type=Path, default=HERE / "data" / "train.jsonl")
    ap.add_argument("--out", type=Path, default=HERE / "data" / "paraphrases.jsonl")
    args = ap.parse_args()

    rng = random.Random(4242)
    by_tool: dict[str | None, list[dict]] = defaultdict(list)
    for line in args.train.read_text().splitlines():
        rec = json.loads(line)
        if rec["source"].startswith("template") or rec["source"].startswith("chat"):
            by_tool[rec["tool"]].append(rec)

    written, tried = 0, 0
    with args.out.open("w") as f:
        for tool, pool in sorted(by_tool.items(), key=lambda kv: str(kv[0])):
            budget = args.chat if tool is None else args.per_tool
            picks = rng.sample(pool, min(budget * 2, len(pool)))
            got = 0
            for rec in picks:
                if got >= budget:
                    break
                tried += 1
                rw = brain_rewrite(rec["text"])
                time.sleep(args.pause)
                if not rw or len(rw) > 160:
                    continue
                if re.sub(r"\W", "", rw.lower()) == re.sub(r"\W", "", rec["text"].lower()):
                    continue  # no-op rewrite
                if rec["tool"] is not None and not anchors_ok(rec["args"], rw):
                    continue
                f.write(json.dumps({"text": rw, "tool": rec["tool"],
                                    "args": rec["args"]}) + "\n")
                f.flush()
                got += 1
                written += 1
            print(f"{tool}: {got}/{budget}")
    print(f"paraphrases kept {written} (tried {tried}) -> {args.out}")


if __name__ == "__main__":
    main()
