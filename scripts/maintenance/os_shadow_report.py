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
* ``not_compared``- ``member_not_in_roster`` / ``dropped_erased`` / ``queue_full`` / ``live_noop`` / ``worker_down`` / ``no_input`` /
  ``shadow_error`` (the shadow's own failure, listed apart as ``shadow_errors``).

``agree_rate`` = agree / (agree + disagree): the number the owner reads before handing a verb to the kernel. Hop p50 / p90 / p95
are over writes the kernel answered (agree or disagree with a kernel state), never the unavailable ones.

With the switch on the client writes exactly ONE line per write, whatever happened to it, and this reader drops nothing: a result code
it does not know is counted as ``unknown`` (in ``disagree``: the conservative side) and shown on its own, a marker line it cannot read
(a repeated key, no result, a bad number) is counted as ``rejected``, and ``unknown_fields`` counts lines with any other field outside
its closed set. Per verb ``agree + disagree + unavailable + not_compared == seen``; overall ``adds_logged + rejected`` equals the
marker lines read.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import Counter
from typing import Optional

#: the marker followed by its payload, OR the marker alone (a line cut right after it, with or without a newline): every marker line
#: is read, and one whose payload is missing or cut short is ``rejected`` (counted), never skipped
LINE = re.compile(r"OS_SHADOW_(ACT|LIST_ADD)(?:\s+(.*)|\s*$)")
VERB_ORDER = ("list_add", "list_remove", "reminder_create", "alarm_create")
#: every field the report reads, with its CLOSED set of values (os_shadow.py writes only these). Anything else becomes ``unknown`` (and
#: an unknown result is a disagreement): the report never echoes a value it does not know, so a planted or corrupted field cannot print text.
UNKNOWN = "unknown"
ALLOWED = {
    "verb": frozenset(VERB_ORDER),
    "src": frozenset({"spoken", "single", "skybridge", "rest", "service", "suggestion", "other"}),
    "cls": frozenset({"shopping", "todo", "personal", "other", "once", "repeat", "-"}),
    "live": frozenset({"added", "refused", "noop"}),
    "kernel": frozenset({"verified", "unverified", "uncertain", "refused", "none"}),
    "result": frozenset({"worker_down", "match", "not_expressible", "kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused",
                         "item_differs", "list_differs", "schedule_differs", "schedule_unproven", "member_differs", "member_unproven",
                         "kernel_unavailable", "timeout", "kernel_malformed", "member_not_in_roster",
                         "dropped_erased", "queue_full", "live_noop", "no_input", "shadow_error"}),
    "reason": frozenset({"-", "no_grant", "manifest", "params", "cause", "forgotten", "other", "timeout", "protocol", "socket",
                         "unavailable", "not_provisioned", "circuit", "no_credential", "closed", "auth", "handshake",
                         "recurring", "no_time", "range", "time", "parse", "hook", "worker", "code", "pending_forget", "crossed", "forgotten_before",
                         "old_kernel"}),
}
NUMBERS = ("hop_ms", "q_ms")
#: a number above this is not a measurement (the add's whole budget is 1.5 s; a day in the queue is already absurd): rejected
MAX_MS = 86_400_000.0
NOT_COMPARED = {"member_not_in_roster", "dropped_erased", "queue_full", "live_noop", "worker_down", "no_input", "shadow_error"}
UNAVAILABLE = ("kernel_unavailable", "timeout")
AGREE = ("match",)
MISMATCH = ("kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused", "item_differs", "list_differs",
            "schedule_differs", "schedule_unproven", "member_differs", "member_unproven", "kernel_malformed", "not_expressible", "kernel_unavailable",
            "timeout")
DISAGREE = tuple(m for m in MISMATCH if m not in UNAVAILABLE)


def key_tag(key: str) -> str:
    """An unknown KEY's name is caller-controlled text in principle: never printed raw, only as an 8-hex tag (enough to tell two
    unknown keys apart and to grep the writer for them)."""
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:8]


def bucket(result: str) -> str:
    if result in AGREE:
        return "agree"
    if result in UNAVAILABLE:
        return "unavailable"
    if result in NOT_COMPARED:
        return "not_compared"
    return "disagree"          # every other code, including one this reader does not know yet: the conservative side


def clean(fields: list, legacy: bool = False) -> "dict | None":
    """One line's ``key=value`` pairs as a record of KNOWN keys with CLOSED values, or None (the line is rejected): a key given twice
    (a later copy must never override the writer's), no ``result`` or ``verb`` (an ``OS_SHADOW_LIST_ADD`` line is ``list_add``), or a
    number field (``hop_ms``, ``q_ms``) that is missing, empty, not a number, not finite (nan, inf), negative or above ``MAX_MS``. An
    unknown value becomes ``unknown``; an unknown KEY is kept as its tag in ``_unknown_keys`` (counted and shown, never its name)."""
    seen: dict = {}
    for kv in fields:
        if "=" not in kv:
            continue
        key, value = kv.split("=", 1)
        if key in seen:
            return None
        seen[key] = value
    if legacy:
        if "verb" in seen:
            return None
        seen["verb"] = "list_add"
    if "result" not in seen or "verb" not in seen:
        return None
    rec = {k: (seen[k] if seen[k] in ALLOWED[k] else UNKNOWN) for k in ALLOWED if k in seen}
    for k in NUMBERS:
        try:
            v = float(seen[k])
        except (KeyError, ValueError):
            return None
        if not math.isfinite(v) or v < 0 or v > MAX_MS:
            return None
        rec[k] = v
    extra = sorted(k for k in seen if k not in ALLOWED and k not in NUMBERS)
    if extra:
        rec["_unknown_keys"] = tuple(key_tag(k) for k in extra)
    return rec


def parse_counted(lines) -> tuple[list[dict], int]:
    """``(records, rejected)``: every marker line is either a record or counted as rejected, never silently skipped."""
    out, rejected = [], 0
    for raw in lines:
        m = LINE.search(raw)
        if m:
            rec = clean((m.group(2) or "").split(), legacy=m.group(1) == "LIST_ADD")
            if rec is None:
                rejected += 1
            else:
                out.append(rec)
    return out, rejected


def parse(lines) -> list[dict]:
    return parse_counted(lines)[0]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, max(0, int(round(p / 100 * (len(v) - 1)))))]


def _answered(recs: list[dict]) -> list[float]:
    return [r["hop_ms"] for r in recs if bucket(r["result"]) in ("agree", "disagree") and r.get("kernel") not in (None, "none")]


#: the owner's pass mark for handing a verb to the kernel (docs/knowledge/zoe-os-integration.md), per verb
PASS_MIN_COMPARED = 200          # at least 200 compared actions (agree + disagree)
PASS_AGREE = (995, 1000)         # at least 99.5% agreement
PASS_UNAVAILABLE = (1, 100)      # unavailable UNDER 1% of the actions the kernel was asked about (agree + disagree + unavailable)
PASS_P90_MS = 10.0               # hop p90 UNDER 10 ms
#: a disagreement the machine cannot even name (an unknown result code, or a reason it does not know / only knows as "other")
UNNAMED_REASONS = (UNKNOWN, "other")


def verdict(agree: int, disagree: int, unavailable: int, unexplained: int, hop_p90: float, answered: Optional[int] = None) -> dict:
    """The five numbers of the pass mark, each checked exactly (no rounding), and which ones fail. ``unexplained`` == 0 is the part a
    machine can check; every listed disagreement still has to be explained by a person before the flip. ``answered``: how many of the
    judged writes carry an ANSWERED hop (a kernel state and a hop): the p90 is evidence only over at least ``PASS_MIN_COMPARED`` of
    them, so lines without hop evidence can never pass the latency number by default."""
    judged, asked = agree + disagree, agree + disagree + unavailable
    fails = []
    if judged < PASS_MIN_COMPARED:
        fails.append(f"compared {judged} < {PASS_MIN_COMPARED}")
    if not judged or agree * PASS_AGREE[1] < PASS_AGREE[0] * judged:
        fails.append(f"agreement {(agree / judged * 100 if judged else 0):.2f}% < 99.5%")
    if unexplained:
        fails.append(f"{unexplained} disagreement(s) with no known code or reason")
    if asked and unavailable * PASS_UNAVAILABLE[1] >= PASS_UNAVAILABLE[0] * asked:
        fails.append(f"unavailable {unavailable / asked * 100:.2f}% >= 1%")
    if answered is not None and judged >= PASS_MIN_COMPARED and answered < PASS_MIN_COMPARED:
        fails.append(f"answered hops {answered} < {PASS_MIN_COMPARED}")   # enough lines, but not enough of them carry hop evidence
    elif not judged or hop_p90 >= PASS_P90_MS:
        fails.append(f"hop p90 {hop_p90:.2f} ms >= {PASS_P90_MS:g} ms" if judged else "no answered hop")
    return {"pass": not fails, "fails": fails}


#: a disagreement code that says the KERNEL cannot yet report what the comparison needs (not that it got it wrong)
NOT_COMPARABLE = ("schedule_unproven",)


def _verb_verdict(recs: list[dict], b: Counter, unexplained: int, hop_p90: float, answered: Optional[int] = None) -> dict:
    """The pass line. One rule before the five numbers: when there IS at least one disagreement and EVERY disagreement is
    ``schedule_unproven`` (the kernel's read-back does not say when a reminder is due), the verb is ``not_comparable``: its agree rate
    measures the kernel's missing field, not a disagreement, and is not reported as a failure. One real disagreement of any other code
    makes it an ordinary ``NOT YET`` again. The counts stay in the table either way."""
    dis = Counter(r["result"] for r in recs if bucket(r["result"]) == "disagree")
    if dis and set(dis) <= set(NOT_COMPARABLE):
        return {"pass": False, "status": "not_comparable", "fails": [f"{code}: {n}" for code, n in sorted(dis.items())]}
    out = verdict(b["agree"], b["disagree"], b["unavailable"], unexplained, hop_p90, answered)
    return {**out, "status": "pass" if out["pass"] else "not_yet"}


def _pass_line(v: dict) -> str:
    vd = v["verdict"]
    if vd["status"] == "not_comparable":
        old = f" (an older kernel without due_at on {v['old_kernel']} of {v['seen']})" if v.get("old_kernel") else ""
        return ("NOT COMPARABLE YET (" + "; ".join(vd["fails"]) + "): cannot meet the pass mark yet: the kernel does not report when a "
                "reminder is due" + old)
    return "PASS" if vd["pass"] else "NOT YET (" + "; ".join(vd["fails"]) + ")"


def verb_summary(recs: list[dict]) -> dict:
    b = Counter(bucket(r["result"]) for r in recs)
    judged = b["agree"] + b["disagree"]
    asked = judged + b["unavailable"]
    hops = _answered(recs)
    unexplained = sum(1 for r in recs if bucket(r["result"]) == "disagree"
                      and (r["result"] == UNKNOWN or r.get("reason", "-") in UNNAMED_REASONS))
    return {
        "compared": judged, "unexplained": unexplained, "old_kernel": sum(1 for r in recs if r.get("reason") == "old_kernel"),
        "unavailable_rate": round(b["unavailable"] / asked, 4) if asked else None,
        "verdict": _verb_verdict(recs, b, unexplained, pct(hops, 90), len(hops)),
        "seen": len(recs), "agree": b["agree"], "disagree": b["disagree"], "unavailable": b["unavailable"],
        "not_compared": b["not_compared"], "agree_rate": round(b["agree"] / judged, 4) if judged else None,
        "unknown": sum(1 for r in recs if r["result"] == UNKNOWN), "shadow_errors": sum(1 for r in recs if r["result"] == "shadow_error"),
        "disagree_by_result": dict(sorted(Counter(r["result"] for r in recs if bucket(r["result"]) == "disagree").items())),
        "disagree_by_reason": dict(sorted(Counter(f"{r['result']}:{r.get('reason', '-')}" for r in recs
                                                  if bucket(r["result"]) == "disagree").items())),
        "hop_answered": len(hops), "hop_ms_p50": round(pct(hops, 50), 2), "hop_ms_p90": round(pct(hops, 90), 2),
        "hop_ms_p95": round(pct(hops, 95), 2),
        "by_cls": dict(sorted(Counter(r.get("cls", "?") for r in recs).items())),
    }


def summarize(recs: list[dict], rejected: int = 0) -> dict:
    """The per-verb table (``verbs``, each verb its own row, an unknown verb as ``unknown``) plus the list-add totals the first report
    gave: those are over ``list_add`` lines ONLY (same names, same meaning, whatever other verbs are in the log). ``lines_logged``,
    ``rejected``, ``unknown``, ``unknown_fields`` and ``shadow_errors`` are over every line."""
    every = recs
    recs = [r for r in every if r["verb"] == "list_add"]
    results = Counter(r["result"] for r in recs)
    compared = [r for r in recs if r["result"] not in NOT_COMPARED]
    answered = [r["hop_ms"] for r in compared if r.get("kernel") not in (None, "none")]
    n = len(compared)
    verbs = sorted({r["verb"] for r in every}, key=lambda v: (VERB_ORDER.index(v) if v in VERB_ORDER else 99, v))
    every_results = Counter(r["result"] for r in every)
    return {
        "verbs": {v: verb_summary([r for r in every if r["verb"] == v]) for v in verbs}, "lines_logged": len(every),
        "adds_logged": len(recs), "compared": n,
        "match": results["match"], "match_rate": round(results["match"] / n, 4) if n else None,
        "mismatch": {k: results[k] for k in MISMATCH if results[k]},
        "refused_by_reason": dict(Counter(r.get("reason", "-") for r in compared if r["result"] == "kernel_refused_live_did")),
        "not_compared": {k: results[k] for k in sorted(NOT_COMPARED) if results[k]},
        "unknown": every_results[UNKNOWN], "rejected": rejected, "shadow_errors": every_results["shadow_error"],
        "unknown_fields": sum(1 for r in every if any(v == UNKNOWN for k, v in r.items() if k != "result")),
        "unknown_keys": dict(sorted(Counter(t for r in every for t in r.get("_unknown_keys", ())).items())),
        "kernel_unavailable": results["kernel_unavailable"], "timeouts": results["timeout"],
        "hop_ms_p50": round(pct(answered, 50), 2), "hop_ms_p95": round(pct(answered, 95), 2), "hop_answered": len(answered),
        "by_src": dict(Counter(r.get("src", "?") for r in recs)), "by_cls": dict(Counter(r.get("cls", "?") for r in recs)),
    }


def render(s: dict) -> str:
    rate = "n/a" if s["match_rate"] is None else f"{s['match_rate'] * 100:.1f}%"
    rows = ["| verb | seen | agree | disagree | unavailable | not compared | unknown | agree rate | unavailable rate | hop p50 ms | "
            "hop p90 ms |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for verb, v in s["verbs"].items():
        vr = "n/a" if v["agree_rate"] is None else f"{v['agree_rate'] * 100:.1f}%"
        ur = "n/a" if v["unavailable_rate"] is None else f"{v['unavailable_rate'] * 100:.1f}%"
        rows.append(f"| {verb} | {v['seen']} | {v['agree']} | {v['disagree']} | {v['unavailable']} | {v['not_compared']} | "
                    f"{v['unknown']} | {vr} | {ur} | {v['hop_ms_p50']} | {v['hop_ms_p90']} |")
    for verb, v in s["verbs"].items():
        if v["disagree_by_reason"]:
            rows.append(f"{verb} disagreements: " + ", ".join(f"{k}={n}" for k, n in v["disagree_by_reason"].items()))
    rows.append("")
    for verb, v in s["verbs"].items():
        rows.append(f"{verb}: " + _pass_line(v))
    rows += ["", f"list add: logged {s['adds_logged']}    compared: {s['compared']}    match: {s['match']} ({rate})",
             f"list add kernel hop (publish + act), {s['hop_answered']} answered: p50 {s['hop_ms_p50']} ms, p95 {s['hop_ms_p95']} ms",
             f"list add kernel unavailable: {s['kernel_unavailable']}    timeouts: {s['timeouts']}",
             f"all verbs: lines {s['lines_logged']}    shadow errors: {s['shadow_errors']}",
             f"unreadable: rejected lines {s['rejected']}    unknown result {s['unknown']} (counted as disagree)    "
             f"lines with an unknown field {s['unknown_fields']}",
             "unknown keys (tag=lines): " + (", ".join(f"{t}={n}" for t, n in s["unknown_keys"].items()) or "none"),
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
    s = summarize(*parse_counted(lines))
    print(json.dumps(s, indent=2) if as_json else render(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
