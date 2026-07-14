#!/usr/bin/env python3
"""Summarize two-stage router shadow2 logs (ZOE_ROUTER_HEAD=shadow2).

Companion to router_shadow_report.py (#1318, stage-1-only shadow). shadow2
runs the FULL two-stage pipeline off the hot path — SetFit shortlist head
(gate 0.5) + FunctionGemma sidecar (:11436) with the shortlist grammar — and
logs what it WOULD have done vs what the live Tier-0/1 router actually did.

Reads the JSONL written by the shadow2 hook (default
services/zoe-data/data/router_head_shadow2.jsonl) and reports:

- turn count + unique utterances (by hash)
- agreement rate: shadow2's would-be route vs the route actually taken
- would-be fallback rate: turns the two-stage pipeline abstained to chat
  (gate abstention or sidecar failure) while the live router took a tool route
- would-be chat false positives (actual chat, shadow2 says tool)
- per-domain confusion (actual -> shadow2 disagreements)
- would-be latency percentiles (total, plus per-stage when logged)

Tolerant of exact field names (built before lane 1's shape froze): the
shadow2 route is read from the first present of final_routed / routed /
shadow2_routed / head_routed; latency from total_ms / shadow2_ms / two_stage_ms
(stage keys: head_ms / stage1_ms, fg_ms / stage2_ms / sidecar_ms).

Usage:
    python3 scripts/maintenance/router_shadow2_report.py [path/to/log.jsonl]
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

DEFAULT_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "services", "zoe-data", "data", "router_head_shadow.jsonl",
)

# `two_stage_domain` is the field the live router (semantic_router.py) actually
# writes for the would-be shadow2/active decision; the others are tolerated for
# forward/backward compatibility. Keep it first so it wins when present.
ROUTE_KEYS = ("two_stage_domain", "final_routed", "routed", "shadow2_routed", "head_routed")
TOTAL_MS_KEYS = ("total_ms", "shadow2_ms", "two_stage_ms")
STAGE1_MS_KEYS = ("head_ms", "stage1_ms")
STAGE2_MS_KEYS = ("fg_ms", "stage2_ms", "sidecar_ms")

# route values that are NOT a real tool route (partial/error/abstain turns)
NON_TOOL_ROUTES = {"chat", "", "none", "null", "abstain", "error", "unknown"}


def _is_tool(route: object) -> bool:
    return isinstance(route, str) and route.strip().lower() not in NON_TOOL_ROUTES


def _pctl(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, int(round(p / 100 * (len(vals) - 1)))))
    return vals[idx]


def _first_num(rec: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        val = rec.get(key)
        if isinstance(val, (int, float)):
            return float(val)
    return None


def shadow_route(rec: dict) -> str | None:
    for key in ROUTE_KEYS:
        val = rec.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def load(path: str) -> list[dict]:
    recs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (isinstance(rec, dict) and "actual_routed" in rec
                    and shadow_route(rec) is not None):
                recs.append(rec)
    return recs


def report(recs: list[dict]) -> str:
    out = []
    n = len(recs)
    out.append(f"turns: {n}")
    if not n:
        return "\n".join(out + ["(no shadow2 records — is ZOE_ROUTER_HEAD=shadow2 live?)"])
    out.append(f"unique utterances: {len({r.get('utt') for r in recs})}")

    # always recompute agreement from the routes: a logged `agree` may be a
    # string ("false" is truthy) or carried over from an earlier stage
    agree = sum(1 for r in recs if shadow_route(r) == r.get("actual_routed"))
    out.append(f"agreement (shadow2 vs actual): {agree}/{n} = {agree / n:.1%}")

    invalid = sum(1 for r in recs if not _is_tool(r.get("actual_routed"))
                  and r.get("actual_routed") != "chat")
    if invalid:
        out.append(f"records with non-route actual_routed (null/abstain/error — "
                   f"excluded from tool denominators): {invalid}")

    shadow_tool = sum(1 for r in recs if _is_tool(shadow_route(r)))
    actual_tool = sum(1 for r in recs if _is_tool(r.get("actual_routed")))
    out.append(f"shadow2 would-tool: {shadow_tool}/{n} = {shadow_tool / n:.1%}")
    out.append(f"actual tool routes: {actual_tool}/{n} = {actual_tool / n:.1%}")

    # would-be fallback: live router took a tool route, shadow2 abstained to chat
    fallback = sum(1 for r in recs
                   if _is_tool(r.get("actual_routed")) and shadow_route(r) == "chat")
    if actual_tool:
        out.append(f"would-be fallback (actual tool, shadow2 chat): "
                   f"{fallback}/{actual_tool} = {fallback / actual_tool:.1%}")

    # would-be chat false positives: actual chat, shadow2 says tool
    chat_fp = sum(1 for r in recs
                  if r.get("actual_routed") == "chat" and _is_tool(shadow_route(r)))
    actual_chat = sum(1 for r in recs if r.get("actual_routed") == "chat")
    if actual_chat:
        out.append(f"shadow2 tool-call on actual-chat turns: {chat_fp}/{actual_chat} = "
                   f"{chat_fp / actual_chat:.1%}")

    out.append("\ndisagreements (actual_routed -> shadow2):")
    conf = Counter((r.get("actual_routed"), shadow_route(r))
                   for r in recs if shadow_route(r) != r.get("actual_routed"))
    if not conf:
        out.append("  (none)")
    for (a, s), c in conf.most_common():
        out.append(f"  {a} -> {s}: {c}")

    for label, keys in (("total", TOTAL_MS_KEYS),
                        ("stage1 (head)", STAGE1_MS_KEYS),
                        ("stage2 (sidecar)", STAGE2_MS_KEYS)):
        lats = [v for r in recs if (v := _first_num(r, keys)) is not None]
        if lats:
            out.append(f"\nwould-be {label} latency ms: p50={_pctl(lats, 50):.1f} "
                       f"p90={_pctl(lats, 90):.1f} p99={_pctl(lats, 99):.1f} "
                       f"max={max(lats):.1f} (n={len(lats)})")
    return "\n".join(out)


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG
    if not os.path.exists(path):
        print(f"no shadow2 log at {path}", file=sys.stderr)
        return 1
    print(f"# two-stage router shadow2 report — {path}")
    print(report(load(path)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
