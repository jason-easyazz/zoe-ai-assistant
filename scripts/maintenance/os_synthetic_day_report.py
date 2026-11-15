#!/usr/bin/env python3
"""Join a synthetic-day run (plan + results file) with the Zoe OS shadow log lines of its time window and judge each verb against the owner's
pass mark (docs/knowledge/zoe-os-synthetic-day.md). Prints counts, milliseconds and request ids only: never text.

    python3 scripts/maintenance/os_synthetic_day_report.py --results R.jsonl --log ~/.zoe-logs/zoe-data.app.log [--explained E.json]

Pass mark, per verb: at least 200 judged expressible acts; agreement at least 99.5%; every disagreement explained; kernel unavailable under 1% of
seen; hop p90 under 10 ms. Planned not_expressible acts are an EXPLAINED disagreement (the plan says so) up to their planned count; any other
disagreement, or an unknown result code, needs an entry in ``--explained`` (``{"result:reason": "why"}``). Overall: the deliberate 'forget that'
was sent, and every demo member delete returned 200 with no "forget NOT confirmed" line in the window.
"""
from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARK = {"judged": 200, "agree_rate": 0.995, "unavailable_max": 0.01, "hop_p90_ms": 10.0}
SLACK_S = 120.0
STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4})\s")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def read_results(path: str) -> tuple[dict, list[dict], list[dict]]:
    meta, reqs, clean = {}, [], []
    for line in Path(path).read_text("utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        {"meta": lambda: meta.update(rec), "req": lambda: reqs.append(rec), "cleanup": lambda: clean.append(rec)}.get(rec.get("type"), lambda: None)()
    return meta, sorted(reqs, key=lambda r: r["t"]), clean


def window_lines(log_paths: list[str], lo: float, hi: float) -> tuple[list[tuple[float, str]], int]:
    """(timestamp, line) of the log lines inside the window, and how many 'forget NOT confirmed' lines fall in it."""
    out, unconfirmed = [], 0
    for p in log_paths:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = STAMP.match(line)
                if not m:
                    continue
                ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S%z").timestamp()
                if not lo <= ts <= hi:
                    continue
                if "OS_SHADOW forget NOT confirmed" in line:
                    unconfirmed += 1
                if "OS_SHADOW_ACT" in line:
                    out.append((ts, line))
    return out, unconfirmed


def judge(verb: str, recs: list[tuple[float, dict]], planned_ne: int, explained: dict, rep) -> dict:
    s = rep.verb_summary([r for _, r in recs])
    ne = sum(1 for _, r in recs if r["result"] == "not_expressible")
    explained_ne = min(ne, planned_ne)
    other_keys = Counter(f"{r['result']}:{r.get('reason', '-')}" for _, r in recs
                         if rep.bucket(r["result"]) == "disagree" and r["result"] != "not_expressible")
    if ne > planned_ne:
        other_keys["not_expressible:unplanned"] = ne - planned_ne        # more than the plan accounts for: needs an explanation
    unexplained_keys = sorted(k for k in other_keys if k not in explained)
    judged = s["agree"] + s["disagree"] - explained_ne
    rate = s["agree"] / judged if judged else None
    unav = s["unavailable"] / s["seen"] if s["seen"] else 0.0
    fails = []
    if judged < MARK["judged"]:
        fails.append(f"judged {judged} < {MARK['judged']}")
    if rate is None or rate < MARK["agree_rate"]:
        fails.append(f"agreement {'n/a' if rate is None else f'{rate:.4f}'} < {MARK['agree_rate']}")
    if unexplained_keys:
        fails.append("unexplained disagreement: " + ", ".join(unexplained_keys))
    if unav >= MARK["unavailable_max"]:
        fails.append(f"unavailable {unav:.4f} >= {MARK['unavailable_max']}")
    if s["hop_answered"] == 0 or s["hop_ms_p90"] >= MARK["hop_p90_ms"]:
        fails.append(f"hop p90 {s['hop_ms_p90']} ms (answered {s['hop_answered']}) not under {MARK['hop_p90_ms']}")
    return {"judged": judged, "agree": s["agree"], "disagree": s["disagree"], "explained_not_expressible": explained_ne,
            "disagree_by_reason": s["disagree_by_reason"], "unavailable": s["unavailable"], "seen": s["seen"],
            "agree_rate": None if rate is None else round(rate, 4), "hop_ms_p50": s["hop_ms_p50"], "hop_ms_p90": s["hop_ms_p90"],
            "fails": fails, "pass": not fails}


def report(results: str, logs: list[str], explained: dict) -> dict:
    drv, rep = _load("os_synthetic_day"), _load("os_shadow_report")
    meta, reqs, clean = read_results(results)
    plan = drv.build_plan(int(meta["seed"]))
    lo = min(r["t"] for r in reqs) - SLACK_S if reqs else 0.0
    hi = max([r["t"] + r["ms"] / 1000 for r in reqs] + [c["t"] for c in clean] or [0.0]) + SLACK_S
    lines, unconfirmed = window_lines(logs, lo, hi)
    recs = [(ts, rec) for ts, line in lines if (rec := rep.clean(line.split("OS_SHADOW_ACT", 1)[1].split()))]
    starts = [r["t"] for r in reqs]
    sent = Counter(r["verb"] for r in reqs)
    verbs, ids = {}, {}
    for v in drv.VERBS[:4]:
        mine = [(ts, r) for ts, r in recs if r["verb"] == v]
        verbs[v] = judge(v, mine, drv.planned_acts(plan, v, "not_expressible"), explained, rep)
        verbs[v].update(planned=sum(1 for r in plan if r["verb"] == v), sent=sent[v])
        ids[v] = [reqs[max(0, bisect.bisect_right(starts, ts) - 1)]["id"] for ts, r in mine if rep.bucket(r["result"]) == "disagree"]
    forget_sent = any(r["id"] == "fin_forget" and r["outcome"] == "ok" for r in reqs)
    deleted = bool(clean) and all(c["forget_http"] == 200 and c["second_removed"] == 0 and not any(c["left"].values()) for c in clean)
    overall = []
    if not forget_sent:
        overall.append("the deliberate 'forget that' was not sent / not answered")
    if not deleted:
        overall.append("a demo member delete is missing or incomplete")
    if unconfirmed:
        overall.append(f"{unconfirmed} 'forget NOT confirmed' line(s): the kernel did not confirm a forget")
    return {"verbs": verbs, "disagreement_request_ids": ids, "forget_that_sent": forget_sent, "members_deleted": deleted,
            "forget_unconfirmed_lines": unconfirmed, "overall_fails": overall, "shadow_lines_in_window": len(recs),
            "acts_planned": sum(r["acts"] for r in plan),
            "pass": all(v["pass"] for v in verbs.values()) and not overall}


def render(r: dict) -> str:
    rows = ["| verb | planned | sent | judged | agree | disagree | unavailable | agree rate | hop p50 | hop p90 | result |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for v, x in r["verbs"].items():
        rows.append(f"| {v} | {x['planned']} | {x['sent']} | {x['judged']} | {x['agree']} | {x['disagree']} | {x['unavailable']} | "
                    f"{x['agree_rate']} | {x['hop_ms_p50']} | {x['hop_ms_p90']} | {'PASS' if x['pass'] else 'FAIL'} |")
    for v, x in r["verbs"].items():
        for f in x["fails"]:
            rows.append(f"FAIL {v}: {f}")
        if x["disagree_by_reason"]:
            rows.append(f"{v} disagreements: " + ", ".join(f"{k}={n}" for k, n in x["disagree_by_reason"].items())
                        + f"  (explained as planned not_expressible: {x['explained_not_expressible']})")
        if r["disagreement_request_ids"][v]:
            rows.append(f"{v} read these request ids: " + " ".join(r["disagreement_request_ids"][v]))
    rows += [f"forget that sent: {r['forget_that_sent']}    members deleted and verified: {r['members_deleted']}"]
    rows += [f"FAIL overall: {f}" for f in r["overall_fails"]]
    return "\n".join(rows + [f"OVERALL: {'PASS' if r['pass'] else 'FAIL'}"])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--results", required=True)
    p.add_argument("--log", action="append", required=True)
    p.add_argument("--explained", default=None)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    r = report(a.results, a.log, json.loads(Path(a.explained).read_text("utf-8")) if a.explained else {})
    print(json.dumps(r, indent=1) if a.json else render(r))
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
