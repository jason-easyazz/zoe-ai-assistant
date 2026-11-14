#!/usr/bin/env python3
"""Score the REDESIGNED correction path against the golden set. A REPORT, not a gate; compare with run_baseline.py.

Each golden case is driven through the idle claim pass (services/zoe-data/memory_claims_pass.process_session) with a
SCRIPTED model: it returns the oracle claim (tests/fixtures/memory_claims_recorded/oracle.json) and the oracle's blind
readings of the stored rows (oracle_readings.json here). Everything after the model is the production code: the turn
filters, the code check of each attribute read, memory_claims_validate, and the guarded writer (memory_claims_apply) in
mode "on" with corrections AND health events enabled, writing to a scratch store - a real MemoryService on an in-memory
collection. No database, no model, no network. So the score is "what the code does with an honest reader's output", an
upper bound for any real model. Synthetic data only.

Lab-only choices, all of them visible in the report: (1) golden turns of one case share a timestamp, so turn t is taken
as said t seconds later; (2) the case's flag_turns are handed over as marker ids (the marker's job in production) and
``trigger`` counts how many of them the old detector alone would have flagged; (3) the value registry is an in-memory
double of the seam, because the registry module is not on this branch: a case that needs its erasers is NOT_RUNNABLE;
(4) a health event is the claim's own row, so events are matched by content, not by which mined fact became one.

    python3 labs/memory-claims-lab/run_claims.py [--json out.json] [--write-score]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime
import json
import logging
import re
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ZD = HERE.parents[1] / "services" / "zoe-data"
FIX = ZD / "tests" / "fixtures"
SCORE = HERE / "score-claims.json"
PASS, FAIL, NOT_RUNNABLE = "PASS", "FAIL", "NOT_RUNNABLE"
KIND_OF = {"agree": "add", "abstain": "add", "refuse": "refuse", "hold": "hold_pending"}
for _p in (str(ZD), str(ZD / "tests"), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load():
    golden = json.loads((FIX / "memory_claims_golden.json").read_text(encoding="utf-8"))["cases"]
    oracle = json.loads((FIX / "memory_claims_recorded" / "oracle.json").read_text(encoding="utf-8"))["oracle"]
    return golden, oracle, json.loads((HERE / "oracle_readings.json").read_text(encoding="utf-8"))


class _Reader:
    """The scripted model: the oracle claim, and the oracle's reading of exactly the rows it is shown."""

    def __init__(self, claim_out, readings):
        self.out, self.by_text = claim_out, readings

    async def claims(self, turns, flagged):
        return json.dumps(self.out)

    async def attributes(self, texts):
        return json.dumps({"rows": [{"row": i, "subject": s, "attribute": a, "span": sp}
                                    for i, t in enumerate(texts) for s, a, sp in self.by_text.get(t, [])]})


class _Registry:
    def __init__(self):
        self.entries = []

    async def record(self, user_id, entries, *, generation, row_ids):
        self.entries.extend(entries)
        return len(entries)


class _Walls:
    def retracted(self, user_id, text):
        import turn_forget

        return bool(turn_forget.blocks_write(user_id, text))

    async def forgotten(self, user_id, text):
        return False


def _at(case, t):
    import people_graph

    return people_graph.parse_ts(case["turns"][t]["at"].replace("Z", "+00:00")) + datetime.timedelta(seconds=t)


class _Src:
    def __init__(self, case, svc):
        self.case, self.svc, self.uid = case, svc, case["member"]

    async def turns(self, session_id):
        return [{"id": f"m{t['t']}", "role": "user", "content": t["text"], "at": _at(self.case, t["t"]),
                 "metadata": json.dumps({"user_id": self.uid})} for t in self.case["turns"]]

    async def graph(self, user_id):
        import memory_claims_pass as cp

        return cp.graph_of([(p["id"], p["name"], p.get("relationship", "")) for p in self.case["graph"]])

    async def rows(self, user_id):
        return await self.svc.list_all_by_status_strict(user_id=user_id, status="approved")

    def marked(self, user_id):
        return frozenset(f"m{t}" for t in self.case["flag_turns"])

    def clear_marker(self, user_id, message_id):
        pass


def _store(case):
    import memory_service
    from memory_service import MemoryService
    from test_memory_authority import _Col

    s = MemoryService(data_dir="/nonexistent/zoe-memory-claims-lab")
    s._col = _Col()
    s._collection = lambda: s._col

    async def no_audit(**_kw):
        return None

    s._append_audit = no_audit
    uid = case["member"]
    for i, r in enumerate(case["rows_before"]):
        s._col.rows[r["id"]] = (r["text"], {
            "user_id": uid, "wing": uid, "room": "conversations", "status": "approved", "source": "voice_fact",
            "added_by": "voice_fact", "memory_type": "fact", "confidence": 0.9, "tags": "", "added_ts": 1000.0 + i,
            "authority": "user_stated", "authority_class": "user_stated", "authority_basis": "deterministic_user_turn"})
    return s, memory_service


def _current(svc, row_id):
    """The approved text a seeded row has become (following supersedes links), or None when nothing approved is left."""
    rows = svc._col.rows
    while True:
        text, meta = rows[row_id]
        if meta.get("status") == "approved":
            return text
        nxt = meta.get("superseded_by_id")
        if not nxt or nxt not in rows:
            return None
        row_id = nxt


async def _run(case, oracle_out, readings, detail):
    import memory_claims as mc
    import memory_claims_pass as cp
    import memory_claims_validate as vm
    import provenance_answers as pa
    import turn_forget
    import user_erase_gate
    import user_prefs

    uid, exp, fails = case["member"], case["expect"], []
    svc, memory_service = _store(case)
    registry, dates, rules = _Registry(), [], []
    cp.reset_state()
    turn_forget.reset()
    user_erase_gate.reset()

    async def not_opted_out(_u):
        return False

    async def fix_date(user_id, old_id, new_id, value):
        dates.append((old_id, new_id, dict(value)))
        return True

    by_text = {r["text"]: readings["rows"].get(case["id"], {}).get(r["id"], []) for r in case["rows_before"]}
    src = _Src(case, svc)
    graph = await src.graph(uid)
    with mock.patch.object(user_prefs, "is_memory_opted_out", not_opted_out), \
            mock.patch.object(memory_service, "_user_opted_out", not_opted_out), \
            mock.patch.dict("os.environ", {"ZOE_BRAIN_SCHEDULER": "off", "ZOE_BRAIN_SLOT_GUARD": "off"}):
        for t in case["turns"]:                   # the turn stream as the service sees it: "forget that" retracts the turn before
            if turn_forget.is_forget_turn(t["text"]):
                prev = turn_forget.previous_turn(uid)
                if prev is not None:
                    turn_forget.retract(uid, prev)
            else:
                turn_forget.note_user_turn(uid, t["text"])
        detail["trigger"] = [bool(cp.detector_flags(case["turns"][t]["text"])) for t in case["flag_turns"]]
        if case["flag_turns"]:
            s = await cp.load_session(src, "lab", uid, 99)
            if s is not None:
                res = await cp.process_session(s, src, _Reader(oracle_out, by_text), svc=svc, walls=_Walls(), registry=registry,
                                               fix_date=fix_date, mode_="on", classes=(mc.CORRECTION, mc.EMERGENCY))
                if res.get("skipped"):
                    fails.append(f"pass skipped ({res['skipped']})")
                rules += [r for _v, r in res.get("verdicts", [])]
                detail["verdicts"] = res.get("verdicts", [])
        # rows
        want = {r["id"]: r["current_text"] for r in exp.get("rows_after", [])}
        changed = 0
        for r in case["rows_before"]:
            now = _current(svc, r["id"])
            changed += now != r["text"]
            if now != want.get(r["id"], r["text"]):
                fails.append(f"row {r['id']}")
        if exp.get("left_as_mined") and changed:
            fails.append("rewrote despite left_as_mined")
        # kind decisions on mined facts: the graph decides (rule D.14)
        if "kind_decisions" in exp:
            rows = [vm.Row(k, d) for k, (d, m) in svc._col.rows.items() if m.get("status") == "approved"]
            ctx = vm.Context(uid, [], rows, graph)
            got = []
            for reading in readings["kinds"].get(case["id"], []):
                verdict, rule = vm.rule_14_kind(reading[0], reading[1], ctx, rows) if reading else ("agree", None)
                got.append(KIND_OF[verdict])
                rules.append(rule)
            if got != exp["kind_decisions"]:
                fails.append(f"kind {got}")
        # the validator rule that stopped it (only observable when the scripted model made a claim)
        if exp.get("invalid_rule") is not None:
            if not oracle_out["claims"] and "kind_decisions" not in exp:
                detail["rule_unobserved"] = True
            elif exp["invalid_rule"] not in rules:
                fails.append(f"invalid_rule {sorted({r for r in rules if r is not None})}")
        # filed health events
        if "events" in exp:
            filed = [(d, m) for d, m in svc._col.rows.values() if m.get("memory_type") == "event"]
            wants = [w for w in exp["events"] if w is not None]
            if len(filed) != len(wants):
                fails.append(f"events filed {len(filed)} want {len(wants)}")
            for w in wants:
                hit = next(((d, m) for d, m in filed if all(h in d for h in w["has"])), None)
                if hit is None:
                    fails.append("event missing")
                    continue
                if "authority_class" in w and hit[1].get("authority_class") != w["authority_class"]:
                    fails.append(f"authority {hit[1].get('authority_class')}")
                if "sensitive_class" in w and pa.sensitive_class(*hit) != w["sensitive_class"]:
                    fails.append("sensitivity")
        # the registry: entries, and stale re-filing of a late-mined fact
        for w in exp.get("registry", []):
            def same(e, w=w):
                v = w["value_norm"]
                return e.role == w["role"] and e.attribute == w["attribute"] and (
                    mc.same_value(e.attribute, e.value_norm, v) if isinstance(v, dict) else float(e.value_norm.get("v", -1)) == v)
            if not any(same(e) for e in registry.entries):
                fails.append(f"registry {w['role']} {w['attribute']}")
        if "dropped_stale" in exp:
            first, last = _at(case, case["flag_turns"][0]), _at(case, len(case["turns"]) - 1)
            for i, (m, reading) in enumerate(zip(case["late_mined"], readings["late"].get(case["id"], []))):
                said = first - datetime.timedelta(seconds=60) if m["before_correction"] else last + datetime.timedelta(seconds=5)
                raw = json.dumps({"rows": [{"row": 0, "subject": reading[0], "attribute": reading[1], "span": reading[2]}]})
                attrs = cp.read_attrs(raw, [m["fact"]], uid, graph, said).get(0, ())
                stale = any(vm.is_stale(registry.entries, a.subject_ref, a.attribute, a.value_norm, said) for a in attrs)
                if stale != exp["dropped_stale"][i]:
                    fails.append(f"stale {i}")
        if "person_important_dates" in exp.get("side_effects", []) and not dates:
            fails.append("structured date not moved")
        if exp.get("not_record_expert"):
            import expert_dispatch as ed

            for dom in ("calendar", "reminders", "lists"):
                if ed._plan(dom, case["turns"][case["flag_turns"][-1]]["text"]) is not None:
                    fails.append(f"routed to {dom}")
    return fails


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            pass


def run_case(case, oracle_out, readings):
    """(status, reason, detail dict) for one case. Never raises."""
    if case.get("requires"):
        return NOT_RUNNABLE, "needs_" + case["requires"], {}
    cap, root, detail = _Capture(), logging.getLogger(), {}
    old = root.level
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    try:
        fails = asyncio.run(_run(case, oracle_out, readings, detail))
        ours = [ln for ln in cap.lines if ln.startswith(("MEMORY_CLAIM", "PENDING_CORRECTION"))]
        for word in case["expect"].get("log_has_no", []):
            if any(re.search(rf"\b{re.escape(word)}\b", ln, re.IGNORECASE) for ln in ours):
                fails.append(f"log leaked {word!r}")
    except Exception as exc:  # noqa: BLE001 - a crash is a fail, reported by type
        return FAIL, "error", {"error": type(exc).__name__}
    finally:
        root.removeHandler(cap)
        root.setLevel(old)
    detail["fails"] = fails
    return (PASS if not fails else FAIL), ("" if not fails else "differs"), detail


def run_all():
    golden, oracle, readings = _load()
    results, totals = {}, collections.defaultdict(collections.Counter)
    flagged = triggered = 0
    for c in golden:
        status, reason, detail = run_case(c, oracle[c["id"]], readings)
        trig = detail.get("trigger", [])
        flagged, triggered = flagged + len(trig), triggered + sum(trig)
        results[c["id"]] = {"class": c["class"], "status": status, "reason": reason, "detail": "; ".join(detail.get("fails", []))
                            or detail.get("error", ""), "verdicts": detail.get("verdicts", []),
                            "rule_unobserved": bool(detail.get("rule_unobserved"))}
        totals[c["class"]][status] += 1
    by_class = {k: {s: totals[k].get(s, 0) for s in (PASS, FAIL, NOT_RUNNABLE)} for k in sorted(totals)}
    return {"cases": len(golden), "totals": {s: sum(v[s] for v in by_class.values()) for s in (PASS, FAIL, NOT_RUNNABLE)},
            "by_class": by_class, "failing_ids": [i for i, r in results.items() if r["status"] == FAIL],
            "not_runnable": {i: r["reason"] for i, r in results.items() if r["status"] == NOT_RUNNABLE},
            "rule_unobserved": [i for i, r in results.items() if r["rule_unobserved"]],
            "trigger": {"flagged_turns": flagged, "old_detector_flags": triggered}, "results": results}


def dump(rep) -> str:
    head = {k: v for k, v in rep.items() if k != "results"}
    body = ",\n".join(f" {json.dumps(i)}: {json.dumps(r)}" for i, r in rep["results"].items())
    return json.dumps(head)[:-1] + ', "results": {\n' + body + "\n}}\n"


def render(rep) -> str:
    lines = [f"{'id':<7}{'class':<12}{'status':<14}detail"]
    for i, r in rep["results"].items():
        lines.append(f"{i:<7}{r['class']:<12}{r['status']:<14}{(r['reason'] + ' ' + r['detail']).strip()} {r['verdicts'] or ''}".rstrip())
    lines.append("")
    for k, v in rep["by_class"].items():
        lines.append(f"{k:<12} pass {v[PASS]:>3}  fail {v[FAIL]:>3}  not_runnable {v[NOT_RUNNABLE]:>3}")
    t = rep["totals"]
    lines.append(f"{'TOTAL':<12} pass {t[PASS]:>3}  fail {t[FAIL]:>3}  not_runnable {t[NOT_RUNNABLE]:>3}  of {rep['cases']}")
    lines.append(f"trigger: the old detector alone flags {rep['trigger']['old_detector_flags']} of {rep['trigger']['flagged_turns']} flagged turns")
    lines.append(f"rule not observable (the scripted model abstains): {', '.join(rep['rule_unobserved']) or '-'}")
    return "\n".join(lines)


def main(argv=None):
    ap_ = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap_.add_argument("--json", help="write the full report as JSON to this path")
    ap_.add_argument("--write-score", action="store_true", help="record the result as labs/memory-claims-lab/score-claims.json")
    a = ap_.parse_args(argv)
    rep = run_all()
    print(render(rep))
    if a.json:
        Path(a.json).write_text(dump(rep), encoding="utf-8")
    if a.write_score:
        SCORE.write_text(dump(rep), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
