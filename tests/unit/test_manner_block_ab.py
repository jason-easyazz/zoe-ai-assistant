"""manner_block_ab.py - the integrity of what the A/B saves and what its verdict may rest on (Greptile #1978).

Three findings, each with a negative control (revert the fix => the test goes red):

* a verdict needs the WHOLE plan: every planned ask a row in both arms, every primary and regression half with data in both arms. An
  interrupted regression run, a dropped row or a half with no data is DO NOT KEEP - never skipped, never "provisional";
* a zoe-data restart discards the invocation: it appends a tombstone, ``load_rows`` stops counting that invocation's rows, the stamp
  is checked before AND after each ask (a restart during the last turn is caught), and a resume re-asks them;
* every row stores a fingerprint sha256(harness version, arm, the exact block text, the split digest): a resume refuses to mix, the
  report refuses changed-text rows, legacy rows (no fingerprint) are refused;
* (#1977 thread) the Runner makes the on-arm eligible for its OWN throw-away user only, since the shipped check needs the account tables.

A fake runner stands in for the brain (no network, no live service, no database, no flag). UNMARKED (Jetson full-directory lane): the
eligibility test imports services/zoe-data (the Flue client), which the slim GitHub lane does not carry.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PERF = REPO / "scripts" / "perf"
if str(PERF) not in sys.path:
    sys.path.insert(0, str(PERF))

import manner_block_ab as mab  # noqa: E402

BOOSTED = ("P6.a", "P5b.b", "P5b.u")          # the halves the synthetic scorer lifts in the on arm
ARMS = ["none", "on:V0"]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Rows go to a scratch dir; the stamp is a variable; the scorer is synthetic (the arm is read off the fake reply)."""
    monkeypatch.setattr(mab, "CACHE", tmp_path / "cache")
    state = {"stamp": "ActiveEnterTimestampMonotonic=1 MainPID=10"}
    monkeypatch.setattr(mab, "zoe_data_stamp", lambda: state["stamp"])

    def score_ask(ask, replies):
        on = replies[0].startswith("[on")
        return {h: [(on if h in BOOSTED else True), {}] for h in ask.scores}

    monkeypatch.setattr(mab, "score_ask", score_ask)
    monkeypatch.setattr(mab, "score_bar", lambda sid, replies: (True, {}))
    return state


@pytest.fixture
def stamp(_isolated):
    return _isolated


class FakeRunner:
    """The brain's stand-in. ``hook(runner)`` runs inside every ask (raise BudgetExceeded, flip the stamp, ...)."""

    def __init__(self, hook=None):
        self.world = mab.sp.World(mab.sp.BASE_SEED)
        self.brain = types.SimpleNamespace(spent_s=0.0, calls=0)
        self.n = 0
        self.asked: list[tuple] = []
        self.hook = hook

    def _go(self, arm, aid, sample):
        self.n += 1
        self.asked.append((arm, aid, sample))
        if self.hook:
            self.hook(self)
        return [f"[{arm}] I hear you."]

    async def ask(self, arm, ask, sample, nonce):
        return self._go(arm, ask.id, sample)

    async def bar(self, arm, sid_tag, ask_id, texts, packet, sample, nonce):
        return self._go(arm, ask_id, sample)


def run(runner, phase, arms=ARMS, run_id="r1"):
    asyncio.run(mab.run_phase(runner, phase, list(arms), run_id, 1.0, lambda *_: None))


def stop_after(k):
    def hook(r):
        if r.n > k:
            raise mab.pab.BudgetExceeded("scripted: the brain budget is spent")
    return hook


def plan_len(phase, arms=ARMS):
    return len(mab.build_plan(phase, mab.sp.World(mab.sp.BASE_SEED), 1.0)) * len(arms)


def full_run(run_id="full"):
    for phase in ("heldout", "regress"):
        run(FakeRunner(), phase, run_id=run_id)


# ── 1. a verdict needs the whole plan ───────────────────────────────────────────────────────────────────────────────────────
def test_positive_control_a_complete_run_with_a_real_lift_is_a_keep():
    full_run()
    _text, final = mab.report("full", "V0")
    assert "KEEP" in final and "DO NOT KEEP" not in final, final


def test_an_interrupted_regression_run_is_never_a_keep():
    run(FakeRunner(), "heldout", run_id="cut")
    run(FakeRunner(hook=stop_after(108 + 40)), "regress", run_id="cut")          # none arm done, the on arm stops 40 asks in
    _text, final = mab.report("cut", "V0")
    assert "DO NOT KEEP" in final and "regress arm on:V0" in final and "no row" in final, final
    assert "(provisional)" not in final


def test_a_regression_phase_that_never_ran_is_never_a_keep():
    run(FakeRunner(), "heldout", run_id="nore")
    _text, final = mab.report("nore", "V0")
    assert "DO NOT KEEP" in final and "regress arm" in final, final


def test_one_dropped_heldout_row_is_never_a_keep():
    full_run("drop")
    p = mab.results_path("drop")
    lines = p.read_text().splitlines()
    idx = next(i for i, ln in enumerate(lines) if json.loads(ln)["phase"] == "heldout" and json.loads(ln)["arm"] == "on:V0")
    p.write_text("\n".join(lines[:idx] + lines[idx + 1:]) + "\n")
    _text, final = mab.report("drop", "V0")
    assert "DO NOT KEEP" in final and "heldout arm on:V0" in final, final


def test_decide_never_skips_a_missing_comparison():
    ups = {h: mab.compare(h, (19, 20), (5, 20)) for h in mab.PRIMARY}
    level = {h: mab.compare(h, (18, 20), (18, 20)) for h in mab.REGRESSION}
    assert mab.decide(ups, level)[0] == "KEEP"
    assert mab.decide({h: c for h, c in ups.items() if h != "P5b.u"}, level)[0] == "DO NOT KEEP"          # a primary half absent
    assert mab.decide(dict(ups, **{"P9.a": mab.compare("P9.a", (0, 0), (5, 20))}), level)[0] == "DO NOT KEEP"   # no data in one arm
    assert mab.decide(ups, {h: c for h, c in level.items() if h != "P7.a"})[0] == "DO NOT KEEP"           # a regression half absent
    assert mab.decide(ups, dict(level, **{"S3": mab.compare("S3", (3, 3), (0, 0))}))[0] == "DO NOT KEEP"
    why = mab.decide(ups, level, ["scripted integrity problem"])[1]
    assert "scripted integrity problem" in why


def test_the_plan_covers_every_required_half():
    """If a required half could never get a row, the completeness rule would make a KEEP impossible."""
    world = mab.sp.World(mab.sp.BASE_SEED)
    got = set()
    for item, _s in mab.build_plan("regress", world, 1.0):
        got |= {item[1]} if isinstance(item, tuple) else set(item.scores)
    assert set(mab.REGRESSION) <= got
    held = set()
    for item, _s in mab.build_plan("heldout", world, 1.0):
        held |= set(item.scores)
    assert set(mab.PRIMARY) <= held


# ── 2. a zoe-data restart discards the invocation ───────────────────────────────────────────────────────────────────────────
def test_a_restart_tombstones_the_invocation_and_a_resume_redoes_its_asks(stamp):
    def flip_at_10(r):
        if r.n == 10:
            stamp["stamp"] = "ActiveEnterTimestampMonotonic=2 MainPID=99"
    first = FakeRunner(hook=flip_at_10)
    with pytest.raises(mab.RestartedMidRun):
        run(first, "heldout", ["none"], run_id="rs")
    assert any("tombstone" in json.loads(ln) for ln in mab.results_path("rs").read_text().splitlines())
    assert mab.load_rows("rs") == []                                          # the 9 rows it had written no longer count
    second = FakeRunner()
    run(second, "heldout", ["none"], run_id="rs")                              # the new boot: a stable stamp
    rows = mab.load_rows("rs")
    assert len(rows) == plan_len("heldout", ["none"]) == second.n             # re-asked ALL of them, each counted once
    assert len({(r["ask"], r["sample"]) for r in rows}) == len(rows)


def test_a_restart_during_the_last_turn_is_caught(stamp):
    total = plan_len("heldout", ["none"])

    def flip_on_last(r):
        if r.n == total:
            stamp["stamp"] = "ActiveEnterTimestampMonotonic=2 MainPID=99"
    with pytest.raises(mab.RestartedMidRun):
        run(FakeRunner(hook=flip_on_last), "heldout", ["none"], run_id="last")
    assert mab.load_rows("last") == []                                         # not even the final row was kept, nor any before it


def test_a_tombstone_discards_only_its_own_invocation(stamp):
    run(FakeRunner(hook=stop_after(5)), "heldout", ["none"], run_id="inv")     # invocation A: 5 rows, stopped by the budget
    assert len(mab.load_rows("inv")) == 5

    def flip_at_3(r):
        if r.n == 3:
            stamp["stamp"] = "ActiveEnterTimestampMonotonic=2 MainPID=99"
    with pytest.raises(mab.RestartedMidRun):
        run(FakeRunner(hook=flip_at_3), "heldout", ["none"], run_id="inv")     # invocation B restarts under it
    assert len(mab.load_rows("inv")) == 5                                      # A's rows survive; B's are gone
    third = FakeRunner()
    run(third, "heldout", ["none"], run_id="inv")
    assert third.n == plan_len("heldout", ["none"]) - 5 and len(mab.load_rows("inv")) == plan_len("heldout", ["none"])


def test_the_report_never_counts_tombstoned_rows(stamp):
    full_run("tomb")
    p = mab.results_path("tomb")
    rows = [json.loads(ln) for ln in p.read_text().splitlines()]
    victim = next(r for r in rows if r["phase"] == "heldout" and r["arm"] == "on:V0")
    with p.open("a") as f:
        mab.write_tombstone(f, victim["inv"], "scripted restart")               # that whole invocation (one phase's rows) is discarded
    _text, final = mab.report("tomb", "V0")
    assert "DO NOT KEEP" in final and "no row" in final, final


# ── 3. the fingerprint: what a row was measured WITH ────────────────────────────────────────────────────────────────────────
def test_the_fingerprint_covers_version_arm_text_and_split(monkeypatch):
    base = mab.fingerprint("on:V1", "digest-a", "text-a")
    assert base == mab.fingerprint("on:V1", "digest-a", "text-a")
    assert base != mab.fingerprint("on:V2", "digest-a", "text-a")             # arm
    assert base != mab.fingerprint("on:V1", "digest-a", "text-b")             # the exact text
    assert base != mab.fingerprint("on:V1", "digest-b", "text-a")             # the split
    monkeypatch.setattr(mab, "HARNESS_VERSION", "manner-ab-next")
    assert base != mab.fingerprint("on:V1", "digest-a", "text-a")             # the harness version


def test_every_saved_row_carries_its_invocation_and_fingerprint():
    run(FakeRunner(hook=stop_after(3)), "heldout", ARMS, run_id="fp")
    for r in mab.load_rows("fp"):
        assert r["inv"] and r["fp"] and r["fp"] == mab.fingerprint(r["arm"], mab.split_digest(mab.sp.World(mab.sp.BASE_SEED)))


def test_a_resume_with_edited_text_is_refused_and_asks_nothing(monkeypatch):
    monkeypatch.setitem(mab.VARIANTS, "V1", "Original text of the variant.")
    run(FakeRunner(hook=stop_after(4)), "heldout", ["on:V1"], run_id="ed")
    before = mab.results_path("ed").read_text()
    monkeypatch.setitem(mab.VARIANTS, "V1", "Edited text of the variant.")
    second = FakeRunner()
    with pytest.raises(RuntimeError, match="refusing to resume"):
        run(second, "heldout", ["on:V1"], run_id="ed")
    assert second.n == 0 and mab.results_path("ed").read_text() == before


def test_positive_control_a_resume_with_unchanged_text_continues(monkeypatch):
    monkeypatch.setitem(mab.VARIANTS, "V1", "Original text of the variant.")
    run(FakeRunner(hook=stop_after(4)), "heldout", ["on:V1"], run_id="same")
    second = FakeRunner()
    run(second, "heldout", ["on:V1"], run_id="same")
    assert second.n == plan_len("heldout", ["on:V1"]) - 4


def test_the_report_refuses_rows_saved_under_a_different_text(monkeypatch):
    monkeypatch.setitem(mab.VARIANTS, "V1", "Original text of the variant.")
    run(FakeRunner(hook=stop_after(90)), "heldout", ["none", "on:V1"], run_id="rep")      # none complete, a few on:V1 rows
    monkeypatch.setitem(mab.VARIANTS, "V1", "Edited text of the variant.")
    text, final = mab.report("rep", "V1")
    assert "DO NOT KEEP" in final and "rows refused" in final and "different block text" in final, final
    assert "== HELDOUT" not in text                                                  # nothing was tallied


def test_a_changed_split_or_harness_version_is_refused_too(monkeypatch):
    run(FakeRunner(hook=stop_after(3)), "heldout", ["none"], run_id="sp")
    monkeypatch.setattr(mab, "split_digest", lambda _w: "a-different-split")
    with pytest.raises(RuntimeError, match="refusing to resume"):
        run(FakeRunner(), "heldout", ["none"], run_id="sp")
    assert "different block text, split or harness" in mab.report("sp", "V0")[1]


def test_a_changed_harness_version_is_refused(monkeypatch):
    run(FakeRunner(hook=stop_after(3)), "heldout", ["none"], run_id="hv")
    monkeypatch.setattr(mab, "HARNESS_VERSION", "manner-ab-next")
    with pytest.raises(RuntimeError, match="refusing to resume"):
        run(FakeRunner(), "heldout", ["none"], run_id="hv")


def test_legacy_rows_without_a_fingerprint_are_refused():
    run(FakeRunner(hook=stop_after(3)), "heldout", ["none"], run_id="old")
    p = mab.results_path("old")
    legacy = []
    for ln in p.read_text().splitlines():
        r = json.loads(ln)
        r.pop("fp"), r.pop("inv")                                              # exactly the rows the first version of the harness wrote
        legacy.append(json.dumps(r))
    p.write_text("\n".join(legacy) + "\n")
    with pytest.raises(RuntimeError, match="legacy"):
        run(FakeRunner(), "heldout", ["none"], run_id="old")
    assert "legacy" in mab.report("old", "V0")[1] and "DO NOT KEEP" in mab.report("old", "V0")[1]


# ── 4. the on arm is eligible for the rig's own user only (#1977 thread; the shipped check needs the account tables) ─────────────
def _rig(monkeypatch):
    mab.zoe_path()
    import persona_layer
    import zoe_flue_client as zc

    @asynccontextmanager
    async def no_database(db=None):
        raise RuntimeError("the rig has no database")
        yield

    monkeypatch.setattr(persona_layer, "_db", no_database)
    user = mab.sb.new_demo_user()
    return zc, mab.Runner(zc, mab.sp.World(mab.sp.BASE_SEED), user, 100.0), user


def test_the_runner_makes_the_on_arm_eligible_for_its_own_user_only(monkeypatch):
    zc, runner, user = _rig(monkeypatch)
    real = runner._real_eligible
    try:
        with runner.arm_env("on:V0"):
            mine = asyncio.run(zc._manner_context_block(user, "I had a rough day"))
            other = asyncio.run(zc._manner_context_block(mab.sb.new_demo_user(), "I had a rough day"))   # another demo id: real check, fails closed
            adult_shaped = asyncio.run(zc._manner_context_block("demo_user", "I had a rough day"))
            guest = asyncio.run(zc._manner_context_block("guest", "I had a rough day"))
        assert mine and mine.strip() == mab.variant_text("V0").strip()
        assert other == "" and adult_shaped == "" and guest == ""
        with runner.arm_env("none"):
            assert asyncio.run(zc._manner_context_block(user, "I had a rough day")) == ""       # flag off => today's bytes
    finally:
        runner.close()
    assert runner.mb.eligible is real                                                         # the override never outlives the rig


def test_the_runner_refuses_a_non_demo_identity(monkeypatch):
    zc, runner, _user = _rig(monkeypatch)
    runner.close()
    with pytest.raises(ValueError):
        mab.Runner(zc, mab.sp.World(mab.sp.BASE_SEED), "alice", 100.0)
