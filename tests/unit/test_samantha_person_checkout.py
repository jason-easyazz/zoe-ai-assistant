"""The checkout-bound half of the P-family tests (UNMARKED: runs in the Jetson full-directory lane,
not the slim GitHub lane, because it imports services/zoe-data modules): the in-tree proactive
selector against the P1/P4 fixtures, the style regexes against persona_drift, and the bar/day-sim
constants the family reuses. The pure scorer / control / judge-gate tests are
tests/unit/test_samantha_person.py (ci_safe).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PERF = REPO / "scripts" / "perf"
ZD = REPO / "services" / "zoe-data"


def _load():
    sys.path.insert(0, str(PERF))
    spec = importlib.util.spec_from_file_location("samantha_person", PERF / "samantha_person.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["samantha_person"] = mod
    spec.loader.exec_module(mod)
    return mod


sp = _load()


def _real_selector_or_skip():
    sys.path.insert(0, str(ZD))
    try:
        from proactive import selector  # noqa: F401
        from memory_digest import fact_has_topic  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"services/zoe-data selector not importable here: {type(exc).__name__}")
    return sp.real_selector


def test_the_in_tree_selector_passes_the_salience_cell():
    real = _real_selector_or_skip()
    acc = sp.run_selector_tier(sp.World(), real)
    res = {h: (sum(1 for c, _ in v if c), len(v)) for h, v in acc.items()}
    # P1: top-1 is the worry, the three decoys are never kept, one raise, order worry > event > mum
    assert res["P1.a"] == (20, 20) and res["P1.b"] == (0, 20) and res["P1.c"] == (20, 20) and res["P1.d"] == (20, 20)


def _counts(acc):
    return {h: (sum(1 for c, _ in v if c), len(v)) for h, v in acc.items()}


def test_the_in_tree_selector_passes_the_sensitive_class_cell():
    """P4 was the known target while no sensitivity class existed; with restraint in code it is a gating
    cell: nothing sensitive to an unconfirmed voice, nothing sensitive to a bare greeting, and the sensitive
    item still delivered on an open question (the use half). Three worlds: the permuted names and ailments."""
    real = _real_selector_or_skip()
    for w in (sp.World(), sp.World("fresh-a"), sp.World("fresh-b")):
        res = _counts(sp.run_selector_tier(w, real))
        assert res["P4.a"] == (20, 20) and res["P4.b"] == (20, 20), (w.seed, res)
        assert res["P4.c"] == (20, 20) and res["P4.d"] == (20, 20), (w.seed, res)
        assert res["P1.a"] == (20, 20) and res["P1.d"] == (20, 20)    # salience is untouched


def test_negative_control_the_same_selector_with_restraint_off_fails_p4():
    """RED WHEN REMOVED: with ZOE_RESTRAINT=off the in-tree selector volunteers the health worry to a stranger
    and to a bare greeting. If this ever passes the cell is not measuring restraint."""
    _real_selector_or_skip()
    res = _counts(sp.run_selector_tier(sp.World(), sp.real_selector_restraint_off))
    assert res["P4.a"][0] == 0 and res["P4.d"][0] == 0 and res["P4.b"][0] == 0
    assert res["P4.c"] == (20, 20)   # the pull still works: the item is simply never held back


def test_shadow_mode_is_not_enforcement():
    _real_selector_or_skip()
    fx = sp.p4_fixture(sp.World(), 0)
    shadow = sp.real_selector(fx, "shadow")
    assert [p["id"] for p in shadow if p["raised"]] == ["health"]    # shadow raises what the old selector raised
    assert [p["id"] for p in sp.real_selector(fx, "enforce") if p["raised"]] == ["event"]


def test_the_mute_probe_passes_on_the_real_code_and_reddens_without_it():
    _real_selector_or_skip()
    w = sp.World()
    assert all(c for c, _ in sp.run_mute_probes(w, "gold")), sp.run_mute_probes(w, "gold")
    assert not any(c for c, _ in sp.run_mute_probes(w, "mute_off"))     # recorded, never honoured
    assert not any(c for c, _ in sp.run_mute_probes(w, "nag"))          # the selector bypassed
    ev = sp.mute_probe(w, "Stop bringing that up.", "gold")[1]
    assert ev["ack_spoken"] is True and ev["re_raised"] is False


def test_the_mute_probe_never_touches_the_live_database_or_the_live_palace():
    _real_selector_or_skip()
    import db_compat
    import memory_service
    before = (db_compat.get_compat_db, memory_service.get_memory_service)
    sp.mute_probe(sp.World(), "Leave it.", "gold")
    assert (db_compat.get_compat_db, memory_service.get_memory_service) == before   # restored
    src = (PERF / "samantha_person.py").read_text()
    body = src[src.index("def mute_probe("):src.index("def run_mute_probes(")]
    assert body.index("db_compat.get_compat_db = fake_db") < body.index("asyncio.run(go(db))")
    assert body.index("memory_service.get_memory_service = ") < body.index("asyncio.run(go(db))")


def test_the_checkout_cli_measures_and_proves_its_controls(tmp_path, capsys):
    _real_selector_or_skip()
    out = tmp_path / "checkout.json"
    assert sp.run_checkout(("zmb-v1",), None, out) == 0
    printed = capsys.readouterr().out
    assert "checkout measurement: OK" in printed and "no live database" in printed
    import json as _json
    body = _json.loads(out.read_text())
    assert body["problems"] == [] and set(body["arms"]) == {"system", "restraint_off+mute_off", "selector_bypassed"}


def test_the_real_selector_adapter_drops_what_gather_drops():
    real = _real_selector_or_skip()
    fx = sp.p1_fixture(sp.World(), 0)
    ids = [p["id"] for p in real(fx)]
    assert "resolved" not in ids and "mood" not in ids and "task" not in ids
    assert ids[0] == "worry" and [p["raised"] for p in real(fx)].count(True) == 1


def test_a_selector_that_keeps_the_passing_mood_is_caught_by_p1():
    # the instrument sees a leak: feed the scorer the in-tree result PLUS the decoy
    fx = sp.p1_fixture(sp.World(), 0)
    leaky = [{"id": "worry", "raised": True}, {"id": "event", "raised": False}, {"id": "mood", "raised": False}]
    out = sp.score_p1(leaky, fx)
    assert out["P1.b"][0] is True and out["P1.c"][0] is False


def test_style_regexes_match_persona_drift():
    sys.path.insert(0, str(ZD))
    try:
        import persona_drift as pdm  # type: ignore
        import persona_layer as pl  # type: ignore
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"persona_drift not importable here: {type(exc).__name__}")
    rec = pl.default_persona()
    for text in ("Night. Sleep well.", "Absolutely! Here you go.", "As an AI language model I can't.",
                 "**Bold** text", "- a\n- b", "One. Two. Three. Four.", "Sure thing, done.", ""):
        mine = sp.style_checks(text)
        theirs = pdm.style_checks(text, rec)
        for check in ("opener", "no_selfid", "no_markdown"):
            assert mine[check] == theirs[check], (text, check)
    band = sp.load_drift_band()
    assert band is None or band("Night. Sleep well.") in ("aligned", "neutral", "deviation")


def test_the_bar_and_day_sim_constants_the_family_relies_on():
    assert sp.sb.VERBATIM_RUN == 7
    assert sp.ds.MEAT and sp.ds.SEAFOOD and "dentist" in sp.ds.TOPICS
    assert callable(sp.ds.score_raise_open) and callable(sp.ds.score_no_reraise) and callable(sp.ds.score_spacing)
    # the family reuses the day-sim's live class (demo users, sessions, proven teardown), unchanged
    assert issubclass(sp.PersonLive, sp.ds.DayLive) and issubclass(sp.ds.DayLive, sp.sb.Live)
    live = sp.PersonLive("t", "", "", False)
    assert live.session("demo_bar_0123abcd", "x").startswith("bar-pf-x-")
    with pytest.raises(ValueError):
        live.chat("jason", "x", "hello")             # the demo-user guard fires before any request


# ── the bar and the day-sim must not regress under ZOE_RESTRAINT=enforce ──────────────────────────────
def _restraint():
    sys.path.insert(0, str(ZD))
    try:
        import restraint
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"restraint not importable here: {type(exc).__name__}")
    return restraint


def test_the_open_turns_of_the_bar_and_the_day_sim_are_pulls_a_bare_greeting_is_not():
    rs = _restraint()
    for opener in (sp.sb.ASK_OPEN_1, sp.sb.ASK_OPEN_2, sp.ds.OPEN_1, sp.ds.OPEN_2):
        assert rs.is_pull(opener), opener
    assert not rs.is_pull("Hi Zoe") and not rs.is_pull("Good morning")


def test_every_bar_and_day_sim_ask_still_gets_the_row_it_was_written_to_find():
    """Offline analysis of the live scenarios (the live run is the owner's gate before the flag flips): for each
    (seeded sentence, ask) pair the packet filter must DELIVER the row in enforce. A pair whose row is not
    sensitive passes trivially; the sensitive ones must be pulled by the ask's own words."""
    rs = _restraint()
    sb, ds = sp.sb, sp.ds
    pairs = [
        ("S1", sb.SAY_SISTER, sb.ASK_SISTER), ("S1-long", sb.SAY_SISTER, sb.ASK_LONG_SISTER),
        ("S7", sb.SAY_DAD_RICH, sb.ASK_DAD), ("S7-long", sb.SAY_DAD_RICH, sb.ASK_LONG_DAD),
        ("S7-short", sb.SAY_DAD_SHORT, sb.ASK_DAD), ("S20", sb.SAY_DOB, sb.ASK_DOB),
        ("S21", sb.SAY_KIDS, sb.ASK_KIDS), ("S22", sb.SAY_ROSTER, sb.ASK_ROSTER),
        ("S2", sb.SAY_NEW_HOME, sb.ASK_HOME), ("S10", sb.SAY_CELLO, sb.ASK_CELLO),
        ("day-3", ds.SAY["d1-mum"], ds.ASK_MUM), ("day-3b", ds.SAY["d2-mum-fix"], ds.ASK_MUM),
        ("day-4", ds.SAY["d3-dentist"], ds.ASK_WHEN), ("day-time", ds.SAY["d3-dentist"], ds.ASK_TIME),
        ("day-5", ds.SAY["d1-project"], ds.ASK_QUOTE), ("day-6", ds.SAY["d1-race"], ds.ASK_RACE),
        ("day-6b", ds.SAY["d2-race-swap"], ds.ASK_RACE), ("day-6n", ds.SAY["d1-health"], ds.ASK_MIGRAINE),
        ("day-6nb", ds.SAY["d2-migraine-neg"], ds.ASK_MIGRAINE), ("day-2", ds.SAY["d1-diet"], ds.ASK_COOK),
    ]
    sensitive_pairs = 0
    for tag, said, asked in pairs:
        classes = rs.classify(said)
        sensitive_pairs += bool(classes)
        dec = rs.decide(said, classes, rs.make_turn(asked), surface="packet")
        assert dec.allow, (tag, classes, dec)
    assert sensitive_pairs >= 8   # the check is not vacuous: most of these rows ARE in a sensitive class


def test_the_s4_worry_is_delivered_to_the_mood_turn_and_the_brief_to_the_open_question():
    rs = _restraint()
    worry = sp.sb.SAY_WORRY
    assert rs.classify(worry) == ("affect",)
    mood = rs.make_turn(sp.sb.ASK_WORRY, mood=True)
    assert rs.decide(worry, ("affect",), mood, surface="packet").allow
    assert rs.decide(worry, ("affect",), rs.make_turn("what should I cook tonight"), surface="packet").allow is False


def test_the_night_shift_and_dog_walk_facts_the_personalisation_hop_relies_on_are_not_sensitive():
    rs = _restraint()
    assert rs.classify(sp.ds.SAY["d1-shift"]) == () and rs.classify(sp.ds.SAY["d1-dog"]) == ()
