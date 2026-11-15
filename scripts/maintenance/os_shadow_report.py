#!/usr/bin/env python3
"""Turn a week of Zoe OS shadow log lines into the comparison table, per verb (docs/knowledge/zoe-os-integration.md, stage b).

Reads the ``OS_SHADOW_ACT verb=...`` lines ``services/zoe-data/os_shadow.py`` writes (codes and numbers only: no item, no note, no
list name, no person) from the files named on the command line, or stdin; an older ``OS_SHADOW_LIST_ADD`` line is read as
``verb=list_add``. Read-only; stdlib only; prints counts and milliseconds, never a line.

    python3 scripts/maintenance/os_shadow_report.py ~/.zoe-logs/zoe-data.*
    python3 scripts/maintenance/os_shadow_report.py --json < some.log

Per verb, every logged write is exactly ONE of:

* ``agree``       - ``match``: the kernel did it and its read-back names the same value the live write stored;
* ``disagree``    - the kernel refused, could not verify, names a different value, answered garbage, or its verb cannot express
  what the live path did (``not_expressible``, e.g. a recurring reminder): every one of these blocks handing that verb to the kernel;
* ``unavailable`` - ``kernel_unavailable`` / ``timeout``: no verdict (counted, never mixed into the agreement rate);
* ``not_compared``- ``member_not_in_roster`` / ``dropped_erased`` / ``queue_full`` / ``live_noop``.

``agree_rate`` = agree / (agree + disagree): the number the owner reads before handing a verb to the kernel. Hop p50 / p90 / p95
are over writes the kernel answered (agree or disagree with a kernel state), never the unavailable ones.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

LINE = re.compile(r"OS_SHADOW_(ACT|LIST_ADD)\s+(.*)")
VERB_ORDER = ("list_add", "list_remove", "reminder_create", "alarm_create")
NOT_COMPARED = {"member_not_in_roster", "dropped_erased", "queue_full", "live_noop"}
UNAVAILABLE = ("kernel_unavailable", "timeout")
AGREE = ("match",)
MISMATCH = ("kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused", "item_differs", "list_differs",
            "kernel_malformed", "not_expressible", "kernel_unavailable", "timeout")
DISAGREE = tuple(m for m in MISMATCH if m not in UNAVAILABLE)


def bucket(result: str) -> str:
    if result in AGREE:
        return "agree"
    if result in UNAVAILABLE:
        return "unavailable"
    if result in NOT_COMPARED:
        return "not_compared"
    return "disagree"          # every other code, including one this reader does not know yet: the conservative side


def parse(lines) -> list[dict]:
    out = []
    for raw in lines:
        m = LINE.search(raw)
        if not m:
            continue
        rec = dict(kv.split("=", 1) for kv in m.group(2).split() if "=" in kv)
        if m.group(1) == "LIST_ADD":
            rec.setdefault("verb", "list_add")
        try:
            rec["hop_ms"] = float(rec.get("hop_ms", "nan"))
        except ValueError:
            continue
        if "result" in rec and "verb" in rec:
            out.append(rec)
    return out


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(p / 100 * (len(v) - 1)))))]


def _answered(recs: list[dict]) -> list[float]:
    return [r["hop_ms"] for r in recs if bucket(r["result"]) in ("agree", "disagree") and r.get("kernel") not in (None, "none")]


def verb_summary(recs: list[dict]) -> dict:
    b = Counter(bucket(r["result"]) for r in recs)
    judged = b["agree"] + b["disagree"]
    hops = _answered(recs)
    return {
        "seen": len(recs), "agree": b["agree"], "disagree": b["disagree"], "unavailable": b["unavailable"],
        "not_compared": b["not_compared"], "agree_rate": round(b["agree"] / judged, 4) if judged else None,
        "disagree_by_result": dict(sorted(Counter(r["result"] for r in recs if bucket(r["result"]) == "disagree").items())),
        "disagree_by_reason": dict(sorted(Counter(f"{r['result']}:{r.get('reason', '-')}" for r in recs
                                                  if bucket(r["result"]) == "disagree").items())),
        "hop_answered": len(hops), "hop_ms_p50": round(pct(hops, 50), 2), "hop_ms_p90": round(pct(hops, 90), 2),
        "hop_ms_p95": round(pct(hops, 95), 2),
        "by_cls": dict(sorted(Counter(r.get("cls", "?") for r in recs).items())),
    }


def summarize(recs: list[dict]) -> dict:
    """The per-verb table (``verbs``) plus the list-add totals the first report gave (kept: same names, same meaning)."""
    results = Counter(r["result"] for r in recs)
    compared = [r for r in recs if r["result"] not in NOT_COMPARED]
    answered = [r["hop_ms"] for r in compared if r.get("kernel") not in (None, "none")]
    n = len(compared)
    verbs = sorted({r["verb"] for r in recs}, key=lambda v: (VERB_ORDER.index(v) if v in VERB_ORDER else 99, v))
    return {
        "verbs": {v: verb_summary([r for r in recs if r["verb"] == v]) for v in verbs},
        "adds_logged": len(recs), "compared": n,
        "match": results["match"], "match_rate": round(results["match"] / n, 4) if n else None,
        "mismatch": {k: results[k] for k in MISMATCH if results[k]},
        "refused_by_reason": dict(Counter(r.get("reason", "-") for r in compared if r["result"] == "kernel_refused_live_did")),
        "not_compared": {k: results[k] for k in sorted(NOT_COMPARED) if results[k]},
        "kernel_unavailable": results["kernel_unavailable"], "timeouts": results["timeout"],
        "hop_ms_p50": round(pct(answered, 50), 2), "hop_ms_p95": round(pct(answered, 95), 2), "hop_answered": len(answered),
        "by_src": dict(Counter(r.get("src", "?") for r in recs)), "by_cls": dict(Counter(r.get("cls", "?") for r in recs)),
    }


def render(s: dict) -> str:
    rate = "n/a" if s["match_rate"] is None else f"{s['match_rate'] * 100:.1f}%"
    rows = ["| verb | seen | agree | disagree | unavailable | not compared | agree rate | hop p50 ms | hop p90 ms |",
            "|---|---|---|---|---|---|---|---|---|"]
    for verb, v in s["verbs"].items():
        vr = "n/a" if v["agree_rate"] is None else f"{v['agree_rate'] * 100:.1f}%"
        rows.append(f"| {verb} | {v['seen']} | {v['agree']} | {v['disagree']} | {v['unavailable']} | {v['not_compared']} | {vr} | "
                    f"{v['hop_ms_p50']} | {v['hop_ms_p90']} |")
    for verb, v in s["verbs"].items():
        if v["disagree_by_reason"]:
            rows.append(f"{verb} disagreements: " + ", ".join(f"{k}={n}" for k, n in v["disagree_by_reason"].items()))
    rows += ["", f"all verbs: logged {s['adds_logged']}    compared: {s['compared']}    match: {s['match']} ({rate})",
             f"kernel hop (publish + act), {s['hop_answered']} answered: p50 {s['hop_ms_p50']} ms, p95 {s['hop_ms_p95']} ms",
             f"kernel unavailable: {s['kernel_unavailable']}    timeouts: {s['timeouts']}",
             "by source: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_src"].items())),
             "by class: " + ", ".join(f"{k}={v}" for k, v in sorted(s["by_cls"].items()))]
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    paths = [a for a in args if a != "--json"]
    lines: list[str] = []
    for p in paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            lines += fh.readlines()
    if not paths:
        lines = sys.stdin.readlines()
    s = summarize(parse(lines))
    print(json.dumps(s, indent=2) if as_json else render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
