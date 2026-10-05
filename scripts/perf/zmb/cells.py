"""The cell script: events in, probes out. Arm-agnostic - it never names a memory system.

``run_cell`` plays a cell's EVENTS against an ``Arm`` (``reset`` -> ``ingest`` ...) and scores its PROBES
with the pure scorers. Every store-tier cell is the same interpreter over different data, which is what lets
one spec drive Z0, Z0-off, Hindsight and Graphiti.

Event forms (a dict in ``cell.events``):

    {"text": "...", "speaker": "owner_taught", ...}            a Turn (see arms.base.Turn)
    {"do": "forget", "entity": "..."}                          "forget everything about ..."
    {"do": "advance_clock", "seconds": 360}                    lab clock (capability ``clock``)
    {"do": "ingest_as", "identity": "guest", "turns": [...]}   turns as another household identity
    {"do": "idle_pass", "transcript": "...", "proposes": [..]} the arm's own nightly pass (``idle_pass``)
    {"do": "hard_delete"}                                      the audited hard delete of the user (capability ``disk``)

Probe forms (a dict in ``cell.probes``; every probe must pass):

    {"kind": "store",   "assertions": [...], "as": identity?}  scorers.score_store over the row export
    {"kind": "facts",   "gold": [[..]], "anti": [[..]]}        fact recall + anti-fact precision over stored rows
    {"kind": "entities","gold": [..]}                          entity precision / recall over stored rows
    {"kind": "disk",    "tokens": ["..."]}                     capability ``disk``: no byte of the arm's REAL on-disk
                                                               palace (SQLite pages, FTS5, write-ahead log, HNSW files) holds a token
    {"kind": "recall",  "query": "...", "k": 5, "needles": [], "anti_needles": [], "canaries": []}
    {"kind": "answer",  "query": "...", "needles": [], "canaries": []}   the scripted reader (capability ``reader``)

A probe or event the arm cannot do (a stub arm, a missing capability) makes the cell SKIP with the reason -
never PASS. ``LiveStoreViolation`` (a ``BaseException``) is never caught: it aborts the run.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from . import scorers
from .arms.base import Arm, Turn
from .spec import Cell
from .world import World

#: row statuses that mean "the store still holds this" (an archived / superseded row is history)
RETAINED = ("approved", "pending", "disputed")

_TURN_KEYS = {"text", "speaker", "day_offset", "writer", "proposes", "op", "attr", "assistant_text",
              "memory_type"}
_CAPS = {"advance_clock": "clock", "ingest_as": "identities", "idle_pass": "idle_pass", "hard_delete": "disk"}
_PROBE_KINDS = ("store", "facts", "entities", "recall", "answer", "disk")


@dataclass
class Outcome:
    verdict: str                       # PASS | FAIL | SKIP | ERROR
    stage: str = ""                    # write | read | answer (FAIL only)
    evidence: "dict[str, Any]" = field(default_factory=dict)
    reason: str = ""                   # SKIP / ERROR: why
    duration_s: float = 0.0
    brain_turns: int = 0


def demo_user(world: World, cell: Cell) -> str:
    """A deterministic ``demo_bar_<8 hex>`` id for this cell on this seed."""
    return "demo_bar_" + hashlib.sha1(f"{world.seed}:{cell.id}".encode()).hexdigest()[:8]


def make_turn(ev: "dict[str, Any]") -> Turn:
    extra = set(ev) - _TURN_KEYS
    if extra:
        raise ValueError(f"unknown turn key(s) {', '.join(sorted(extra))}")
    ev = dict(ev)
    ev["proposes"] = tuple(ev.get("proposes") or ())
    return Turn(**ev)


def required_capabilities(cell: Cell) -> "set[str]":
    need: set[str] = set()
    for ev in cell.events:
        if ev.get("do") in _CAPS:
            need.add(_CAPS[ev["do"]])
    for p in cell.probes:
        if p.get("kind") == "answer":
            need.add("reader")
        if p.get("kind") == "disk":
            need.add("disk")
        if p.get("as"):
            need.add("identities")
    return need


def _retained_texts(rows: "list[dict]") -> "list[str]":
    return [r.get("text", "") for r in rows if r.get("status") in RETAINED]


def _play(cell: Cell, arm: Arm) -> "list[dict[str, Any]]":
    """Play the events; returns the counters of every idle pass (a pass that did not RUN proves nothing)."""
    passes: list[dict[str, Any]] = []
    for ev in cell.events:
        do = ev.get("do")
        if do is None:
            arm.ingest([make_turn(ev)])
        elif do == "forget":
            arm.forget(ev["entity"])
        elif do == "advance_clock":
            arm.advance_clock(float(ev["seconds"]))
        elif do == "ingest_as":
            arm.ingest_as(ev["identity"], [make_turn(t) for t in ev["turns"]])
        elif do == "idle_pass":
            passes.append(arm.run_idle_pass(ev["transcript"], list(ev.get("proposes") or ())))
        elif do == "hard_delete":
            arm.hard_delete()
        else:
            raise ValueError(f"unknown event action {do!r}")
    return passes


def _probe(p: "dict[str, Any]", arm: Arm) -> scorers.Score:
    kind = p.get("kind")
    if kind not in _PROBE_KINDS:
        raise ValueError(f"unknown probe kind {kind!r} (known: {', '.join(_PROBE_KINDS)})")
    if kind == "store":
        rows = (arm.stats_as(p["as"]) if p.get("as") else arm.stats())["rows"]
        return scorers.score_store(rows, p["assertions"], stage=p.get("stage", "write"))
    if kind in ("facts", "entities"):
        texts = _retained_texts(arm.stats()["rows"])
        if kind == "facts":
            return scorers.score_facts(texts, p.get("gold") or [], p.get("anti") or [],
                                       min_recall=float(p.get("min_recall", 1.0)))
        return scorers.score_entities(texts, p.get("gold") or [],
                                      min_precision=float(p.get("min_precision", 1.0)),
                                      min_recall=float(p.get("min_recall", 0.75)),
                                      ignore=p.get("ignore") or ())
    if kind == "disk":   # capability ``disk``: the bytes Chroma leaves behind (counts only, never text)
        return scorers.score_disk(arm.disk_residue(list(p["tokens"])))
    if kind == "recall":
        rows = arm.recall(p["query"], int(p.get("k", 5)))
        text = "\n".join(r.get("text", "") for r in rows)
        return scorers.score_needles(text, p.get("needles") or (), p.get("anti_needles") or (),
                                     p.get("canaries") or (), stage="read")
    reply = arm.answer(p["query"])  # kind == "answer": the scripted reader (capability ``reader``)
    return scorers.score_needles(reply, p.get("needles") or (), p.get("anti_needles") or (),
                                 p.get("canaries") or (), stage="answer")


def run_cell(cell: Cell, world: World, arm: Arm) -> Outcome:
    """Run ONE (already rendered) store-tier cell. Never raises for an arm that cannot do the work."""
    t0 = time.monotonic()

    def done(o: Outcome) -> Outcome:
        o.duration_s = round(time.monotonic() - t0, 4)
        return o

    if cell.tier != "store":
        return done(Outcome("SKIP", reason=cell.skip_reason or "brain-tier cell: not runnable in the lab"))
    missing = required_capabilities(cell) - set(arm.capabilities)
    if missing:
        return done(Outcome("SKIP", reason=f"arm {arm.name} lacks capability: {', '.join(sorted(missing))}"))
    if not cell.probes:
        return done(Outcome("ERROR", reason="a store-tier cell with no probes proves nothing"))
    try:
        needs_disk = "disk" in required_capabilities(cell)
        arm.reset(demo_user(world, cell), **({"disk": True} if needs_disk else {}))
        passes = _play(cell, arm)
        for rep in passes:
            if rep.get("skipped_reason") or rep.get("error"):
                raise RuntimeError("the idle pass did not run (" + str(rep.get("skipped_reason")
                                                                     or rep.get("error"))[:60] + ")")
        scores = [_probe(p, arm) for p in cell.probes]
    except NotImplementedError as exc:  # a stub arm / a call the arm does not have: a SKIP, never a PASS
        return done(Outcome("SKIP", reason=str(exc)[:300]))
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:  # a broken cell or arm: loud
        return done(Outcome("ERROR", reason=f"{type(exc).__name__}: {str(exc)[:200]}"))
    merged = scorers.merge(*scores)
    return done(Outcome("PASS" if merged.ok else "FAIL", stage=merged.stage,
                        evidence={"probes": [s.evidence for s in scores],
                                  **({"idle_pass": [{k: v for k, v in rep.items() if isinstance(v, (int, bool))}
                                                    for rep in passes]} if passes else {})}))
