"""ZOE_RISK_CUES: the risk-cue tier (risk_cues.py) and its seam to the Flue brain (zoe_flue_client._risk_cue_block).

What is pinned (synthetic names only, ci_safe; every sentence below is written from the CATEGORIES of everyday life - none is a
person-bench sentence, and none of the bench's held-out asks is read by this file):
  * the data: every pattern compiles, every note fits its patterns, every category exists in English AND Spanish, the heading and
    rule exist, and a worst-case block fits the budget;
  * detection, per category, per language - each cue has a sentence that fires it, and the RED-WHEN-REMOVED control: the same
    sentence with the cue's pattern deleted from the data does not fire (so a pass cannot come from some other cue);
  * no fire on non-plans (a question, a past story, a hypothetical, a negated plan, a statement with no time anchor, a goodbye),
    on a plan with no risk, on a guarded risk (a backup mentioned, two times on different days, ample lead time);
  * who gets it: a minor, a child role, an unknown role, a missing account, a guest, a synthetic id, a failed or slow lookup - no
    block; an adult - yes (the id string is only ever used to REFUSE);
  * the restraint tier: no block on a goodbye / presence / silence, a spoken mute, an unconfirmed voice, or an enforce-muted topic;
  * one block per turn, once per conversation, at most two concerns from different categories, heading and rule present;
  * shadow logs counts and categories only (no message text) and injects nothing;
  * flag off (the default) is the wire it always was, byte for byte, for every shape; the seam has one construction site.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from contextlib import asynccontextmanager

import aiosqlite
import pytest

import lexicons
import manner_block as mb
import persona_layer as pl
import risk_cues as rc
import zoe_flue_client as zc

pytestmark = pytest.mark.ci_safe

_REAL_PACK = rc._pack

# sha256 of the rendered block for one fixed plan per language: editing the heading, the rule or a note is a deliberate act with a
# measured A/B in the PR (docs/knowledge/risk-cue-tier.md), never a drive-by
GOLDEN_PLAN = {"en": "I'm driving to my sister's tonight, three hours, and I'm exhausted.",
               "es": "Esta noche voy a conducir cuatro horas y estoy agotado."}
GOLDEN_SHA256 = {
    "en": "15caef25d2e0b4aab399c1d7f48fa6b5e2c5bcecd6fee49dba5e617f77745336",
    "es": "1f181e538fdfe5c19cd9da12a3366445f65bb61e56f755c1bf046eb3e9165571",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("ZOE_RISK_CUES", "ZOE_MANNER_BLOCK", "ZOE_PERSONA_LAYER", "ZOE_FLUE_WIRE", "ZOE_FLUE_STREAM_ENABLED",
              "ZOE_STRIP_NARRATION", "ZOE_SEAM_RECALL_INJECT", "ZOE_VERIFY_ON_CHALLENGE", "ZOE_TRIVIA_HEDGE",
              "ZOE_SYNTHETIC_USER_ALLOWLIST", "ZOE_RESTRAINT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.setenv("ZOE_IDENTITY_BLOCK", "0")
    pl.invalidate_snapshot()
    rc._reset_state()
    _REAL_PACK.cache_clear()
    yield
    pl.invalidate_snapshot()
    rc._reset_state()
    _REAL_PACK.cache_clear()       # the real function, even while a test has swapped rc._pack for a stub


@pytest.fixture
def adult(monkeypatch):
    """Every real member is an adult unless a test says otherwise (the lookup itself is tested against a real sqlite below)."""
    async def is_adult(_uid, db=None):
        return True

    monkeypatch.setattr(mb, "_account_is_adult", is_adult)


@pytest.fixture
def sent(monkeypatch):
    """Run one turn through the real seam and return the message the sidecar would receive."""

    def turn(message, uid="zed_uid", session="s1", **kwargs):
        cap = []

        async def fake_wire2(session_id, payload, **kw):
            cap.append(json.loads(payload)["body"])
            yield "ok"

        monkeypatch.setattr(zc, "_run_turn_aggregated_wire2", fake_wire2)

        async def go():
            return [c async for c in zc.run_flue_brain_streaming(message, session, uid, **kwargs)]

        asyncio.run(go())
        assert len(cap) == 1
        return cap[0]
    return turn


# -- the data -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("lang", ["en", "es"])
def test_the_data_is_sound(lang):
    pack = rc._pack(lang)
    assert pack is not None, lang
    assert pack.skipped == 0, "a pattern in the lexicon does not compile, or a cue is missing a required key"
    assert pack.heading.startswith("##") and pack.rule and pack.intent and pack.when and pack.past and pack.veto
    assert {s.category for s in pack.specs} | ({"timing"} if pack.timing else set()) == set(rc.CATEGORY_ORDER), lang
    ids = [s.id for s in pack.specs]
    assert len(ids) == len(set(ids)), "duplicate cue ids"
    for s in pack.specs:
        slots = {int(n) for n in __import__("re").findall(r"\{(\d+)\}", s.note)}
        assert slots <= set(range(len(s.all))), f"{lang}/{s.id}: the note uses a slot its patterns do not fill"
        assert "\n" not in s.note
    t = pack.timing
    assert t is not None and t.tight and t.late and t.double
    for tpl, n in ((t.tight, 3), (t.late, 3), (t.double, 2)):
        tpl.format(*["x"] * n)


def test_english_has_every_cue_spanish_has_a_cue_per_category():
    assert {c.category for c in rc._pack("en").specs} == set(rc.CATEGORY_ORDER) - {"timing"}
    assert {c.category for c in rc._pack("es").specs} == set(rc.CATEGORY_ORDER) - {"timing"}
    assert rc.available_languages() == ("en", "es")


def test_a_worst_case_block_fits_the_budget():
    for lang in ("en", "es"):
        pack = rc._pack(lang)
        notes = sorted((s.note.format(*["x" * rc.SLOT_CHARS] * len(s.all)) for s in pack.specs), key=len, reverse=True)
        body = rc.render([rc.Cue("c", "a", notes[0]), rc.Cue("d", "b", notes[1])], lang)
        assert body, lang      # two worst-case notes still fit (else render() would have dropped to one, or none)
        assert len(body) <= rc.MAX_BLOCK_CHARS


def test_render_drops_a_note_that_does_not_fit_and_never_truncates():
    big = rc.Cue("safety_health", "a", "x" * (rc.MAX_BLOCK_CHARS + 1))
    small = rc.Cue("money_contract", "b", "short")
    body = rc.render([small, big], "en")
    assert "short" in body and "x" * 50 not in body
    assert rc.render([big], "en") == ""
    assert rc.render([small], "xx") == ""        # no data for the language


def test_the_rendered_block_is_stable():
    for lang, want in GOLDEN_SHA256.items():
        cues = rc.pick(rc.detect(GOLDEN_PLAN[lang]))
        assert cues, lang
        assert _sha(rc.render(cues, lang)) == want, f"the {lang} risk-cue block changed: re-measure it (A/B) before editing the golden"


def test_the_block_shape_heading_one_line_per_concern_one_rule():
    body = rc.render(rc.pick(rc.detect("I'm going to bet my whole bonus on one horse tomorrow. I'm driving there tired.")), "en")
    lines = body.split("\n")
    assert lines[0] == "## Before you answer, check"
    assert all(ln.startswith("- ") for ln in lines[1:-1]) and 1 <= len(lines) - 2 <= rc.MAX_NOTES
    assert lines[-1] == rc._pack("en").rule and "once" in lines[-1] and "kindly" in lines[-1]


# -- detection per category, per language ---------------------------------------------------------------------
#: (language, sentence, the cue id that must fire)
FIRES = [
    ("en", "I'm driving to my sister's tonight, three hours, and I'm exhausted.", "hazard_impaired"),
    ("en", "Tonight I'll go for a swim after the party. I've had a few beers.", "hazard_impaired"),
    ("en", "Tomorrow I'm going to play football with the lads even though my ankle is still swollen.", "injury_exertion"),
    ("en", "I'll ride the bike down the hill this afternoon without a helmet.", "skip_protection"),
    ("en", "I'm going to stop taking my antidepressants next week, I feel fine.", "stop_medication"),
    ("en", "For lunch today I'm having a bag of crisps and a cola.", "thin_meal_junk"),
    ("en", "Dinner tonight is just toast.", "thin_meal_staple"),
    ("en", "Starting tomorrow I'm fasting for five days.", "no_food"),
    ("en", "I'm going to bet my whole bonus on the match on Saturday.", "all_in_money"),
    ("en", "I'm buying into this crypto coin tonight, it's guaranteed profit.", "sure_thing"),
    ("en", "I'm handing in my notice tomorrow and I have no savings at all.", "quit_no_income"),
    ("en", "I'll sign the lease tomorrow without checking the small print.", "sign_unread"),
    ("en", "I'm going to pay a deposit in advance to a guy I found on marketplace.", "pay_stranger"),
    ("en", "I'm going to read out my verification code to the support guy who rang me.", "scam_details"),
    ("en", "Tomorrow I'll delete all the photos on my phone to free some space.", "delete_all_no_backup"),
    ("en", "I'm going to factory reset the laptop tonight.", "wipe_device_no_backup"),
    ("en", "I'm going to text my landlord that he is pathetic.", "harsh_message"),
    ("en", "I'm furious, so I'm walking out of the meeting right now.", "rash_while_angry"),
    ("en", "We fly to Madrid tomorrow and I don't have a passport.", "lacks_item"),
    ("en", "I'll drive up to the cabin on Saturday but I haven't booked anything yet.", "unbooked"),
    ("en", "I'm going to quit tomorrow and I haven't told my family.", "untold"),
    ("en", "Tomorrow I'll leave the house at 7:45 for my 9am flight.", "timing_tight"),
    ("en", "Tomorrow I'll arrive at the airport at 9:00 for my 9am flight.", "timing_late"),
    ("en", "My flight is at 9am tomorrow but I'll check out at 8:30.", "timing_tight"),
    ("en", "Tomorrow I'm picking up the kids at 3:30 and seeing the dentist at 3:30.", "timing_double"),
    ("es", "Esta noche voy a conducir cuatro horas y estoy agotado.", "hazard_impaired"),
    ("es", "Mañana voy a correr diez kilómetros aunque me duele la rodilla.", "injury_exertion"),
    ("es", "Voy a bajar en bici sin casco esta tarde.", "skip_protection"),
    ("es", "Mañana voy a dejar mis pastillas, ya me siento bien.", "stop_medication"),
    ("es", "La cena de hoy es solo arroz.", "thin_meal_staple"),
    ("es", "Hoy el almuerzo es un paquete de patatas fritas y un refresco.", "thin_meal_junk"),
    ("es", "Voy a apostar todos mis ahorros en el partido del sábado.", "all_in_money"),
    ("es", "Voy a invertir en esta cripto, es una cosa segura.", "sure_thing"),
    ("es", "Mañana voy a renunciar y no tengo ahorros.", "quit_no_income"),
    ("es", "Mañana voy a firmar el contrato sin leerlo.", "sign_unread"),
    ("es", "Mañana voy a borrar todos los archivos del portátil.", "delete_all_no_backup"),
    ("es", "Voy a enviarle un mensaje a mi jefe diciendo que es un inútil.", "harsh_message"),
    ("es", "Estoy furiosa y voy a renunciar ahora mismo.", "rash_while_angry"),
    ("es", "Mañana vuelo a Lima y no tengo pasaporte.", "lacks_item"),
    ("es", "El sábado voy a ir a la playa, pero todavía no he reservado el hotel.", "unbooked"),
    ("es", "Voy a renunciar mañana y no se lo he dicho a nadie.", "untold"),
    ("es", "Mañana tengo que salir a las 8:30 para mi vuelo de las 9:00.", "timing_tight"),
    ("es", "Mañana voy a ir al dentista a las 4 y a recoger a los niños a las 4.", "timing_double"),
]


def test_every_cue_has_a_firing_sentence_in_each_language_that_defines_it():
    covered = {(lang, cue) for lang, _t, cue in FIRES}
    for lang in ("en", "es"):
        pack = rc._pack(lang)
        defined = {s.id for s in pack.specs} | {"timing_tight", "timing_late", "timing_double"}
        # Spanish ships no `timing_late` sentence of its own (the template is exercised through the tight path); every other cue has one
        missing = {c for c in defined if (lang, c) not in covered} - ({"timing_late"} if lang == "es" else set())
        assert not missing, f"{lang}: cues with no firing sentence: {sorted(missing)}"


@pytest.mark.parametrize("lang,text,cue", FIRES)
def test_the_cue_fires_on_its_plan(lang, text, cue):
    got = {c.id for c in rc.detect(text)}
    assert cue in got, (text, sorted(got))
    assert rc.detect(text)[0].category in rc.CATEGORY_ORDER


def _without(lang, cue_id, monkeypatch):
    """The pack with one cue (or the timing arithmetic) deleted - the negative control for a firing sentence."""
    pack = rc._pack(lang)
    if cue_id.startswith("timing"):
        gone = rc._Pack(**{**pack.__dict__, "timing": None})
    else:
        gone = rc._Pack(**{**pack.__dict__, "specs": tuple(s for s in pack.specs if s.id != cue_id)})
    monkeypatch.setattr(rc, "_pack", lambda code: gone if code == lang else _REAL_PACK(code))


@pytest.mark.parametrize("lang,text,cue", FIRES)
def test_red_when_removed_the_same_plan_without_that_cue_does_not_fire_it(lang, text, cue, monkeypatch):
    _without(lang, cue, monkeypatch)
    assert cue not in {c.id for c in rc.detect(text)}


def test_a_second_language_text_is_found_without_a_language_hint():
    """Short Spanish sentences full of cognates read as English by the language detector: the cues are tried in every language."""
    lang, cues = rc.detect_with_language("Esta noche voy a conducir cuatro horas y estoy agotado.")
    assert lang == "es" and cues
    assert rc.detect_with_language("I'm driving to my sister's tonight, three hours, and I'm exhausted.")[0] == "en"


def test_the_timing_arithmetic_is_not_just_a_pattern():
    assert rc.detect("Tomorrow I'll leave at 5 for my 9am flight.") == []                     # four hours of lead: fine
    assert [c.id for c in rc.detect("Tomorrow I'll leave at 7:45 for my 9am flight.")] == ["timing_tight"]
    assert [c.id for c in rc.detect("Tomorrow I'll leave at 6:30 for my 9am flight.")] == []     # 150 min >= the 120-minute lead
    assert [c.id for c in rc.detect("Tomorrow I'll leave at 9:30 for my 9am flight.")] == ["timing_late"]
    assert rc.detect("Tomorrow I'll leave at 8:30 for my 9am train.") == []                      # a train needs 15 minutes, not 120
    note = rc.detect("Tomorrow I'll leave at 7:45 for my 9am flight.")[0].note
    assert "flight (9am)" in note and "leave (7:45)" in note and "75" in note       # names the specific elements


# -- no fire ---------------------------------------------------------------------------------------------------
NO_FIRE = [
    # not an announced plan
    ("en", "Should I drive tonight after no sleep?"),                         # a question
    ("en", "Can I factory reset my laptop tonight?"),
    ("en", "I drove home tired yesterday and it was fine."),                  # a past story
    ("en", "I was going to bet my whole bonus on the match."),                # a plan that was dropped
    ("en", "I'm not going to drive tonight, I'm exhausted."),                 # a negated plan: the SAFE decision
    ("en", "If I drive tonight I'll be exhausted."),                          # hypothetical
    ("en", "What if I quit tomorrow with no savings?"),
    ("en", "My flight is at 9am and check out is at 8:30."),                  # a statement with no plan marker and no time anchor
    ("en", "Good night Zoe"), ("en", "bye, back tomorrow"), ("en", "ok"), ("en", "Are you there?"),
    ("en", "How are you today?"), ("en", "I'm tired."), ("en", "I had a few beers last night."),
    # a plan with no risk (these double as the 'good plan praised' guard)
    ("en", "I'm going to paint the fence on Saturday."),
    ("en", "Tomorrow I'll cook a proper lasagne for everyone."),
    ("en", "I'm going to call the vet tomorrow about the cat's vaccinations."),
    ("en", "I'll put twenty dollars a week into my savings."),
    ("en", "I'm going to walk to the shops this afternoon and get some milk."),
    ("en", "Tonight I'll read for an hour and go to bed early."),
    ("en", "I'm going to drive to the coast tomorrow, I slept really well."),     # a hazard with no impairment
    ("en", "I'm going to the gym tomorrow, my knee feels great."),                # exertion with no injury cue
    ("en", "Tomorrow I'm making a soup for lunch with chicken and vegetables."),
    ("en", "I'll text my sister that dinner is at seven."),                       # a send verb with no hostile word
    # a guarded risk
    ("en", "Tomorrow I'll delete all the photos on my phone, I have a backup on the cloud."),   # the absent guard
    ("en", "Tomorrow I'm doing the dentist at 3 and the gym at 3 on Friday."),                  # (see the day test below too)
    ("es", "¿Debería conducir esta noche si estoy agotado?"),
    ("es", "Ayer conduje agotado y no pasó nada."),
    ("es", "No voy a conducir esta noche, estoy agotado."),
    ("es", "Buenas noches Zoe"),
    ("es", "Voy a pintar la valla el sábado."),
    ("es", "Mañana voy a cocinar una lasaña para todos."),
]


@pytest.mark.parametrize("lang,text", NO_FIRE)
def test_no_cue_on(lang, text):
    assert rc.detect(text) == [], text


def test_two_things_on_different_days_are_not_a_clash():
    assert rc.detect("Tomorrow I'll see the dentist at 3 and on friday I'll see the gym at 3.") == []
    assert [c.id for c in rc.detect("Tomorrow I'll see the dentist at 3 and the gym at 3.")] == ["timing_double"]


#: (language, a plan that WOULD fire the cue but for its guard, the cue the guard holds back)
GUARDED = [
    ("en", "Tomorrow I'll delete all the photos on my phone, I have a backup on the cloud.", "delete_all_no_backup"),
    ("en", "I'm going to factory reset the laptop tonight, everything is backed up first.", "wipe_device_no_backup"),
    ("en", "Lunch today is chicken and a cola.", "thin_meal_junk"),
    ("en", "Tonight's dinner is just rice and beans.", "thin_meal_staple"),
    ("en", "I'm sick of the smell, so I'm going to quit smoking tomorrow.", "rash_while_angry"),
    ("es", "Mañana voy a borrar todos los archivos del portátil, ya tengo una copia de seguridad.", "delete_all_no_backup"),
    ("es", "Mañana el almuerzo es una ensalada con pollo y un refresco.", "thin_meal_junk"),
    ("es", "La cena de hoy es solo arroz con huevos.", "thin_meal_staple"),
]


@pytest.mark.parametrize("lang,text,cue", GUARDED)
def test_a_guard_is_what_holds_its_cue_back(lang, text, cue, monkeypatch):
    """Green with the guard, RED (the cue fires) with the guard deleted from the data: the guard, not luck, is what stays quiet."""
    assert cue not in {c.id for c in rc.detect(text)}
    pack = _REAL_PACK(lang)
    specs = tuple(rc._Spec(s.id, s.category, s.all, (), s.note) if s.id == cue else s for s in pack.specs)
    monkeypatch.setattr(rc, "_pack", lambda code: rc._Pack(**{**pack.__dict__, "specs": specs}) if code == lang else _REAL_PACK(code))
    assert cue in {c.id for c in rc.detect(text)}, "the example never reached the guard: it does not prove the guard"


def test_flying_as_a_passenger_is_not_a_hazard():
    assert rc.detect("I'm flying to Sydney tomorrow and I'm exhausted.") == []


def test_a_plain_statement_of_a_hazard_is_not_a_plan_but_the_same_words_with_intent_are():
    assert rc.detect("I get tired when I drive.") == []
    assert rc.detect("I'm going to drive and I'm exhausted.")


def test_a_pasted_essay_is_not_scanned():
    long = "I'm driving tonight and I'm exhausted. " + "word " * 200
    assert len(long) > rc.MAX_MESSAGE_CHARS and rc.detect(long) == []


# -- selection: at most two, from different categories ---------------------------------------------------------
def test_pick_is_at_most_two_one_per_category_in_priority_order():
    cues = [rc.Cue("timing", "a", "t"), rc.Cue("money_contract", "b", "m1"), rc.Cue("money_contract", "c", "m2"),
            rc.Cue("safety_health", "d", "s"), rc.Cue("prerequisite", "e", "p")]
    assert [c.id for c in rc.pick(cues)] == ["d", "b"]
    assert rc.pick([]) == []


def test_a_plan_with_many_risks_still_yields_one_short_block():
    text = ("Tomorrow I'm going to bet my whole bonus on a sure thing horse, drive there exhausted, "
            "and sign the paperwork without reading it. I haven't told anyone.")
    cues = rc.detect(text)
    assert len(cues) >= 4
    body = rc.render(rc.pick(cues), "en")
    assert body.count("\n- ") == rc.MAX_NOTES and len(body) <= rc.MAX_BLOCK_CHARS


# -- who gets it -----------------------------------------------------------------------------------------------
@pytest.fixture
def mdb(tmp_path, monkeypatch):
    """A real sqlite with the two tables the eligibility check reads, routed through persona_layer._db."""
    path = str(tmp_path / "r.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE member_modes (user_id TEXT PRIMARY KEY, mode TEXT NOT NULL, minor INTEGER NOT NULL DEFAULT 0,"
                " updated_by TEXT, updated_at TEXT)")
    con.execute("CREATE TABLE auth_users (user_id TEXT PRIMARY KEY, username TEXT, role TEXT NOT NULL)")
    con.executemany("INSERT INTO auth_users VALUES (?,?,?)", [
        ("ada_uid", "ada", "user"), ("mia_uid", "mia", "child"), ("teo_uid", "teo", "Teenager"), ("kid_uid", "kid", "user"),
        ("demo_user", "demo_user", "user"), ("test-jason", "test-jason", "admin"), ("demo_adult_1", "x", "user"),
        ("odd_uid", "odd", "visitor"), ("fam_uid", "fam", "family-admin")])
    con.execute("INSERT INTO member_modes VALUES ('kid_uid', 'kid', 1, 'o', 't')")
    con.commit()
    con.close()

    @asynccontextmanager
    async def ctx(db=None):
        if db is not None:
            yield db
            return
        conn = await aiosqlite.connect(path)
        try:
            yield conn
        finally:
            await conn.close()

    monkeypatch.setattr(pl, "_db", ctx)
    return path


PLAN = "I'm driving to my sister's tonight, three hours, and I'm exhausted."


@pytest.mark.parametrize("uid,want", [
    ("ada_uid", True), ("fam_uid", True),
    ("kid_uid", False),                       # member_modes: kid + minor
    ("mia_uid", False), ("teo_uid", False),   # child roles
    ("odd_uid", False),                       # unknown role: not positively an adult
    ("nobody_uid", False),                    # no account row
    ("demo_user", False), ("test-jason", False), ("demo_adult_1", False),   # synthetic-shaped ids: refused even with an adult role
    ("guest", False), ("anonymous", False), ("voice-guest", False), ("voice-daemon", False), ("", False), ("  ", False),
])
def test_who_gets_the_block(mdb, monkeypatch, uid, want):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    got = asyncio.run(rc.block_for(uid, PLAN))
    assert bool(got) is want, uid
    if want:
        assert got.startswith("## Before you answer, check")


def test_a_failed_lookup_fails_closed(monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")

    @asynccontextmanager
    async def broken(db=None):
        raise RuntimeError("database is down")
        yield  # pragma: no cover

    monkeypatch.setattr(pl, "_db", broken)
    assert asyncio.run(rc.block_for("ada_uid", PLAN)) == ""


def test_a_slow_lookup_fails_closed(monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    monkeypatch.setattr(mb, "LOOKUP_BUDGET_S", 0.05)

    async def slow(_uid, db=None):
        await asyncio.sleep(1)
        return True

    monkeypatch.setattr(mb, "_account_is_adult", slow)
    assert asyncio.run(rc.block_for("ada_uid", PLAN)) == ""


def test_no_database_is_read_when_nothing_fires_or_the_flag_is_off(monkeypatch):
    @asynccontextmanager
    async def boom(db=None):
        raise AssertionError("no lookup expected")
        yield  # pragma: no cover

    monkeypatch.setattr(pl, "_db", boom)
    assert asyncio.run(rc.block_for("ada_uid", PLAN)) == ""                       # flag off
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    assert asyncio.run(rc.block_for("ada_uid", "I'm going to paint the fence on Saturday.")) == ""   # no cue: no lookup


def test_the_synthetic_refusal_honours_the_allowlist_only_to_stay_consistent(adult, monkeypatch):
    """The id is only ever used to REFUSE: a harness id is refused; the repo-wide allowlist (a lab opting a demo id back in) is respected."""
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    assert asyncio.run(rc.block_for("demo_bar_ab12cd34", PLAN)) == ""
    monkeypatch.setenv("ZOE_SYNTHETIC_USER_ALLOWLIST", "demo_bar_ab12cd34")
    assert asyncio.run(rc.block_for("demo_bar_ab12cd34", PLAN)).startswith("## Before")


# -- the restraint tier ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["Are you there?", "...", "good night Zoe", "ok", "I've got to go, bye"])
def test_quiet_turns_get_no_block(adult, monkeypatch, text):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    assert asyncio.run(rc.block_for("ada_uid", text)) == ""


def test_a_farewell_that_also_announces_a_risky_plan_is_a_plan_not_a_goodbye(adult, monkeypatch):
    """clean_goodbye reads a WHOLE-utterance goodbye; 'night Zoe, I'm driving home exhausted' is not one, so the plan is still read."""
    import clean_goodbye

    text = "night Zoe, I'm driving home tonight and I'm exhausted"
    assert clean_goodbye.classify(text) == ""
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    assert asyncio.run(rc.block_for("ada_uid", text)).startswith("## Before you answer")


def test_a_pure_goodbye_never_gets_a_block_even_if_a_cue_matched(adult, monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    import clean_goodbye

    monkeypatch.setattr(rc, "detect_with_language", lambda *_a, **_k: ("en", [rc.Cue("safety_health", "x", "boom")]))
    assert clean_goodbye.classify("good night Zoe") == "farewell"
    assert asyncio.run(rc.block_for("ada_uid", "good night Zoe")) == ""
    assert asyncio.run(rc.block_for("ada_uid", PLAN)) != ""        # positive control: the same patched cue passes on a plan


def test_a_spoken_mute_turn_gets_no_block_even_if_a_cue_matched(adult, monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    import restraint

    monkeypatch.setattr(rc, "detect_with_language", lambda *_a, **_k: ("en", [rc.Cue("safety_health", "x", "boom")]))
    assert restraint.parse_utterance("don't mention that again") is not None
    assert asyncio.run(rc.block_for("ada_uid", "don't mention that again")) == ""


def test_an_unconfirmed_voice_gets_no_block(adult, monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    import restraint

    restraint.bind_verdict(False)
    try:
        assert asyncio.run(rc.block_for("ada_uid", PLAN)) == ""
    finally:
        restraint.bind_verdict(None)
    assert asyncio.run(rc.block_for("ada_uid", PLAN)) != ""


def test_an_enforce_muted_topic_gets_no_block(adult, monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    import restraint

    async def mutes(_uid):
        return [restraint.Mute("m1", frozenset(restraint.stems("sister")), "", "")]

    monkeypatch.setattr(restraint, "list_mutes", mutes)
    assert asyncio.run(rc.block_for("ada_uid", PLAN, "m1")) == ""                 # "sister" is muted and the plan mentions her
    assert asyncio.run(rc.block_for("ada_uid", "I'm driving to the coast tonight, three hours, and I'm exhausted.", "m2")) != ""
    monkeypatch.setenv("ZOE_RESTRAINT", "shadow")
    assert asyncio.run(rc.block_for("ada_uid", PLAN, "m3")) != ""                 # mutes only withhold in enforce


# -- once per conversation ---------------------------------------------------------------------------------------
def test_the_same_concern_is_raised_once_per_conversation(adult, monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    first = asyncio.run(rc.block_for("ada_uid", PLAN, "convo-1"))
    assert first
    assert asyncio.run(rc.block_for("ada_uid", "I'm driving anyway tonight, still exhausted.", "convo-1")) == ""   # not again
    assert asyncio.run(rc.block_for("ada_uid", PLAN, "convo-2")) == first                                         # another conversation
    assert asyncio.run(rc.block_for("bob_uid", PLAN, "convo-1")) == first                                         # another member
    other = asyncio.run(rc.block_for("ada_uid", "Tomorrow I'll sign the lease without reading it.", "convo-1"))
    assert other.startswith("## Before you answer") and "Contract:" in other and "Safety:" not in other          # a NEW concern still comes through


def test_the_raised_memory_expires_and_is_bounded(adult, monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    assert asyncio.run(rc.block_for("ada_uid", PLAN, "c"))
    for k in list(rc._raised):
        rc._raised[k] = {i: t - rc.RAISED_TTL_S - 1 for i, t in rc._raised[k].items()}
    assert asyncio.run(rc.block_for("ada_uid", PLAN, "c"))                         # expired: raised again
    monkeypatch.setattr(rc, "RAISED_MAX", 3)
    for i in range(10):
        rc._remember(f"u{i}", "s", [rc.Cue("c", "x", "n")])
    assert len(rc._raised) == 3


# -- modes ---------------------------------------------------------------------------------------------------------
def test_mode_reads_the_flag_per_call(monkeypatch):
    assert rc.mode() == "off" and not rc.enabled()
    for raw, want in (("on", "on"), ("1", "on"), ("TRUE", "on"), ("shadow", "shadow"), ("Shadow", "shadow"), ("off", "off"),
                      ("", "off"), ("enforce", "off"), ("maybe", "off")):
        monkeypatch.setenv("ZOE_RISK_CUES", raw)
        assert rc.mode() == want, raw


def test_shadow_logs_counts_and_categories_only_and_injects_nothing(adult, monkeypatch, caplog):
    monkeypatch.setenv("ZOE_RISK_CUES", "shadow")
    secret = "my sister's place"
    with caplog.at_level(logging.INFO, logger="risk_cues"):
        out = asyncio.run(rc.block_for("ada_uid", f"I'm driving to {secret} tonight, three hours, and I'm exhausted."))
    assert out == ""
    line = [r.getMessage() for r in caplog.records if "RISK_CUES" in r.getMessage()]
    assert len(line) == 1 and "mode=shadow" in line[0] and "n=1" in line[0] and "cats=safety_health" in line[0]
    assert "sister" not in caplog.text and "driving" not in caplog.text and "exhausted" not in caplog.text


# -- the seam: flag off is today's bytes ------------------------------------------------------------------------
SHAPES = [PLAN, "Hello there", "Tomorrow I'll sign the lease without reading it.", "Are you there?"]


def test_flag_off_is_byte_identical_for_every_shape(sent, adult):
    for msg in SHAPES:
        assert sent(msg) == f" zoe-uid:zed_uid\n{msg}"
        assert sent(msg, uid="guest") == f" zoe-uid:guest\n{msg}"
        assert sent(msg, uid="") == msg
        assert sent(msg, replay_isolation=True) == f" zoe-replay:1\n zoe-uid:zed_uid\n{msg}"


def test_shadow_is_byte_identical_to_off(sent, adult, monkeypatch):
    off = [sent(m, session=f"a{i}") for i, m in enumerate(SHAPES)]
    monkeypatch.setenv("ZOE_RISK_CUES", "shadow")
    assert [sent(m, session=f"b{i}") for i, m in enumerate(SHAPES)] == off


def test_flag_on_adds_exactly_one_block_after_the_users_words(sent, adult, monkeypatch):
    off = sent(PLAN)
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    on = sent(PLAN)
    body = rc.render(rc.pick(rc.detect(PLAN)), "en")
    assert on == f"{off}\n{zc._RISK_BLOCK_OPEN}\n{body}\n{zc._RECALL_BLOCK_CLOSE}"
    assert on.count("## Before you answer, check") == 1 and on.count(zc._RISK_BLOCK_OPEN) == 1
    # the delimiters are the ones the sidecar's stale-block elision already knows (no sidecar change)
    assert zc._RISK_BLOCK_OPEN.startswith("[MEMORY CONTEXT") and zc._RISK_BLOCK_OPEN.endswith("]")
    assert zc._RECALL_BLOCK_CLOSE == "[END MEMORY CONTEXT]"


def test_flag_on_without_a_cue_is_still_todays_bytes(sent, adult, monkeypatch):
    off = [sent(m, session=f"a{i}") for i, m in enumerate(SHAPES[1:])]
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    on = [sent(m, session=f"b{i}") for i, m in enumerate(SHAPES[1:2] + SHAPES[3:])]
    assert on == [off[0], off[2]]                                 # "Hello there" and "Are you there?" carry nothing


def test_the_conversation_id_reaches_the_once_per_conversation_memory(sent, adult, monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    assert "## Before you answer" in sent(PLAN, session="x1")
    assert "## Before you answer" not in sent(PLAN, session="x1")      # same conversation: raised once
    assert "## Before you answer" in sent(PLAN, session="x2")


def test_a_minor_and_a_guest_get_no_block_on_the_wire(sent, mdb, monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    assert "## Before you answer" not in sent(PLAN, uid="kid_uid")
    assert "## Before you answer" not in sent(PLAN, uid="guest")
    assert "## Before you answer" in sent(PLAN, uid="ada_uid")


def test_one_block_per_turn_the_cue_yields_to_every_other_block(sent, adult, monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    base = sent(PLAN, session="p0")
    assert "## Before you answer" in base

    async def hop(*_a, **_k):
        return "[HOP]"

    monkeypatch.setattr(zc, "_hop_context_block", hop)
    out = sent(PLAN, session="p1")
    assert "[HOP]" in out and "## Before you answer" not in out


def _direct(monkeypatch, message, **kwargs):
    """The composing function itself, where the day brief and a proactive raise arrive as keyword arguments."""
    cap = []

    async def fake_wire2(session_id, payload, **kw):
        cap.append(json.loads(payload)["body"])
        yield "ok"

    monkeypatch.setattr(zc, "_run_turn_aggregated_wire2", fake_wire2)

    async def go():
        return [c async for c in zc._run_flue_brain_streaming_turn(message, "sd", "zed_uid", **kwargs)]

    asyncio.run(go())
    return cap[0]


def test_the_cue_yields_to_the_day_brief_and_a_proactive_raise(adult, monkeypatch):
    monkeypatch.setenv("ZOE_RISK_CUES", "on")
    assert "## Before you answer" in _direct(monkeypatch, PLAN)                       # positive control: the same call, no other block
    rc._reset_state()
    raised = _direct(monkeypatch, PLAN, raise_block="[RAISE x]\nask\n[END RAISE]")
    assert "[RAISE x]" in raised and "## Before you answer" not in raised
    rc._reset_state()
    brief = _direct(monkeypatch, PLAN, day_brief_block="[BRIEF x]\nb\n[END BRIEF]")
    assert "[BRIEF x]" in brief and "## Before you answer" not in brief


def test_the_seam_has_one_construction_site_and_it_is_after_the_other_blocks():
    from pathlib import Path

    src = Path(zc.__file__).read_text()
    assert src.count("risk_block = await _risk_cue_block(message, uid, session_id)") == 1
    assert src.count('brain_message = f"{brain_message}\\n{risk_block}"') == 1
    assert src.index("offer_block = await _pending_offer_block(uid)") < src.index("risk_block = await _risk_cue_block(")


def test_the_lexicon_identity_of_the_other_sections_is_untouched():
    """Adding a section to the lexicon files must not change any other section: the manner block text is the same bytes."""
    import manner_block

    assert manner_block.block_text("en").startswith("With people: when someone shares a feeling")
    assert "risk_cues" in lexicons.load("en") and "risk_cues" in lexicons.load("es")
    assert "risk_cues" not in lexicons.load("fr")        # a language with no entry gets no block, never English's
    assert rc.detect("Je vais conduire ce soir, je suis épuisé et je n'ai pas dormi.") == []


def test_the_data_digest_changes_with_the_data(monkeypatch):
    a = rc.data_digest()
    real = lexicons.load
    monkeypatch.setattr(lexicons, "load", lambda code: {**real(code), "risk_cues": {"x": 1}} if code == "en" else real(code))
    assert rc.data_digest() != a
