#!/usr/bin/env python3
"""Score TODAY'S memory-correction code against the golden set. A REPORT, not a gate.

Each case in services/zoe-data/tests/fixtures/memory_claims_golden.json is driven through the production path that exists now
(value_correction.apply, memory_quality.reconcile_for_ingest, MemoryService.ingest's emergency conversion, superseded_by_correction)
against in-memory doubles: no database, no model, no network. A case is PASS (today's observable outcome equals ``expect``),
FAIL (it differs) or NOT_RUNNABLE (the path it needs does not exist yet: ``requires``). Synthetic data only.

    python3 labs/memory-claims-lab/run_baseline.py [--json out.json] [--write-baseline]
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ZD = ROOT / "services" / "zoe-data"
GOLDEN = ZD / "tests" / "fixtures" / "memory_claims_golden.json"
BASELINE = HERE / "baseline-today.json"
PASS, FAIL, NOT_RUNNABLE = "PASS", "FAIL", "NOT_RUNNABLE"


def _imports():
    for p in (str(ZD), str(ZD / "tests")):
        if p not in sys.path:
            sys.path.insert(0, p)


class _Row:
    def __init__(self, i, text, uid):
        self.id, self.text, self.metadata = i, text, {"user_id": uid}


class _Svc:
    """The member's approved rows. review(edit) rewrites the row in place: the 'current text' of that row."""

    def __init__(self, rows, uid):
        self.rows = [_Row(r["id"], r["text"], uid) for r in rows]
        self.edits = 0

    async def list_by_status(self, **_k):
        return list(self.rows)

    async def search(self, *_a, **_k):
        return []

    async def review(self, rid, decision=None, edits=None, actor=None, **_k):
        for r in self.rows:
            if r.id == rid and decision == "edit":
                r.text = edits
                self.edits += 1
        return object()


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            pass


@contextlib.contextmanager
def _graph(case):
    """The member's people graph as a roster double (people_graph._all dispatches on the SQL it is given)."""
    import people_graph
    import person_extractor

    uid = case["member"]
    people = case["graph"]

    async def ensure(_db):
        return object(), False

    async def fake_all(_db, sql, params=()):
        if "SELECT id, name FROM people" in sql:
            return [(p["id"], p["name"]) for p in people] if params and params[0] == uid else []
        if "SELECT relationship FROM people" in sql:
            return [(p.get("relationship", ""),) for p in people if p["id"] == params[0]]
        raise RuntimeError("not modelled")

    with mock.patch.object(person_extractor, "_ensure_db", ensure), mock.patch.object(people_graph, "_all", fake_all):
        yield


async def _apply(text, uid, svc):
    import correction_apply
    import user_prefs
    import value_correction as vc

    async def not_opted_out(_u):
        return False

    with mock.patch.object(user_prefs, "is_memory_opted_out", not_opted_out), \
            mock.patch.object(correction_apply, "_is_unidentified", lambda _u: False):
        return await vc.apply(text, uid, None, svc=svc)


async def _file(facts, uid):
    """Ingest each (fact, said) through a real MemoryService on a fake collection; returns [(text, metadata)] of the row each filed."""
    import memory_service
    from memory_service import MemoryService
    from test_memory_authority import _Col

    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE", "ZOE_MEDICAL_SAFETY"):
        os.environ.pop(k, None)
    s = MemoryService(data_dir="/nonexistent/zoe-memory-claims-lab")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def not_opted_out(_u):
        return False

    s._append_audit = no_audit
    out = []
    with mock.patch.object(memory_service, "_user_opted_out", not_opted_out):
        for fact, said in facts:
            before = {r.id for st in ("approved", "pending") for r in await s.list_by_status(user_id=uid, status=st)}
            await s.ingest(fact, user_id=uid, source="turn_digest", source_excerpt=said, anchor_text=said)
            rows = [r for st in ("approved", "pending") for r in await s.list_by_status(user_id=uid, status=st) if r.id not in before]
            out.append((rows[0].text, dict(rows[0].metadata or {})) if rows else (None, {}))
    return out


async def _run(case):
    import expert_dispatch as ed
    import memory_quality as mq
    import value_correction as vc

    uid, exp, fails = case["member"], case["expect"], []
    svc = _Svc(case["rows_before"], uid)
    original = {r["id"]: r["text"] for r in case["rows_before"]}
    vc.reset_state()
    with _graph(case):
        for t in case["flag_turns"]:
            await _apply(case["turns"][t]["text"], uid, svc)
        # rows
        want = {r["id"]: r["current_text"] for r in exp.get("rows_after", [])}
        for r in svc.rows:
            if r.text != want.get(r.id, original[r.id]):
                fails.append(f"row {r.id}")
        if exp.get("left_as_mined") and svc.edits:
            fails.append("rewrote despite left_as_mined")
        # stale re-filing
        if "dropped_stale" in exp:
            for i, m in enumerate(case["late_mined"]):
                said_at = time.time() - 60 if m["before_correction"] else time.time() + 5
                if vc.superseded_by_correction(uid, m["fact"], said_at=said_at) != exp["dropped_stale"][i]:
                    fails.append(f"stale {i}")
        # routing
        if exp.get("not_record_expert"):
            for dom in ("calendar", "reminders", "lists"):
                if ed._plan(dom, case["turns"][case["flag_turns"][-1]]["text"]) is not None:
                    fails.append(f"routed to {dom}")
        # kind decisions
        if "kind_decisions" in exp:
            got = [(await mq.reconcile_for_ingest(svc, m["fact"], uid))[0] for m in case["mined"] if m["check"] == "kind"]
            if got != exp["kind_decisions"]:
                fails.append(f"kind {got}")
        # emergency filing
        if "events" in exp:
            ms = [m for m in case["mined"] if m["check"] == "event"]
            filed = await _file([(m["fact"], m["said"]) for m in ms], uid)
            import provenance_answers as pa

            for m, want_ev, (text, meta) in zip(ms, exp["events"], filed):
                is_event = meta.get("memory_type") == "event"
                if want_ev is None:
                    if is_event or text != m["fact"]:
                        fails.append("event filed or text changed")
                    continue
                if not is_event or any(h not in (text or "") for h in want_ev["has"]):
                    fails.append("event missing")
                    continue
                if "authority_class" in want_ev and meta.get("authority_class") != want_ev["authority_class"]:
                    fails.append("authority")
                if "sensitive_class" in want_ev and pa.sensitive_class(text, meta) != want_ev["sensitive_class"]:
                    fails.append("sensitivity")
    return fails


def run_case(case):
    """(status, reason, detail) for one case. Never raises."""
    if case.get("requires"):
        return NOT_RUNNABLE, "needs_" + case["requires"], ""
    _imports()
    cap = _Capture()
    root = logging.getLogger()
    old = root.level
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    try:
        fails = asyncio.run(_run(case))
        for word in case["expect"].get("log_has_no", []):
            if any(re.search(rf"\b{re.escape(word)}\b", ln, re.IGNORECASE) for ln in cap.lines):
                fails.append(f"log leaked {word!r}")
    except Exception as exc:  # noqa: BLE001 - a crash is a fail, reported by type
        return FAIL, "error", type(exc).__name__
    finally:
        root.removeHandler(cap)
        root.setLevel(old)
    return (PASS, "", "") if not fails else (FAIL, "differs", "; ".join(fails))


def run_all():
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]
    results, totals = {}, collections.defaultdict(lambda: collections.Counter())
    for c in cases:
        status, reason, detail = run_case(c)
        results[c["id"]] = {"class": c["class"], "status": status, "reason": reason, "detail": detail}
        totals[c["class"]][status] += 1
    by_class = {k: {s: totals[k].get(s, 0) for s in (PASS, FAIL, NOT_RUNNABLE)} for k in sorted(totals)}
    allt = {s: sum(v[s] for v in by_class.values()) for s in (PASS, FAIL, NOT_RUNNABLE)}
    return {"cases": len(cases), "totals": allt, "by_class": by_class,
            "failing_ids": [i for i, r in results.items() if r["status"] == FAIL],
            "not_runnable": {i: r["reason"] for i, r in results.items() if r["status"] == NOT_RUNNABLE},
            "results": results}


def render(rep):
    lines = [f"{'id':<7}{'class':<12}{'status':<14}reason / detail"]
    for i, r in rep["results"].items():
        lines.append(f"{i:<7}{r['class']:<12}{r['status']:<14}{(r['reason'] + ' ' + r['detail']).strip()}")
    lines.append("")
    for k, v in rep["by_class"].items():
        lines.append(f"{k:<12} pass {v[PASS]:>3}  fail {v[FAIL]:>3}  not_runnable {v[NOT_RUNNABLE]:>3}")
    t = rep["totals"]
    lines.append(f"{'TOTAL':<12} pass {t[PASS]:>3}  fail {t[FAIL]:>3}  not_runnable {t[NOT_RUNNABLE]:>3}  of {rep['cases']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", help="write the full report as JSON to this path")
    ap.add_argument("--write-baseline", action="store_true", help="record the result as labs/memory-claims-lab/baseline-today.json")
    a = ap.parse_args(argv)
    rep = run_all()
    print(render(rep))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    if a.write_baseline:
        BASELINE.write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
