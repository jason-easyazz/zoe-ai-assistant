#!/usr/bin/env python3
"""restraint_eval.py - the offline measurement of the restraint sensitivity classifier (``restraint.classify``).

The ledger's bar before ``ZOE_RESTRAINT`` may leave shadow is ">= 90 % class-exact on a fresh set with 0 plain false
positives" (docs/knowledge/restraint.md "Languages"). This script is that measurement, reproducible with no network, no
live service and no database:

    python3 scripts/perf/restraint_eval.py            # the table: dev and held-out, per class, with and without the head
    python3 scripts/perf/restraint_eval.py --misses   # also list the DEV misses (never the held-out ones)

"class-exact" is the repo's definition (the labelled class is among the classes returned: ``want in classify(text)``);
``strict`` additionally requires the returned set to equal the labelled set exactly (reported, not the bar).

SPLITS. ``dev`` is everything the vocabulary and the head were tuned on (the three legacy English sets whose misses were read
and the invented synthetic set in ``tests/fixtures/restraint_labelled.json``). ``held-out`` is never read to tune: the legacy
blind set 3 (``HELD3_*`` in ``tests/test_restraint_lexicon.py``) and the blind set in the same fixture, written by a
separate author without sight of the lexicon or the head. A held-out miss is counted, never listed here.

Stdlib + the repo; the head arm needs numpy and fastembed (bge-small, already loaded by ``semantic_router``).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZD = ROOT / "services" / "zoe-data"
TESTS = ZD / "tests"
FIXTURE = TESTS / "fixtures" / "restraint_labelled.json"
CLASSES = ("health", "money", "family_conflict", "grief", "other_member", "affect")

sys.path.insert(0, str(ZD))
sys.path.insert(0, str(TESTS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)           # type: ignore[arg-type]
    spec.loader.exec_module(mod)                           # type: ignore[union-attr]
    return mod


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_sets() -> dict[str, list[tuple[tuple[str, ...], str]]]:
    """``{"dev": [...], "held_out": [...]}``, each item ``(classes, text)`` (``()`` is plain)."""
    tl = _load("tl_restraint_lexicon", TESTS / "test_restraint_lexicon.py")
    tr = _load("tr_restraint", TESTS / "test_restraint.py")
    fx = _fixture()

    def sens(rows):
        return [((w,), t) for w, t in rows]

    def plain(rows):
        return [((), t) for t in rows]

    dev = (sens(tl.FRESH_SENSITIVE) + sens(tl.HELD2_SENSITIVE) + sens(tr.HELD_OUT_SENSITIVE)
           + plain(tl.FRESH_PLAIN) + plain(tl.HELD2_PLAIN) + plain(tr.HELD_OUT_PLAIN)
           + [(tuple(i["classes"]), i["text"]) for i in fx["dev"]])
    seen = {_norm(t) for _w, t in dev}                      # a held-out sentence that also sits in dev is not held out: dropped
    legacy = [r for r in sens(tl.HELD3_SENSITIVE) + plain(tl.HELD3_PLAIN) if _norm(r[1]) not in seen]
    blind = [(tuple(i["classes"]), i["text"]) for i in fx["held_out"] if _norm(i["text"]) not in seen]
    return {"dev": dev, "held_out": legacy + blind, "held_out: legacy set 3": legacy, "held_out: blind set": blind}


def score(items, classify_fn) -> dict:
    """Per-class recall (the labelled class is among the returned ones), strict set-equality, and plain false positives."""
    per = {c: [0, 0, 0] for c in CLASSES}                  # n, hit, strict
    plain_n = plain_fp = 0
    confusion: Counter = Counter()
    misses = []
    for want, text in items:
        got = tuple(classify_fn(text))
        if not want:
            plain_n += 1
            if got:
                plain_fp += 1
                misses.append(("FP", want, got, text))
            continue
        for w in want:
            row = per[w]
            row[0] += 1
            if w in got:
                row[1] += 1
            else:
                confusion[(w, got or ("none",))] += 1
                misses.append(("MISS", (w,), got, text))
            if set(got) == set(want):
                row[2] += 1
    n = sum(r[0] for r in per.values())
    hit = sum(r[1] for r in per.values())
    return {"per": per, "n": n, "hit": hit, "plain_n": plain_n, "plain_fp": plain_fp,
            "confusion": confusion, "misses": misses}


def _pct(a: int, b: int) -> str:
    return f"{100 * a / b:5.1f}%" if b else "  n/a"


def table(title: str, res: dict) -> str:
    out = [f"{title}: class-exact {res['hit']}/{res['n']} ({_pct(res['hit'], res['n']).strip()}), "
           f"plain false positives {res['plain_fp']}/{res['plain_n']}"]
    out.append(f"  {'class':<16}{'n':>5}{'class-exact':>14}{'strict':>10}")
    for c in CLASSES:
        n, h, s = res["per"][c]
        out.append(f"  {c:<16}{n:>5}{h:>8} {_pct(h, n)}{_pct(s, n):>10}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--misses", action="store_true", help="list the dev misses (held-out misses are never listed)")
    ap.add_argument("--confusion", action="store_true", help="also print the held-out miss confusion (counts only)")
    ap.add_argument("--no-head",action="store_true", help="measure the word list alone (the pre-head classifier)")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args()

    import restraint
    import restraint_head

    sets = load_sets()
    if not args.no_head and restraint_head.available():
        import numpy as np
        from fastembed import TextEmbedding
        model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")    # what semantic_router loads in the live service

        def _embed(text: str):
            v = np.asarray(next(iter(model.embed([text]))), dtype=np.float32)
            return v / (np.linalg.norm(v) + 1e-9)
        restraint_head.set_embedder(_embed)
    arms ={"lexicon only": lambda t: restraint.classify(t, head=False)}
    if not args.no_head:
        arms["lexicon + head"] = lambda t: restraint.classify(t, head=True)
    summary = {}
    for arm, fn in arms.items():
        for split, items in sets.items():
            res = score(items, fn)
            summary[f"{arm} / {split}"] = {"class_exact": res["hit"] / max(res["n"], 1), "n": res["n"],
                                           "plain_fp": res["plain_fp"], "plain_n": res["plain_n"],
                                           "per_class": {c: (r[1] / r[0] if r[0] else None) for c, r in res["per"].items()}}
            if not args.json:
                print(table(f"[{arm}] {split}", res))
                if args.misses and split == "dev":
                    for kind, want, got, text in res["misses"]:
                        print(f"    {kind} want={'/'.join(want) or 'plain'} got={'/'.join(got) or 'none'}: {text}")
                if split == "held_out" and res["confusion"] and args.confusion:
                    print("  held-out miss confusion (labelled class -> returned):")
                    for (w, got), k in sorted(res["confusion"].items(), key=lambda x: -x[1]):
                        print(f"    {w:<16}-> {'/'.join(got):<24}{k}")
                print()
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    print(f"head: {restraint_head.describe()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
