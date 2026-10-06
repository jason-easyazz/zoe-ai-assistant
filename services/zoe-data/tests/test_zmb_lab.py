"""Zoe Memory Bench lab driver: the REAL ``MemoryService`` over an in-memory store, with negative controls.

This is where the standing rule "break the fix and the test must go red" is made mechanical:

* every control cell goes RED when its feature is switched off (``control_pass`` over the whole spec);
* a control that does NOT turn its cell red makes the runner REFUSE the run - proven here with a genuinely
  broken instrument (a control switch that disables nothing) and with a genuinely vacuous cell;
* the cells measure the real code, not the environment flag: break the wall itself (no flag) and they go red;
* the lab can never reach a live store (demo ids only, the live-store guard, a pinned scratch directory) and
  leaves nothing patched behind.

Synthetic data only; no network, no model, no Postgres (``ci_safe``).
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

import pytest

import live_store_guard
import memory_authority as ma
import memory_extractor
import memory_service
import memory_tombstones

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import artifact, cells as cellmod, lab_driver, runner, spec, world  # noqa: E402
from zmb.arms.base import Turn  # noqa: E402
from zmb.arms.z0 import Z0Arm, reader_answer  # noqa: E402

DEMO = "demo_bar_0a1b2c3d"
CELLS = spec.load_cells()
BY_ID = {c.id: c for c in CELLS}
ALL = frozenset(lab_driver.CONTROLS)
TARGETS = sorted(c.id for c in CELLS if c.is_target)


def _run(cell_id, arm, seed=world.BASELINE_SEED):
    w = world.make_world(seed)
    return cellmod.run_cell(BY_ID[cell_id].rendered(w), w, arm)


@pytest.fixture
def arm():
    a = Z0Arm()
    yield a
    a.close()


@pytest.fixture(scope="module")
def full_control_pass():
    return runner.control_pass(CELLS, world.make_world(), ALL)


@pytest.fixture(scope="module")
def full_measure():
    a = Z0Arm()
    try:
        return runner.run_cells(CELLS, world.make_world(), a)
    finally:
        a.close()


# ── the controls: every controlled cell goes red ──────────────────────────────

def test_every_controlled_cell_goes_red_with_its_features_off(full_control_pass):
    cp = full_control_pass
    assert cp["ok"] and cp["green"] == [] and cp["not_run"] == []
    runnable = {c.id for c in CELLS
                if c.controls and c.expected == "PASS" and c.tier == "store"
                and cellmod.required_capabilities(c) <= set(Z0Arm.capabilities)}
    # 136 with chromadb present (the two ``disk`` cells run), 134 in the slim CI lane where they are declared skips
    # (99 before the temporal / recall / poisoning / provenance / graph axes; +6 for the cells #1895 fixes; +3 for the two
    # timelines: C2.history_read and C4.valid_from_is_event_time leave the targets, + C2.history_is_labelled)
    assert len(runnable) in (134, 136) and cp["checked"] == cp["red"] == len(runnable)
    assert {r["id"] for r in cp["rows"]} == runnable
    assert all(r["verdict"] == "FAIL" and r["stage"] in ("write", "read", "answer") for r in cp["rows"])


@pytest.mark.parametrize("control", sorted(lab_driver.CONTROLS))
def test_each_control_is_named_by_a_cell_and_flips_the_cells_it_alone_guards(control):
    naming = [c for c in CELLS if control in c.controls]
    assert naming, f"control {control!r} guards no cell: it would be an un-instrumented switch"
    alone = [c for c in naming if set(c.controls) <= {control}]
    if not alone:   # a two-layer defence (the quality gate + the extractor): off ALONE flips nothing, both do
        assert control == "gate"
        return
    cp = runner.control_pass(CELLS, world.make_world(), frozenset({control}))
    if cp["checked"] == 0:
        # every cell naming this control needs a capability this lane lacks (the ``disk`` cells need chromadb,
        # absent from the slim CI lane): a declared skip, not a proof either way
        pytest.skip(f"control {control!r}: its cells need a capability this lane lacks")
    assert cp["ok"] and cp["checked"] <= len(alone) and cp["red"] == cp["checked"]


def test_the_s1_signature_appears_when_the_authority_wall_is_off():
    """Z0-off reproduces the incident: the owner's row superseded, the new row stamped by the real writer."""
    w = world.make_world()
    cell = BY_ID["A1.digest.home"].rendered(w)
    a = Z0Arm(off=frozenset({"authority"}))
    try:
        a.reset(DEMO)
        cellmod._play(cell, a)
        rows = a.stats()["rows"]
    finally:
        a.close()
    owner = [r for r in rows if w.slots["home"] in r["text"]]
    intr = [r for r in rows if w.slots["home_intruder"] in r["text"]]
    assert [r["status"] for r in owner] == ["superseded"]            # the owner's row is gone from recall
    assert intr and all(r["origin"] == "digest" for r in intr)       # ... replaced by the model's, stamped honestly


def test_with_the_wall_on_the_same_cell_holds_the_contradiction_as_a_disputed_candidate(arm):
    w = world.make_world()
    arm.reset(DEMO)
    cellmod._play(BY_ID["A1.digest.home"].rendered(w), arm)
    rows = arm.stats()["rows"]
    assert [r["status"] for r in rows if w.slots["home"] in r["text"]] == ["approved"]
    held = [r for r in rows if w.slots["home_intruder"] in r["text"]]
    assert [r["status"] for r in held] == ["disputed"] and held[0]["contradicts_id"]


# ── the refusal: a control that stays green voids the run ────────────────────

@contextlib.contextmanager
def _controls_do_nothing(features, svc):
    yield     # a broken instrument: the switch is wired to nothing


def test_a_broken_control_switch_is_caught_and_the_run_is_refused(monkeypatch, tmp_path):
    """RED-BEFORE-GREEN for the instrument. With the real switch, ``--axis authority`` runs (see below). With
    a control that disables nothing, every controlled cell stays GREEN, the runner refuses, and nothing is
    measured."""
    monkeypatch.setattr(lab_driver, "controls_off", _controls_do_nothing)
    cp = runner.control_pass(CELLS, world.make_world(), ALL)
    assert not cp["ok"] and len(cp["green"]) > 90 and cp["red"] == 0

    measured = []
    monkeypatch.setattr(runner, "make_arm", lambda name: measured.append(name) or Z0Arm())
    argv = ["--axis", "authority", "--results", str(tmp_path / "r.json"), "--trend", str(tmp_path / "t.jsonl"),
            "--baseline", str(tmp_path / "b.json")]
    assert runner.main(argv) == 2
    art = json.loads((tmp_path / "r.json").read_text())
    assert art["status"] == "error" and "instrument not instrumented" in art["refusal"]
    assert art["axes"] is None and art["cells"] == [] and art["instrument"]["ok"] is False
    assert len(art["instrument"]["green"]) > 0 and measured == []          # the arm was never built
    assert not (tmp_path / "b.json").exists()
    # and with the real switch restored the same command runs and passes the instrument check
    monkeypatch.undo()
    monkeypatch.setattr(runner, "revision", lambda: None)
    assert runner.main(argv) == 0
    art = json.loads((tmp_path / "r.json").read_text())
    assert art["status"] == "partial" and art["instrument"]["ok"] and art["axes"]["authority"]["claimable"]


def test_a_vacuous_cell_that_cannot_fail_is_refused_too(monkeypatch, tmp_path):
    """A cell whose probe cannot go red (it asserts the absence of a string nothing ever writes) is exactly the
    silent-pass the standing rule exists for: its control stays green and the run is void."""
    vacuous = spec.Cell(id="A9.vacuous", axis="authority", title="cannot fail", tier="store", kind="script",
                        controls=("authority",), events=({"text": "User lives in {home}.", "speaker": "owner_taught"},),
                        probes=({"kind": "store", "assertions": [{"op": "absent", "contains": ["zzz-never-written"]}]},))
    monkeypatch.setattr(runner, "load_cells", lambda: CELLS + [vacuous])
    monkeypatch.setattr(runner, "revision", lambda: None)
    cp = runner.control_pass(CELLS + [vacuous], world.make_world(), ALL)
    assert cp["green"] == ["A9.vacuous"] and not cp["ok"]
    argv = ["--only", "A9.vacuous", "--results", str(tmp_path / "r.json"), "--trend", str(tmp_path / "t.jsonl"),
            "--baseline", str(tmp_path / "b.json")]
    assert runner.main(argv) == 2
    art = json.loads((tmp_path / "r.json").read_text())
    assert "A9.vacuous" in art["refusal"] and art["cells"] == []


def test_a_controlled_cell_that_errors_under_its_control_is_not_proof(monkeypatch):
    broken = spec.Cell(id="A9.broken", axis="authority", title="t", tier="store", kind="script",
                       controls=("authority",), events=({"do": "no_such_action"},),
                       probes=({"kind": "store", "assertions": []},))
    cp = runner.control_pass([broken], world.make_world(), ALL)
    assert cp["not_run"] == ["A9.broken"] and not cp["ok"] and cp["green"] == []


# ── the cells measure the real code, not the environment flag ────────────────

def test_breaking_the_wall_itself_turns_the_authority_cells_red_without_any_control_flag(monkeypatch, arm):
    ok = _run("A1.digest.home", arm)
    assert ok.verdict == "PASS"
    monkeypatch.setattr(ma, "find_conflict", lambda *a, **k: None)      # the conflict scan finds nothing
    monkeypatch.setattr(ma, "may_override", lambda *a, **k: True)       # every writer may override everyone
    assert os.environ.get("ZOE_MEMORY_AUTHORITY") in (None, "")          # ... and the flag was never touched
    for cid in ("A1.digest.home", "A1.mcp.pet", "A1.decay_sweep.work", "A5.digest.home"):
        assert _run(cid, arm).verdict == "FAIL", cid


def test_breaking_the_identity_wall_itself_turns_the_identity_cells_red(monkeypatch, arm):
    assert _run("H1.digest", arm).verdict == "PASS"
    monkeypatch.setattr(memory_service, "_identity_assertion_blocked", lambda *a, **k: False)
    assert _run("H1.digest", arm).verdict == "FAIL" and _run("H3.third_person_candidate", arm).verdict == "FAIL"


def test_breaking_the_tombstone_itself_turns_the_forget_cells_red(monkeypatch, arm):
    assert _run("F2.late_writer.digest", arm).verdict == "PASS"
    monkeypatch.setattr(memory_tombstones, "matching_tombstone", lambda *a, **k: None)
    assert _run("F2.late_writer.digest", arm).verdict == "FAIL"


def test_breaking_the_real_extractor_turns_the_extraction_cells_red(monkeypatch, arm):
    assert _run("B1.date_day_first", arm).verdict == "PASS"
    monkeypatch.setattr(memory_extractor, "extract_candidates", lab_driver.lazy_extract_candidates)
    out = _run("B1.date_day_first", arm)
    assert out.verdict == "FAIL" and out.evidence["probes"][0]["anti_hits"] == 1   # the month-first reading fired


# ── the shipped baseline: what Z0 does today ─────────────────────────────────

def test_z0_measures_as_documented(full_measure):
    by = {r["id"]: r for r in full_measure}
    for c in CELLS:
        r = by[c.id]
        if c.tier == "full":
            assert r["verdict"] == "SKIP" and r["reason"] and r["brain_turns"] == 0
        elif cellmod.required_capabilities(c) - set(Z0Arm.capabilities):
            # a disk cell where chromadb is not installed (the slim CI lane): a declared SKIP, never a PASS
            assert r["verdict"] == "SKIP" and "lacks capability" in r["reason"], (c.id, r)
        elif c.is_target:
            # a KNOWN failure. If this starts passing you fixed the thing: flip `expected` to PASS in the
            # spec, give the cell a control, and re-record the baseline.
            assert r["verdict"] == "FAIL", f"{c.id} now passes - lock the improvement in"
        else:
            assert r["verdict"] == "PASS", (c.id, r["evidence"])
    assert artifact.hard_violations(full_measure, BY_ID) == []
    # B9/E1b/H5 fixed (#1882); I1/I1b/I2.attributed/I4 fixed by the own-words wall (#1894): graded cells.
    # Every target below was MEASURED red on main (see its cell's note)
    assert TARGETS == sorted([
        "F3.after_tombstone_ttl",
        "I2.third_party_fragment.third_party"])
    assert len([c for c in CELLS if c.id.startswith("A1.")]) == 56
    assert all(isinstance(r["duration_s"], float) and r["brain_turns"] == 0 for r in full_measure)


def test_the_axis_table_for_z0_is_claimable_with_wilson_intervals(full_measure, full_control_pass):
    axes = artifact.axis_stats(full_measure, BY_ID, full_control_pass["ok"])
    a = axes["authority"]
    # 72 held-back / writer-matrix / A6-A7 cells + A3 x5 + A8 x2 graded (the A8 sanity cell is not evidence)
    # (A3 x3 are fixed by #1895 and now graded passes)
    assert a["n"] == 79 and a["pass"] == 79 and a["claimable"] and a["hard_violations"] == []
    assert a["targets_failing"] == []
    for name in ("identity", "forgetting", "abstention", "extraction", "emotional", "temporal", "recall", "poisoning"):
        assert axes[name]["claimable"] and axes[name]["n"] > 0 and not axes[name]["hard_violations"], name
    assert (axes["recall"]["n"], axes["recall"]["pass"]) == (4, 4)
    assert (axes["temporal"]["n"], axes["temporal"]["pass"]) == (12, 12)
    assert axes["temporal"]["targets_failing"] == []     # C2 / C4 fixed: two timelines (audit P2.1)
    assert (axes["poisoning"]["n"], axes["poisoning"]["pass"]) == (7, 6)
    # only the bare third-party fragment is left: it needs a speaker verdict, not a text rule
    assert axes["poisoning"]["targets_failing"] == ["I2.third_party_fragment.third_party"]
    assert axes["forgetting"]["targets_failing"] == ["F3.after_tombstone_ttl"]
    assert axes["extraction"]["targets_failing"] == []   # B9 fixed in #1882
    assert not any(axes[n]["uncontrolled"] for n in axes)


def test_the_real_nightly_digest_ran_in_the_incident_cells(arm):
    out = _run("A2.incident.home", arm)
    assert out.verdict == "PASS"
    (rep,) = out.evidence["idle_pass"]
    assert rep["extracted"] == 1 and rep["superseded"] == 0 and rep.get("candidates") == 1   # it RAN, and held back


def test_the_same_cells_give_the_same_verdicts_on_a_held_out_seed(full_measure):
    a = Z0Arm()
    try:
        held = runner.run_cells(CELLS, world.make_world("fresh-held-out"), a)
    finally:
        a.close()
    assert {r["id"]: r["verdict"] for r in held} == {r["id"]: r["verdict"] for r in full_measure}


def test_a_guard_trip_during_the_run_is_an_error_but_an_earlier_one_is_not_this_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "revision", lambda: None)
    monkeypatch.setattr(live_store_guard, "_TRIPS", live_store_guard.trip_count() + 7)  # some other test's trips
    argv = ["--only", "H4.owner_teach_allowed", "--results", str(tmp_path / "r.json"),
            "--trend", str(tmp_path / "t.jsonl"), "--baseline", str(tmp_path / "b.json")]
    assert runner.main(argv) == 0
    assert json.loads((tmp_path / "r.json").read_text())["teardown"] == {
        "proven": True, "kind": "lab: in-memory store, nothing persisted", "live_store_guard_trips": 0}

    real_ingest = Z0Arm.ingest

    def trips(self, turns):
        live_store_guard._trip("a lab write reached a live store")     # counts a trip, as the backstop does
        return real_ingest(self, turns)
    monkeypatch.setattr(Z0Arm, "ingest", trips)
    assert runner.main(argv) == 2
    art = json.loads((tmp_path / "r.json").read_text())
    assert art["status"] == "error" and art["teardown"]["proven"] is False
    assert art["teardown"]["live_store_guard_trips"] >= 1


def test_a_full_runner_run_leaves_an_artifact_with_no_household_text_and_zero_guard_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "revision", lambda: {"commit": "x", "dirty": False})
    argv = ["--results", str(tmp_path / "r.json"), "--trend", str(tmp_path / "t.jsonl"),
            "--baseline", str(tmp_path / "b.json")]
    before = live_store_guard.trip_count()
    assert runner.main(argv + ["--record-baseline"]) == 0
    art = json.loads((tmp_path / "r.json").read_text())
    assert art["status"] == "ok" and art["instrument"]["ok"] and art["teardown"]["live_store_guard_trips"] == 0
    assert live_store_guard.trip_count() == before
    assert artifact.household_strings_in(art, world.make_world().all_strings() + world.pool_strings()) == []
    assert json.loads((tmp_path / "b.json").read_text())["cells"]["A1.digest.home"] == "PASS"
    assert runner.main(argv + ["--compare-baseline"]) == 0
    assert runner.main(argv + ["--arm", "Z0-off"]) == 0 and json.loads(
        (tmp_path / "r.json").read_text())["run_kind"] == "control"


# ── the lab store ────────────────────────────────────────────────────────────

def test_lab_collection_where_operators_get_update_delete():
    col = lab_driver.LabCollection()
    col.upsert(ids=["a", "b", "c"], documents=["x y", "y z", "z"],
               metadatas=[{"user_id": "u", "n": 1, "s": "p"}, {"user_id": "u", "n": 2, "s": "q"},
                          {"user_id": "v", "n": 3, "s": "p"}])
    assert col.get(where={"user_id": "u"})["ids"] == ["a", "b"]
    assert col.get(where={"$or": [{"s": "q"}, {"user_id": "v"}]})["ids"] == ["b", "c"]
    assert col.get(where={"$and": [{"user_id": "u"}, {"n": {"$gte": 2}}]})["ids"] == ["b"]
    assert col.get(where={"s": {"$ne": "p"}})["ids"] == ["b"]
    assert col.get(where={"n": {"$in": [1, 3]}})["ids"] == ["a", "c"]
    assert col.get(where={"n": {"$nin": [1, 3]}})["ids"] == ["b"]
    assert col.get(ids=["c", "zzz"])["ids"] == ["c"] and col.get(limit=1, offset=1)["ids"] == ["b"]
    col.update(ids=["a"], metadatas=[{"user_id": "u", "n": 9}])
    assert col.get(ids=["a"])["metadatas"][0]["n"] == 9 and col.get(ids=["a"])["documents"][0] == "x y"
    col.delete(ids=["b"])
    assert col.count() == 2 and col.get(ids=["b"])["ids"] == []


def test_lab_collection_query_is_deterministic_and_honours_where():
    col = lab_driver.LabCollection()
    col.upsert(ids=["a", "b", "c"], documents=["blue whale song", "blue sky", "green field"],
               metadatas=[{"user_id": "u"}, {"user_id": "u"}, {"user_id": "v"}])
    r = col.query(query_texts=["blue whale"], n_results=3)
    assert r["ids"][0][0] == "a" and r["distances"][0][0] < r["distances"][0][1]
    assert col.query(query_texts=["blue whale"], n_results=3) == r
    assert col.query(query_texts=["blue whale"], n_results=3, where={"user_id": "v"})["ids"][0] == ["c"]


def test_controls_off_restores_everything_and_rejects_typos():
    svc = lab_driver.load_service()
    before = (os.environ.get("ZOE_MEMORY_AUTHORITY"), os.environ.get("ZOE_AFFECT_CONSENT_GATE"),
              svc.memory_service._identity_assertion_blocked, svc.memory_tombstones.matching_tombstone,
              svc.memory_tombstones.add, svc.memory_extractor.extract_candidates,
              svc.memory_quality.is_storable_fact, svc.memory_service.MemoryService.review)
    with lab_driver.controls_off(ALL, svc):
        assert os.environ["ZOE_MEMORY_AUTHORITY"] == "off" and os.environ["ZOE_AFFECT_CONSENT_GATE"] == "off"
        assert svc.memory_extractor.extract_candidates is lab_driver.lazy_extract_candidates
        assert svc.memory_service.MemoryService.review is not before[7]
    after = (os.environ.get("ZOE_MEMORY_AUTHORITY"), os.environ.get("ZOE_AFFECT_CONSENT_GATE"),
             svc.memory_service._identity_assertion_blocked, svc.memory_tombstones.matching_tombstone,
             svc.memory_tombstones.add, svc.memory_extractor.extract_candidates,
             svc.memory_quality.is_storable_fact, svc.memory_service.MemoryService.review)
    assert after == before
    with pytest.raises(ValueError, match="unknown control"):
        with lab_driver.controls_off({"authorty"}, svc):
            pass
    with pytest.raises(RuntimeError):                       # restored even when the block raises
        with lab_driver.controls_off({"authority"}, svc):
            raise RuntimeError("boom")
    assert os.environ.get("ZOE_MEMORY_AUTHORITY") == before[0]


def test_lazy_extractor_has_every_flaw_the_anti_needles_look_for():
    out = lambda msg, **k: " | ".join(c.text for c in lab_driver.lazy_extract_candidates(msg, **k))  # noqa: E731
    assert "July 8 1991" in out("My birthday is 8/7/1991") or "August 7 1991" in out("My birthday is 8/7/1991")
    assert "wife is named Tove" in out("Here are Tove and Leo") and "husband is named Leo" in out("Here are Tove and Leo")
    assert "User asked" in out("when is my dentist appointment?")
    assert "I don't have any notes" in out("thanks", assistant_response="I don't have any notes.")
    assert "child is named Biscuit" in out("my dog is named Biscuit")
    assert "lives in Hobart" in out("I don't live in Hobart any more")


# ── the arm ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["jason", "demo_user", "test-jason", "guest", "demo_bar_XYZ", "demo_bar_0a1b2c3",
                                 "demo_bar_0a1b2c3d4", "", "member-a", "demo_0a1b2c3d"])
def test_the_arm_refuses_every_non_demo_identity(arm, bad):
    with pytest.raises(ValueError, match="non-demo identity"):
        arm.reset(bad)


def test_the_arm_requires_reset_and_the_lab_refuses_a_live_palace(arm, monkeypatch):
    with pytest.raises(RuntimeError, match="reset"):
        arm.ingest([Turn("User lives in Hobart.", "owner_taught")])
    monkeypatch.setattr(live_store_guard, "is_live_palace", lambda d: True)
    with pytest.raises(lab_driver.LabRefusal, match="live palace"):
        arm.reset(DEMO)


def test_the_lab_pin_refuses_a_service_imported_against_the_live_palace(monkeypatch):
    live = str(Path(live_store_guard._real_home()) / ".mempalace")
    monkeypatch.setattr(memory_service, "_MEMPALACE_DATA", live)
    with pytest.raises(lab_driver.LabRefusal, match="LIVE palace"):
        lab_driver.pin_scratch_stores()
    monkeypatch.undo()
    lab_driver.pin_scratch_stores()    # a pinned (conftest) session passes
    assert not live_store_guard.is_live_palace(os.environ["MEMPALACE_DATA_DIR"])


def test_the_arm_interface_end_to_end_and_leaves_nothing_patched(arm):
    originals = (memory_service._user_opted_out, memory_service.get_memory_service, memory_tombstones.time,
                 memory_extractor.extract_candidates, os.environ.get("ZOE_MEMORY_AUTHORITY"), sys.modules.get("pending_suggestions"))
    arm.reset(DEMO)
    w = world.make_world()
    s = w.slots
    rep = arm.ingest([Turn(f"User's friend {s['friend']} lives in {s['home']}.", "owner_taught"),
                      Turn(f"my wife's name is {s['spouse']}", "owner_typed"),
                      Turn("thanks", "owner_typed", assistant_text="never mined"),
                      Turn("an assistant line", "assistant")])
    assert rep.turns == 4 and rep.written == 2 and "assistant turn: never mined" in rep.notes
    st = arm.stats()
    assert st["counts"] == {"approved": 2} and len(st["rows"]) == 2 and set(st["rows"][0]) >= set(
        ("id", "text", "status", "authority_class", "origin", "contradicts_id", "entity_type", "memory_type", "user_id"))
    by_text = {r["text"]: r for r in st["rows"]}
    assert by_text[f"User's friend {s['friend']} lives in {s['home']}."]["authority_class"] == "user_stated"
    hits = arm.recall(f"where does {s['friend']} live", 3)
    assert hits and s["friend"] in hits[0]["text"] and all(r["status"] == "approved" for r in hits)
    assert "forgotten" in arm.forget(s["friend"]).lower()
    assert arm.stats()["counts"].get("archived") == 1
    assert not any(s["friend"] in r["text"] for r in arm.recall(s["friend"], 5))   # forgotten rows are not recalled
    assert arm.as_of("q", "2026-01-01T00:00:00Z") == []              # before anything was known: nothing was true
    arm.close()
    arm.close()                                                      # idempotent
    assert (memory_service._user_opted_out, memory_service.get_memory_service, memory_tombstones.time,
            memory_extractor.extract_candidates, os.environ.get("ZOE_MEMORY_AUTHORITY"),
            sys.modules.get("pending_suggestions")) == originals     # nothing patched is left behind


def test_arm_names_and_capabilities():
    assert Z0Arm().name == "Z0" and Z0Arm(off=ALL).name == "Z0-off"
    assert Z0Arm(off=frozenset({"authority"})).name == "Z0-off[authority]"
    assert {"clock", "controls", "reader", "identities", "idle_pass"} <= Z0Arm.capabilities
    for name in ("Z0", "Z0-off"):
        from zmb.arms import make_arm
        a = make_arm(name)
        assert isinstance(a, Z0Arm) and a.name == name
        a.close()


def test_the_clock_capability_is_what_expires_the_tombstone(arm):
    w = world.make_world()
    s = w.slots
    arm.reset(DEMO)
    arm.ingest([Turn(f"User's friend {s['friend']} lives in {s['home']}.", "owner_taught")])
    arm.forget(s["friend"])
    late = Turn("a transcript naming them", "system_writer", writer="digest",
                proposes=(f"User's friend {s['friend']} rang.",))
    assert arm.ingest([late]).refused == 1                          # inside the TTL: refused
    arm.advance_clock(360)
    assert arm.ingest([late]).written == 1                          # past it: resurrected (target F3)


def test_the_scripted_reader_declines_unless_one_row_covers_the_question():
    rows = [{"text": "User's optometrist is Dr Vale (zorbl-17)."}]
    assert reader_answer(rows, "when is my dentist appointment") == "I don't have that saved."
    assert "zorbl-17" in reader_answer(rows, "who is my optometrist")
    assert "zorbl-17" in reader_answer(rows, "when is my dentist appointment", sycophantic=True)
    assert reader_answer([], "anything", sycophantic=True) == "I don't have that saved."


# ── the temporal / recall / poisoning / provenance / graph axes ──────────────

NEW_CONTROLS = {
    "supersede": ["C1.update_typed", "C1.update_moved_phrase", "C5.retracted_not_served", "C5.retracted_via_turn_digest"],
    "entailment": ["C1.update_via_turn_digest"],
    "invalidate": ["C1.old_fact_invalidated_not_deleted"],
    "event_time": ["C4.valid_from_is_event_time"],
    "history": ["C2.history_is_labelled"],
    "retrieval": ["C3.dated_event", "C4.since_year_kept", "D1.hit5_after_30_filler", "D2.hit5_after_100_filler",
                  "D3.hit5_after_300_filler", "D4.hit5_paraphrase_after_100_filler"],
    "provenance": ["A3.typed_turn_rows", "A3.voice_verified_turn_rows", "A3.taught_rows", "A3.nightly_digest_rows",
                   "A3.user_turn_rows_rate"],
    "topic": ["C6.no_collateral_invalidation"],
}


@pytest.mark.parametrize("control", sorted(NEW_CONTROLS))
def test_each_new_control_flips_exactly_the_cells_that_name_it(control):
    """The five switches this axis set added: off ALONE, exactly the listed cells go red - and every other
    controlled cell is not even run (a switch that turned an unrelated cell red would be a different switch)."""
    cp = runner.control_pass(CELLS, world.make_world(), frozenset({control}))
    assert cp["ok"] and sorted(r["id"] for r in cp["rows"]) == sorted(NEW_CONTROLS[control])
    assert cp["red"] == len(NEW_CONTROLS[control]) and cp["green"] == [] and cp["not_run"] == []


@pytest.mark.parametrize("control", sorted(NEW_CONTROLS))
def test_an_uncontrolled_pass_is_refused_for_every_new_control(control, monkeypatch, tmp_path):
    """RED-BEFORE-GREEN per switch: a cell that names the control but cannot go red with it off (it asserts the
    absence of a string nothing writes) stays GREEN, so the run is REFUSED - the lab will not report a PASS no
    control can turn red."""
    vac = spec.Cell(id="X9.vacuous", axis="temporal", title="cannot fail", tier="store", kind="script",
                    controls=(control,), events=({"text": "User lives in {home}.", "speaker": "owner_taught"},),
                    probes=({"kind": "store", "assertions": [{"op": "absent", "contains": ["zzz-never-written"]}]},))
    cp = runner.control_pass(CELLS + [vac], world.make_world(), frozenset({control}))
    assert cp["green"] == ["X9.vacuous"] and not cp["ok"]
    monkeypatch.setattr(runner, "load_cells", lambda: CELLS + [vac])
    monkeypatch.setattr(runner, "revision", lambda: None)
    argv = ["--only", "X9.vacuous", "--results", str(tmp_path / "r.json"), "--trend", str(tmp_path / "t.jsonl"),
            "--baseline", str(tmp_path / "b.json")]
    assert runner.main(argv) == 2
    art = json.loads((tmp_path / "r.json").read_text())
    assert art["status"] == "error" and "X9.vacuous" in art["refusal"] and art["cells"] == []


def test_the_new_axes_refuse_when_a_control_switch_is_wired_to_nothing(monkeypatch, tmp_path):
    """The whole new spec through the real runner: with a switch that disables nothing every temporal / recall /
    provenance / graph control cell stays green and the run is refused, nothing measured."""
    monkeypatch.setattr(lab_driver, "controls_off", _controls_do_nothing)
    cp = runner.control_pass(CELLS, world.make_world(), ALL)
    new_ids = {i for ids in NEW_CONTROLS.values() for i in ids} | {"A8.inferred_cannot_close_user_edge",
                                                                    "A8.refused_edge_is_held_not_lost",
                                                                    "I3.assistant_text_canary"}   # I4 is a target: no control
    assert new_ids <= set(cp["green"]) and not cp["ok"]
    monkeypatch.setattr(runner, "revision", lambda: None)
    argv = ["--axis", "temporal,recall,poisoning", "--results", str(tmp_path / "r.json"),
            "--trend", str(tmp_path / "t.jsonl"), "--baseline", str(tmp_path / "b.json")]
    assert runner.main(argv) == 2
    art = json.loads((tmp_path / "r.json").read_text())
    assert "instrument not instrumented" in art["refusal"] and art["cells"] == []


async def _true():
    return True


def test_the_new_cells_measure_the_real_code_not_the_control_flag(monkeypatch, arm):
    """Break the real function (no ZMB switch touched) and the cell that claims it goes red."""
    import memory_supersede
    import person_extractor
    assert os.environ.get("ZOE_MEMORY_IMPLICIT_SUPERSEDE") in (None, "")       # the lab sets it per operation only
    for cid in ("C1.update_typed", "C5.retracted_not_served", "C6.no_collateral_invalidation",
                "C1.old_fact_invalidated_not_deleted", "D2.hit5_after_100_filler", "A3.typed_turn_rows",
                "A8.inferred_cannot_close_user_edge", "I3.assistant_text_canary"):
        assert _run(cid, arm).verdict == "PASS", cid
    monkeypatch.setattr(memory_supersede, "conflict_pairs", lambda rows: [])           # the pass finds nothing
    assert _run("C1.update_typed", arm).verdict == "FAIL" and _run("C5.retracted_not_served", arm).verdict == "FAIL"
    monkeypatch.undo()
    monkeypatch.setattr(memory_supersede, "same_topic", lambda a, b: True)
    monkeypatch.setattr(memory_supersede, "exclusive_conflict", lambda a, b: True)    # a greedy matcher
    assert _run("C6.no_collateral_invalidation", arm).verdict == "FAIL"
    monkeypatch.undo()
    real = memory_service.MemoryService._supersede_by_sync

    def deleting(self, user_id, old_id, new_id):
        done = real(self, user_id, old_id, new_id)
        if done:
            self._collection().delete(ids=[old_id])
        return done
    monkeypatch.setattr(memory_service.MemoryService, "_supersede_by_sync", deleting)  # history deleted
    assert _run("C1.old_fact_invalidated_not_deleted", arm).verdict == "FAIL"
    monkeypatch.undo()

    def newest_first(self, query, user_id, limit, depth_by_pid=None):                  # a ranking that ignores the query
        import datetime
        rows = self._visible_rows(user_id, datetime.datetime.now(datetime.timezone.utc))
        return sorted(rows, key=lambda r: (float(r.metadata.get("added_ts") or 0), r.id), reverse=True)[:limit]
    monkeypatch.setattr(memory_service.MemoryService, "_semantic_search", newest_first)
    out = _run("D2.hit5_after_100_filler", arm)
    assert out.verdict == "FAIL" and out.evidence["probes"][0]["hit_at_k"]["hits"] == 0 and out.stage == "read"
    monkeypatch.undo()
    real_build = memory_service.MemoryService._build_metadata

    def no_provenance(**k):
        md = real_build(**k)
        md.pop("source_excerpt", None)
        md.pop("user_turn_id", None)
        return md
    monkeypatch.setattr(memory_service.MemoryService, "_build_metadata", staticmethod(no_provenance))
    assert _run("A3.typed_turn_rows", arm).verdict == "FAIL"
    monkeypatch.undo()
    monkeypatch.setattr(person_extractor, "_edge_may_change", lambda *a, **k: _true())  # the edge wall lets everyone in
    assert os.environ.get("ZOE_MEMORY_AUTHORITY") in (None, "")
    assert _run("A8.inferred_cannot_close_user_edge", arm).verdict == "FAIL"
    assert _run("A8.refused_edge_is_held_not_lost", arm).verdict == "FAIL"
    monkeypatch.undo()


def test_the_live_flags_are_set_around_an_operation_and_restored(arm, monkeypatch):
    """Z0 runs with the flags the live service runs with (OFF in the code, ON in .env) - per operation only."""
    monkeypatch.delenv("ZOE_MEMORY_IMPLICIT_SUPERSEDE", raising=False)
    monkeypatch.setenv("ZOE_TEMPORAL_RELATIONSHIPS_ENABLED", "0")
    seen = {}
    real = memory_service.MemoryService.ingest

    async def spy(self, *a, **k):
        seen["supersede"] = os.environ.get("ZOE_MEMORY_IMPLICIT_SUPERSEDE")
        seen["edges"] = os.environ.get("ZOE_TEMPORAL_RELATIONSHIPS_ENABLED")
        return await real(self, *a, **k)
    monkeypatch.setattr(memory_service.MemoryService, "ingest", spy)
    arm.reset(DEMO)
    arm.ingest([Turn("User lives in Perth.", "owner_taught")])
    assert seen == {"supersede": "1", "edges": "1"}
    assert "ZOE_MEMORY_IMPLICIT_SUPERSEDE" not in os.environ and os.environ["ZOE_TEMPORAL_RELATIONSHIPS_ENABLED"] == "0"
    off = Z0Arm(off=frozenset({"supersede"}))
    try:
        off.reset(DEMO)
        off.ingest([Turn("User lives in Perth.", "owner_taught")])
        assert seen["supersede"] == "off"                      # the control wins over the live flag
    finally:
        off.close()


def test_the_row_export_carries_provenance_and_the_validity_interval(arm):
    w = world.make_world()
    s = w.slots
    arm.reset(DEMO)
    arm.ingest([Turn(f"I live in {s['home_old']}", "owner_typed"), Turn(f"I live in {s['home']}", "owner_typed")])
    assert arm.run_conflict_pass()["superseded"] == 1
    rows = {r["text"]: r for r in arm.stats()["rows"]}
    old, new = rows[f"User lives in {s['home_old']}"], rows[f"User lives in {s['home']}"]
    assert old["status"] == "superseded" and old["invalid_at"] and old["superseded_by_id"] == new["id"]
    assert new["status"] == "approved" and new["valid_from"] and new["supersedes_id"] == old["id"]
    assert new["source_excerpt"] == f"I live in {s['home']}" and new["user_turn_id"] and new["authority_class"] == "user_stated"
    from zmb.arms.base import OPTIONAL_ROW_KEYS
    assert set(OPTIONAL_ROW_KEYS) <= set(new)
    # a plain question never sees the replaced row; a history question gets it back, LABELLED (C2)
    assert not any(s["home_old"] in r["text"] for r in arm.recall("where do I live", 5))
    back = [r for r in arm.recall("where did I live before", 5) if s["home_old"] in r["text"]]
    assert back and back[0]["text"].startswith("Before that") and back[0]["status"] == "superseded"
    # the two timelines meet with no gap and no overlap: the old row's invalid_at IS the new row's valid_from
    assert old["invalid_at"] >= old["valid_from"] and old["invalid_at"] == new["valid_from"]
    import datetime as _dt
    iso = lambda ts: _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat()   # noqa: E731
    between = iso((old["valid_from"] + old["invalid_at"]) / 2)
    assert [r["text"] for r in arm.as_of("where do I live", between)] == [old["text"]]        # then: the old home
    assert [r["text"] for r in arm.as_of("where do I live", iso(old["invalid_at"]))] == [new["text"]]   # [from, to)
    assert [r["text"] for r in arm.as_of("where do I live", iso(new["valid_from"] + 60))] == [new["text"]]


def test_the_people_graph_capability_is_the_real_writer_over_sqlite(arm):
    s = world.make_world().slots
    arm.reset(DEMO)
    a, b = s["friend"], s["sibling"]
    arm.write_edge(a, b, "friend", "personal", "user_stated", "conversation")
    arm.write_edge(a, b, "spouse", "family", "inferred", "digest")
    (edge,) = arm.edges()
    assert (edge["rel_type"], edge["current"], edge["authority"], edge["origin"]) == ("friend", True, "user_stated", "conversation")
    held = [r for r in arm.stats()["rows"] if r["status"] == "disputed"]
    assert len(held) == 1 and held[0]["contradicts_id"].startswith("edge:") and held[0]["authority_class"] != "user_stated"
    arm.reset("demo_bar_0a1b2c3e")                          # reset opens a fresh graph: nothing leaks across cells
    assert arm.edges() == []


def test_a_cell_that_needs_a_capability_the_arm_lacks_skips_it_never_passes():
    from zmb.arms.base import Arm

    class NoGraph(Arm):
        name = "no-graph"
        capabilities = frozenset()

        def reset(self, u): ...
        def ingest(self, t): ...
        def recall(self, q, k=10): ...
        def forget(self, e): ...
        def as_of(self, q, ts): ...
        def stats(self): ...
    w = world.make_world()
    for cid, cap in (("A8.inferred_cannot_close_user_edge", "edges"), ("C1.update_typed", "conflict_pass")):
        out = cellmod.run_cell(BY_ID[cid].rendered(w), w, NoGraph())
        assert out.verdict == "SKIP" and cap in out.reason, cid


def test_the_recall_corpus_is_deterministic_and_the_filler_is_what_it_says(arm):
    from zmb import needles
    a, b = needles.corpus("zmb-v1"), needles.corpus("zmb-v1")
    assert a == b and len(a) == 20 and len({n.subject for n in a}) == 20 and len({n.answer for n in a}) == 20
    assert needles.corpus("fresh-x") != a                                       # a held-out seed is a new corpus
    f = needles.chatter("zmb-v1", 100)
    assert f == needles.chatter("zmb-v1", 100) and len(f) == 100 and f != needles.chatter("zmb-v1", 100, salt="x")
    kinds = {k: sum(1 for x in f if x["kind"] == k) for k in ("chatter", "preference", "near_miss")}
    assert all(kinds.values()) and sum(kinds.values()) == 100
    assert len({x["text"] for x in f}) == 100                                    # every turn distinct: no idempotency collapse
    near = {x["text"] for x in f if x["kind"] == "near_miss"}
    assert near and not any(n.subject in t or n.answer in t for n in a for t in near)   # a near-miss is about someone else
    arm.reset(DEMO)
    w = world.make_world()
    cellmod._play(BY_ID["D2.hit5_after_100_filler"].rendered(w), arm, w)
    rows = arm.stats()["rows"]
    assert len(rows) > 50 and {n.fact for n in a} <= {r["text"] for r in rows}
    assert not any(r["status"] != "approved" for r in rows)


@pytest.mark.parametrize("cid", ["D1.hit5_after_30_filler", "D2.hit5_after_100_filler", "D3.hit5_after_300_filler",
                                 "D4.hit5_paraphrase_after_100_filler"])
def test_every_recall_cell_reports_its_hit_rate_with_a_wilson_interval(cid, arm):
    out = _run(cid, arm)
    h = out.evidence["probes"][0]["hit_at_k"]
    assert out.verdict == "PASS" and (h["n"], h["k"]) == (20, 5) and h["hits"] == 20 and h["rate"] == 1.0
    assert h["wilson95"][0] > 0.83 and h["min_rate"] in (0.85, 0.9, 0.95)


def test_the_decision_rule_cells_exist_with_the_rule_s_thresholds():
    """The cells the decision rule names (docs/research/memory-system-decision-2026-10-05.md section 6): D at 100
    filler >= 90%, provenance >= 95%, C1 / C2 / C6, poisoning, A8 - each present, on the right axis."""
    d2 = BY_ID["D2.hit5_after_100_filler"]
    assert d2.axis == "recall" and d2.probes[0]["min_rate"] == 0.9 and d2.probes[0]["k"] == 5
    assert d2.events[1]["turns"] == 100
    rate = BY_ID["A3.user_turn_rows_rate"]
    assert rate.axis == "authority" and rate.probes[0]["assertions"][0]["min"] == 0.95
    assert rate.probes[0]["assertions"][0]["fields"] == ["source_excerpt", "user_turn_id", "authority_class"]
    for cid in ("C1.update_typed", "C1.old_fact_invalidated_not_deleted", "C2.history_read",
                "C6.no_collateral_invalidation"):
        assert BY_ID[cid].axis == "temporal"
    assert {c.id for c in CELLS if c.axis == "poisoning"} >= {"I1.pasted_email_instruction", "I3.assistant_text_canary",
                                                              "I4.system_prefixed_user_line"}
    assert {c.id for c in CELLS if c.id.startswith("A8.")} >= {"A8.inferred_cannot_close_user_edge"}
    # every axis of the bench has cells now (the instrument can finally say ADOPTABLE or not on every named axis)
    assert {c.axis for c in CELLS} == set(spec.AXES.values())


def test_no_cell_id_or_title_names_a_household_string():
    pool = world.pool_strings()
    for c in CELLS:
        for text in (c.id, c.title):
            for name in pool:
                assert name.lower() not in text.lower().replace(".", " ").replace("_", " ").split(), (c.id, name)


# ── Z0e: the same MemoryService over a REAL Chroma collection with the service's MiniLM embedder (first contact 2026-10-06) ──

needs_embedder = pytest.mark.skipif(not lab_driver.embedder_available(), reason="needs chromadb and Chroma's MiniLM ONNX model already on disk (never downloaded)")


def test_z0e_is_a_registered_arm_without_the_disk_cells_and_skips_where_there_is_no_embedder(monkeypatch):
    from zmb.arms import ARMS, make_arm
    assert "Z0e" in ARMS
    a = make_arm("Z0e")
    assert a.name == "Z0e" and a.embed is True and "disk" not in a.capabilities and "conflict_pass" in a.capabilities       # F5 / F6 stay Z0's (hash vectors on purpose)
    monkeypatch.setattr(lab_driver, "embedder_available", lambda: False)
    o = _run("D1.hit5_after_30_filler", a)
    assert o.verdict == "SKIP" and "never downloads" in o.reason                    # a missing model is a skip, never a pass and never a download
    a.close()


@needs_embedder
def test_z0e_retrieves_with_real_embeddings_and_a_broken_retrieval_turns_it_red():
    from zmb.arms import make_arm
    e = make_arm("Z0e")
    try:
        for cid in ("D1.hit5_after_30_filler", "D4.hit5_paraphrase_after_100_filler"):
            assert _run(cid, e).verdict == "PASS", cid
        e.reset("demo_bar_0a1b2c3d")
        assert isinstance(e._lab_service.col, lab_driver.EmbedCollection)                # real Chroma, not the bag-of-words dict
    finally:
        e.close()
    broken = Z0Arm(name="Z0e", embed=True, off=frozenset({"retrieval"}))
    try:
        assert _run("D1.hit5_after_30_filler", broken).verdict == "FAIL"                  # the negative control reaches the embedder-backed arm too
    finally:
        broken.close()


@needs_embedder
def test_z0e_ranks_by_meaning_where_the_labs_bag_of_words_has_no_overlap_to_go_on():
    """'what is the name of my pet' shares no content word with 'My dog is called Biscuit': the lab's word-overlap ranking ties at zero (its order is an accident
    of the row ids), the embedder puts the dog first among distractors."""
    from zmb.arms import make_arm
    a = make_arm("Z0e")
    try:
        a.reset("demo_bar_0a1b2c3d")
        a.ingest([Turn(t, "owner_taught") for t in ("Quarterly tax returns are due in October.", "My dog is called Biscuit.", "The boiler was serviced in March.",
                                                     "I like crisp toast.")])
        assert "Biscuit" in a.recall("what is the name of my pet", 1)[0]["text"]
    finally:
        a.close()
