"""self_model: Zoe knows what she is, what she can do today and what she cannot - generated from the real registries.

One group per promise, each with a break-the-fix control (swap the protection out and the test must go red):

  registry    the tool groups/tools come from the sidecar's own TypeScript (parsed), the committed snapshot is current, every
              router tool is a tool the brain has, every group/flag/unsupported id has a line in every enabled language
  detection   the self-questions per language (rows), what is NOT a self-question (commands, small talk), "can you X" only
              answered for X on the closed list; an un-enabled language contributes nothing
  answers     the capabilities answer names the surfaces and three concrete things; the honest "no" for an unsupported ask;
              the truthful wake-word answer (the audio-kept and background-capture clauses appear only when they are true)
  state       a dark flag moves into/out of "not switched on today" with the environment; a down service is not offered
  block       byte-stable static prefix, counts only (never names), capped, sections dropped in the declared order
  shadow      ZOE_SELF_MODEL=shadow (the default) / off: the turn is untouched - same result, no IO, one log line
  wiring      the fast_tiers wrapper, the provenance ledger, the "what did you just use?" answer

Synthetic data only. No network, no database, no live service (probes and counts are stubbed).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import pytest

import self_model as sm

pytestmark = pytest.mark.ci_safe

REPO = Path(sm.__file__).resolve().parents[2]
UID = "demo_bar_00000001"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Every test starts from the real registry and a clean environment for the flags this module reads."""
    for flag in [d["flag"] for d in sm.neutral()["dark_flags"]] + ["ZOE_SELF_MODEL", "ZOE_VOICE_SAVE_AUDIO", "ZOE_ASK_TO_REMEMBER",
                                                                    "ZOE_MEMORY_PROVENANCE_ANSWERS", "ZOE_FORGOTTEN_SHIELD_DAYS"]:
        monkeypatch.delenv(flag, raising=False)
    sm.reset()
    yield
    sm.reset()


def run(coro):
    return asyncio.run(coro)


UP = sm.Live(now=1_791_500_000.0, night_open=False, music="ok", home="ok")


def ask(text, **kw):
    q = sm.classify(text)
    assert q is not None, text
    return run(sm.answer(q, kw.pop("user", UID), live=kw.pop("live", UP), **kw))


# ── registry ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_registry_parsed_from_the_sidecar_sources():
    reg = sm.parse_registry((REPO / "labs/flue-zoe-brain-2x/src/tools/tool-groups.ts").read_text(),
                            (REPO / "labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts").read_text())
    names = [g for g, _ in reg["groups"]]
    assert len(names) >= 10 and "weather" in names and "memory" in names
    assert ("get_weather" in dict(reg["groups"])["weather"]) and reg["core"][:2] == ["get_time", "recall_memory"]
    assert reg["activator"] == "activate_abilities" and "activate_abilities" in reg["tools"]
    assert reg["ungrouped"] == ["web_search"]            # the one tool that is always disclosed and flag-gated


def test_snapshot_is_current():
    """The committed snapshot is what a deploy without labs/ reads: it must equal the parse (regenerate with --snapshot)."""
    live = sm._registry_from_sources()
    assert live, "sidecar sources not readable"
    assert json.loads(sm.SNAPSHOT.read_text()) == json.loads(json.dumps(live))


def test_every_router_tool_is_a_tool_the_brain_has():
    reg = sm.registry()
    have = set(reg["tools"])
    for domain, tools in sm.router_domains().items():
        assert set(tools) <= have, (domain, set(tools) - have)


def test_every_group_flag_and_unsupported_id_has_a_line_in_every_language():
    groups, nf = set(sm.groups()), sm.neutral()
    for lang in sm.languages():
        lx = sm.lex(lang)
        assert groups == set(lx["groups"]), (lang, groups ^ set(lx["groups"]))
        for g, spec in lx["groups"].items():
            assert {"label", "do", "ask", "words"} <= set(spec) and spec["words"], (lang, g)
        assert {d["key"] for d in nf["dark_flags"]} == set(lx["dark"]), lang
        assert {u["id"] for u in nf["unsupported"]} == set(lx["unsupported"]), lang
        assert {r["key"] for r in nf["memory_rules"]} == set(lx["memory"]), lang
        assert {e["key"] for e in nf["egress"]} == set(lx["egress"]), lang
        assert set(nf["surfaces"]) == set(lx["surfaces"]), lang
        assert set(sm.KINDS) <= set(lx["cues"]), lang
        for u in nf["unsupported"]:
            assert u["alt"] in groups, u


def test_every_flag_named_in_the_neutral_file_is_in_the_inventory():
    inv = json.loads((REPO / "docs/knowledge/flag-inventory.json").read_text())["flags"]["prod"]
    nf = sm.neutral()
    flags = [d["flag"] for d in nf["dark_flags"]] + [r["flag"] for r in nf["memory_rules"] if r.get("flag")] \
        + [e["flag"] for e in nf["egress"] if e.get("flag")]
    assert not [f for f in flags if f not in inv]
    assert "ZOE_SELF_MODEL" in inv                                         # registered (the inventory is regenerated, never hand-edited)
    for d in nf["dark_flags"]:                                             # the neutral default agrees with the code default
        inv_default = sm.inventory_default(d["flag"])
        assert inv_default in (None, d["default"]), (d["flag"], inv_default, d["default"])


def test_no_english_prose_in_the_module():
    """The words live in lexicons_data (blueprint 2.9): the module may not carry sentence literals."""
    src = (Path(sm.__file__)).read_text()
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    code = re.sub(r"#.*", "", code)
    for lit in re.findall(r'"([^"\n]{12,})"', code):
        assert not re.search(r"\b(?:I'm|I can|you can|your|please|sorry)\b", lit, re.I), lit


# ── detection ─────────────────────────────────────────────────────────────────────────────────────────────────────────

ROWS = {
    "en": {
        "capabilities": ["What can you do?", "hey zoe what can you do for me", "what are you able to do", "How can you help me?"],
        "can_you": ["Can you order groceries?", "could you call my mum", "are you able to book a flight"],
        "tools": ["what tools do you have?", "what are you connected to"],
        "how_remember": ["How do you remember things?", "how does your memory work", "do you forget things"],
        "listening": ["Are you always listening?", "are you recording me", "Is the microphone always on?", "do you listen all the time"],
        "know_me": ["Do you know who I am?", "do you recognise me"],
        "see_me": ["Can you see me?", "do you have a camera", "are you watching me"],
        "running_on": ["What are you running on?", "where do you run", "is this local", "are you in the cloud", "are you chatgpt"],
        "cannot": ["What can't you do?", "what are your limits"],
        "made_by": ["Who made you?", "who built you"],
        "who_are_you": ["Who are you?", "tell me about yourself"],
        "used_to_answer": ["What did you just use to answer that?", "which tool did you use", "how did you work that out"],
    },
    "es": {
        "capabilities": ["¿Qué puedes hacer?", "oye zoe que puedes hacer", "en qué puedes ayudarme"],
        "can_you": ["¿Puedes pedir comida?", "puedes llamar a mi madre"],
        "listening": ["¿Estás siempre escuchando?", "me estás grabando"],
        "how_remember": ["¿Cómo funciona tu memoria?"],
        "running_on": ["¿Estás en la nube?"],
        "cannot": ["¿Qué no puedes hacer?"],
        "made_by": ["¿Quién te hizo?"],
    },
}

NOT_SELF_QUESTIONS = [
    "What's the weather like today?", "set a timer for five minutes", "turn off the kitchen lights", "play some jazz",
    "what's on my calendar tomorrow", "remember that my favourite tea is peppermint", "who is Marisol?", "what time is it",
    "I can't find my keys", "add milk to my shopping list", "tell me a joke", "what do you know about me", "why did you say that",
    "my sister can do a handstand", "do you know what the capital of Peru is", "how are you", "what do you think about jazz",
    "I think you can do better than that, you know I was only asking about the weather and whether it will rain on Saturday",
    "Where do I live?", "what are you doing for dinner tomorrow", "cuéntame un chiste", "pon música", "enciende la luz",
    "tell me what you can do if the power goes out in the kitchen and the fridge stops working and everything in it is going off tonight",
    "",
]


@pytest.mark.parametrize("lang,kind,text", [(l, k, t) for l, kinds in ROWS.items() for k, ts in kinds.items() for t in ts])
def test_self_questions_are_recognised(lang, kind, text):
    q = sm.classify(text)
    assert q is not None and (q.kind, q.lang) == (kind, lang), (text, q)


@pytest.mark.parametrize("text", NOT_SELF_QUESTIONS)
def test_ordinary_turns_are_not_self_questions(text):
    assert sm.classify(text) is None, text


def test_can_you_is_answered_only_for_the_closed_unsupported_list():
    """A request phrased "can you..." is a REQUEST: it goes on to the router and the brain, never intercepted."""
    for text in ("can you set a timer for 5 minutes", "can you turn off the lights", "can you play some music", "can you read me a book",
                 "can you call me Bob", "could you remind me to call the plumber", "can you add milk to my list"):
        q = sm.classify(text)
        assert q is not None and q.kind == "can_you"
        assert sm.classify_can_you(q.x, q.lang)[0] != "unsupported", text
        assert run(sm.answer(q, UID, live=UP)) is None, text
    for text, uid in (("can you order groceries", "purchase"), ("can you please book a table", "purchase"), ("can you email my boss", "message_others"),
                      ("can you call my mum", "phone"), ("can you pay my bills", "money"), ("can you turn off the oven", "devices"),
                      ("can you find me a flight to Perth", "travel")):
        q = sm.classify(text)
        assert sm.classify_can_you(q.x, q.lang) == ("unsupported", uid), text


def test_the_requested_action_decides_not_a_word_inside_its_object():
    """Greptile #1965: "add a fan to my shopping list" and "remind me to book a flight" were refused because ``fan`` / ``flight``
    were matched anywhere. A supported ACTION wins; a closed-list item counts only as the object of one of its own verbs."""
    for text in ("Can you add a fan to my shopping list?", "Can you remind me to book a flight tomorrow?", "can you put a fan on my list",
                 "can you set a timer for the oven", "could you schedule a hotel call for Friday", "can you note that the taxi is booked",
                 "can you turn on the lights in the garage", "can you play a song about a train ticket",
                 "¿Puedes añadir un ventilador a mi lista de la compra?", "puedes recordarme reservar un vuelo mañana"):
        q = sm.classify(text)
        assert q is not None and q.kind == "can_you", text
        assert sm.classify_can_you(q.x, q.lang)[0] == "supported", (text, sm.classify_can_you(q.x, q.lang))
        assert run(sm.answer(q, UID, live=UP)) is None, text
    for text, uid in (("can you turn off the fan", "devices"), ("could you please switch the heater on", "devices"), ("can you lock the front door", "devices"),
                      ("can you open the garage", "devices"), ("can you find me a flight to Perth", "travel"), ("can you get me a taxi", "travel"),
                      ("puedes apagar el ventilador", "devices"), ("puedes buscarme un vuelo a Lima", "travel")):
        q = sm.classify(text)
        assert sm.classify_can_you(q.x, q.lang) == ("unsupported", uid), text
        assert run(sm.answer(q, UID, live=UP)), text


def test_a_refusal_never_beats_a_supported_action_through_the_tier(monkeypatch):
    monkeypatch.setenv("ZOE_SELF_MODEL", "enforce")
    assert run(sm.tier("can you turn off the fan", UID)) is not None          # control: the tier does refuse a real one
    for text in ("Can you add a fan to my shopping list?", "Can you remind me to book a flight tomorrow?"):
        assert run(sm.tier(text, UID)) is None


def test_a_language_without_an_enabled_file_contributes_nothing():
    assert sm.classify("何ができますか") is None and sm.classify("あなたは何ができますか") is None
    assert sm.lex("ja") == {} and sm.lex("") == {}


def test_unsupported_ask_in_spanish_is_answered_in_spanish():
    out = ask("¿Puedes pedir comida?")
    assert out.startswith("No puedo pedir") and "lista de la compra" in out and "I can't" not in out


# ── answers ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_capabilities_names_the_surfaces_and_three_concrete_things():
    out = ask("what can you do?")
    lx = sm.lex("en")
    assert "touch panel" in out and "Telegram" in out and "web chat" in out and "Hey Zoe" in out
    do_phrases = [s["do"] for s in lx["groups"].values()]
    assert sum(1 for p in do_phrases if p in out) >= 3
    assert len(out) < 700 and out.count(".") <= 6
    assert not any(n in out for n in sm._unsupported_names(lx))              # nothing from the cannot list is offered


def test_capabilities_on_voice_is_short():
    out = ask("what can you do?", channel="voice")
    assert len(out) < len(ask("what can you do?")) and "I can also help with" not in out and "Just ask me for any of it" in out


def test_unsupported_ask_is_an_honest_no_with_what_she_can_do_instead():
    out = ask("can you order groceries?")
    assert out.startswith("No - I can't order or buy") and "shopping list" in out and out.endswith("Want me to?")


def test_instead_offer_is_dropped_when_the_alternative_is_not_available(monkeypatch):
    live = sm.Live(now=UP.now, music="ok", home="down")
    out = ask("can you turn off the oven", live=live)
    assert out.startswith("No - I can only control the lights") and "I can do the lights, though" not in out   # the lights bridge is down


def test_dark_capabilities_are_listed_as_not_today_and_follow_the_environment(monkeypatch):
    out = ask("what can't you do?")
    assert "Not switched on yet:" in out and sm.lex("en")["dark"]["web_lookup"] in out
    monkeypatch.setenv("ZOE_WEB_SEARCH_TOOL", "1")
    monkeypatch.setenv("ZOE_PERSONA_LAYER", "off")
    sm.reset()
    keys = [k for k, _ in sm.facts().dark]
    assert "web_lookup" not in keys and "persona" in keys and "web_lookup" in sm.facts().egress
    assert "a web lookup" in ask("what are you running on?")                  # it leaves the box only when it is on
    assert "a web lookup" not in (sm.build_block("en") or "")


def test_shadow_state_counts_as_not_today():
    assert dict(sm.facts().dark)["retire_fact"] == "shadow"


def test_listening_answer_is_the_truthful_wake_word_answer(monkeypatch):
    base = ask("are you always listening?")
    assert 'only listen for "Hey Zoe"' in base and "never to the cloud" in base and "kept on this box" not in base
    assert "Background capture" not in base
    monkeypatch.setenv("ZOE_VOICE_SAVE_AUDIO", "1")
    sm.reset()
    assert "Short clips" in ask("are you always listening?")
    sm.reset()


def test_listening_answer_follows_the_real_capture_config(monkeypatch):
    """Greptile #1965: with background capture on, "nothing you say is recorded or sent anywhere before the wake word" is FALSE and
    appending a clause does not undo it. The listening answer is a function of the capture config, in both languages."""
    promise = {"en": "nothing you say is recorded or sent anywhere", "es": "nada de lo que dices se graba"}
    off = {"en": ask("are you always listening?"), "es": ask("¿Estás siempre escuchando?")}
    for lang, out in off.items():
        assert promise[lang] in out, (lang, out)
    on_by_rows = sm.Live(now=UP.now, ambient_recent=7)
    for lang, text in (("en", "are you always listening?"), ("es", "¿Estás siempre escuchando?")):
        out = ask(text, live=on_by_rows)
        assert promise[lang] not in out, (lang, out)
        assert ("without the wake word" in out) if lang == "en" else ("sin que digas la palabra de activacion" in out), out
        assert "Until you say it" not in out and "Hasta que lo dices" not in out, out
    monkeypatch.setenv("AMBIENT_CAPTURE_ENABLED", "true")          # zoe-data's own env says so, no rows yet
    sm.reset()
    for lang, text in (("en", "are you always listening?"), ("es", "¿Estás siempre escuchando?")):
        out = ask(text)
        assert promise[lang] not in out, (lang, out)
        assert "keep that text" in out or "guardo ese texto" in out, out
    monkeypatch.setenv("AMBIENT_CAPTURE_ENABLED", "false")
    sm.reset()
    assert promise["en"] in ask("are you always listening?")


def _self_model_files():
    return sorted((Path(sm.__file__).with_name("lexicons_data")).glob("self_model_??.json"))


def test_every_advertised_off_record_phrase_is_a_working_cue(monkeypatch):
    """Greptile #1965: the Spanish answer told people to say "que quede entre nosotros" while ``parse_off_record`` knew only English
    cues, so a user who followed the instruction was still remembered. The phrases each language file advertises are data
    (``off_record_phrases``); every one must (a) appear in that language's memory answer, (b) be parsed as a bare cue that arms the
    next turn, (c) mark a same-turn disclosure and (d) close a turn after a comma - in EVERY self-model language file."""
    import memory_provenance as mp

    files = _self_model_files()
    assert {f.stem[-2:] for f in files} >= {"en", "es"}
    monkeypatch.setenv("ZOE_MEMORY_PROVENANCE_ANSWERS", "1")
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        phrases = d.get("off_record_phrases") or []
        assert phrases, f"{f.name}: no off_record_phrases - the answer advertises a cue the parser must know"
        said = (d["memory"]["off_record"] + " " + d["memory"]["never_kept"]).casefold()
        quoted = re.findall(r'"([^"]+)"', d["memory"]["off_record"])
        assert quoted and all(q in phrases for q in quoted), (f.name, quoted, phrases)
        for ph in phrases:
            assert ph.casefold() in said, (f.name, ph)
            bare = mp.parse_off_record(ph)
            assert bare is not None and bare.payload == "", (f.name, ph, bare)
            lead = mp.parse_off_record(f"{ph}: my sister is pregnant and I have not told anyone")
            assert lead is not None and lead.payload.startswith("my sister"), (f.name, ph, lead)
            tail = mp.parse_off_record(f"My sister is pregnant and I have not told anyone, {ph}")
            assert tail is not None and tail.payload.startswith("My sister"), (f.name, ph, tail)
            uid = f"demo_otr_{f.stem[-2:]}_{abs(hash(ph)) % 10000}"
            assert mp.claim_turn(uid, ph) is False                       # the bare cue arms the next turn ...
            assert mp.claim_turn(uid, "something private I said next") is True   # ... and the next turn is off the record
            mp.reset(uid)


def test_a_question_about_the_cue_is_not_a_cue_in_spanish_either():
    import memory_provenance as mp

    for t in ("¿qué significa extraoficial?", "¿es confidencial este documento?", "quiero un informe confidencial para mañana por favor"):
        assert mp.parse_off_record(t) is None, t


def test_memory_answer_states_only_rules_whose_code_is_on(monkeypatch):
    out = ask("how do you remember things?")
    for needle in ("with the day you said them", "off the record", "stays forgotten for good", "why did you say that"):
        assert needle in out, needle
    monkeypatch.setenv("ZOE_MEMORY_PROVENANCE_ANSWERS", "0")
    monkeypatch.setenv("ZOE_FORGOTTEN_SHIELD_DAYS", "30")
    sm.reset()
    out2 = ask("how do you remember things?")
    assert "off the record\" and I won't" not in out2 and "why did you say that" not in out2 and "for 30 days" in out2


def test_down_service_is_not_offered_and_is_said_plainly():
    down = sm.Live(now=UP.now, music="down", home="ok")
    out = ask("what can you do?", live=down)
    assert "play music" not in out and "Heads up: music is not answering" in out
    # the BLOCK's static prefix does not change with a service going down (byte-stable); the state line says so
    assert "music not answering" in sm.build_block("en", down) and "music working" in sm.build_block("en", UP)
    asleep = sm.Live(now=UP.now, music="asleep")
    assert "play music" in ask("what can you do?", live=asleep) or "music" in ask("what can you do?", live=asleep)


def test_tools_answer_counts_come_from_the_registry(monkeypatch):
    reg = sm.registry()
    real = len(set(reg["tools"]) - {reg["activator"], "web_search"})       # the optional, flag-gated tool is not counted while it is off
    assert sm.tool_count() == real and f"{real} tools in {len(sm.groups())} groups" in ask("what tools do you have?")
    monkeypatch.setenv("ZOE_WEB_SEARCH_TOOL", "1")
    sm.reset()
    assert sm.tool_count() == real + 1


def test_know_me_gives_the_asker_their_own_name_and_never_without_a_verified_voice(monkeypatch):
    async def name(_uid):
        return "Ines"

    async def enrol(_uid):
        return True, False

    monkeypatch.setattr(sm, "_own_name", name)
    monkeypatch.setattr(sm, "_enrolment", enrol)
    out = ask("do you know who I am?")
    assert "you're Ines" in out and "I know your voice too" in out and "your face" not in out
    assert "couldn't tell who was speaking" in ask("do you know who I am?", speaker_verified=False) and "Ines" not in ask(
        "do you know who I am?", speaker_verified=False)
    assert "not signed in" in ask("do you know who I am?", user="guest")


def test_see_me_follows_the_face_flag(monkeypatch):
    assert "Face recognition is switched off" in ask("can you see me?")
    monkeypatch.setenv("ZOE_FACE_ID_ENABLED", "true")
    sm.reset()
    assert "Face recognition is switched on" in ask("can you see me?")


# ── the block ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_block_is_byte_stable_static_first_volatile_last():
    a = sm.build_block("en", sm.Live(now=1_791_500_000.0, accounts=3, voices=2, faces=1))
    b = sm.build_block("en", sm.Live(now=1_791_599_999.0, accounts=3, voices=2, faces=1, night_open=True, music="down"))
    assert a != b and a.split("\nHousehold:")[0] == b.split("\nHousehold:")[0]
    assert sm.build_block("en", UP) == sm.build_block("en", UP)
    assert a.index("Household:") < a.index("Right now:") and a.index("I cannot:") < a.index("Household:")


def test_block_carries_counts_only_never_names():
    block = sm.build_block("en", sm.Live(now=UP.now, accounts=4, voices=2, faces=1))
    assert "4 accounts, 2 with a voice profile, 1 with a face profile" in block
    assert not re.search(r"\b[A-Z][a-z]+ (?:and|,) [A-Z][a-z]+\b", block.split("Household:")[1])


def test_block_is_capped_and_drops_in_the_declared_order():
    full = sm.build_block("en", sm.Live(now=UP.now, accounts=3))
    assert len(full) <= sm.neutral()["max_block_chars"]
    small = sm.build_block("en", sm.Live(now=UP.now, accounts=3), cap=len(full) - 10)
    assert "I run on:" not in small and "I live on:" in small and "I cannot:" in small      # runs_on goes first
    tiny = sm.build_block("en", sm.Live(now=UP.now, accounts=3), cap=900)
    assert len(tiny) <= 900 and "ABOUT ME" in tiny and "I can:" in tiny


def test_block_in_spanish_uses_the_spanish_file():
    assert "SOBRE MI" in sm.build_block("es", UP) and "SOBRE MI" not in sm.build_block("en", UP)
    assert sm.build_block("ja", UP) == ""


# ── break the fix ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_break_the_fix_a_new_group_in_the_sidecar_reaches_the_answer_and_a_missing_line_is_caught(monkeypatch):
    g = (REPO / "labs/flue-zoe-brain-2x/src/tools/tool-groups.ts").read_text()
    t = (REPO / "labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts").read_text()
    grown = g.replace("memory: ['remember_fact'", "garden: ['water_plants'],\n  memory: ['remember_fact'")
    reg = sm.parse_registry(grown, t)
    assert "garden" in [x for x, _ in reg["groups"]]
    monkeypatch.setattr(sm, "registry", lambda: reg)
    sm.facts.cache_clear()
    assert "garden" in sm.groups()
    missing = set(sm.groups()) - set(sm.lex("en")["groups"])
    assert missing == {"garden"}              # the drift guard (test_every_group_...) would be red: the group has no label/ask/words


def test_break_the_fix_removing_the_unsupported_entry_stops_the_honest_no(monkeypatch):
    monkeypatch.setattr(sm, "_unsupported_res", lambda lang: ())
    q = sm.classify("can you order groceries?")
    assert run(sm.answer(q, UID, live=UP)) is None                           # the control: without the entry nothing is said


def test_break_the_fix_a_hand_written_capability_is_not_in_the_generated_answer():
    out = ask("what can you do?") + ask("what tools do you have?")
    for invented in ("order", "book", "phone", "email", "pay", "thermostat"):
        assert invented not in out.lower().replace("for example", ""), invented


# ── shadow / off: the turn is untouched ───────────────────────────────────────────────────────────────────────────────

SEED_SET = [t for kinds in ROWS["en"].values() for t in kinds] + NOT_SELF_QUESTIONS + [t for kinds in ROWS["es"].values() for t in kinds]


@pytest.fixture
def no_io(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("shadow/off must not touch IO")

    monkeypatch.setattr(sm, "probe", boom)
    monkeypatch.setattr(sm, "_count", boom)
    monkeypatch.setattr(sm, "_own_name", boom)


@pytest.mark.parametrize("env", [None, "shadow", "off", "0", "garbage"])
def test_shadow_and_off_leave_the_turn_byte_identical(monkeypatch, caplog, no_io, env):
    if env is not None:
        monkeypatch.setenv("ZOE_SELF_MODEL", env)
    expect_log = env in (None, "shadow", "garbage")
    caplog.set_level(logging.INFO, logger="self_model")
    for text in SEED_SET:
        assert run(sm.tier(text, UID, "s1", channel="chat")) is None, text
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("SELF_MODEL")]
    questions = [t for t in SEED_SET if sm.classify(t) is not None]
    if expect_log:
        assert len(lines) == len(questions) and all(re.match(r"SELF_MODEL mode=\w+ kind=\w+ lang=\w+ verdict=", l) for l in lines)
        assert sum("would_answer=1" in l for l in lines) == sum(
            1 for t in questions if sm.classify(t).kind != "can_you" or sm.classify_can_you(sm.classify(t).x, sm.classify(t).lang)[0] == "unsupported")
    else:
        assert not lines


def test_the_fast_tiers_wrapper_returns_exactly_the_core_result_in_shadow(monkeypatch, no_io):
    import fast_tiers

    sentinel = object()
    seen = []

    async def core(text, user_id, session_id, **kw):
        seen.append((text, user_id, session_id, dict(kw)))
        return sentinel

    async def no_prov(*a, **k):
        return None

    monkeypatch.setattr(fast_tiers, "_resolve_core", core)
    monkeypatch.setattr(fast_tiers, "_provenance_tier", no_prov)
    for env in (None, "shadow", "off"):
        if env:
            monkeypatch.setenv("ZOE_SELF_MODEL", env)
        for text in SEED_SET:
            kw = {"channel": "chat", "extra_ctx": {"speaker_verified": True}}
            assert run(fast_tiers.resolve(text, UID, "s1", **kw)) is sentinel
            assert seen[-1] == (text, UID, "s1", kw)             # the core saw exactly what it always saw


def test_enforce_answers_the_self_questions_and_only_those(monkeypatch):
    import fast_tiers

    monkeypatch.setenv("ZOE_SELF_MODEL", "enforce")
    monkeypatch.setattr(sm, "live_state", lambda **k: _coro(UP))
    sentinel = object()

    async def core(*a, **k):
        return sentinel

    async def no_prov(*a, **k):
        return None

    monkeypatch.setattr(fast_tiers, "_resolve_core", core)
    monkeypatch.setattr(fast_tiers, "_provenance_tier", no_prov)
    res = run(fast_tiers.resolve("what can you do?", UID, "s1", channel="chat"))
    assert res is not sentinel and res.tier == "self_model" and res.domain == "self_model" and "touch panel" in res.reply
    assert run(fast_tiers.resolve("can you set a timer for 5 minutes", UID, "s1", channel="chat")) is sentinel
    assert run(fast_tiers.resolve("what's the weather", UID, "s1", channel="chat")) is sentinel


async def _coro(v):
    return v


def test_a_failing_tier_never_breaks_the_turn(monkeypatch):
    import fast_tiers

    monkeypatch.setenv("ZOE_SELF_MODEL", "enforce")
    sentinel = object()

    async def core(*a, **k):
        return sentinel

    async def no_prov(*a, **k):
        return None

    async def boom(*a, **k):
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(fast_tiers, "_resolve_core", core)
    monkeypatch.setattr(fast_tiers, "_provenance_tier", no_prov)
    monkeypatch.setattr(sm, "live_state", boom)
    assert run(fast_tiers.resolve("what can you do?", UID, "s1", channel="chat")) is sentinel


# ── the ledger: "what did you just use to answer that?" ───────────────────────────────────────────────────────────────

def test_used_to_answer_after_a_self_answer_says_it_came_from_the_self_model(monkeypatch):
    import memory_provenance as mp

    monkeypatch.setenv("ZOE_SELF_MODEL", "enforce")
    monkeypatch.setattr(sm, "live_state", lambda **k: _coro(UP))
    uid = "demo_bar_0000beef"
    mp.note_user_turn(uid, "what can you do?", "s9")
    res = run(sm.tier("what can you do?", uid, "s9", channel="chat"))
    assert res is not None and mp.previous_reply(uid) is None             # (seq not advanced yet) ...
    mp.note_user_turn(uid, "what did you just use to answer that?", "s9")
    rec = mp.previous_reply(uid)
    assert rec is not None and rec.kind == "direct" and rec.tier == "self_model"
    out = run(sm.tier("what did you just use to answer that?", uid, "s9", channel="chat"))
    assert "my own description of what I can do" in out.reply
    import provenance_answers as pa

    assert pa.direct_explanation("self_model", "self_model").startswith("That was me describing myself")


def test_used_to_answer_after_anything_else_reuses_provenance(monkeypatch):
    import provenance_answers as pa

    monkeypatch.setenv("ZOE_SELF_MODEL", "enforce")
    called = []

    async def fake_explain(user_id, *, channel=None, **k):
        called.append((user_id, channel))
        return "PROVENANCE ANSWER"

    monkeypatch.setattr(pa, "explain", fake_explain)
    out = run(sm.tier("what did you use for that?", UID, "s2", channel="chat"))
    assert out.reply == "PROVENANCE ANSWER" and called == [(UID, "chat")]
