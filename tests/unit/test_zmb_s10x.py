"""ZMB S10x (quote-backed retirement): the data, the cells, the live-tier driver and its instrument check.

The store-tier cells run (and are proven red under their controls) in ``services/zoe-data/tests/test_zmb_lab.py`` with the rest of the spec;
this file pins what is specific to S10x: the pilot's data shapes, that every cell is declared the way the bake-off needs it (Z0-only, its own
axis, a control that names a real feature), that the live-tier cells are exactly the bars the driver scores, and that the driver is an
instrument: a perfect judge passes every bar and the naive rule (retrieval's top-1, no judgement) fails them.

Synthetic data only; no network, no model (the judges are scripted), no Postgres.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import cells as cellmod, lab_driver, s10x_data, s10x_live, spec  # noqa: E402

CELLS = {c.id: c for c in spec.load_cells()}
S10X = [c for c in CELLS.values() if c.id.startswith("S10x.")]


# ── the data ──────────────────────────────────────────────────────────────────

def test_the_pilots_shapes_are_all_there():
    assert len(s10x_data.PAIRS) == 30 and len(s10x_data.NON_CHANGES) == 30 and len(s10x_data.HARD_NON_CHANGES) == 10
    assert len(s10x_data.GENERIC) == 40 and len(s10x_data.pool()) == 100 and len(set(s10x_data.pool())) == 100
    assert len(s10x_data.other_person_copies()) == 30
    assert all("Priya" in c for c in s10x_data.other_person_copies())
    assert all(o != c for o, c in zip((p[0] for p in s10x_data.PAIRS), s10x_data.other_person_copies()))


def test_the_newest_rows_of_the_pool_are_the_copies():
    """So a store whose retrieval is broken (newest first) offers the copies, which is what lets the copy cell go red."""
    pool = s10x_data.pool()
    assert pool[-30:] == s10x_data.other_person_copies() and pool[:40] == s10x_data.GENERIC


def test_every_old_row_names_its_object_and_every_change_names_the_same_object():
    import re
    stop = {"the", "a", "my", "and", "i", "to", "of", "in", "on", "at", "we", "it", "is", "up", "me", "now", "again", "off", "out"}
    shared = 0
    for old, say, _fact in s10x_data.PAIRS:
        a = {w for w in re.findall(r"[a-z]+", old.lower()) if w not in stop and len(w) > 3}
        b = {w for w in re.findall(r"[a-z]+", say.lower()) if w not in stop and len(w) > 3}
        shared += bool(a & b)
    assert shared >= 18      # most pairs share an object word; the rest ("I switched to tea" / coffee) are what retrieval has to bridge


# ── the cells ─────────────────────────────────────────────────────────────────

def test_the_s10x_cells_are_declared_the_way_the_bakeoff_needs_them():
    store = [c for c in S10X if c.tier == "store"]
    live = [c for c in S10X if c.tier == "full"]
    assert len(store) == 15 and len(live) == 7
    assert all(c.axis == "retirement" for c in S10X)                # its own axis: the temporal axis the decision rule compares is untouched
    assert all(cellmod.z0_only(c) for c in store)                   # every one needs quote_retire: Z0 only, SKIP on every other arm
    assert {c.id for c in store if c.sanity} == {"S10x.verified_voice_retires"}
    for c in store:
        assert c.sanity or c.controls, f"{c.id}: a store cell with no control cannot support a claim"
        assert set(c.controls) <= set(lab_driver.CONTROLS)
        assert all(k.startswith("retire_") or k == "retrieval" for k in c.controls)
    assert all(c.skip_reason and "clone brain" in c.skip_reason for c in live)


def test_the_nine_axes_of_the_decision_rule_are_unchanged():
    assert {"authority", "extraction", "temporal", "recall", "abstention", "forgetting", "emotional", "identity", "poisoning"} <= set(spec.AXES.values())
    assert spec.AXES["r"] == "retirement"
    from zmb import bakeoff_gates
    assert bakeoff_gates.WIN_AXES == {"B": "extraction", "C": "temporal", "D": "recall", "E": "abstention"}     # S10x is on none of them
    assert "retirement" not in bakeoff_gates.HARD_AXES


def test_every_s10x_control_is_wired_to_a_feature():
    named = {k for c in S10X for k in c.controls}
    assert {"retire_cue", "retire_judge", "retire_speaker", "retire_ownwords", "retire_offered", "retire_owner_row", "retire_shadow",
            "retire_quote", "retire_forget"} <= named          # every retire_* control guards at least one cell


def test_the_live_cells_are_exactly_the_bars_the_driver_scores():
    live = {c.id.removeprefix("S10x.live.") for c in S10X if c.tier == "full"}
    assert live == set(s10x_live.BARS)
    # and the numbers in the spec text are the numbers the driver gates on
    by = {c.id.removeprefix("S10x.live."): c for c in S10X if c.tier == "full"}
    assert "24 of the 30" in by["right_rows"].title and s10x_live.MIN_RIGHT == 24
    assert "at most 2 of the 40" in by["non_changes"].title and s10x_live.MAX_WRONG == 2
    assert "none of the 10 hard" in by["hard_set"].title and "none of the 30" in by["other_person_copies"].title
    assert "at most 40 ms" in by["latency"].title and s10x_live.MAX_LATENCY_MS == 40.0


# ── the verdicts are pure and the bars bite ────────────────────────────────────────

def _m(**over):
    m = {"n_changes": 30, "changes": {"right": 30, "other_row_retired": 0, "copies_retired": 0},
         "non_changes": {"mentions": 30, "wrong_on_mentions": 0, "hard": 10, "wrong_on_hard": 0, "judge_calls": 9},
         "as_of": {"checked": 30, "ok": 30}, "third_party_pasted": {"turns": 90, "retired": 0, "judge_calls": 0},
         "latency": {"turns": 39, "p50_ms": 0.2, "p95_ms": 0.3, "judge_calls": 0, "cue_turns": 27, "cue_p50_ms": 58.0, "cue_p95_ms": 90.0}}
    for k, v in over.items():
        m[k] = {**m[k], **v} if isinstance(m[k], dict) else v
    return m


def _verdict(m, cell):
    return s10x_live.verdicts(m)[f"S10x.live.{cell}"]["verdict"]


def test_a_clean_measurement_passes_every_bar():
    assert {c["verdict"] for c in s10x_live.verdicts(_m()).values()} == {"PASS"}


@pytest.mark.parametrize("over,cell,verdict", [
    ({"changes": {"right": 24}}, "right_rows", "PASS"), ({"changes": {"right": 23}}, "right_rows", "FAIL"),
    ({"changes": {"right": 30, "other_row_retired": 3}}, "right_rows", "FAIL"),
    ({"non_changes": {"wrong_on_mentions": 2}}, "non_changes", "PASS"), ({"non_changes": {"wrong_on_mentions": 3}}, "non_changes", "FAIL"),
    ({"non_changes": {"wrong_on_hard": 1}}, "hard_set", "FAIL"), ({"changes": {"copies_retired": 1}}, "other_person_copies", "FAIL"),
    ({"third_party_pasted": {"retired": 1}}, "third_party_pasted", "FAIL"), ({"third_party_pasted": {"judge_calls": 1}}, "third_party_pasted", "FAIL"),
    ({"as_of": {"ok": 29}}, "as_of", "FAIL"), ({"as_of": {"checked": 0, "ok": 0}}, "as_of", "FAIL"),
    ({"latency": {"p95_ms": 41.0}}, "latency", "FAIL"), ({"latency": {"judge_calls": 1}}, "latency", "FAIL"),
    ({"latency": {"cue_p95_ms": 500.0}}, "latency", "PASS"),              # REPORTED, not gated (see the bar text)
])
def test_each_bar_has_its_edge(over, cell, verdict):
    assert _verdict(_m(**over), cell) == verdict


def test_the_output_carries_counts_and_verdicts_only():
    import json
    blob = json.dumps(s10x_live.verdicts(_m())).lower()
    from zmb import world
    for name in world.pool_strings():
        assert name.lower() not in blob


# ── the driver is an instrument: a perfect judge passes, the naive rule fails ──────────────────────

@pytest.fixture(scope="module")
def bow_runs():
    gold = {say: old for old, say, _f in s10x_data.PAIRS}
    return (s10x_live.run(s10x_live.oracle_judge(gold), embed=False, log=lambda *_: None, label="oracle"),
            s10x_live.run(s10x_live.naive_judge, embed=False, log=lambda *_: None, label="naive"))


def test_the_oracle_never_retires_a_non_change_a_copy_or_a_third_partys_sentence(bow_runs):
    oracle, _naive = bow_runs
    v = oracle["cells"]
    for cell in ("non_changes", "hard_set", "other_person_copies", "third_party_pasted", "as_of", "latency"):
        assert v[f"S10x.live.{cell}"]["verdict"] == "PASS", (cell, v[f"S10x.live.{cell}"])
    assert oracle["third_party_pasted"]["judge_calls"] == 0 and oracle["latency"]["judge_calls"] == 0


def test_the_naive_rule_fails_the_non_change_bars_and_a_gate_in_front_of_it_still_does_not_save_it(bow_runs):
    _oracle, naive = bow_runs
    v = naive["cells"]
    assert v["S10x.live.non_changes"]["verdict"] == "FAIL" and v["S10x.live.hard_set"]["verdict"] == "FAIL"
    assert naive["non_changes"]["wrong_on_hard"] >= 5            # the cue words the gate lets through are exactly what a top-1 cannot judge


@pytest.mark.skipif(not lab_driver.embedder_available(), reason="needs chromadb and the cached MiniLM model (the slim CI lane skips)")
def test_on_the_real_embedder_the_candidate_stage_finds_the_old_row_and_the_control_turns_it_red():
    """The record's candidate stage through the service's own blended search over real Chroma + MiniLM (Z0e): the old row is among the three
    offered on at least 24 of 30 changes (measured 30 of 30: the other-person copies are filtered out before the top 3), and breaking retrieval
    turns the cell red. (The full live-tier driver on Z0e takes ~45 s per judge and is run by hand: ``s10x_live.py --self-check``.)"""
    from zmb import world
    from zmb.arms.z0 import Z0Arm
    w = world.make_world()
    cell = CELLS["S10x.pool_right_rows_retired"].rendered(w)
    for off, want in ((frozenset(), "PASS"), (frozenset({"retrieval"}), "FAIL")):
        arm = Z0Arm(name="Z0e", embed=True, off=off)
        try:
            assert cellmod.run_cell(cell, w, arm).verdict == want, sorted(off)
        finally:
            arm.close()


def test_the_clone_judge_asks_the_clone_with_the_production_prompt(monkeypatch):
    """The real judge is the voice lane's own call pointed at the clone brain: same prompt, same parser, the clone's URL."""
    import asyncio
    import httpx
    seen = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"pick": 2}'}}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def post(self, url, json=None):
            seen["url"], seen["payload"] = url, json
            return _Resp()
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    class _Row:
        def __init__(self, text):
            self.text = text
    judge = s10x_live.clone_judge("http://127.0.0.1:11500/")
    assert asyncio.run(judge("I gave up the cello.", [_Row("User plays the cello."), _Row("User likes oat milk.")])) == 2
    assert seen["url"] == "http://127.0.0.1:11500/v1/chat/completions"
    prompt = seen["payload"]["messages"][1]["content"]
    assert "I gave up the cello." in prompt and "1. User plays the cello." in prompt and seen["payload"]["temperature"] == 0.0


def test_an_unreachable_clone_is_a_miss_never_a_retirement(monkeypatch):
    import asyncio
    import httpx

    class _Down:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx, "AsyncClient", _Down)
    assert asyncio.run(s10x_live.clone_judge("http://127.0.0.1:1")("I gave up the cello.", [])) is None


def test_the_cli_refuses_to_run_without_a_target(capsys):
    with pytest.raises(SystemExit) as e:
        s10x_live.main([])
    assert e.value.code == 2
