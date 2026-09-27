"""Score the controller on the 50-pair fixture. Hand-run: ``python3 run_fixture.py``.

Prints per-category accuracy and per-decision precision/recall, and the same
table for the two negative controls (no overlap check; no richer rule) so the
value of each rule is visible as a number, not an assertion.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bitemporal import ADD, NONE, SUPERSEDE, UPDATE, Fact, apply, live_texts, reconcile  # noqa: E402
from fake_judge import fake_judge  # noqa: E402
from fixture import CASES  # noqa: E402

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)
EVENTS = (ADD, UPDATE, SUPERSEDE, NONE)


def build(case: dict) -> tuple[Fact, dict[int, Fact]]:
    store = {
        cid: Fact(id=cid, text=text, valid_from=vf, valid_until=vu, created_at="2026-01-01")
        for cid, text, vf, vu in case["nb"]
    }
    new = Fact(id=0, text=case["new"], valid_from=case.get("new_from"),
               valid_until=case.get("new_until"))
    return new, store


def run(**kw) -> dict:
    """→ {"accuracy", "per_cat", "precision", "recall", "rows", "failures"}."""
    per_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    tp, fp, fn = Counter(), Counter(), Counter()
    failures, rows = [], []
    for i, case in enumerate(CASES):
        new, store = build(case)
        d = reconcile(new, list(store.values()), judge=fake_judge, now=NOW, **kw)
        got = (d.event, d.target_id)
        exp = tuple(case["expect"])
        ok = got == exp
        # post-apply invariants the fixture pins
        store = apply(d, new, store, now=NOW)
        live = sorted(live_texts(store))
        if ok and "expect_live" in case:
            ok = sorted(t.lower() for t in case["expect_live"]) == [t.lower() for t in live]
        if ok and "expect_live_count" in case:
            ok = len(live) == case["expect_live_count"]
        if ok and "expect_rows" in case:
            ok = len(store) == case["expect_rows"]
        per_cat[case["cat"]][1] += 1
        if ok:
            per_cat[case["cat"]][0] += 1
            tp[exp[0]] += 1
        else:
            fp[got[0]] += 1
            fn[exp[0]] += 1
            failures.append((i, case["cat"], case["new"], exp, got, d.reason, live))
        rows.append((i, case["cat"], ok, got, exp))
    correct = sum(v[0] for v in per_cat.values())
    precision = {e: tp[e] / (tp[e] + fp[e]) if (tp[e] + fp[e]) else 1.0 for e in EVENTS}
    recall = {e: tp[e] / (tp[e] + fn[e]) if (tp[e] + fn[e]) else 1.0 for e in EVENTS}
    return {
        "accuracy": correct / len(CASES),
        "per_cat": {k: (v[0], v[1]) for k, v in per_cat.items()},
        "precision": precision, "recall": recall,
        "macro_precision": sum(precision.values()) / len(EVENTS),
        "macro_recall": sum(recall.values()) / len(EVENTS),
        "failures": failures, "rows": rows,
    }


def report(title: str, r: dict) -> None:
    print(f"\n== {title} ==  accuracy {r['accuracy']:.2f}  "
          f"macro P {r['macro_precision']:.2f}  macro R {r['macro_recall']:.2f}")
    for cat, (ok, n) in r["per_cat"].items():
        print(f"  {cat:<12} {ok:>2}/{n}")
    for e in EVENTS:
        print(f"  {e:<10} P {r['precision'][e]:.2f}  R {r['recall'][e]:.2f}")
    for f in r["failures"]:
        print(f"  MISS #{f[0]:02d} [{f[1]}] {f[2]!r}: expected {f[3]} got {f[4]} — {f[5]}; live={f[6]}")


if __name__ == "__main__":
    report("controller (overlap + richer)", run())
    report("NEGATIVE CONTROL: no overlap check", run(overlap_check=False))
    report("NEGATIVE CONTROL: no richer rule (newest wins)", run(richer_rule=False))
