#!/usr/bin/env python3
"""Summarize SetFit router-head shadow logs (ZOE_ROUTER_HEAD=shadow).

Reads the JSONL written by services/zoe-data/semantic_router._head_shadow
(default services/zoe-data/data/router_head_shadow.jsonl) and reports:

- turn count + unique utterances (by hash)
- agreement rate: head's gated decision vs the route actually taken
- coverage: share of turns the gated head would decide itself (non-chat)
- per-domain confusion (actual_routed -> head_routed disagreement counts)
- head latency percentiles

Usage:
    python3 scripts/maintenance/router_shadow_report.py [path/to/log.jsonl]
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_LOG = os.path.join(
    REPO, "services", "zoe-data", "data", "router_head_shadow.jsonl",
)

# The router rotates the shadow log into `<log>.1`, `<log>.2`, … once it hits its
# size cap. Import the router's own segment contract rather than re-deriving the
# naming here, so this report can't drift from the writer.
sys.path.insert(0, os.path.join(REPO, "services", "zoe-data"))
from semantic_router import shadow_log_segments  # noqa: E402


def _pctl(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, int(round(p / 100 * (len(vals) - 1)))))
    return vals[idx]


def load(path: str) -> list[dict]:
    """Read every rotated segment, oldest first — not just the live file."""
    recs = []
    for segment in shadow_log_segments(path):
        with open(segment, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict) and "head_pred" in rec:
                    recs.append(rec)
    return recs


def report(recs: list[dict]) -> str:
    out = []
    n = len(recs)
    out.append(f"turns: {n}")
    if not n:
        return "\n".join(out + ["(no shadow records — is ZOE_ROUTER_HEAD=shadow live?)"])
    out.append(f"unique utterances: {len({r.get('utt') for r in recs})}")

    agree = sum(1 for r in recs if r.get("agree"))
    out.append(f"agreement (head gated vs actual): {agree}/{n} = {agree / n:.1%}")

    head_decided = sum(1 for r in recs if r.get("head_routed") != "chat")
    actual_tool = sum(1 for r in recs if r.get("actual_routed") != "chat")
    out.append(f"head coverage (gated non-chat): {head_decided}/{n} = {head_decided / n:.1%}")
    out.append(f"actual non-chat routes: {actual_tool}/{n} = {actual_tool / n:.1%}")

    # would-be chat false positives: actual routed chat, head says tool
    chat_fp = sum(1 for r in recs
                  if r.get("actual_routed") == "chat" and r.get("head_routed") != "chat")
    actual_chat = n - actual_tool
    if actual_chat:
        out.append(f"head tool-call on actual-chat turns: {chat_fp}/{actual_chat} = "
                   f"{chat_fp / actual_chat:.1%}")

    out.append("\ndisagreements (actual_routed -> head_routed):")
    conf = Counter((r.get("actual_routed"), r.get("head_routed"))
                   for r in recs if not r.get("agree"))
    if not conf:
        out.append("  (none)")
    for (a, h), c in conf.most_common():
        out.append(f"  {a} -> {h}: {c}")

    lats = [float(r["head_ms"]) for r in recs if isinstance(r.get("head_ms"), (int, float))]
    if lats:
        out.append(f"\nhead latency ms: p50={_pctl(lats, 50):.2f} "
                   f"p90={_pctl(lats, 90):.2f} p99={_pctl(lats, 99):.2f} "
                   f"max={max(lats):.2f}")

    confs = [float(r["head_conf"]) for r in recs if isinstance(r.get("head_conf"), (int, float))]
    if confs:
        out.append(f"head confidence: p50={_pctl(confs, 50):.2f} "
                   f"p10={_pctl(confs, 10):.2f}")
    return "\n".join(out)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG
    if not os.path.exists(path):
        print(f"no shadow log at {path}", file=sys.stderr)
        return 1
    print(f"# router head shadow report — {path}")
    print(report(load(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
