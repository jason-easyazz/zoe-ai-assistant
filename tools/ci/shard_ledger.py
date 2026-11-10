#!/usr/bin/env python3
"""Prove a sharded pytest lane ran EVERY test exactly once: ``verify`` the shard ledgers against the full one.

    python3 tools/ci/shard_ledger.py verify --lane zoe-data --full full.json --expect-shards 4 shard-*.json
    python3 tools/ci/shard_ledger.py weights --out tools/ci/shard_weights.json ledger-*.json   # refresh the weights

Inputs are ledgers written by ``tools/ci/pytest_shard_ledger.py``. Exit 0 only when ALL hold:

* the full ledger is a clean ``--collect-only`` run (exit 0) with at least one test;
* exactly ``--expect-shards`` shard ledgers exist, each a finished run that exited 0 and selected >= 1 test;
* the shards' selections, taken together, equal the full selection as a MULTISET — a dropped test, a test in
  two shards, or a shard that collected a different tree all fail;
* inside every shard, every selected test produced a report (``ran == selected``).

Node ids are compared with any trailing parametrize id (``[...]``) stripped: a few ci_safe tests parametrize
over freshly minted tokens, so their ids differ between two processes while the TEST is the same one. The
multiset comparison still counts every parametrized case. Pure stdlib, so the aggregator job needs no deps.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

_PARAM = re.compile(r"\[.*\]$", re.S)


def norm(nodeid: str) -> str:
    return _PARAM.sub("[]", nodeid) if nodeid.endswith("]") else nodeid


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(lane: str, full: dict, shards: list[tuple[str, dict]], expect_shards: int) -> list[str]:
    """Return the list of violations (empty = the shards partition the full selection exactly)."""
    errs: list[str] = []
    if not full.get("collect_only"):
        errs.append(f"{lane}: the full ledger is not a --collect-only run")
    if full.get("exitstatus") != 0:
        errs.append(f"{lane}: the full collection exited {full.get('exitstatus')!r}, not 0 (collection error?)")
    want = Counter(norm(n) for n in full.get("selected") or [])
    if not want:
        errs.append(f"{lane}: the full collection selected ZERO tests")
    if len(shards) != expect_shards:
        errs.append(f"{lane}: expected {expect_shards} shard ledgers, found {len(shards)}")
    got: Counter = Counter()
    for name, led in shards:
        sel = led.get("selected") or []
        if led.get("collect_only"):
            errs.append(f"{lane}/{name}: a shard ledger from a --collect-only run proves nothing ran")
        if led.get("exitstatus") != 0:
            errs.append(f"{lane}/{name}: shard exited {led.get('exitstatus')!r}, not 0 (unfinished or failed)")
        if not sel:
            errs.append(f"{lane}/{name}: shard selected ZERO tests")
        missing_reports = set(sel) - set(led.get("ran") or [])
        if missing_reports:
            sample = sorted(missing_reports)[:5]
            errs.append(f"{lane}/{name}: {len(missing_reports)} selected test(s) never reported, e.g. {sample}")
        got.update(norm(n) for n in sel)
    dropped = want - got
    extra = got - want
    if dropped:
        errs.append(f"{lane}: {sum(dropped.values())} test(s) in the full selection ran in NO shard, "
                    f"e.g. {sorted(dropped)[:5]}")
    if extra:
        errs.append(f"{lane}: {sum(extra.values())} test(s) ran more often than collected (or are not in the "
                    f"full selection), e.g. {sorted(extra)[:5]}")
    return errs


def file_weights(ledgers: list[dict]) -> dict[str, float]:
    """{test file: measured seconds} summed over the per-test ``durations`` of the given ledgers (a plain
    ``{nodeid: seconds}`` dict is accepted too). Rounded to 0.1 s so a refresh diff shows real changes only."""
    out: dict[str, float] = {}
    for led in ledgers:
        durations = led.get("durations", led) if isinstance(led, dict) else {}
        for nodeid, secs in durations.items():
            if not isinstance(secs, (int, float)):
                continue
            f = nodeid.split("::", 1)[0]
            out[f] = out.get(f, 0.0) + float(secs)
    return {f: round(s, 1) for f, s in sorted(out.items())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--lane", required=True)
    v.add_argument("--full", required=True, type=Path)
    v.add_argument("--expect-shards", required=True, type=int)
    v.add_argument("shards", nargs="*", type=Path)
    w = sub.add_parser("weights", help="write {test file: seconds} from shard ledgers (the shard weights file)")
    w.add_argument("--out", required=True, type=Path)
    w.add_argument("ledgers", nargs="+", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "weights":
        files = file_weights([_load(p) for p in a.ledgers])
        doc = {"note": "GENERATED by tools/ci/shard_ledger.py weights from CI shard ledgers: measured seconds per test "
                       "file, used ONLY to cut the validate shards evenly. Stale is harmless (uneven shards, never a "
                       "dropped test); refresh by hand, never per PR.",
               "files": files}
        a.out.write_text(json.dumps(doc, indent=0, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {a.out}: {len(files)} files, {sum(files.values()):.0f}s")
        return 0
    if not a.full.is_file():
        print(f"::error::{a.lane}: full ledger {a.full} is missing — the collect job did not produce it")
        return 1
    full = _load(a.full)
    shards = [(p.name, _load(p)) for p in sorted(a.shards) if p.is_file()]
    errs = verify(a.lane, full, shards, a.expect_shards)
    total = sum(len(led.get("selected") or []) for _, led in shards)
    print(f"{a.lane}: full selection {len(full.get('selected') or [])} tests; "
          f"{len(shards)} shard(s) selected {total}: "
          + ", ".join(f"{n}={len(led.get('selected') or [])}" for n, led in shards))
    for e in errs:
        print(f"::error::{e}")
    if not errs:
        print(f"{a.lane}: OK — the shards partition the full selection exactly and every selected test reported")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
