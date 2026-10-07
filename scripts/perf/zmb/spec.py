"""The scenario SPEC format: cells as data, rendered from a seeded world.

A spec file is JSON (stdlib only - the slim CI lane has no YAML):

    {"spec_version": 1, "axis": "authority", "cells": [ <cell>, ... ]}

A **cell** is one claim the bench makes about a memory system:

    id            unique, ``<axis letter><n>.<shape>``  (``A1.digest.home``)
    title         one line: what the cell proves
    tier          ``store`` (no brain reply: runs in the lab) | ``full`` (needs the brain's reply)
    kind          which cell script runs it (``cells.KINDS``); a ``full`` cell may omit the script
    expected      ``PASS`` (default) or ``FAIL``: a known failure, a TARGET (tracked, never a regression)
    controls      the features whose removal MUST turn this cell red (``lab_driver.CONTROLS``). A PASS cell with
                  none is *uncontrolled* and cannot support a claim
    sanity        true = guards the instrument (a positive control / scripted-reader check): it must pass, but
                  it is not evidence about the system and is left out of the axis pass rate
    skip_reason   a declared-but-skipped cell says WHY (a brain-tier cell in the lab)
    lme_map       the LongMemEval / BEAM ability it corresponds to ("" = none: the field has no such cell)
    params        what the script needs; strings may use ``{world_slot}`` placeholders
    matrix        optional expansion: ``{"writer": [...], "attr": [{...}, ...]}``. ``<writer>`` and
                  ``<attr.field>`` in ANY string of the cell are replaced, one cell per combination; a matrix
                  entry's ``_controls`` / ``_expected`` keys override that cell's own

World placeholders (``{owner}``, ``{home}``, ``{dob_text}`` ...) are filled by ``World.render_deep`` at run
time, so the SAME spec runs on the baseline seed, a held-out seed and every arm.
"""
from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .lab_driver import CONTROLS
from .world import World, placeholders

SPEC_VERSION = 1
SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"

#: the nine axes (letter -> name), the same vocabulary as samantha_bar.AXES
AXES = {"a": "authority", "b": "extraction", "c": "temporal", "d": "recall", "e": "abstention",
        "f": "forgetting", "g": "emotional", "h": "identity", "i": "poisoning",
        # the capability axes (2026-10-07): what Hindsight and MemPalace are built for, not storage hygiene
        "j": "exact_words", "k": "reflection", "l": "multi_hop", "m": "protocol"}
TIERS = ("store", "full")
EXPECTED = ("PASS", "FAIL")
_CELL_KEYS = {"id", "title", "tier", "kind", "expected", "controls", "sanity", "skip_reason", "lme_map",
              "params", "events", "probes", "matrix", "axis", "note"}


class SpecError(ValueError):
    """A scenario spec is malformed. Always loud: a half-read spec must never run."""


@dataclass(frozen=True)
class Cell:
    id: str
    axis: str
    title: str
    tier: str = "store"
    kind: str = ""
    expected: str = "PASS"
    controls: tuple[str, ...] = ()
    sanity: bool = False
    skip_reason: str = ""
    lme_map: str = ""
    note: str = ""
    params: "dict[str, Any]" = field(default_factory=dict)
    events: tuple = ()
    probes: tuple = ()

    @property
    def is_target(self) -> bool:
        return self.expected == "FAIL"

    def rendered(self, world: World) -> "Cell":
        """The same cell with every world placeholder filled."""
        return replace(self, params=world.render_deep(self.params), events=world.render_deep(self.events),
                       probes=world.render_deep(self.probes))


def _subst(obj: Any, env: "dict[str, Any]") -> Any:
    """Replace ``<name>`` / ``<name.field>`` matrix variables in every string."""
    def one(s: str) -> str:
        def rep(m: "re.Match[str]") -> str:
            name, sub = m.group(1), m.group(2)
            if name not in env:
                return m.group(0)
            val = env[name]
            if sub:
                if not isinstance(val, dict) or sub not in val:
                    raise SpecError(f"matrix variable <{name}.{sub}> is not defined")
                val = val[sub]
            return str(val)
        return re.sub(r"<([a-z_]+)(?:\.([a-z_]+))?>", rep, s)
    if isinstance(obj, str):
        return one(obj)
    if isinstance(obj, list):
        return [_subst(v, env) for v in obj]
    if isinstance(obj, dict):
        return {k: _subst(v, env) for k, v in obj.items()}
    return obj


def expand_matrix(raw: "dict[str, Any]") -> "list[dict[str, Any]]":
    """One raw cell dict -> its concrete cells (a cell with no ``matrix`` expands to itself)."""
    matrix = raw.get("matrix")
    base = {k: v for k, v in raw.items() if k != "matrix"}
    if not matrix:
        return [base]
    if not isinstance(matrix, dict) or not matrix:
        raise SpecError(f"cell {raw.get('id')!r}: matrix must be a non-empty object of lists")
    names = list(matrix)
    for n in names:
        if not isinstance(matrix[n], list) or not matrix[n]:
            raise SpecError(f"cell {raw.get('id')!r}: matrix.{n} must be a non-empty list")
    out = []
    for combo in itertools.product(*(matrix[n] for n in names)):
        env = dict(zip(names, combo))
        cell = _subst(base, env)
        for v in combo:  # a matrix entry may override cell-level fields with ``_``-prefixed keys
            if isinstance(v, dict):
                for k, val in v.items():
                    if k.startswith("_"):
                        cell[k[1:]] = val
        out.append(cell)
    return out


def _to_cell(raw: "dict[str, Any]", file_axis: str) -> Cell:
    unknown = set(raw) - _CELL_KEYS
    if unknown:
        raise SpecError(f"cell {raw.get('id')!r}: unknown key(s) {', '.join(sorted(unknown))}")
    for req in ("id", "title"):
        if not raw.get(req):
            raise SpecError(f"a cell needs {req!r}: {raw!r:.80}")
    axis = raw.get("axis") or file_axis
    if axis not in AXES.values():
        raise SpecError(f"cell {raw['id']!r}: unknown axis {axis!r} (known: {', '.join(AXES.values())})")
    tier = raw.get("tier", "store")
    if tier not in TIERS:
        raise SpecError(f"cell {raw['id']!r}: tier must be one of {TIERS}, got {tier!r}")
    expected = raw.get("expected", "PASS")
    if expected not in EXPECTED:
        raise SpecError(f"cell {raw['id']!r}: expected must be PASS or FAIL, got {expected!r}")
    controls = tuple(raw.get("controls") or ())
    bad = [c for c in controls if c not in CONTROLS]
    if bad:
        raise SpecError(f"cell {raw['id']!r}: unknown control(s) {', '.join(bad)} (known: {', '.join(CONTROLS)})")
    if tier == "full" and not raw.get("skip_reason"):
        raise SpecError(f"cell {raw['id']!r}: a full-tier cell the lab cannot run must say why (skip_reason)")
    if tier == "store" and not raw.get("kind"):
        raise SpecError(f"cell {raw['id']!r}: a store-tier cell needs a kind (the script that runs it)")
    if raw.get("skip_reason") and tier == "store":
        raise SpecError(f"cell {raw['id']!r}: a store-tier cell runs - it cannot also be skipped")
    if expected == "FAIL" and controls:
        raise SpecError(f"cell {raw['id']!r}: a known-FAIL target is already red, so it cannot be a control cell")
    cell = Cell(id=raw["id"], axis=axis, title=raw["title"], tier=tier, kind=raw.get("kind", ""),
                expected=expected, controls=controls, sanity=bool(raw.get("sanity", False)),
                skip_reason=raw.get("skip_reason", ""), lme_map=raw.get("lme_map", ""),
                note=raw.get("note", ""), params=dict(raw.get("params") or {}),
                events=tuple(raw.get("events") or ()), probes=tuple(raw.get("probes") or ()))
    # every placeholder must be a slot the world mints, or a typo would ship a literal "{owner}"
    from .world import make_world
    slots = set(make_world().slots)
    for blob in (cell.params, cell.events, cell.probes):
        for s in _strings(blob):
            miss = placeholders(s) - slots
            if miss:
                raise SpecError(f"cell {cell.id!r}: unknown world slot(s) {', '.join(sorted(miss))}")
    return cell


def _strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)


def parse_spec(doc: "dict[str, Any]") -> "list[Cell]":
    """One spec document -> cells (matrices expanded, everything validated)."""
    if not isinstance(doc, dict) or doc.get("spec_version") != SPEC_VERSION:
        raise SpecError(f"spec_version must be {SPEC_VERSION}")
    axis = doc.get("axis", "")
    if axis not in AXES.values():
        raise SpecError(f"spec axis {axis!r} is not one of {', '.join(AXES.values())}")
    cells = doc.get("cells")
    if not isinstance(cells, list) or not cells:
        raise SpecError("a spec needs a non-empty 'cells' list")
    out: list[Cell] = []
    for raw in cells:
        for concrete in expand_matrix(raw):
            out.append(_to_cell(concrete, axis))
    return out


def load_cells(directory: "Path | None" = None) -> "list[Cell]":
    """Every cell of every ``*.json`` under the scenario directory, ids unique."""
    d = Path(directory) if directory else SCENARIO_DIR
    files = sorted(d.glob("*.json"))
    if not files:
        raise SpecError(f"no scenario specs under {d}")
    cells: list[Cell] = []
    for f in files:
        try:
            cells.extend(parse_spec(json.loads(f.read_text(encoding="utf-8"))))
        except json.JSONDecodeError as exc:
            raise SpecError(f"{f.name}: not valid JSON ({exc.msg} at line {exc.lineno})") from None
        except SpecError as exc:
            raise SpecError(f"{f.name}: {exc}") from None
    seen: set[str] = set()
    for c in cells:
        if c.id in seen:
            raise SpecError(f"duplicate cell id {c.id!r}")
        seen.add(c.id)
    return cells


def select(cells: "list[Cell]", only: "str | None" = None, axis: "str | None" = None) -> "list[Cell]":
    """``--only`` (ids or id prefixes ending in ``.``/``*``) and ``--axis`` (letter or name), intersected.
    An unknown id / axis or an empty result raises ``SpecError`` - a typo never silently runs nothing."""
    out = list(cells)
    if only is not None:
        toks = [t.strip() for t in only.split(",") if t.strip()]
        if not toks:
            raise SpecError("--only needs at least one cell id")
        ids = {c.id for c in cells}
        keep: set[str] = set()
        for t in toks:
            if t.endswith("*"):
                hit = {i for i in ids if i.startswith(t[:-1])}
            else:
                hit = {t} & ids
            if not hit:
                raise SpecError(f"--only {t!r} matches no cell (try --list)")
            keep |= hit
        out = [c for c in out if c.id in keep]
    if axis is not None:
        names = []
        for t in (x.strip().lower() for x in axis.split(",") if x.strip()):
            n = AXES.get(t, t)
            if n not in AXES.values():
                raise SpecError(f"unknown axis {t!r} (known: {', '.join(f'{k}={v}' for k, v in AXES.items())})")
            names.append(n)
        if not names:
            raise SpecError("--axis needs at least one axis")
        out = [c for c in out if c.axis in names]
    if not out:
        raise SpecError("the selection is empty")
    return out
