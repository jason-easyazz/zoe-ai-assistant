"""ZMB: the temporal / recall / poisoning / provenance / graph-edge axes - the pieces that need no service module.

The scorers (``fraction_have``, ``epoch_year``, ``origins``, ``score_edges``, ``score_hits``), the recall corpus
generator, and the cell interpreter's new events (``needles``, ``filler``, ``conflict_pass``, ``edge``) and probes
(``hit_at_k``, ``edges``) run here against a SCRIPTED arm, so they sit in the slim ``-m ci_safe`` lane. The real
controls (the lab over the real ``MemoryService``) are proven in ``services/zoe-data/tests/test_zmb_lab.py``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import cells as cellmod, needles, scorers, spec, world  # noqa: E402
from zmb.arms.base import Arm, IngestReport, OPTIONAL_ROW_KEYS, Turn  # noqa: E402


# ── scorers ──────────────────────────────────────────────────────────────────

def _row(text, status="approved", **kw):
    return {"id": text[:6], "text": text, "status": status, "origin": "chat_regex", **kw}


def test_fraction_have_is_the_provenance_rate_and_needs_every_field():
    full = {"source_excerpt": "x", "user_turn_id": "t", "authority_class": "user_stated"}
    rows = [_row("a", **full), _row("b", **full), _row("c", **{**full, "source_excerpt": ""})]
    a = {"op": "fraction_have", "contains": [], "statuses": ["approved"], "fields": list(full), "min": 0.6}
    assert scorers.score_store(rows, [a]).ok
    got = scorers.score_store(rows, [{**a, "min": 0.95}])
    assert not got.ok and got.stage == "write"
    assert got.evidence["fractions"] == [{"assertion": 0, "rows": 3, "complete": 2, "rate": 0.6667}]   # counts only
    assert not scorers.score_store([], [{**a, "min": 0.0}]).ok                  # no rows is never a rate of 100%
    only = [_row("a", origin="voice_fact", **full), _row("b", **{**full, "user_turn_id": ""})]
    assert scorers.score_store(only, [{**a, "origins": ["voice_fact"], "min": 1.0}]).ok   # origins filter the rows


def test_epoch_year_reads_the_utc_year_of_a_stored_time():
    jan_2018 = 1514764800.0
    rows = [_row("lives in A", valid_from=jan_2018)]
    ok = {"op": "epoch_year", "contains": ["lives"], "statuses": ["approved"], "field": "valid_from", "equals": "2018"}
    assert scorers.score_store(rows, [ok]).ok                                    # the spec's placeholder renders to text
    assert not scorers.score_store(rows, [{**ok, "equals": 2026}]).ok
    assert not scorers.score_store([_row("lives in A", valid_from="")], [ok]).ok    # a store with no valid time fails
    assert not scorers.score_store([_row("lives in A")], [ok]).ok
    # an unrelated op never evaluates the year (an old spec with a non-numeric 'equals' must not crash)
    assert scorers.score_store(rows, [{"op": "all_have", "contains": [], "field": "status", "equals": "approved"}]).ok


def test_score_edges_reads_the_people_graph_export():
    edges = [{"a": "A", "b": "B", "rel_type": "friend", "current": False},
             {"a": "B", "b": "A", "rel_type": "spouse", "current": True},
             {"a": "A", "b": "C", "rel_type": "friend", "current": True}]
    pair = {"a": "A", "b": "B"}
    ok = [{"op": "current_rel", **pair, "equals": "spouse"}, {"op": "closed_rel", **pair, "equals": "friend"},
          {"op": "history_len", **pair, "n": 2}, {"op": "no_current_rel", **pair, "equals": "friend"}]
    assert scorers.score_edges(edges, ok).ok                                     # the pair is unordered
    bad = scorers.score_edges(edges, [{"op": "current_rel", **pair, "equals": "friend"},
                                      {"op": "history_len", **pair, "n": 1}], stage="write")
    assert not bad.ok and bad.evidence["failed"] == [0, 1] and bad.stage == "write"
    assert not scorers.score_edges([], [{"op": "current_rel", **pair, "equals": "friend"}]).ok   # no edge, no pass
    with pytest.raises(ValueError, match="unknown edge assertion op"):
        scorers.score_edges(edges, [{"op": "currnt_rel", **pair}])


def test_score_hits_is_a_rate_with_a_wilson_interval_never_text():
    s = scorers.score_hits(18, 20, k=5, min_rate=0.9, label="direct")
    assert s.ok and s.evidence["hit_at_k"] == {"k": 5, "n": 20, "hits": 18, "rate": 0.9,
                                               "wilson95": s.evidence["hit_at_k"]["wilson95"], "min_rate": 0.9,
                                               "queries": "direct"}
    lo, hi = s.evidence["hit_at_k"]["wilson95"]
    assert 0.69 < lo < 0.71 and 0.97 < hi < 0.99
    bad = scorers.score_hits(17, 20, k=5, min_rate=0.9)
    assert not bad.ok and bad.stage == "read"
    assert not scorers.score_hits(0, 0, k=5, min_rate=0.0).ok                    # nothing asked is not a pass


# ── the recall corpus ────────────────────────────────────────────────────────

def test_needles_are_distinct_seeded_and_the_paraphrase_shares_only_the_name():
    a = needles.corpus("zmb-v1")
    assert len(a) == 20 and a == needles.corpus("zmb-v1") and a != needles.corpus("fresh-1")
    for n in a:
        assert n.subject in n.fact and n.answer in n.fact and n.subject in n.direct and n.subject in n.paraphrase
        assert n.answer not in n.direct and n.answer not in n.paraphrase           # the question does not give it away
        words = lambda t: set(re.findall(r"[a-z0-9]+", t.lower())) - {"s"}         # noqa: E731
        shared = (words(n.fact) & words(n.paraphrase)) - {"the", "a", "is", "in", "at", "to", "of"}
        assert shared <= {n.subject.lower()}, (n.fact, n.paraphrase, shared)         # only the subject's name
    with pytest.raises(ValueError):
        needles.corpus("x", 21)


def test_filler_is_seeded_varied_and_never_about_a_needle():
    f = needles.chatter("zmb-v1", 300)
    assert len(f) == 300 and f == needles.chatter("zmb-v1", 300)
    assert len({x["text"] for x in f}) == 300
    assert needles.chatter("zmb-v1", 30) != needles.chatter("zmb-v1", 30, salt="b")
    used = needles.corpus("zmb-v1")
    for x in f:
        assert not any(n.subject in x["text"] or n.answer in x["text"] for n in used), x
        assert x["speaker"] in ("owner_typed", "owner_taught")
    assert {x["kind"] for x in f} == {"chatter", "preference", "near_miss"}


# ── the cell interpreter, against a scripted arm ─────────────────────────────

class _Scripted(Arm):
    """A store that keeps every taught / typed turn verbatim and ranks by shared words; records what it was asked."""
    name = "scripted"
    capabilities = frozenset({"conflict_pass", "edges"})

    def __init__(self, blind: bool = False):
        self.rows: "list[dict]" = []
        self.asked: "list[str]" = []
        self.passes = 0
        self.graph: "list[dict]" = []
        self.blind = blind

    def reset(self, user_id): self.rows, self.asked, self.passes, self.graph = [], [], 0, []

    def ingest(self, turns):
        for t in turns:
            self.rows.append({"id": f"r{len(self.rows)}", "text": t.text, "status": "approved", "origin": t.speaker})
        return IngestReport(turns=len(turns), written=len(turns))

    def recall(self, query, k=10):
        self.asked.append(query)
        if self.blind:
            return list(reversed(self.rows))[:k]                                   # ignores the query
        q = set(re.findall(r"[a-z0-9]+", query.lower()))
        return sorted(self.rows, key=lambda r: -len(q & set(re.findall(r"[a-z0-9]+", r["text"].lower()))))[:k]

    def forget(self, entity): return ""
    def as_of(self, query, ts): raise NotImplementedError("no as-of")
    def stats(self): return {"rows": self.rows, "counts": {}, "writes_refused": 0}
    def run_conflict_pass(self): self.passes += 1; return {"pairs": 0, "superseded": 0}

    def write_edge(self, a, b, rel, group, authority, origin):
        self.graph.append({"a": a, "b": b, "rel_type": rel, "current": True, "authority": authority, "origin": origin})

    def edges(self): return list(self.graph)


def _cell(events, probes, cid="D9.scripted"):
    return spec.Cell(id=cid, axis="recall", title="t", tier="store", kind="script", events=tuple(events),
                     probes=tuple(probes))


def test_hit_at_k_finds_the_needles_among_the_filler_and_a_blind_store_misses_them():
    w = world.make_world()
    c = _cell([{"do": "needles"}, {"do": "filler", "turns": 60}],
              [{"kind": "hit_at_k", "k": 5, "min_rate": 0.9, "queries": "direct"}])
    arm = _Scripted()
    out = cellmod.run_cell(c.rendered(w), w, arm)
    h = out.evidence["probes"][0]["hit_at_k"]
    assert out.verdict == "PASS" and (h["n"], h["hits"]) == (20, 20) and len(arm.rows) == 80
    assert len(arm.asked) == 20                                                   # one question per needle
    blind = cellmod.run_cell(c.rendered(w), w, _Scripted(blind=True))
    assert blind.verdict == "FAIL" and blind.stage == "read" and blind.evidence["probes"][0]["hit_at_k"]["hits"] == 0
    para = _cell([{"do": "needles"}], [{"kind": "hit_at_k", "k": 5, "min_rate": 0.5, "queries": "paraphrase"}])
    arm2 = _Scripted()
    assert cellmod.run_cell(para.rendered(w), w, arm2).verdict == "PASS"
    assert needles.corpus(w.seed)[0].paraphrase in arm2.asked                      # the paraphrase set was asked
    with pytest.raises(ValueError, match="direct"):
        cellmod._hit_at_k({"queries": "bogus"}, arm2, w.seed)


def test_the_corpus_follows_the_world_seed_so_a_held_out_run_is_a_new_corpus():
    c = _cell([{"do": "needles"}], [{"kind": "hit_at_k", "k": 5, "min_rate": 1.0}])
    a1, a2 = _Scripted(), _Scripted()
    w1, w2 = world.make_world(), world.make_world("fresh-held")
    assert cellmod.run_cell(c.rendered(w1), w1, a1).verdict == "PASS"
    assert cellmod.run_cell(c.rendered(w2), w2, a2).verdict == "PASS"
    assert [r["text"] for r in a1.rows] != [r["text"] for r in a2.rows]


def test_conflict_pass_and_edge_events_and_the_edges_probe_drive_the_arm():
    w = world.make_world()
    c = spec.Cell(id="A9.scripted", axis="authority", title="t", tier="store", kind="script",
                  events=({"do": "edge", "a": "{friend}", "b": "{sibling}", "rel": "friend"},
                          {"do": "conflict_pass"}),
                  probes=({"kind": "edges", "assertions": [
                      {"op": "current_rel", "a": "{friend}", "b": "{sibling}", "equals": "friend"}]},))
    arm = _Scripted()
    out = cellmod.run_cell(c.rendered(w), w, arm)
    assert out.verdict == "PASS" and arm.passes == 1
    assert arm.graph[0]["authority"] == "user_stated" and arm.graph[0]["origin"] == "conversation"   # the defaults


def test_a_cell_that_needs_a_missing_capability_is_a_skip_with_the_capability_named():
    w = world.make_world()
    arm = _Scripted()
    arm.capabilities = frozenset()
    for events, probes, cap in (([{"do": "conflict_pass"}], [{"kind": "store", "assertions": []}], "conflict_pass"),
                                ([{"do": "edge", "a": "x", "b": "y", "rel": "friend"}], [{"kind": "store", "assertions": []}], "edges"),
                                ([{"text": "t", "speaker": "owner_typed"}], [{"kind": "edges", "assertions": []}], "edges")):
        out = cellmod.run_cell(_cell(events, probes).rendered(w), w, arm)
        assert out.verdict == "SKIP" and cap in out.reason


def test_an_unknown_probe_kind_is_a_loud_error_and_the_new_ones_are_declared():
    assert {"edges", "hit_at_k"} <= set(cellmod._PROBE_KINDS)
    w = world.make_world()
    out = cellmod.run_cell(_cell([{"text": "t", "speaker": "owner_typed"}], [{"kind": "nope"}]).rendered(w), w, _Scripted())
    assert out.verdict == "ERROR" and "unknown probe kind" in out.reason


def test_the_world_gained_slots_without_moving_an_existing_one():
    """The new draws are APPENDED after every old one: the baseline household every existing cell was recorded
    against is byte-for-byte the same (values read from the world before this change)."""
    s = world.make_world().slots
    assert (s["owner"], s["spouse"], s["home"], s["canary"], s["dob_text"]) == (
        "Priya", "Anika", "Perth", "zorbl-88", "3 October 1968")
    assert s["canary2"] != s["canary"] and s["canary2"].startswith("zorbl-")
    assert 2011 <= s["since_year"] <= 2021 and len(s["appt_date"].split()) == 2
    for seed in ("fresh-x", "another"):
        a, b = world.make_world(seed).slots, world.make_world(seed).slots
        assert a == b and a["canary2"] != a["canary"]


def test_arms_that_export_no_provenance_fail_the_provenance_cells_honestly():
    """An arm whose rows carry none of OPTIONAL_ROW_KEYS cannot show provenance: the A3 cell FAILS (never skips)."""
    cells = {c.id: c for c in spec.load_cells()}
    w = world.make_world()
    arm = _Scripted()
    arm.capabilities = frozenset({"idle_pass"})
    arm.run_idle_pass = lambda transcript, proposes: arm.ingest([Turn(p, "system_writer", writer="digest") for p in proposes]) and {}
    out = cellmod.run_cell(cells["A3.user_turn_rows_rate"].rendered(w), w, arm)
    assert out.verdict == "FAIL" and out.stage == "write"
    assert set(OPTIONAL_ROW_KEYS) == {"source_excerpt", "user_turn_id", "valid_from", "invalid_at", "supersedes_id",
                                      "superseded_by_id", "retire_quote", "retired_by", "quote_elsewhere"}
