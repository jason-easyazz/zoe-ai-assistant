"""The struggle-fed router retrain-and-adopt loop (Phase 3; scripts/maintenance/router_struggle_retrain.py + the ledger in
services/zoe-data/router_retrain_ledger.py). Contract: docs/architecture/struggle-detector.md -> *Phase 3*.

The adopt gate is the whole safety story: an autonomous loop that can adopt a worse router eventually will. These tests pin it, the
time split (no leakage), forget, shadow-never-swaps and rollback. Every utterance is INVENTED (public repo). Pure logic + temp files:
no brain, no sidecar, no systemd (the deploy / restart seams are stubbed and every live path is redirected to tmp).

Negative controls (run by hand when written; each turns the named tests red):
  * a regressing candidate adopted   -> set GOLDEN_MAX_BROKEN = 1 (test_one_broken_golden_case_refuses...), or drop the per-case
                                        check from decide_adoption (same test)
  * a non-fixing candidate adopted   -> make fixes_known_failures `len(fixed) >= 0` (test_a_candidate_that_does_not_fix...,
                                        test_a_shuffled_label_control...)
  * leakage                          -> in group_and_split take the OLDEST wordings as holdout (test_time_split...)
  * forget                           -> drop the tombstone filter in select_examples (test_a_forgotten_example_never_reenters)
  * shadow swaps                     -> make act() adopt when `decision.adopt` regardless of mode (test_shadow_never_swaps)
  * rollback                         -> skip restore_lkg in rollback() (test_rollback_restores_the_previous_generation)
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))
sys.path.insert(0, str(REPO / "scripts" / "maintenance"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod             # @dataclass resolves annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


rs = _load("router_selftrain", REPO / "scripts" / "maintenance" / "router_selftrain.py")
rr = _load("router_struggle_retrain", REPO / "scripts" / "maintenance" / "router_struggle_retrain.py")
import router_retrain_ledger as ledger  # noqa: E402


@pytest.fixture(autouse=True)
def _ledger_dir(tmp_path, monkeypatch):
    """Every test gets its own ledger dir: never the live ~/.zoe/router_retrain."""
    monkeypatch.setenv("ZOE_ROUTER_RETRAIN_DIR", str(tmp_path / "rr"))
    yield


INC = rs.EvalScore(overall_pct=91.4, chat_fp_pct=0.0, p50_ms=386.0, n=81)
REPLAY_OK = rs.ReplayVerdict(ran=True, passed=True, detail="n=20")


def golden(n=81, wrong=(3, 7)):
    return {f"g{i}": i not in wrong for i in range(n)}


def holdout(n=12, wrong=range(8)):
    return {f"h{i}": i not in set(wrong) for i in range(n)}


def decide(**over):
    kw = dict(holdout_incumbent=holdout(), holdout_candidate={f"h{i}": True for i in range(12)},
              golden_incumbent=golden(), golden_candidate=golden(), incumbent=INC,
              candidate=rs.EvalScore(overall_pct=91.4, chat_fp_pct=0.0, p50_ms=400.0, n=81),
              replay=REPLAY_OK, forget_clean=True)
    kw.update(over)
    return rr.decide_adoption(**kw)


# ── the adopt gate ────────────────────────────────────────────────────────────
def test_a_candidate_that_fixes_the_known_failures_and_breaks_nothing_is_adopted():
    d = decide()
    assert d.adopt and all(d.checks.values()) and d.reasons == []
    assert d.numbers["fixed"] == 8 and d.numbers["broken"] == 0 and d.numbers["needed"] == 3


def test_one_broken_golden_case_refuses_even_when_the_aggregate_holds():
    """Per CASE, not just the aggregate: the candidate fixes g3 but breaks g10 - the total is unchanged, the regression is real."""
    cand = golden(wrong=(7, 10))
    d = decide(golden_candidate=cand)
    assert not d.adopt and d.checks["golden_no_case_regression"] is False and d.checks["golden_aggregate_holds"] is True
    assert d.numbers["golden_broken_ids"] == ["g10"]


def test_a_corpus_regression_in_aggregate_refuses():
    d = decide(candidate=rs.EvalScore(overall_pct=90.1, chat_fp_pct=0.0, p50_ms=400.0, n=81))
    assert not d.adopt and d.checks["golden_aggregate_holds"] is False


def test_a_candidate_that_does_not_fix_the_known_failures_is_refused():
    same = holdout()
    d = decide(holdout_candidate=dict(same))                                  # learned nothing that transfers
    assert not d.adopt and d.checks["fixes_known_failures"] is False
    two = {**holdout(), "h0": True, "h1": True}                               # fixes 2 of 8: under max(3, 25% of 8)
    assert not decide(holdout_candidate=two).adopt


def test_fixing_some_while_breaking_others_counts_net():
    cand = {**holdout(), "h0": True, "h1": True, "h2": True, "h3": True, "h8": False, "h9": False}   # 4 fixed, 2 broken: net 2 < 3
    d = decide(holdout_candidate=cand)
    assert not d.adopt and d.numbers["fixed"] == 4 and d.numbers["broken"] == 2


def test_the_fix_bar_scales_with_the_incumbents_misses():
    inc = {f"h{i}": i >= 40 for i in range(50)}                               # the incumbent misses 40
    cand = {**inc, **{f"h{i}": True for i in range(9)}}                        # fixes 9 < ceil(25% x 40) = 10
    assert not decide(holdout_incumbent=inc, holdout_candidate=cand).adopt
    cand = {**inc, **{f"h{i}": True for i in range(10)}}
    assert decide(holdout_incumbent=inc, holdout_candidate=cand).adopt


def test_a_shuffled_label_control_fails_gate_a():
    """The instrument check: a model trained on shuffled labels answers the held-out failures no better than chance - here it gets
    exactly what the incumbent got, plus noise that breaks two."""
    cand = {**holdout(), "h8": False, "h9": False}
    assert decide(holdout_candidate=cand).checks["fixes_known_failures"] is False


def test_an_incumbent_with_no_misses_leaves_nothing_to_prove():
    allright = {f"h{i}": True for i in range(12)}
    assert decide(holdout_incumbent=allright, holdout_candidate=allright).checks["fixes_known_failures"] is False


def test_too_few_held_out_failures_is_not_enough_evidence():
    small_inc = {f"h{i}": i >= 6 for i in range(9)}
    d = decide(holdout_incumbent=small_inc, holdout_candidate={f"h{i}": True for i in range(9)})
    assert not d.adopt and d.checks["holdout_enough"] is False


def test_hard_gates_each_refuse():
    assert decide(candidate=rs.EvalScore(91.4, 1.2, 400.0, 81)).checks["chat_fp_zero"] is False
    assert decide(candidate=rs.EvalScore(91.4, 0.0, 700.0, 81)).checks["p50_under_budget"] is False
    assert decide(candidate=rs.EvalScore(91.4, 0.0, 400.0, 80)).checks["corpus_intact"] is False
    assert not decide(replay=rs.ReplayVerdict(False, False, "skipped")).adopt          # a skip is NOT a pass
    assert not decide(replay=rs.ReplayVerdict(True, False, "said-vs-did regressed")).adopt
    assert decide(forget_clean=False).checks["forget_clean"] is False
    mismatched = {f"x{i}": True for i in range(12)}
    assert not decide(holdout_candidate=mismatched).adopt                                 # different cases are not comparable


def test_the_gate_takes_no_override_and_the_script_has_no_force_flag():
    assert set(inspect.signature(rr.decide_adoption).parameters) == {
        "holdout_incumbent", "holdout_candidate", "golden_incumbent", "golden_candidate", "incumbent", "candidate", "replay",
        "forget_clean"}
    with pytest.raises(SystemExit):
        rr.main(["--force-adopt"])
    for name in dir(rr):
        assert "force" not in name.lower() or name.startswith("__")


def test_the_command_line_can_only_make_a_run_safer():
    assert rr.feed_mode({}) == "off" and rr.feed_mode({rr.FLAG: "shadow"}) == "shadow" and rr.feed_mode({rr.FLAG: "on"}) == "on"
    assert rr.feed_mode({rr.FLAG: "bogus"}) == "off"
    assert rr.effective_mode("on", cli_shadow=True) == "shadow" and rr.effective_mode("off", cli_shadow=False) == "off"


# ── collect + the time split ─────────────────────────────────────────────────
NORM = rs._normalize


def row(i, text, label="weather", ts=None, source="followup", status="proposed", by="", user="member-a"):
    return {"event_id": f"e{i}", "user_id": user, "text": text, "label": label, "label_source": source, "status": status,
            "decided_by": by, "ts": 1_000_000.0 + i * 3600 if ts is None else ts, "proposal_id": f"p{i % 3}"}


def test_only_strong_labels_are_collected():
    rows = [row(1, "will it be windy at noon"),
            row(2, "is it going to pour later", source="router", status="proposed"),
            row(3, "is the brolly needed tomorrow", source="router", status="accepted", by=rr.POLICY_DECIDER),
            row(4, "how cold does it get tonight", source="router", status="accepted", by="admin-1")]
    ex, dropped = rr.select_examples(rows, tombstones={}, frozen=set(), normalize=NORM)
    assert [e.event_id for e in ex] == ["e1", "e4"] and dropped["weak_label"] == 2      # a person's accept is an owner correction


def test_the_frozen_corpus_and_bad_labels_never_enter():
    frozen = rs.frozen_corpus_texts(rs.FROZEN_CORPUS)
    a_case = json.loads(rs.FROZEN_CORPUS.read_text().splitlines()[0])["text"]
    ex, dropped = rr.select_examples([row(1, a_case), row(2, "sing me something mellow", label="chat"), row(3, "")],
                                     tombstones={}, frozen=frozen, normalize=NORM)
    assert ex == [] and dropped == {"frozen_corpus": 1, "bad_label": 1, "no_text": 1}


def _examples(n, label="weather"):
    rows = [row(i, f"invented weather ask number {i} please", label=label) for i in range(n)]
    return rr.select_examples(rows, tombstones={}, frozen=set(), normalize=NORM)[0]


def test_time_split_holds_out_the_newest_and_never_leaks():
    ex = _examples(50)
    train, hold, info = rr.group_and_split(ex, trained_keys=set())
    assert len(hold) == 10 and len(train) == 40 and info["holdout_cutoff_ts"] is not None
    assert max(e.ts for e in train) < min(e.ts for e in hold)                         # every held-out failure is newer
    assert not {e.text_key for e in train} & {e.text_key for e in hold}
    assert rr.evidence_shortfall(len(train), len(hold)) is None


def test_respellings_of_one_request_share_a_side_and_conflicts_are_dropped():
    rows = [row(1, "What's the weather tomorrow?"), row(2, "what is the weather tomorrow"),           # one wording, two spellings
            row(3, "turn the porch thing on", label="smart_home"), row(4, "turn the porch thing on", label="music")]
    ex = rr.select_examples(rows, tombstones={}, frozen=set(), normalize=NORM)[0]
    train, hold, info = rr.group_and_split(ex, trained_keys=set())
    assert info["wordings"] == 1 and info["duplicates_folded"] == 1 and info["conflicting_label"] == 2
    assert len(train) + len(hold) == 1


def test_a_wording_a_served_generation_learned_is_never_used_to_prove_it():
    ex = _examples(50)
    newest = max(ex, key=lambda e: e.ts)
    train, hold, _ = rr.group_and_split(ex, trained_keys={newest.text_key})
    assert newest.text_key not in {e.text_key for e in hold} and newest.text_key in {e.text_key for e in train}


def test_too_little_data_says_how_much_is_needed():
    train, hold, _ = rr.group_and_split(_examples(12), trained_keys=set())
    why = rr.evidence_shortfall(len(train), len(hold))
    assert why and "not enough evidence" in why and "~50 needed" in why


def test_holdout_cases_carry_no_words_in_their_ids_and_score_by_domain():
    ex = _examples(1, label="lists")
    case = rr.holdout_cases(ex)[0]
    assert case["id"] == f"h-{ex[0].text_key}" and "invented" not in case["id"]
    assert set(case["expected"]) == set(rr.DOMAIN_TOOLS["lists"])


def test_train_rows_repeat_the_failures_and_add_a_replay_sample():
    ex = _examples(3)
    for e in ex:
        e.tool, e.args = "get_weather", {}
    replay = rr.replay_sample([{"text": f"r{i}", "tool": None, "args": {}} for i in range(500)], 300, "stamp1")
    assert replay == rr.replay_sample([{"text": f"r{i}", "tool": None, "args": {}} for i in range(500)], 300, "stamp1")   # deterministic
    rows = rr.train_rows(ex, replay, repeat=2)
    assert len(rows) == 3 * 2 + 300 and rows[0]["source"] == "struggle-followup"


# ── forget ───────────────────────────────────────────────────────────────────
def _record(gen=None, status="shadow", examples=()):
    rec = {"stamp": "s", "status": status, "examples": list(examples)}
    if gen:
        rec["gen"] = gen

    def _add(d):
        d["runs"].append(rec)
        if status == "adopted":
            d["previous"], d["served"] = d.get("served"), gen
    ledger.update(_add)


def test_forget_removes_the_example_tombstones_it_and_flags_a_served_generation():
    ex = _examples(3)
    _record(gen="g1", status="adopted", examples=[ex[0].ledger_row("train"), ex[1].ledger_row("holdout")])
    _record(status="shadow", examples=[ex[2].ledger_row("train")])
    res = ledger.forget(event_ids=[ex[0].event_id])
    data = ledger.load()
    g1 = next(r for r in data["runs"] if r.get("gen") == "g1")
    assert res["examples"] == 1 and res["flagged"] == 1 and g1["needs_retrain"] is True and g1["forgotten"] == 1
    assert ex[0].event_id not in json.dumps(data["runs"])                              # the record no longer names it
    assert ex[0].text_key in data["tombstones"]["text_keys"] and ledger.status()["served_needs_retrain"] is True


def test_forgetting_a_held_out_example_does_not_flag_retrain():
    ex = _examples(2)
    _record(gen="g1", status="adopted", examples=[ex[0].ledger_row("holdout")])
    assert ledger.forget(event_ids=[ex[0].event_id])["flagged"] == 0                  # scored, never learned


def test_a_forgotten_example_never_reenters():
    ex = _examples(1)
    _record(examples=[ex[0].ledger_row("train")])
    ledger.forget(event_ids=[ex[0].event_id])
    again = [row(77, ex[0].text)]                                                     # the same words, said again later: a new event
    kept, dropped = rr.select_examples(again, tombstones=ledger.load()["tombstones"], frozen=set(), normalize=NORM)
    assert kept == [] and dropped["forgotten"] == 1


def test_a_user_delete_tombstones_every_example_of_that_user():
    ex = _examples(2)
    _record(examples=[e.ledger_row("train") for e in ex])
    ledger.forget(user_id="member-a")
    kept, dropped = rr.select_examples([row(9, "a brand new weather ask")], tombstones=ledger.load()["tombstones"], frozen=set(),
                                       normalize=NORM)
    assert kept == [] and dropped["forgotten"] == 1


def test_forget_deletes_a_runs_leftover_words(tmp_path):
    ex = _examples(1)
    _record(examples=[ex[0].ledger_row("train")])
    work = ledger.root() / ledger.WORK / "s1"
    work.mkdir(parents=True)
    (work / ledger.IDS_FILE).write_text(json.dumps({"event_ids": [ex[0].event_id], "user_keys": []}))
    (work / f"train{ledger.TEXT_SUFFIX}").write_text("invented words\n")
    other = ledger.root() / ledger.WORK / "s2"
    other.mkdir(parents=True)
    (other / ledger.IDS_FILE).write_text(json.dumps({"event_ids": ["unrelated"], "user_keys": []}))
    (other / f"train{ledger.TEXT_SUFFIX}").write_text("other words\n")
    assert ledger.forget(event_ids=[ex[0].event_id])["texts_deleted"] == 1
    assert not list(work.glob("*" + ledger.TEXT_SUFFIX)) and list(other.glob("*" + ledger.TEXT_SUFFIX))


def test_a_forget_that_lands_mid_run_blocks_adoption():
    ex = _examples(2)
    rows = [e.ledger_row("train") for e in ex]
    _record(examples=[])                     # the ledger exists (a forget with no ledger dir is "nothing stored")
    ledger.forget(event_ids=[ex[1].event_id])
    assert ledger.tombstoned(rows) == [rows[1]]          # what run() feeds the gate as forget_clean = False
    rr._record({"stamp": "mid", "status": "shadow", "examples": rows})
    mid = next(r for r in ledger.load()["runs"] if r.get("stamp") == "mid")
    assert mid["examples"] == [rows[0]] and mid["forgotten"] == 1         # the record never names the forgotten turn


def test_a_forget_before_any_run_still_tombstones_and_a_corrupt_ledger_fails_closed():
    assert not ledger.root().exists()
    assert ledger.forget(event_ids=["e1"], text_keys=["k1"]) == {"examples": 0, "flagged": 0, "texts_deleted": 0}
    assert {"e1"} <= set(ledger.load()["tombstones"]["event_ids"]) and "k1" in ledger.load()["tombstones"]["text_keys"]
    (ledger.root() / ledger.LEDGER).write_text("{not json")
    with pytest.raises(ValueError):
        ledger.forget(event_ids=["e1"])


def test_a_turn_forgotten_before_its_first_run_never_reenters_by_wording():
    """Codex #4: the forget path hands the wording key over while the struggle row still exists, so a pre-record forget tombstones the
    WORDING, not only the event id - the same words under a new event id stay out."""
    ex = _examples(1)[0]
    ledger.forget(event_ids=[ex.event_id], text_keys=[ex.text_key])            # no run has recorded it yet
    kept, dropped = rr.select_examples([row(500, ex.text)], tombstones=ledger.load()["tombstones"], frozen=set(), normalize=NORM)
    assert kept == [] and dropped["forgotten"] == 1


def test_the_ledger_never_holds_words():
    ex = _examples(3)
    _record(examples=[e.ledger_row("train") for e in ex])
    raw = (ledger.root() / ledger.LEDGER).read_text()
    assert "invented" not in raw and "weather ask" not in raw
    assert oct((ledger.root() / ledger.LEDGER).stat().st_mode)[-3:] == "600"


# ── shadow never swaps; rollback ──────────────────────────────────────────────
def _fake_gguf(path: Path, tag: bytes) -> Path:
    path.write_bytes(b"GGUF" + tag + b"\0" * 64)
    return path


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Every live path router_selftrain's deploy / restore touches, redirected (the 9-byte-file incident: patch the WHOLE set)."""
    archive = tmp_path / "archive"
    archive.mkdir()
    monkeypatch.setattr(rs, "SERVED_GGUF", tmp_path / "served.gguf")
    monkeypatch.setattr(rs, "ARCHIVE_DIR", archive)
    monkeypatch.setattr(rs, "DEPLOY_MARKER", tmp_path / "deploy_in_progress.json")
    monkeypatch.setattr(rs, "PROVENANCE", tmp_path / "provenance.json")
    monkeypatch.setattr(rs, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(rs, "MIN_GGUF_BYTES", 16)
    monkeypatch.setattr(rs, "systemctl", lambda *a, **k: None)
    monkeypatch.setattr(rs, "restart_and_verify_sidecar", lambda expect: True)
    monkeypatch.setattr(rs, "prune_archive", lambda: None)
    monkeypatch.setattr(rs, "run_eval", lambda *a, **k: INC)          # the post-deploy LIVE eval: no sidecar in a test
    return tmp_path


def test_shadow_never_swaps(sandbox, monkeypatch):
    _fake_gguf(rs.SERVED_GGUF, b"INCUMBENT")
    calls = []
    monkeypatch.setattr(rs, "deploy", lambda *a, **k: calls.append(a) or 0)
    passing = decide()
    assert passing.adopt
    rec = {"stamp": "s1"}
    status = rr.act("shadow", passing, rec, stamp="s1", cand_gguf=_fake_gguf(sandbox / "cand.gguf", b"CAND"), run_dir=sandbox / "run",
                    inc_live=INC, train=[], journal={})
    assert status == "shadow" and calls == [] and rec["outcome"] == "shadow: WOULD ADOPT"
    assert rs.SERVED_GGUF.read_bytes()[:13] == b"GGUFINCUMBENT" and ledger.load()["served"] is None


def test_on_with_a_failed_gate_never_swaps(sandbox, monkeypatch):
    calls = []
    monkeypatch.setattr(rs, "deploy", lambda *a, **k: calls.append(a) or 0)
    failing = decide(replay=rs.ReplayVerdict(False, False, "skipped"))
    assert rr.act("on", failing, {}, stamp="s1", cand_gguf=sandbox / "c.gguf", run_dir=sandbox / "run", inc_live=INC, train=[],
                  journal={}) == "rejected" and calls == []


def test_on_with_every_gate_passed_adopts_and_keeps_the_generation(sandbox, monkeypatch):
    _fake_gguf(rs.SERVED_GGUF, b"INCUMBENT")
    cand = _fake_gguf(sandbox / "cand.gguf", b"CANDIDATE")
    run_dir = sandbox / "run"
    (run_dir / "merged").mkdir(parents=True)
    monkeypatch.setattr(rr, "_apply_proposals", lambda gen, train: {"applied": 0})
    rec = {"stamp": "s1", "examples": []}
    status = rr.act("on", decide(), rec, stamp="s1", cand_gguf=cand, run_dir=run_dir, inc_live=INC, train=[], journal={})
    data = ledger.load()
    assert status == "adopted" and data["served"] == "gs1" and data["previous"] is None
    assert rs.SERVED_GGUF.read_bytes()[:13] == b"GGUFCANDIDATE"                        # the real deploy() swapped the file
    gen = next(r for r in data["runs"] if r.get("gen") == "gs1")
    assert Path(gen["merged"]).exists() and Path(gen["lkg"]).exists()                  # the lineage + the instant-rollback copy


def test_rollback_restores_the_previous_generation(sandbox, monkeypatch):
    _fake_gguf(rs.SERVED_GGUF, b"INCUMBENT")
    run_dir = sandbox / "run"
    (run_dir / "merged").mkdir(parents=True)
    monkeypatch.setattr(rr, "_apply_proposals", lambda gen, train: {})
    rr.act("on", decide(), {"stamp": "s1"}, stamp="s1", cand_gguf=_fake_gguf(sandbox / "cand.gguf", b"CANDIDATE"), run_dir=run_dir,
           inc_live=INC, train=[], journal={})
    assert rs.SERVED_GGUF.read_bytes()[:13] == b"GGUFCANDIDATE"
    assert rr.rollback() == 0
    data = ledger.load()
    assert rs.SERVED_GGUF.read_bytes()[:13] == b"GGUFINCUMBENT"                        # the model that served before it, back
    assert data["served"] is None and next(r for r in data["runs"] if r.get("gen") == "gs1")["status"] == "rolled_back"
    assert rr.rollback() == 1                                                          # nothing of this loop is served any more


def test_a_failed_post_deploy_check_is_not_a_generation(sandbox, monkeypatch):
    monkeypatch.setattr(rs, "deploy", lambda *a, **k: 1)
    rec = {"stamp": "s1"}
    assert rr.act("on", decide(), rec, stamp="s1", cand_gguf=sandbox / "c.gguf", run_dir=sandbox / "run", inc_live=INC, train=[],
                  journal={}) == "rolled_back_at_deploy"
    assert ledger.load()["served"] is None


# ── where and when it may run ─────────────────────────────────────────────────
def test_window_conflicts():
    assert rr.window_conflict(datetime(2026, 10, 19, 1, 0), 90) is None                # 01:00-02:30
    assert "02:45-03:15" in rr.window_conflict(datetime(2026, 10, 19, 1, 30), 90)      # ... 03:00 overlaps the night window
    assert "04:00-04:55" in rr.window_conflict(datetime(2026, 10, 19, 4, 30), 10)
    assert rr.window_conflict(datetime(2026, 10, 19, 5, 0), 120) is None
    assert "02:45-03:15" in rr.window_conflict(datetime(2026, 10, 19, 23, 30), 240)    # runs past midnight into tomorrow's window


def test_memory_plan_never_trains_into_the_floor_with_the_voice_stack_up():
    may, stop, why = rr.memory_plan(2054, 4000, stop_brain_allowed=False)             # tonight's measurement, voice stack up
    assert not may and not stop and "not training now" in why
    assert rr.memory_plan(2054, 4000, stop_brain_allowed=True)[:2] == (True, True)
    assert rr.memory_plan(6000, 4000, stop_brain_allowed=False)[:2] == (True, False)


def test_recover_never_starts_a_brain_this_loop_did_not_stop(sandbox, monkeypatch):
    started = []
    monkeypatch.setattr(rr, "_restore_brain", lambda: started.append(1) or True)
    assert rr.recover() == 0 and started == []
    rr.brain_marker().parent.mkdir(parents=True, exist_ok=True)
    rr.brain_marker().write_text("{}")
    monkeypatch.setattr(rr, "unit_active", lambda unit: unit == rr.NIGHT_UNIT)
    assert rr.recover() == 0 and started == []                                          # the night window owns the brain now
    monkeypatch.setattr(rr, "unit_active", lambda unit: False)
    assert rr.recover() == 0 and started == [1]


def test_estimates_use_the_measured_run_once_there_is_one():
    assert rr.estimates({"runs": []}) == (rr.DEFAULT_EST_PEAK_MB, rr.DEFAULT_EST_MINUTES)
    peak, mins = rr.estimates({"runs": [{"measure": {"peak_rss_mb": 3000, "total_minutes": 50}}]})
    assert math.isclose(peak, 3450) and math.isclose(mins, 60)


def test_a_forget_just_before_the_swap_still_stops_it(sandbox, monkeypatch):
    calls = []
    monkeypatch.setattr(rs, "deploy", lambda *a, **k: calls.append(a) or 0)
    ex = _examples(1)
    _record(examples=[])
    ledger.forget(event_ids=[ex[0].event_id])                     # lands after decide_adoption said forget_clean
    rec = {"stamp": "s1", "examples": [ex[0].ledger_row("train")]}
    assert rr.act("on", decide(), rec, stamp="s1", cand_gguf=sandbox / "c.gguf", run_dir=sandbox / "run", inc_live=INC, train=[],
                  journal={}) == "rejected" and calls == []


# ── the replay gate replays the CANDIDATE, into its own artifact ────────────────
class _Sidecar:
    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True

    def wait(self):
        return 0


def test_the_replay_gate_points_the_probe_at_the_candidate_and_never_at_the_shared_artifact(tmp_path, monkeypatch):
    seen = {}
    side = _Sidecar()
    results = tmp_path / "replay.json"

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        results.write_text(json.dumps({"status": "pass", "summary": {"n_samples": 20, "ok_rate": 1.0}}))
        return type("P", (), {"returncode": 0, "stdout": "probe ok", "stderr": ""})()
    monkeypatch.setattr(rs, "http_ok", lambda url, timeout=5: True)
    monkeypatch.setattr(rs, "_start_scratch_sidecar", lambda gguf, port: seen.update(sidecar=(str(gguf), port)) or side)
    monkeypatch.setattr(rs.subprocess, "run", fake_run)
    v = rs.replay_gate({}, candidate_gguf=tmp_path / "cand.gguf", results=results, trend=tmp_path / "trend.jsonl")
    assert v.ran and v.passed and side.killed
    assert seen["sidecar"] == (str(tmp_path / "cand.gguf"), rs.SCRATCH_PORT)
    assert seen["env"]["ZOE_ROUTER_SIDECAR_URL"] == f"http://127.0.0.1:{rs.SCRATCH_PORT}"
    cmd = seen["cmd"]
    assert cmd[cmd.index("--results") + 1] == str(results) and "voice_regression_last.json" not in " ".join(cmd)
    assert cmd[:2] == ["flock", "/tmp/zoe-voice-harness.lock"]
    assert "voice_regression_last" not in str(rs.REPLAY_RESULTS)                       # the default is the loop's own file too


def test_a_candidate_sidecar_that_cannot_start_is_did_not_run(tmp_path, monkeypatch):
    def boom(gguf, port):
        raise SystemExit("ABORT: something already listens on :11437")
    monkeypatch.setattr(rs, "http_ok", lambda url, timeout=5: True)
    monkeypatch.setattr(rs, "_start_scratch_sidecar", boom)
    monkeypatch.setattr(rs.subprocess, "run", lambda *a, **k: pytest.fail("the probe must not run without the candidate"))
    v = rs.replay_gate({}, candidate_gguf=tmp_path / "c.gguf", results=tmp_path / "r.json")
    assert not v.ran and not v.passed


def test_the_probe_accepts_the_results_and_trend_flags():
    src = (REPO / "scripts" / "maintenance" / "voice_regression_probe.py").read_text()
    assert 'ap.add_argument("--results"' in src and 'ap.add_argument("--trend"' in src


def test_held_out_eval_results_keep_ids_and_verdicts_only(tmp_path, monkeypatch):
    out = tmp_path / "h.json"

    def fake_eval(gguf, port, out_path, launch, label, corpus=None):
        out_path.write_text(json.dumps({"summary": {}, "cases": [
            {"id": "h-abc", "style": "struggle", "text": "invented household words", "expected": ["get_weather"],
             "predicted": "get_weather", "source": "two_stage", "args": {"location": "invented place"}, "ok": True}]}))
        return INC
    monkeypatch.setattr(rs, "run_eval", fake_eval)
    _, payload = rr._eval(tmp_path / "g.gguf", 1, out, True, "x", corpus=tmp_path / "c.jsonl")
    raw = out.read_text()
    assert "invented" not in raw and payload["cases"][0]["ok"] is True and payload["cases"][0]["id"] == "h-abc"


# ── Codex round (2026-10-19): lineage, replay leakage, the swap race, transactional publication, a real shuffled-label control ──
def _gen(gen, parent, keys, status="adopted"):
    def _add(d):
        d["runs"].append({"gen": gen, "parent": parent, "status": status,
                          "examples": [{"e": f"e-{k}", "u": "u", "k": k, "s": "train"} for k in keys]})
    ledger.update(_add)


def test_the_whole_warm_start_lineage_counts_as_learned():
    """Codex #1: gC was warm-started from gB, which was warm-started from gA. Whatever gA learned, gC holds - so gA's wordings can
    never be held out as "unseen", even though gA is neither served nor previous."""
    ex = _examples(50)
    oldest_learned = max(ex, key=lambda e: e.ts)                              # the newest wording, which a fresh split would hold out
    _gen("gA", None, [oldest_learned.text_key])
    _gen("gB", "gA", ["kB"])
    _gen("gC", "gB", ["kC"])
    ledger.update(lambda d: d.update(served="gC", previous="gB"))
    keys = rr.lineage_trained_keys(ledger.load())
    assert {oldest_learned.text_key, "kB", "kC"} <= keys
    _, hold, _ = rr.group_and_split(ex, trained_keys=keys)
    assert oldest_learned.text_key not in {e.text_key for e in hold}


def test_forgetting_an_ancestors_example_flags_the_served_descendant():
    _gen("gA", None, ["kA"])
    _gen("gB", "gA", ["kB"])
    ledger.update(lambda d: d.update(served="gB", previous="gA"))
    ledger.forget(event_ids=["e-kA"])
    runs = {r["gen"]: r for r in ledger.load()["runs"]}
    assert runs["gA"]["needs_retrain"] and runs["gB"]["needs_retrain"]          # gB inherited what gA learned
    assert ledger.status()["served_needs_retrain"] is True


def test_a_held_out_wording_in_the_committed_corpus_is_never_trained_or_held_out():
    """Codex #2: the replay sample came from committed files nobody checked against the holdout."""
    ex = _examples(50)
    train, hold, _ = rr.group_and_split(ex, trained_keys=set())
    for e in train + hold:
        e.tool = "get_weather"
    committed = [{"text": hold[0].text, "tool": "get_weather", "args": {}}] + [{"text": f"committed row {i}", "tool": None, "args": {}}
                                                                               for i in range(40)]
    rows, dropped = rr.prepare_training(train, hold, committed, "s", n_replay=1000)
    assert dropped == 1 and rr.leaked(rows, hold) == []
    assert hold[0].text_key in rr.lineage_trained_keys({"runs": [], "tombstones": {}}, committed)   # and the split would not hold it out


def test_a_forget_during_the_swap_rolls_the_candidate_back(sandbox, monkeypatch):
    """Codex #3: the forget lands AFTER the reservation, while the model is being swapped in. The reservation is a ledger record the
    forget flags; the commit sees the flag and the previous model goes back."""
    _fake_gguf(rs.SERVED_GGUF, b"INCUMBENT")
    run_dir = sandbox / "run"
    (run_dir / "merged").mkdir(parents=True)
    ex = _examples(1)[0]
    real_deploy = rs.deploy

    def deploy_while_forgetting(*a, **k):
        rc = real_deploy(*a, **k)
        ledger.forget(event_ids=[ex.event_id])
        return rc
    monkeypatch.setattr(rs, "deploy", deploy_while_forgetting)
    monkeypatch.setattr(rr, "_apply_proposals", lambda gen, train: pytest.fail("a rolled-back candidate applies nothing"))
    rec = {"stamp": "s1", "examples": [ex.ledger_row("train")]}
    status = rr.act("on", decide(), rec, stamp="s1", cand_gguf=_fake_gguf(sandbox / "cand.gguf", b"CANDIDATE"), run_dir=run_dir,
                    inc_live=INC, train=[], journal={})
    data = ledger.load()
    assert status == "rolled_back_at_publish" and rs.SERVED_GGUF.read_bytes()[:13] == b"GGUFINCUMBENT"
    assert data["served"] is None and not rr.adopt_marker().exists()
    assert not (ledger.root() / "generations" / "gs1").exists()                # never a generation: it learned a forgotten turn


def test_a_publication_failure_after_the_swap_rolls_back(sandbox, monkeypatch):
    """Codex #5: the checkpoint move fails after deploy() swapped the model - the candidate must not stay served unrecorded."""
    _fake_gguf(rs.SERVED_GGUF, b"INCUMBENT")
    run_dir = sandbox / "run"
    (run_dir / "merged").mkdir(parents=True)

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(rr.shutil, "move", boom)
    with pytest.raises(OSError):
        rr.act("on", decide(), {"stamp": "s1"}, stamp="s1", cand_gguf=_fake_gguf(sandbox / "cand.gguf", b"CANDIDATE"), run_dir=run_dir,
               inc_live=INC, train=[], journal={})
    data = ledger.load()
    assert rs.SERVED_GGUF.read_bytes()[:13] == b"GGUFINCUMBENT" and data["served"] is None
    assert next(r for r in data["runs"] if r.get("gen") == "gs1")["status"] == "rolled_back_at_publish"
    assert not rr.adopt_marker().exists()


def test_recover_rolls_back_a_publication_killed_before_its_commit(sandbox, monkeypatch):
    """The process dies after the swap and before the ledger commit (no except clause runs on SIGKILL): the durable marker is all
    that is left, and --recover puts the previous model back."""
    _fake_gguf(rs.SERVED_GGUF, b"INCUMBENT")
    run_dir = sandbox / "run"
    (run_dir / "merged").mkdir(parents=True)
    real_unpublish, real_update = rr._unpublish, rr.ledger.update
    monkeypatch.setattr(rr, "_unpublish", lambda *a, **k: False)            # "killed" before it could undo anything
    monkeypatch.setattr(rr.ledger, "update", _fail_on_commit(real_update))
    with pytest.raises(KeyboardInterrupt):
        rr.act("on", decide(), {"stamp": "s1"}, stamp="s1", cand_gguf=_fake_gguf(sandbox / "cand.gguf", b"CANDIDATE"), run_dir=run_dir,
               inc_live=INC, train=[], journal={})
    assert rs.SERVED_GGUF.read_bytes()[:13] == b"GGUFCANDIDATE" and rr.adopt_marker().exists()
    monkeypatch.setattr(rr, "_unpublish", real_unpublish)                     # a fresh process: --recover
    monkeypatch.setattr(rr.ledger, "update", real_update)
    assert rr.recover() == 0
    assert rs.SERVED_GGUF.read_bytes()[:13] == b"GGUFINCUMBENT" and not rr.adopt_marker().exists()
    assert ledger.load()["served"] is None


def _fail_on_commit(real):
    def update(fn):
        if fn.__name__ == "_commit":
            raise KeyboardInterrupt("killed mid-publication")
        return real(fn)
    return update


# A REAL shuffled-label control through the data path: the seam rows -> select -> time split -> training rows -> a tiny bag-of-words
# "trainer" that learns ONLY from the rows prepare_training wrote -> the held-out failures scored by domain -> the gate.
_VOCAB = {
    "weather": ["umbrella", "forecast", "drizzle", "sunny"], "timers": ["countdown", "egg", "kettle", "stopwatch"],
    "lists": ["groceries", "basket", "shopping", "pantry"], "music": ["playlist", "song", "jazz", "tune"],
    "calendar": ["appointment", "meeting", "diary", "dentist"], "smart_home": ["lamp", "heater", "porch", "fan"],
    "reminders": ["remind", "nudge", "prompt", "memo"], "time": ["clock", "oclock", "hour", "midnight"],
}


def _seam_rows(n=200):
    labels = sorted(_VOCAB)
    out = []
    for i in range(n):
        lab = labels[i % len(labels)]
        w = _VOCAB[lab]
        out.append(row(i, f"{w[i % 4]} {w[(i // 8) % 4]} request {i}", label=lab))
    return out


def _stub_train(rows):
    from collections import Counter, defaultdict
    model = defaultdict(Counter)
    for r in rows:
        for w in r["text"].split():
            model[w][r["tool"]] += 1
    return model


def _stub_predict(model, text):
    from collections import Counter
    votes = Counter()
    for w in text.split():
        votes.update(model.get(w, {}))
    return votes.most_common(1)[0][0] if votes else None


def _pipeline(shuffle: bool):
    ex, _ = rr.select_examples(_seam_rows(), tombstones={}, frozen=set(), normalize=NORM)
    train, hold, _ = rr.group_and_split(ex, trained_keys=set())
    for e in train + hold:
        e.tool = rr.DOMAIN_TOOLS[e.label][0]                                    # the oracle agreed with the label it was given
    if shuffle:                                                               # the CONTROL: the training labels, and only those, shuffled
        import random
        tools = [e.tool for e in train]
        random.Random(7).shuffle(tools)
        for e, t in zip(train, tools):
            e.tool = t
    rows, _ = rr.prepare_training(train, hold, [], "s", n_replay=0)
    model = _stub_train(rows)
    cases = rr.holdout_cases(hold)
    cand = {c["id"]: _stub_predict(model, c["text"]) in c["expected"] for c in cases}
    inc = {c["id"]: False for c in cases}                                      # these are failures: the incumbent got every one wrong
    return decide(holdout_incumbent=inc, holdout_candidate=cand)


def test_a_real_shuffled_label_control_fails_gate_a_through_the_pipeline():
    """Codex non-blocking #1. With the true labels the stub trained on the pipeline's own rows fixes the held-out failures and passes;
    trained on SHUFFLED labels it cannot - so a pipeline that leaked the held-out labels, or a gate that ignored them, would show."""
    real = _pipeline(shuffle=False)
    control = _pipeline(shuffle=True)
    assert real.adopt and real.numbers["fixed"] >= 30
    assert not control.adopt and control.checks["fixes_known_failures"] is False
    assert control.numbers["fixed"] < control.numbers["needed"]


# ── Codex round 2 (2026-10-19) ─────────────────────────────────────────────────
def test_a_forget_by_wording_alone_deletes_a_running_runs_words():
    """An older event is forgotten while a run holds the SAME wording under a new event id and user: the wording key in ids.json is
    what lets the forget delete those words at once (not at the run's own cleanup)."""
    ex = _examples(1)[0]
    work = ledger.root() / ledger.WORK / "s9"
    work.mkdir(parents=True)
    (work / ledger.IDS_FILE).write_text(json.dumps({"event_ids": ["e-new"], "user_keys": ["u-other"], "text_keys": [ex.text_key]}))
    (work / f"train{ledger.TEXT_SUFFIX}").write_text("invented words\n")
    assert ledger.forget(event_ids=["e-old"], text_keys=[ex.text_key])["texts_deleted"] == 1
    assert not list(work.glob("*" + ledger.TEXT_SUFFIX))


def test_the_adoption_marker_is_durable_before_the_swap(sandbox, monkeypatch):
    """The marker is the only thing a host crash after the swap leaves: file AND directory are fsynced before deploy() runs."""
    import os as _os
    events = []
    real_fsync = _os.fsync
    monkeypatch.setattr(_os, "fsync", lambda fd: events.append("fsync") or real_fsync(fd))

    def deploy(*a, **k):
        events.append("deploy:marker" if rr.adopt_marker().exists() else "deploy:no-marker")
        return 1
    monkeypatch.setattr(rs, "deploy", deploy)
    rr.act("on", decide(), {"stamp": "s1"}, stamp="s1", cand_gguf=sandbox / "c.gguf", run_dir=sandbox / "run", inc_live=INC,
           train=[], journal={})
    assert events[:3] == ["fsync", "fsync", "deploy:marker"]
