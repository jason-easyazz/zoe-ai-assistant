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
    {"do": "conflict_pass"}                                    the arm's nightly implicit-conflict pass (``conflict_pass``)
    {"do": "edge", "a": "..", "b": "..", "rel": "friend", "group": "personal",
     "authority": "user_stated", "origin": "conversation"}     a people-graph write (``edges``)
    {"do": "needles"}                                          teach the seeded recall corpus (``needles.corpus``)
    {"do": "filler", "turns": 100}                             N seeded household-chatter turns (``needles.chatter``)
    {"do": "hard_delete"}                                      the audited hard delete of the user (capability ``disk``)
    {"do": "exact_needles"}                                    (j) teach the 20 sentences the owner SAID, each on its day (``life.exact_turns``)
    {"do": "hop_facts", "which": "a"|"b"}                      (l) teach the first / the second fact of the 20 two-fact questions (``life.hop_corpus``)
    {"do": "life"}                                             (k) thirty days of a household's turns, each on its day (``life.life``)
    {"do": "life_pass", "propose": ["true", "fabricated", ...]} (k) the nightly model's SCRIPTED proposals (the truth, and each kind of mistake), to an arm whose model the lab scripts; an own-model arm gets none
    {"do": "protocol_facts"}                                   (m) teach the facts the protocol prompts ask about (``life.protocol_corpus``)
    {"do": "night_pass", "propose": [...]}                     (k) the night mind's pass, own-model arms only (the model the lab scripts is a SKIP); ``propose`` = the mistakes the fake brain makes
    {"do": "dense_life", "per_day": 40}                        (k) thirty days with ``per_day`` routine commands a day around the life's turns (two threads planted late in the day)
    {"do": "variant_life", "name": "drift|flat|restraint|resolution"}   (k) a small synthetic month for one night-mind cell

Probe forms (a dict in ``cell.probes``; every probe must pass):

    {"kind": "store",   "assertions": [...], "as": identity?}  scorers.score_store over the row export
    {"kind": "facts",   "gold": [[..]], "anti": [[..]]}        fact recall + anti-fact precision over stored rows
    {"kind": "entities","gold": [..]}                          entity precision / recall over stored rows
    {"kind": "disk",    "tokens": ["..."]}                     capability ``disk``: no byte of the arm's REAL on-disk
                                                               palace (SQLite pages, FTS5, write-ahead log, HNSW files) holds a token
    {"kind": "recall",  "query": "...", "k": 5, "needles": [], "anti_needles": [], "canaries": []}
    {"kind": "edges",   "assertions": [...]}                   the people graph (``scorers.score_edges``; ``edges``)
    {"kind": "hit_at_k", "k": 5, "min_rate": 0.9, "queries": "direct"|"paraphrase"}   the corpus's needles retrieved
    {"kind": "answer",  "query": "...", "needles": [], "canaries": []}   the scripted reader (capability ``reader``)
    {"kind": "exact", "k": 5, "min_rate": 0.9}                 (j) every exact needle's sentence is in ``recall_exact`` word for word (``exact_words``)
    {"kind": "exact_when", "k": 5, "min_rate": 0.9}            (j) ... and the arm says which day it was said (``exact_words``)
    {"kind": "hops", "k": 8, "min_rate": 0.7}                  (l) both facts of every two-fact question are in ``recall_linked`` (``multi_hop``)
    {"kind": "observations", "score": "true"|"current"|"attributed"}   (k) the arm's observation export vs the life's gold (``observations``)
    {"kind": "threads", "min_recall": 0.7} / {"kind": "useful"}        (k) thread recall / the "what's been going on" answers (``observations``,
                                                               the arm's OWN model: a scripted-model arm SKIPs)
    {"kind": "protocol", "metric": "fire_when_needed", "protocol": "zoe"}   (m) the protocol's trigger + the arm's packet + the scripted reader (``protocol``)
    {"kind": "compression"} / {"kind": "late_threads"} / {"kind": "citations"} / {"kind": "change_quiet", "variant": "drift|flat"} / {"kind": "restraint"} /
    {"kind": "resolution"} / {"kind": "weights"}              (k) K6-K12, the night mind's cells (own-model arms only)

``params.play_group``: cells that share a group and a seed share ONE play of their events (one expensive ingest, several read-only probes).

A probe or event the arm cannot do (a stub arm, a missing capability) makes the cell SKIP with the reason -
never PASS. ``LiveStoreViolation`` (a ``BaseException``) is never caught: it aborts the run.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from . import life as lifemod, needles as needlemod, scorers, scorers_cap as cap
from .arms.base import Arm, Turn
from .spec import Cell
from .world import World

#: row statuses that mean "the store still holds this" (an archived / superseded row is history)
RETAINED = ("approved", "pending", "disputed")

_TURN_KEYS = {"text", "speaker", "day_offset", "writer", "proposes", "op", "attr", "assistant_text",
              "memory_type"}
_CAPS = {"advance_clock": "clock", "ingest_as": "identities", "idle_pass": "idle_pass",
         "conflict_pass": "conflict_pass", "edge": "edges", "hard_delete": "disk", "life_pass": "idle_pass", "night_pass": "idle_pass"}
#: the capability axes' probes (j exact words, k reflection, l multi-hop, m protocol): the capability an arm must DECLARE for the probe
NIGHT_PROBES = ("compression", "late_threads", "citations", "change_quiet", "restraint", "resolution", "weights")
_PROBE_CAPS = {"exact": "exact_words", "exact_when": "exact_words", "hops": "multi_hop", "observations": "observations",
               "threads": "observations", "useful": "observations", "protocol": "protocol", **{k: "observations" for k in NIGHT_PROBES}}
_PROBE_KINDS = ("store", "facts", "entities", "recall", "answer", "edges", "hit_at_k", "disk") + tuple(_PROBE_CAPS)


@dataclass
class Outcome:
    verdict: str                       # PASS | FAIL | SKIP | ERROR
    stage: str = ""                    # write | read | answer (FAIL only)
    evidence: "dict[str, Any]" = field(default_factory=dict)
    reason: str = ""                   # SKIP / ERROR: why
    duration_s: float = 0.0
    brain_turns: int = 0


def demo_user(world: World, cell: Cell) -> str:
    """A deterministic ``demo_bar_<8 hex>`` id for this cell on this seed (cells of one ``play_group`` share it: they share one store)."""
    return "demo_bar_" + hashlib.sha1(f"{world.seed}:{cell.params.get('play_group') or cell.id}".encode()).hexdigest()[:8]


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
        if p.get("kind") == "edges":
            need.add("edges")
        if p.get("kind") == "disk":
            need.add("disk")
        if p.get("as"):
            need.add("identities")
        if p.get("kind") in _PROBE_CAPS:
            need.add(_PROBE_CAPS[p["kind"]])
    return need


def _retained_texts(rows: "list[dict]") -> "list[str]":
    return [r.get("text", "") for r in rows if r.get("status") in RETAINED]


def _play(cell: Cell, arm: Arm, world: "World | None" = None) -> "list[dict[str, Any]]":
    """Play the events; returns the counters of every idle pass (a pass that did not RUN proves nothing).
    ``world`` supplies the seed of the generated events (``needles`` / ``filler``)."""
    passes: list[dict[str, Any]] = []
    seed = (world.seed if world is not None else "zmb-v1")
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
        elif do == "conflict_pass":
            arm.run_conflict_pass()
        elif do == "edge":
            arm.write_edge(ev["a"], ev["b"], ev["rel"], ev.get("group", "personal"),
                           ev.get("authority", "user_stated"), ev.get("origin", "conversation"))
        elif do == "needles":
            arm.ingest([Turn(n.fact, "owner_taught") for n in needlemod.corpus(seed)])
        elif do == "filler":
            arm.ingest([Turn(f["text"], f["speaker"]) for f in needlemod.chatter(seed, int(ev["turns"]),
                                                                                   str(ev.get("salt", "")))])
        elif do == "hard_delete":
            arm.hard_delete()
        elif do == "exact_needles":
            arm.ingest([Turn(t["text"], "owner_typed", day_offset=t["day_offset"]) for t in lifemod.exact_turns(seed)])
        elif do == "hop_facts":
            which = str(ev.get("which", "a"))
            if which not in ("a", "b"):
                raise ValueError(f"hop_facts which must be 'a' or 'b', got {which!r}")
            items = lifemod.hop_corpus(seed)
            turns = [(x.day_a, x.fact_a) if which == "a" else (x.day_b, x.fact_b) for x in items]
            arm.ingest([Turn(text, "owner_taught", day_offset=d) for d, text in sorted(turns, key=lambda t: -t[0])])
        elif do == "life":
            lf = lifemod.life(seed)
            arm.ingest([Turn(t["text"], "owner_taught" if t["speaker"] == "taught" else "owner_typed", day_offset=30 - t["day"]) for t in lf.turns])
        elif do == "life_pass":
            passes.append(_life_pass(arm, seed, list(ev.get("propose") or ())))
        elif do == "night_pass":
            if getattr(arm, "nightly_model", "scripted") != "own":
                raise NotImplementedError(f"arm {arm.name}: the nightly model is scripted in this lab, so there is no night pass to run (use Z0n)")
            passes.append(arm.reflect_pass(propose=list(ev.get("propose") or ()), seed=seed) if getattr(arm, "takes_lies", False) else arm.reflect_pass())
        elif do == "dense_life":
            items, _late = lifemod.dense_layout(seed, int(ev.get("per_day", 40)))
            for it in items:
                if it.kind == "life":
                    arm.ingest([Turn(it.text, "owner_typed", day_offset=30 - it.day)])
                else:
                    arm.add_night_turns([it.text], 30 - it.day)
        elif do == "variant_life":
            for t in lifemod.variant_life(str(ev["name"]), seed).turns:
                arm.ingest([Turn(t["text"], "owner_typed", day_offset=30 - t["day"])])
        elif do == "protocol_facts":
            sentences, _prompts = lifemod.protocol_corpus(seed)
            arm.ingest([Turn(s, "owner_taught") for s in sentences])
        else:
            raise ValueError(f"unknown event action {do!r}")
    return passes


_LIFE_KINDS = {"true": "proposals_true", "fabricated": "proposals_fabricated", "stale": "proposals_stale", "hedged": "proposals_hedged",
               "said": "proposals_presented_as_said"}


def _life_pass(arm: Arm, seed: str, kinds: "list[str]") -> "dict[str, Any]":
    """The nightly pass over the life. An arm whose nightly model the LAB scripts (``nightly_model == "scripted"``: Z0's digest) is handed the
    truths and each kind of mistake a model makes, as ``proposes``. An arm that runs its OWN model (``"own"``: Hindsight's observation layer) is
    handed NOTHING: the scripted lies (fabricated links, stale facts, mis-attributions) would be retained as facts and consolidated into its
    observations, so K1-K3 would measure the harness's falsehoods instead of the arm's own derivation. It reflects over the life it already ingested."""
    bad = [k for k in kinds if k not in _LIFE_KINDS]
    if bad:
        raise ValueError(f"unknown life_pass kind(s) {', '.join(bad)} (known: {', '.join(_LIFE_KINDS)})")
    if getattr(arm, "nightly_model", "scripted") == "own":
        # an arm whose model is the LAB's fake brain (Z0n) is handed the mistakes a model makes, to show the checks around it hold; a real model gets nothing
        return arm.reflect_pass(propose=kinds, seed=seed) if getattr(arm, "takes_lies", False) else arm.reflect_pass()
    lf = lifemod.life(seed)
    proposes: "list[str]" = []
    for k in kinds:
        proposes += getattr(lf, _LIFE_KINDS[k])
    return arm.run_idle_pass("\n".join(t["text"] for t in lf.turns if t["speaker"] == "typed"), proposes, judge=False)


def _norm_day(d: Any) -> "float | None":
    return None if d is None or d == "" else float(d)


def _exact(p: "dict[str, Any]", arm: Arm, seed: str) -> scorers.Score:
    """(j) For every exact needle: ask for it the way the owner would; PASS the item when one hit holds the sentence word for word."""
    k = int(p.get("k", 5))
    corpus = lifemod.exact_corpus(seed)
    hits, days = [], []
    for x in corpus:
        got = arm.recall_exact(x.question, k)
        found = [h for h in got if cap.contains_span(h.get("text", ""), x.sentence)]
        hits.append(bool(found))
        about = [h for h in got if scorers.contains_phrase(h.get("text", ""), x.subject)]     # "when": the first row that is ABOUT it, its words or not
        days.append(_norm_day(about[0].get("day_offset")) if about else None)
    if p["kind"] == "exact_when":
        return cap.score_when(days, [x.day_offset for x in corpus], min_rate=float(p.get("min_rate", 0.9)))
    return cap.score_exact(hits, [x.style for x in corpus], k=k, min_rate=float(p.get("min_rate", 0.9)))


def _hops(p: "dict[str, Any]", arm: Arm, seed: str) -> scorers.Score:
    """(l) For every two-fact question: both facts must be in the arm's associative packet (a row holding the subject and the answer token each)."""
    k = int(p.get("k", 8))
    both, kinds, a_only, b_only = [], [], 0, 0
    for it in lifemod.hop_corpus(seed):
        texts = [r.get("text", "") for r in arm.recall_linked(it.question, k)]
        a, b = cap.fact_in_rows(texts, it.a_need), cap.fact_in_rows(texts, it.b_need)
        both.append(a and b)
        kinds.append(it.kind)
        a_only += int(a and not b)
        b_only += int(b and not a)
    return cap.score_hops(both, kinds, a_only, b_only, k=k, min_rate=float(p.get("min_rate", 0.7)))


def _reflection(p: "dict[str, Any]", arm: Arm, seed: str) -> scorers.Score:
    """(k) The observation export against the life's gold. ``threads`` / ``useful`` need the arm's OWN model: where the lab scripted it the cell
    SKIPs (what a scripted model says is the script's, not the store's)."""
    gold = lifemod.gold_for_scoring(lifemod.life(seed))
    kind = p["kind"]
    if kind == "observations":
        return cap.score_observations(arm.observations().get("items") or [], gold, kind=str(p.get("score", "true")),
                                      min_precision=float(p.get("min_precision", 0.95)), min_decidable=int(p.get("min_decidable", 3)),
                                      min_observations=int(p.get("min_observations", 3)))
    first = arm.observations()
    if first.get("model") == "scripted":
        raise NotImplementedError(f"arm {arm.name}: the nightly model is scripted in this lab, so what an observation SAYS is the script's: "
                                  "thread recall and usefulness are measured only on an arm that runs its own model")
    if kind == "threads":
        return cap.score_threads(first.get("items") or [], gold, min_recall=float(p.get("min_recall", 0.7)))
    if kind in NIGHT_PROBES:
        return _night(p, arm, seed, first)
    lf = lifemod.life(seed)
    answers = [arm.observations(q).get("items") or [] for q, _ids in lf.questions]
    return cap.score_useful(answers, [ids for _q, ids in lf.questions], gold, min_rate=float(p.get("min_rate", 0.7)))


def _night(p: "dict[str, Any]", arm: Arm, seed: str, first: "dict[str, Any]") -> scorers.Score:
    """(k) K6-K12, the night mind's cells: what the pass COMPRESSED, found in a dense day, POINTED at, noticed, held back, resolved and labelled."""
    kind = p["kind"]
    lf = lifemod.life(seed)
    gold = lifemod.gold_for_scoring(lf)
    items = first.get("items") or []
    if kind == "compression":
        return cap.score_compression(items, gold, n_turns=sum(1 for t in lf.turns if t["speaker"] == "typed"), max_per_thread=int(p.get("max_per_thread", 3)),
                                     max_share=float(p.get("max_share", 0.6)))
    if kind == "late_threads":
        _items, late = lifemod.dense_layout(seed, int(p.get("per_day", 40)))
        return cap.score_late_threads(items, gold, late, min_recall=float(p.get("min_recall", 0.7)))
    if kind == "citations":
        return cap.score_citations(items, arm.turn_text, min_observations=int(p.get("min_observations", 3)))
    if kind == "change_quiet":
        flat = str(p.get("variant")) == "flat"
        return cap.score_change_quiet(arm.threads(), arm.changes(), lifemod.variant_life(str(p["variant"]), seed).gold, flat=flat,
                                      max_false=float(p.get("max_false", 0.05)))
    if kind == "restraint":
        g = lifemod.variant_life("restraint", seed).gold
        asked = [(key, any(key in str(i.get("text", "")).lower() for i in (arm.observations(q).get("items") or []))) for q, key in g["asks"]]
        return cap.score_restraint(arm.morning_plan(int(p.get("days", 14))), arm.threads(), g, asked)
    if kind == "resolution":
        return cap.score_resolution(arm.threads(), lifemod.variant_life("resolution", seed).gold)
    gold_labels = lifemod.labelled_moments()
    return cap.score_weights(arm.moment_labels([g["text"] for g in gold_labels]), gold_labels, min_spearman=float(p.get("min_spearman", 0.5)),
                             min_accuracy=float(p.get("min_accuracy", 0.85)))


def uses_night(cell: Cell) -> bool:
    """Does this cell need an arm with its own nightly model (Z0n) to be proven? Any ``night_*`` control, or a night-mind probe."""
    return any(c.startswith("night") for c in cell.controls) or any(p.get("kind") in NIGHT_PROBES for p in cell.probes)


def _protocol(p: "dict[str, Any]", arm: Arm, seed: str) -> scorers.Score:
    """(m) The lab half: each protocol's trigger policy decides whether recall FIRES; the arm builds the packet and the scripted reader answers from
    it (or says it does not know). The verdict is on ``p["protocol"]``; the numbers of every protocol ride in the evidence (counts only)."""
    _facts, prompts = lifemod.protocol_corpus(seed)
    want = str(p.get("protocol", "zoe"))
    if want not in lifemod.POLICIES:
        raise ValueError(f"unknown protocol {want!r} (known: {', '.join(lifemod.POLICIES)})")
    per: "dict[str, list[dict[str, Any]]]" = {}
    for name, (policy, _src) in lifemod.POLICIES.items():
        recs = []
        for pr in prompts:
            fired = bool(policy(pr.text))
            recs.append({"kind": pr.kind, "fired": fired, "gold": pr.gold,
                         "answer": arm.protocol_answer(pr.text, lifemod.prompt_anchor(pr), fired, int(p.get("k", 5)))})
        per[name] = recs
    s = cap.score_protocol(per[want], str(p["metric"]))
    s.evidence["compared"] = {name: cap.protocol_metrics(recs)[str(p["metric"])] for name, recs in per.items()}
    return s


def _hit_at_k(p: "dict[str, Any]", arm: Arm, seed: str) -> scorers.Score:
    """For every needle of the seeded corpus: ask it (``direct`` or ``paraphrase``) and look in the top-k rows for
    ONE row that holds both the subject's name and the answer token. A pure string match, no judge."""
    which = str(p.get("queries", "direct"))
    if which not in ("direct", "paraphrase"):
        raise ValueError(f"hit_at_k queries must be 'direct' or 'paraphrase', got {which!r}")
    k = int(p.get("k", 5))
    corpus = needlemod.corpus(seed)
    hits = 0
    for n in corpus:
        rows = arm.recall(n.direct if which == "direct" else n.paraphrase, k)
        if any(scorers.contains_phrase(r.get("text", ""), n.subject)
               and scorers.contains_phrase(r.get("text", ""), n.answer) for r in rows):
            hits += 1
    return scorers.score_hits(hits, len(corpus), k=k, min_rate=float(p.get("min_rate", 0.9)), label=which)


def _probe(p: "dict[str, Any]", arm: Arm, seed: str = "zmb-v1") -> scorers.Score:
    kind = p.get("kind")
    if kind not in _PROBE_KINDS:
        raise ValueError(f"unknown probe kind {kind!r} (known: {', '.join(_PROBE_KINDS)})")
    if kind == "store":
        rows = (arm.stats_as(p["as"]) if p.get("as") else arm.stats())["rows"]
        return scorers.score_store(rows, p["assertions"], stage=p.get("stage", "write"))
    if kind == "edges":
        return scorers.score_edges(arm.edges(), p["assertions"], stage=p.get("stage", "write"))
    if kind == "hit_at_k":
        return _hit_at_k(p, arm, seed)
    if kind in ("exact", "exact_when"):
        return _exact(p, arm, seed)
    if kind == "hops":
        return _hops(p, arm, seed)
    if kind in ("observations", "threads", "useful") or kind in NIGHT_PROBES:
        return _reflection(p, arm, seed)
    if kind == "protocol":
        return _protocol(p, arm, seed)
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
        group = cell.params.get("play_group")
        key = (str(group), world.seed, hashlib.sha1(repr(cell.events).encode()).hexdigest()) if group else None
        if key is not None and getattr(arm, "_zmb_played", None) == key:
            passes = []                       # the same group, seed and events were just played into THIS store: read it again (probes never write)
        else:
            arm._zmb_played = None
            arm.reset(demo_user(world, cell), **({"disk": True} if needs_disk else {}))
            passes = _play(cell, arm, world)
        for rep in passes:
            if rep.get("skipped_reason") or rep.get("error"):
                raise RuntimeError("the idle pass did not run (" + str(rep.get("skipped_reason")
                                                                     or rep.get("error"))[:60] + ")")
        arm._zmb_played = key
        scores = [_probe(p, arm, world.seed) for p in cell.probes]
    except NotImplementedError as exc:  # a stub arm / a call the arm does not have: a SKIP, never a PASS
        return done(Outcome("SKIP", reason=str(exc)[:300]))
    except (ValueError, KeyError, TypeError, RuntimeError) as exc:  # a broken cell or arm: loud
        return done(Outcome("ERROR", reason=f"{type(exc).__name__}: {str(exc)[:200]}"))
    merged = scorers.merge(*scores)
    return done(Outcome("PASS" if merged.ok else "FAIL", stage=merged.stage,
                        evidence={"probes": [s.evidence for s in scores],
                                  **({"idle_pass": [{k: v for k, v in rep.items() if isinstance(v, (int, bool))}
                                                    for rep in passes]} if passes else {})}))
