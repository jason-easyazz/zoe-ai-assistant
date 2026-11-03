"""Commit-bound, complete baselines for the Samantha benches (T-4, deep dive 2, 2026-10-19).

The bar's baseline held S1-S8 only, so 15 scenarios could not regress (``compare``
iterated the baseline's keys: a cell missing from the baseline was vacuously "not
regressed"); the day-sim and the person bench had no baseline at all. Three rules,
one place, used by samantha_bar.py, samantha_day_sim.py and samantha_person.py:

  * RECORD only a COMPLETE run (every cell of the bench, no partial selection) on a
    CLEAN, verified tree: the baseline names the commit + tree it is evidence for.
    A dirty or unverifiable tree, an error status, or a missing cell is refused.
  * COMPARE refuses a baseline that lacks a cell the run is about to score: a cell
    with no baseline is unmeasured, and unmeasured is never green.
  * A REGRESSION is a cell that was PASS in the baseline and is not PASS now (SKIP,
    ERROR, INCONCLUSIVE all count: a cell that used to prove something and now proves
    nothing has regressed). A cell that was not PASS cannot regress; one that newly
    passes is an improvement (re-record to lock it in).

Stdlib only, pure functions, no I/O beyond ``load`` / ``write``.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable

PASS = "PASS"


def record_problem(revision: dict[str, Any] | None, *, status: str, partial: bool,
                   verdicts: dict[str, str], cells: Iterable[str]) -> str | None:
    """Why this run may NOT become the baseline, or None."""
    if partial:
        return "a partial run (--only / --axis / --n-cap) never records a baseline"
    if status != "ok":
        return f"the run's status is {status!r}, not 'ok'"
    if not isinstance(revision, dict) or not revision.get("commit"):
        return "the service revision is unknown: a baseline must name the commit it is evidence for"
    if revision.get("clean_verified") is not True:
        return "the service tree's cleanliness could not be verified"
    if revision.get("dirty"):
        return (f"the service tree at {str(revision.get('commit'))[:10]} is DIRTY: an uncommitted tree "
                "cannot be the bar (commit or stash the live checkout's changes first)")
    missing = sorted(set(cells) - set(verdicts))
    if missing:
        return f"the run did not score every cell (missing {', '.join(missing)})"
    return None


def make(harness: str, harness_version: str, verdicts: dict[str, str], revision: dict[str, Any],
         **extra: Any) -> dict[str, Any]:
    return {"harness": harness, "harness_version": harness_version,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "revision": revision, "commit": revision.get("commit"), "tree": revision.get("tree"),
            "cells": dict(sorted(verdicts.items())), **extra}


def load(path: Path, valid: Iterable[str]) -> tuple[dict[str, Any] | None, str | None]:
    """(baseline, None) or (None, problem). A missing file, bad JSON, no ``cells``
    mapping, or a verdict outside ``valid`` is a named problem, never "no baseline,
    so nothing regressed"."""
    path = Path(path)
    if not path.exists():
        return None, f"no baseline at {path} - record one with --record-baseline"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"baseline {path} unreadable: {exc}"
    cells = data.get("cells") if isinstance(data, dict) else None
    if not isinstance(cells, dict) or not cells:
        return None, f"baseline {path} has no 'cells' - re-record it"
    allowed = set(valid)
    bad = sorted(k for k, v in cells.items() if v not in allowed)
    if bad:
        return None, f"baseline {path} has unrecognised verdict(s) for {', '.join(bad)} - re-record it"
    return data, None


def missing_cells(baseline: dict[str, Any] | None, expected: Iterable[str], key: str = "cells") -> list[str]:
    """Cells the run will score that the baseline does not hold (each is unmeasured)."""
    have = set(((baseline or {}).get(key) or {}))
    return sorted(set(expected) - have)


def compare(current: dict[str, str], baseline: dict[str, Any] | None, expected: Iterable[str],
            key: str = "cells") -> dict[str, Any]:
    """Regressions / improvements over the ``expected`` cells; ``refused`` when the
    baseline lacks one of them (the comparison would be vacuous for that cell)."""
    exp = sorted(set(expected))
    base = (baseline or {}).get(key) or {}
    missing = [c for c in exp if c not in base]
    regressions = sorted(c for c in exp if base.get(c) == PASS and current.get(c) != PASS)
    improvements = sorted(c for c in exp if current.get(c) == PASS and c in base and base[c] != PASS)
    return {"has_baseline": bool(base), "regressions": regressions, "improvements": improvements,
            "missing_in_baseline": missing, "refused": bool(missing),
            "red": bool(regressions) or bool(missing),
            "baseline_commit": ((baseline or {}).get("revision") or {}).get("commit") or (baseline or {}).get("commit")}


def write(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
