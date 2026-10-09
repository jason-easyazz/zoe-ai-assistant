"""The distress hand-off (``distress_handoff``): detector, per-tier precision/recall, language independence, the fixed reply, the notification.
Synthetic phrases only (``fixtures/distress_labelled.json``, 208 turns, en + es). Each guard has a negative control that goes red without it. ``ci_safe``."""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import re
from pathlib import Path

import pytest

import distress_handoff as dh
import lexicons

pytestmark = pytest.mark.ci_safe
SETS = json.loads((Path(__file__).parent / "fixtures" / "distress_labelled.json").read_text(encoding="utf-8"))
ROWS = [(lang, label, t) for s in ("dev", "held_out_1", "blind_2") for lang, tiers in SETS[s].items() for label, items in tiers.items() for t in items]
NEVER_WAKE = "I want to go to sleep and never wake up"
KNOWN_MISSES = {NEVER_WAKE, "he is dying of cancer and I am scared"}      # the limits stated in docs/knowledge/distress-handoff.md


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in (dh.ENV, dh.ENV_GENTLE, "ZOE_HOUSEHOLD_COUNTRY", "ZOE_LOCATION_COUNTRY", "ZOE_DISTRESS_CONTACT_NAME", "ZOE_DISTRESS_CONTACT_USER", "ZOE_DISTRESS_NOTIFY",
              "ZOE_DISTRESS_LINES_FILE"):
        monkeypatch.delenv(k, raising=False)
    dh._NOTIFIED.clear()


def table(scope=None):
    got = [(label, dh.detect(t).tier) for lang, label, t in ROWS if scope in (None, lang)]
    out = {}
    for tier in ("handoff", "gentle", "none"):
        tp, pred, lab = (sum(1 for a, b in got if a == b == tier), sum(1 for _a, b in got if b == tier), sum(1 for a, _b in got if a == tier))
        out[tier] = {"n": lab, "precision": tp / pred if pred else 1.0, "recall": tp / lab if lab else 1.0}
    return out


def test_per_tier_precision_and_recall_on_the_labelled_set():
    for scope in (None, "en", "es"):
        t = table(scope)
        print(scope or "all", {k: (v["n"], round(v["precision"], 3), round(v["recall"], 3)) for k, v in t.items()})
        assert t["handoff"]["recall"] >= 0.95 and t["handoff"]["precision"] >= 0.97 and t["gentle"]["recall"] >= 0.9 and t["gentle"]["precision"] >= 0.95, (scope, t)
        assert t["none"]["precision"] >= 0.95 and t["none"]["recall"] >= 0.99, (scope, t)
    assert len(ROWS) >= 200 and {r[0] for r in ROWS} == {"en", "es"}
    assert {t for _l, label, t in ROWS if dh.detect(t).tier != label} == KNOWN_MISSES                      # every miss is a stated limit
    assert [t for _l, label, t in ROWS if label == "handoff" and dh.detect(t).tier == "none"] == [NEVER_WAKE]


@pytest.mark.parametrize("text,tier", [
    ("The film was about suicide", "gentle"), ("my friend said she wanted to die", "gentle"), ("I don't want to die", "gentle"), ("I want to die", "handoff"),
    ("I want my mum to die", "none"), ("I'm dying to see it", "none"), ("In the movie he says I want to die", "gentle"),
    ("I watched a film and I want to die", "handoff"),                # the clause break: the film is not about THIS clause
    ("I don't know what to do, I want to die", "handoff"),          # the comma ends the negation's reach
    ("I'm not going to kill myself, don't worry", "gentle"),
])
def test_the_negation_and_quotation_guards_route_to_the_gentler_class(text, tier):
    assert dh.detect(text).tier == tier


@pytest.mark.parametrize("change,phrase,before,after", [
    ({"neg": None}, "I don't want to die", "gentle", "handoff"), ({"quote": None}, "in the song he sings I want to die", "gentle", "handoff"),
    ({"idioms": ()}, "I could kill myself for forgetting my keys", "none", "gentle"),
])
def test_negative_controls_remove_a_guard_and_the_phrase_and_the_labelled_set_go_red(monkeypatch, change, phrase, before, after):
    assert dh.detect(phrase).tier == before
    base = table()
    packs = {lang: (lambda p: dataclasses.replace(p, **change) if p else p)(dh._pack(lang)) for lang in lexicons.LANGS}
    monkeypatch.setattr(dh, "_pack", packs.get)
    assert dh.detect(phrase).tier == after
    now = table()
    assert any(now[t][m] < base[t][m] for t in base for m in ("precision", "recall")), (base, now)


def test_negative_control_open_fillers_turn_a_third_party_wish_into_a_handoff(monkeypatch):
    assert dh.detect("I want my mum to die").tier == "none"
    d = lexicons.load("en")["distress"]
    monkeypatch.setattr(lexicons, "load", lambda lang: {"distress": {**d, "fillers": ["\\w+"]}} if lang == "en" else {})
    monkeypatch.setattr(dh, "_lit", lambda p: dh._strip(p))                 # fillers as regex: "any word" between the parts
    dh._pack.cache_clear()
    try:
        assert dh.detect("I want my mum to die").tier == "handoff"
    finally:
        monkeypatch.undo()
        dh._pack.cache_clear()


def test_the_detector_reads_no_word_of_any_language():
    tree = ast.parse(Path(dh.__file__).read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
    bad = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
           and re.search(r"suicid|kill|\bdie\b|hurt|abus|harm|morir|matar", n.value, re.I)]
    assert not bad, bad


def test_a_new_language_is_a_data_file_and_nothing_else(monkeypatch):
    de = {"distress": {"enabled": True, "fillers": ["wirklich"], "negation": ["nicht"], "clause_breaks": ["aber"], "groups": {"want": ["ich will"], "die": ["sterben"]},
                       "cues": [{"id": "x", "cls": "suicide", "tier": "handoff", "negatable": True, "seq": ["want", "die"]}]}}
    real = lexicons.load
    monkeypatch.setattr(lexicons, "load", lambda lang: de if lang == "de" else real(lang))
    dh._pack.cache_clear()
    try:
        assert dh.detect("ich will wirklich sterben") == dh.Verdict("handoff", "suicide", "de", "cue")
        assert dh.detect("ich will nicht sterben").tier == "gentle"
    finally:
        monkeypatch.undo()
        dh._pack.cache_clear()


def test_the_flags_default_to_enforce_for_the_floor_and_shadow_for_the_gentle_tier(monkeypatch, caplog):
    assert (dh.handoff_mode(), dh.gentle_mode()) == ("enforce", "shadow")
    for raw, want in (("off", "off"), ("0", "off"), ("shadow", "shadow"), ("enforce", "enforce"), ("typo", "enforce"), ("1", "enforce")):
        monkeypatch.setenv(dh.ENV, raw)
        assert dh.handoff_mode() == want, raw
    monkeypatch.setenv(dh.ENV, "off")
    assert dh.gentle_mode() == "off" and run(dh.handle("I want to die", "u1")) is None
    monkeypatch.setenv(dh.ENV, "shadow")
    assert run(dh.handle("I want to die", "u1")) is None and not dh.guarded_text("I want to die")
    monkeypatch.setenv(dh.ENV, "enforce")
    assert dh.guarded_text("I want to die") and not dh.guarded_text("the film was about suicide")
    monkeypatch.setenv(dh.ENV_GENTLE, "enforce")
    assert run(dh.handle("the film was about suicide", "u1")) and dh.guarded_text("the film was about suicide")
    caplog.clear()
    with caplog.at_level("INFO"):
        run(dh.handle("I took too many pills", "demo_bar_1"))
    lines = [r.getMessage() for r in caplog.records if "DISTRESS" in r.getMessage()]
    assert "DISTRESS_HANDOFF user=demo_bar_1 tier=handoff lang=en mode=enforce" in lines and not any(w in ln.lower() for ln in lines for w in ("pills", "took"))


def test_the_lifeline_number_appears_only_when_the_country_config_says_au(monkeypatch):
    v = dh.detect("I want to die")
    assert "13 11 14" not in dh.build_reply(v) and "your local crisis line" in dh.build_reply(v)       # no country: no number guessed
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")
    assert "Lifeline on 13 11 14" in dh.build_reply(v) and "000" in dh.build_reply(v)
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "GB")
    assert "13 11 14" not in dh.build_reply(v) and "Samaritans on 116 123" in dh.build_reply(v)
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "ZZ")
    assert re.search(r"\d", dh.build_reply(v)) is None
    monkeypatch.delenv("ZOE_HOUSEHOLD_COUNTRY")
    monkeypatch.setenv("ZOE_LOCATION_COUNTRY", "Australia")                                             # the existing location config is the fallback
    assert "13 11 14" in dh.build_reply(v)


def test_the_reply_is_two_warm_sentences_with_the_contact_and_a_child_wording_in_the_cue_language(monkeypatch):
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")
    monkeypatch.setenv("ZOE_DISTRESS_CONTACT_NAME", "Sam")
    v = dh.detect("I want to die")
    adult, child = dh.build_reply(v, user_id="u1"), dh.build_reply(v, minor=True, user_id="u1")
    assert all(len([s for s in re.split(r"[.!?]\s+|[.!?]$", x) if s.strip()]) == 2 for x in (adult, child))
    assert "Sam" in adult and "Lifeline" in adult and "Kids Helpline on 1800 55 1800" in child and "Lifeline" not in child
    monkeypatch.setenv("ZOE_DISTRESS_CONTACT_USER", "sam_id")
    assert "Sam" not in dh.build_reply(v, user_id="sam_id") and "grown-up you trust" in dh.build_reply(v, minor=True, user_id="sam_id")   # never "ring yourself"
    assert not any(w in (adult + child).lower() for w in ("depress", "diagnos", "therap", "haha", "joke", "lol"))
    danger = dh.build_reply(dh.detect("someone is breaking into the house"))
    assert danger.startswith("I can't send help myself") and "000" in danger and "13 11 14" not in danger
    assert "Lifeline al 13 11 14" in dh.build_reply(dh.detect("quiero morirme")) and dh.build_reply(dh.detect("quiero morirme")).startswith("Me alegra")


def test_fast_tiers_answers_first_the_brain_is_not_asked_and_a_normal_turn_is_left_alone(monkeypatch):
    import brief_first_turn
    import fast_tiers
    import zoe_flue_client as zfc
    from proactive import selector
    monkeypatch.setenv("ZOE_EXPERT_ENABLED", "0")
    res = run(fast_tiers.resolve("I want to die", "demo_bar_1", "s1", channel="voice"))
    assert res.tier == "distress_handoff" and res.domain == "safety" and "glad you told me" in res.reply
    assert run(fast_tiers.resolve("what time is it", "demo_bar_1", "s1", channel="voice")) is None

    def boom(*_a, **_k):
        raise AssertionError("the brain / the day brief / the raise must not run on a hand-off turn")

    async def aboom(*_a, **_k):
        boom()
    monkeypatch.setattr(zfc, "_run_flue_brain_streaming_turn", boom)
    monkeypatch.setattr(brief_first_turn, "prepare", aboom)
    monkeypatch.setattr(selector, "prepare", aboom)

    async def go():
        sink = {}
        return [d async for d in zfc.run_flue_brain_streaming("I want to die", "s1", "demo_bar_1", outcome_sink=sink)], sink
    chunks, sink = run(go())
    assert len(chunks) == 1 and "glad you told me" in chunks[0] and sink
    monkeypatch.setenv(dh.ENV, "off")                                            # control: flag off, the brain path is taken (and the stub raises)
    assert run(fast_tiers.resolve("I want to die", "demo_bar_1", "s1", channel="voice")) is None
    with pytest.raises(AssertionError):
        run(go())


@pytest.fixture
def bell(monkeypatch):
    import contextlib
    from unittest.mock import AsyncMock, MagicMock

    import db_pool
    import identity_facts
    import push
    db = MagicMock(execute=AsyncMock(), commit=AsyncMock())
    bc = AsyncMock()

    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    monkeypatch.setattr(push.broadcaster, "broadcast", bc)
    monkeypatch.setattr(identity_facts, "resolve_identity", AsyncMock(return_value=None))
    for k, v in (("ZOE_DISTRESS_CONTACT_USER", "adult_1"), ("ZOE_DISTRESS_CONTACT_NAME", "Sam"), ("ZOE_HOUSEHOLD_COUNTRY", "AU")):
        monkeypatch.setenv(k, v)
    return db.execute, bc


def test_the_adult_is_told_once_plainly_and_without_the_words(bell):
    execute, bc = bell
    v = dh.detect("I want to die")
    run(dh._notify("kid_1", v, True))
    run(dh._notify("kid_1", v, True))                                                # the second sighting inside six hours: silent
    assert execute.await_count == 1 and bc.await_count == 1
    params = execute.await_args.args[1]
    assert params[1] == "adult_1" and all(w in params[3] for w in ("worried Zoe", "Kids Helpline", "Sam")) and "die" not in params[3].lower()
    run(dh._notify("kid_2", v, True))
    assert execute.await_count == 2                                                   # another member is another notice


@pytest.mark.parametrize("who,minor,notify,text,n", [
    ("adult_1", False, "all", "I want to die", 0), ("guest", False, "all", "I want to die", 0), ("kid_1", False, "off", "I want to die", 0),
    ("grownup_2", False, "minors", "I want to die", 0), ("kid_1", True, "minors", "I want to die", 1), ("grownup_2", False, "all", "I want to die", 1),
    ("kid_1", True, "all", "the film was about suicide", 0),
])
def test_who_is_notified(bell, monkeypatch, who, minor, notify, text, n):
    monkeypatch.setenv("ZOE_DISTRESS_NOTIFY", notify)
    run(dh._notify(who, dh.detect(text), minor))
    assert bell[0].await_count == n
    monkeypatch.delenv("ZOE_DISTRESS_CONTACT_USER")                                  # no contact configured: nobody is told
    run(dh._notify(who + "_x", dh.detect(text), minor))
    assert bell[0].await_count == n
