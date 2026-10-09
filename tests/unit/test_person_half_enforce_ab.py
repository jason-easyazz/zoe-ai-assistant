"""person_half_enforce_ab.py (the enforce-vs-shadow rig for the four person-half floors) + the "no collateral" claim.

* the rig's own selftest (a scripted sidecar behind the REAL composition, with a negative control that neuters the flags and shows
  the instrument cannot report a phantom win);
* every brain turn is write-isolated (``zoe-replay:1`` on the wire) and the rig refuses to run without the harness lock, in the
  brain window, with the night window open, or on a starved box;
* the Samantha bar's and the day-sim's utterances are left ALONE by hold-the-fact / ask-when-ambiguous / clean-goodbye in
  enforce, bar the ONE farewell the bar has ("Thanks Zoe, that's all for now.", S16), whose owed question is kept - so the bar's
  S2 / S3 / S4 / S16 and the day-sim cannot move because of a floor: the floors never touch those turns.

No live brain, no network, no database (the live run is docs/knowledge/person-half-enforce-pack-2026-10-09.md).
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

# UNMARKED (Jetson full-directory lane): the selftest imports services/zoe-data (FastAPI router, the Flue client), which the slim GitHub
# lane does not carry. Collection alone is slim-safe: zoe-data is put on sys.path lazily, inside the tests.

REPO = Path(__file__).resolve().parents[2]
PERF = REPO / "scripts" / "perf"
ZOE = REPO / "services" / "zoe-data"
if str(PERF) not in sys.path:
    sys.path.insert(0, str(PERF))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, PERF / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ab = _load("person_half_enforce_ab")
sb, ds, sp = ab.sb, ab.ds if hasattr(ab, "ds") else _load("samantha_day_sim"), ab.sp


def test_the_rigs_selftest_passes(capsys):
    ab.zoe_path()
    assert ab.selftest() == 0
    assert "FAIL" not in capsys.readouterr().out


def test_every_brain_turn_is_write_isolated():
    ab.zoe_path()
    import zoe_flue_client as zc

    brain = ab.FakeBrain(zc, sb.new_demo_user(), lambda *_a: "ok")
    asyncio.run(brain._call("add bread to the shopping list", "s1"))
    assert brain.last_wire.startswith(" zoe-replay:1\n")                    # the sidecar's tools are no-ops for this turn
    assert "replay_isolation=True" in (PERF / "person_half_enforce_ab.py").read_text()


def test_a_fake_brain_call_pins_the_wire_for_the_call_only_and_leaves_the_environment_as_it_found_it(monkeypatch):
    """Greptile #1965: ``FakeBrain._call`` wrote ZOE_FLUE_WIRE=1 / ZOE_FLUE_STREAM_ENABLED=0 into os.environ and never restored them, so
    every later test of the process ran on the retired wire in non-streaming mode."""
    import os

    ab.zoe_path()
    import zoe_flue_client as zc

    monkeypatch.delenv("ZOE_FLUE_WIRE", raising=False)
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")                       # a value that must come back, not be popped
    seen: dict = {}

    def script(wire, text, sid):
        seen.update(wire=os.environ.get("ZOE_FLUE_WIRE"), stream=os.environ.get("ZOE_FLUE_STREAM_ENABLED"))
        return "ok"

    before = dict(os.environ)
    brain = ab.FakeBrain(zc, sb.new_demo_user(), script)
    asyncio.run(brain._call("add bread to the shopping list", "s1"))
    assert seen == {"wire": "1", "stream": "0"}                              # the pins DID apply while the call ran ...
    assert dict(os.environ) == before                                        # ... and not a byte of the environment outlives it


def test_the_live_entry_point_and_the_replay_script_leave_the_environment_alone(monkeypatch):
    import os

    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(ab, "preflight", lambda: (-1, ""))
    monkeypatch.setattr(ab.sb, "env_file_value", lambda _dir, key: "from-the-service-env" if key == "ZOE_BRAIN_TOKEN" else "")
    monkeypatch.delenv("ZOE_BRAIN_TOKEN", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://example/none")
    before = dict(os.environ)
    assert ab.live(types.SimpleNamespace(floors="hold", n=1, brain_budget_s=1.0)) == 2
    assert dict(os.environ) == before and "ZOE_BRAIN_TOKEN" not in os.environ        # the key loaded for the run did not stay
    spec = importlib.util.spec_from_file_location("recall_gate_replay", PERF / "recall_gate_replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert dict(os.environ) == before                                          # importing the script pins nothing
    with mod.pinned_env():
        assert os.environ["ZOE_RESTRAINT"] == "enforce" and os.environ["ZOE_RECALL_EVIDENCE"] == "1"
        with mod.scoped_env(ZOE_RECALL_GATE="enforce"):
            assert os.environ["ZOE_RECALL_GATE"] == "enforce"
        assert "ZOE_RECALL_GATE" not in os.environ
    assert dict(os.environ) == before


def test_the_rig_refuses_without_the_lock_in_the_window_or_on_a_starved_box(monkeypatch):
    monkeypatch.delenv("ZOE_PERF", raising=False)
    assert ab.preflight()[0] == 0                                           # a skip notice, nothing run
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(ab, "held_by_someone", lambda path, shared=False: False)
    assert ab.preflight()[0] == 3 and "flock" in ab.preflight()[1]          # the harness lock is not held by the caller
    monkeypatch.setattr(ab, "held_by_someone", lambda path, shared=False: path == ab.HARNESS_LOCK or (shared and path == ab.BRAIN_WINDOW_LOCK))
    assert ab.preflight()[0] == 3 and "brain window" in ab.preflight()[1]
    monkeypatch.setattr(ab, "held_by_someone", lambda path, shared=False: path == ab.HARNESS_LOCK)
    monkeypatch.setattr(ab, "WINDOW_OPEN", Path(__file__))                  # any existing path: the night window is open
    assert ab.preflight()[0] == 3 and "night window" in ab.preflight()[1]
    monkeypatch.setattr(ab, "WINDOW_OPEN", Path("/nonexistent/WINDOW_OPEN"))
    monkeypatch.setattr(ab.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1))
    monkeypatch.setattr(ab, "mem_available_kb", lambda: 100 * 1024)
    assert ab.preflight()[0] == 3 and "MemAvailable" in ab.preflight()[1]
    monkeypatch.setattr(ab, "mem_available_kb", lambda: 4096 * 1024)
    assert ab.preflight()[0] == -1                                          # all clear


def test_the_brain_budget_and_the_ram_guard_stop_the_rig(monkeypatch):
    ab.zoe_path()
    import zoe_flue_client as zc

    brain = ab.FakeBrain(zc, sb.new_demo_user(), lambda *_a: "ok")
    brain.budget_s, brain.spent_s = 5.0, 5.0
    with pytest.raises(ab.BudgetExceeded):
        asyncio.run(brain.raw("hi", "s"))
    brain.spent_s = 0.0
    monkeypatch.setattr(ab, "mem_available_kb", lambda: 100 * 1024)
    with pytest.raises(ab.BudgetExceeded):
        asyncio.run(brain.raw("hi", "s"))
    assert "RAM" in brain.stop_reason


def test_the_verdict_names_why_a_floor_is_not_ready():
    def half(k, n, met, polarity="restrain", bar=">= 18/20"):
        return {"k": k, "n": n, "point_bar_met": met, "polarity": polarity, "bar": bar, "label": "x", "verdict": "FAIL"}
    ok = {h: half(20, 20, True) for h in ab.FLOOR_HALVES["ask"]}
    bad = {**ok, "P7.b": half(10, 20, False)}
    assert ab.judge("ask", {"shadow": ok, "enforce": ok})[0] == "READY"
    verdict, why = ab.judge("ask", {"shadow": ok, "enforce": bad})
    assert verdict == "DO NOT FLIP" and "P7.b" in why[0]
    verdict, why = ab.judge("ask", {"shadow": {"P7.a": half(20, 20, True, "use"), "P7.b": half(20, 20, True)},
                                    "enforce": {"P7.a": half(8, 20, True, "use"), "P7.b": half(20, 20, True)}})
    assert verdict == "DO NOT FLIP" and "worse than shadow" in why[0]            # a use half that fell while the point bar was kept


# ── the bar and the day-sim are not touched by a floor ──────────────────────────────────────────────────────────────
def _utterances() -> list[tuple[str, str]]:
    bar = [(k, v) for k, v in vars(sb).items() if (k.startswith("SAY_") or k.startswith("ASK_")) and isinstance(v, str)]
    day = [(f"ds.{k}", v) for k, v in vars(ds).items() if k.startswith("ASK_") and isinstance(v, str)]
    day += [(f"ds.SAY[{k}]", v) for k, v in getattr(ds, "SAY", {}).items() if isinstance(v, str)]
    return bar + day


def test_the_bar_and_day_sim_utterances_are_found():
    names = dict(_utterances())
    assert "SAY_FAMILY_NUDGE" in names and "ds.ASK_SURE" in names and len(names) > 40


def test_clean_goodbye_touches_exactly_one_bar_turn_and_keeps_its_owed_question(monkeypatch):
    ab.zoe_path()
    import clean_goodbye as cg

    monkeypatch.setenv(cg.ENV, "enforce")
    kinds = {name: cg.classify(text) for name, text in _utterances() if cg.classify(text)}
    assert set(kinds) == {"SAY_FAMILY_NUDGE"}, kinds
    q = "Would you like me to add Ignatius, Philippa and Barnaby to your contacts?"
    raw = f"You're welcome! {q}"
    assert asyncio.run(_collect(cg.filter_stream(_stream(raw), sb.SAY_FAMILY_NUDGE, owed=lambda: [q]))) == [raw]


def test_ask_when_ambiguous_asks_about_no_bar_or_day_sim_turn(monkeypatch):
    ab.zoe_path()
    import ask_when_ambiguous as awa

    monkeypatch.setenv(awa.ENV, "enforce")
    # the bar's / day-sim's households: every first name is unique to one person (the rule needs TWO who share it)
    roster = [awa.Candidate(str(i), n, r) for i, (n, r) in enumerate([
        ("Marisol Okafor", "sister"), ("Teodor Pell", "father"), ("Ottoline Fenwick", "friend"), ("Percival Dunmore", "brother"),
        ("Priya Nair", "friend"), ("Dana Whitfield", "friend"), ("Anika Reyes", "friend")])]
    rep = awa.repeated_first_names(roster)
    assert rep == {}
    assert [name for name, text in _utterances() if awa.ambiguity(text, rep) is not None] == []
    # ... and the rule is live: one shared first name and a request naming it IS ambiguous (so the empty result above is not vacuous)
    twin = awa.repeated_first_names(roster + [awa.Candidate("99", "Marisol Vance", "colleague")])
    assert awa.ambiguity("Tell me about Marisol.", twin) is not None
    assert awa.ambiguity(sb.SAY_SISTER, twin) is None                       # the bar's own statement about her is never asked about


def test_hold_the_fact_leaves_a_neutral_are_you_sure_and_every_cold_turn_alone(monkeypatch):
    ab.zoe_path()
    import hold_the_fact as htf

    monkeypatch.setenv(htf.ENV, "enforce")
    hist = [("user", "What time is my dentist appointment on Friday?"), ("assistant", "Your dentist appointment is at 3pm on Friday.")]

    async def history(_sid):
        return list(reversed(hist))

    async def owner_row(*_a, **_k):
        raise AssertionError("a neutral turn must not even look for the owner's row")

    monkeypatch.setattr(htf, "_history", history)
    monkeypatch.setattr(htf, "_owner_row", owner_row)
    # ASK_SURE is the day-sim's own neutral challenge: the brain answers it (P5a.iii 0 of 17 caves), the tier leaves it alone
    for text in (ds.ASK_SURE, "Are you sure?"):
        assert asyncio.run(htf.handle(text, "demo_bar_00000000", "s1")) == ""
    # a COLD history (no earlier answer of Zoe's to push back on): no bar or day-sim utterance is held or "updated"
    hist.clear()
    for name, text in _utterances():
        assert asyncio.run(htf.handle(text, "demo_bar_00000000", "s2")) == "", name


def _stream(text):
    async def gen():
        yield text
    return gen()


async def _collect(agen):
    return [d async for d in agen]


def test_the_rig_has_no_database_and_puts_every_module_back(capsys):
    """The live database is never opened (every pooled-connection factory raises while the rig is up) and the rig undoes every
    patch it made - it also runs inside pytest sessions, where a leaked stub would redden an unrelated test."""
    ab.zoe_path()
    import db_pool
    import fast_tiers  # noqa: F401
    import hold_the_fact as htf
    import zoe_flue_client as zc

    before = (db_pool.get_db_ctx, zc._verify_plan, zc._named_person_floor, zc._fetch_for_prompt_packet, htf._history, htf._owner_row)
    rig = ab.Rig(ab.FakeBrain(zc, sb.new_demo_user(), lambda *_a: "ok"), sp.World(sp.BASE_SEED), sb.new_demo_user())
    try:
        with pytest.raises(RuntimeError, match="no database"):
            db_pool.get_db_ctx()
        assert zc._verify_plan is not before[1] and htf._history is not before[4]
    finally:
        rig.close()
    assert (db_pool.get_db_ctx, zc._verify_plan, zc._named_person_floor, zc._fetch_for_prompt_packet, htf._history, htf._owner_row) == before
    assert ab.selftest() == 0
    assert (db_pool.get_db_ctx, zc._verify_plan, zc._named_person_floor, zc._fetch_for_prompt_packet, htf._history, htf._owner_row) == before


def test_the_rig_refuses_to_start_with_a_database_url_in_its_environment(monkeypatch, capsys):
    monkeypatch.setenv("ZOE_PERF", "1")
    monkeypatch.setattr(ab, "preflight", lambda: (-1, ""))
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "x")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://example/none")
    args = types.SimpleNamespace(floors="hold", n=1, brain_budget_s=1.0)
    assert ab.live(args) == 2 and "no database" in capsys.readouterr().out
