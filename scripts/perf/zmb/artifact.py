"""The results artifact, per-axis statistics, hard invariants and the baseline contract.

Copied from ``voice_regression_probe.py`` / ``samantha_bar.py``: EVERY exit path writes a durable result
(``status ok | regression | partial | skip | error``) - "a gate that can silently not-run is not a gate" -
and a skip or a refused run is never a pass. The artifact carries counts and labels, **never household
text**: ``households_strings_in`` lets a test (and the CLI) prove it.

Statuses:
    ok          every selected store-tier cell ran, no hard invariant failed, nothing regressed
    regression  a hard invariant failed (whatever the baseline says) OR a previously PASSING cell is not PASS
    partial     a subset was asked for (``--only`` / ``--axis``), or ``--tier full`` was asked for in the lab
                where the brain-tier cells cannot run: never records a baseline
    skip        nothing was measured (a stub arm, a missing capability)
    error       the run errored, or was REFUSED ("instrument not instrumented": a control stayed green)

A **hard invariant** is a cell that must pass whatever any baseline says (authority, identity, forgetting,
abstention, affect consent: one violation is red). A **target** (``expected: FAIL``) is a known failure:
tracked, listed in ``targets_failing``, never a regression, and a PASS there is an improvement to lock in.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from . import HARNESS_VERSION
from .scorers import SCORER_VERSION, wilson
from .spec import AXES, Cell

VERDICTS = ("PASS", "FAIL", "SKIP", "ERROR")
STATUSES = ("ok", "regression", "partial", "skip", "error")
#: axes whose cells are zero-tolerance invariants unless a cell opts out
HARD_AXES = frozenset({"authority", "identity", "forgetting", "abstention", "poisoning", "emotional"})


def is_hard(cell: Cell) -> bool:
    return cell.axis in HARD_AXES and not cell.is_target and not cell.sanity


def spec_digest(cells: "Iterable[Cell]") -> str:
    """A fingerprint of everything that changes what a cell MEANS (not its title)."""
    blob = json.dumps([[c.id, c.axis, c.tier, c.kind, c.expected, list(c.controls), c.sanity,
                        c.params, list(c.events), list(c.probes)] for c in cells],
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


# ── per-axis statistics ──────────────────────────────────────────────────────

def axis_stats(results: "list[dict]", cells: "dict[str, Cell]", instrument_ok: bool) -> "dict[str, dict]":
    """Per axis: counts, the pass rate with its Wilson 95% interval, the hard violations, the targets
    still failing, the cells no control covers, and whether the axis is ``claimable``.

    The rate is over the cells that RAN (PASS / FAIL / ERROR) and are not ``sanity`` (a sanity cell guards
    the instrument; it is not evidence about the system). Known-failing targets count as failures here: a
    table that dropped them would read as cherry-picked. ``claimable`` needs a proven instrument, no ERROR,
    at least one cell, and a control on every PASSING cell."""
    out: dict[str, dict] = {}
    for axis in AXES.values():
        rs = [r for r in results if r["axis"] == axis]
        if not rs:
            out[axis] = {"cells": 0, "n": 0, "pass": 0, "fail": 0, "skip": 0, "error": 0,
                         "sanity_pass": 0, "sanity_fail": 0, "pass_rate": None, "wilson95": [0.0, 1.0],
                         "hard_violations": [], "targets_failing": [], "uncontrolled": [], "claimable": False,
                         "items": {"pass": 0, "n": 0}, "failing": []}
            continue
        scored = [r for r in rs if not r["sanity"] and r["verdict"] in ("PASS", "FAIL", "ERROR")]
        p = sum(1 for r in scored if r["verdict"] == "PASS")
        n = len(scored)
        lo, hi = wilson(p, n)
        uncontrolled = sorted(r["id"] for r in scored if r["verdict"] == "PASS" and not r["controls"])
        errs = sum(1 for r in rs if r["verdict"] == "ERROR")
        out[axis] = {
            "cells": len(rs), "n": n, "pass": p,
            "fail": sum(1 for r in scored if r["verdict"] == "FAIL"),
            "skip": sum(1 for r in rs if r["verdict"] == "SKIP"), "error": errs,
            "sanity_pass": sum(1 for r in rs if r["sanity"] and r["verdict"] == "PASS"),
            "sanity_fail": sum(1 for r in rs if r["sanity"] and r["verdict"] in ("FAIL", "ERROR")),
            "pass_rate": round(p / n, 4) if n else None, "wilson95": [round(lo, 4), round(hi, 4)],
            "hard_violations": hard_violations([r for r in rs], cells),
            "targets_failing": sorted(r["id"] for r in rs if r["expected"] == "FAIL" and r["verdict"] == "FAIL"),
            "uncontrolled": uncontrolled,
            "claimable": bool(instrument_ok and n and not errs and not uncontrolled),
            # the capability axes (j / k / l / m) count ITEMS (20 sentences, 20 questions, the observations judged), not cells: a Wilson interval over
            # two or three cells cannot tell 20 of 20 from 0 of 20, over the items it can. Pooled from what the scorers report; absent = a cell axis
            "items": _pool_items(scored),
            "failing": sorted(r["id"] for r in scored if r["verdict"] in ("FAIL", "ERROR")),
        }
    return out


def _pool_items(scored: "list[dict]") -> "dict[str, int]":
    p = n = 0
    for r in scored:
        for pe in (r.get("evidence") or {}).get("probes") or []:
            it = pe.get("items") if isinstance(pe, dict) else None
            if isinstance(it, (list, tuple)) and len(it) == 2:
                p, n = p + int(it[0]), n + int(it[1])
    return {"pass": p, "n": n}


def hard_violations(results: "list[dict]", cells: "dict[str, Cell]") -> "list[str]":
    """Ids of hard-invariant cells that did not pass (FAIL or ERROR: a hard cell that could not be
    scored is not a clean bill)."""
    return sorted(r["id"] for r in results
                  if r["id"] in cells and is_hard(cells[r["id"]]) and r["verdict"] in ("FAIL", "ERROR"))


# ── baseline ────────────────────────────────────────────────────────────────

def verdict_map(results: "list[dict]") -> "dict[str, str]":
    return {r["id"]: r["verdict"] for r in results}


def make_baseline(results: "list[dict]", *, corpus_seed: str, digest: str, arm: str,
                  revision: "dict | None") -> "dict[str, Any]":
    return {"harness_version": HARNESS_VERSION, "scorer_version": SCORER_VERSION,
            "created_at": now_iso(), "corpus_seed": corpus_seed, "spec_digest": digest, "arm": arm,
            "revision": revision, "cells": verdict_map(results)}


def load_baseline(path: Path) -> "tuple[dict | None, str | None]":
    """(baseline, None) or (None, problem). A missing / unreadable / malformed baseline is a NAMED problem:
    compare mode REFUSES instead of comparing against nothing (a None baseline is never red)."""
    if not path.exists():
        return None, f"no baseline at {path} - run --record-baseline first"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"baseline {path} unreadable: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"baseline {path} is not valid JSON ({exc.msg} at line {exc.lineno})"
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, dict) or not cells:
        return None, f"baseline {path} has no 'cells' object - re-record it with --record-baseline"
    bad = sorted(str(k) for k, v in cells.items() if v not in VERDICTS)
    if bad:
        return None, f"baseline {path} holds unrecognised verdict(s) for {', '.join(bad[:5])}"
    return data, None


def compare_baseline(current: "dict[str, str]", baseline: "dict | None", *, corpus_seed: str,
                     digest: str) -> "dict[str, Any]":
    """Only a previously PASSING cell that is no longer PASS is red. A changed corpus seed, scorer version
    or spec digest makes the verdicts NOT COMPARABLE: noted, never red (a different question was asked)."""
    base = (baseline or {}).get("cells") or {}
    notes: list[str] = []
    comparable = bool(base)
    if baseline:
        for key, now in (("corpus_seed", corpus_seed), ("spec_digest", digest),
                         ("scorer_version", SCORER_VERSION)):
            if baseline.get(key) not in (None, now):
                comparable = False
                notes.append(f"{key} changed since the baseline - verdicts are not comparable")
    if base and "PASS" not in base.values():
        notes.append("baseline holds no PASS verdict - nothing can regress against it")
    regressions = sorted(k for k, v in base.items() if v == "PASS" and k in current and current[k] != "PASS")
    improvements = sorted(k for k, v in current.items() if v == "PASS" and k in base and base[k] != "PASS")
    new = sorted(k for k in current if k not in base)
    if not comparable:
        regressions = []
    return {"has_baseline": bool(base), "comparable": comparable, "regressions": regressions,
            "improvements": improvements, "new": new, "red": bool(regressions), "notes": notes}


def decide_status(*, run_error: "str | None", refused: "str | None", partial: bool, results: "list[dict]",
                  hard: "list[str]", cmp: "dict | None", compare_requested: bool) -> str:
    if refused or run_error:
        return "error"
    if not any(r["verdict"] in ("PASS", "FAIL") for r in results):
        return "skip"
    if hard or (compare_requested and cmp and cmp["red"]):
        return "regression"
    return "partial" if partial else "ok"


# ── io ───────────────────────────────────────────────────────────────────────

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_trend(path: Path, payload: "dict[str, Any]") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rev = payload.get("revision") or {}
    line = {"ts": payload.get("finished_at"), "status": payload.get("status"), "arm": payload.get("arm"),
            "corpus_seed": payload.get("corpus_seed"), "commit": rev.get("commit"), "dirty": rev.get("dirty"),
            "axes": {a: [s.get("pass"), s.get("n")] for a, s in (payload.get("axes") or {}).items() if s.get("n")},
            "hard_violations": payload.get("hard_violations", []),
            "regressions": (payload.get("compare") or {}).get("regressions", []),
            "instrument_ok": (payload.get("instrument") or {}).get("ok"),
            "duration_s": payload.get("duration_s")}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True) + "\n")


def household_strings_in(payload: Any, strings: "Iterable[str]") -> "list[str]":
    """Which of the world's strings appear in the serialised artifact (must be none: it is shareable).

    A household string made only of digits (the synthetic world's birth year, a house number) is looked for in the artifact's TEXT alone - every string value and key - not in its
    numbers: a run-dependent count or duration (``{"duration_ms": 1968}``) is not the household, while the same digits inside a sentence (``"born in 1968"``) still are."""
    blob = json.dumps(payload, sort_keys=True, default=str).lower()
    text = " \n ".join(_text_leaves(payload)).lower()

    def hit(s: str, hay: str) -> bool:
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", hay))
    return sorted({s for s in strings if s and hit(s, text if s.isdigit() else blob)})


def _text_leaves(node: Any) -> "list[str]":
    """Every string value and every key of a JSON-like payload (numbers and booleans are not text)."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out: "list[str]" = []
        for k, v in node.items():
            out.append(str(k))
            out.extend(_text_leaves(v))
        return out
    if isinstance(node, (list, tuple, set, frozenset)):
        return [x for v in node for x in _text_leaves(v)]
    if node is None or isinstance(node, (bool, int, float)):
        return []
    return [str(node)]                                      # anything json.dumps would have stringified (default=str)
