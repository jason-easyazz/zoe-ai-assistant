"""A lights command heard with BOTH command words gone (live 2026-11-06, fixed as a class behind ZOE_DEVICE_MISHEAR_GUARD).

The live turn: a short "turn on the light" from the kitchen panel reached Zoe as a two-word sound-alike sentence - the verb and
its particle fused into one word ("shown" for "turn on") and the light noun heard as "lot". No deterministic matcher claimed it,
the STT snap could not (it repairs ONE word, and "shown" shares no onset with "turn"), the router head said lists @ 0.98, the
voice lane spoke "Let me check your lists." and the brain answered a request nobody made. The owner repeated it and the light
came on.

Three repairs, all behind the flag (default off = byte-identical to before):
1. "lot" / "lots" join the misheard light nouns in a switch frame ("turn on the lot").
2. The WHOLE utterance "<fused on-verb> the <light noun>" ("shown the lot", "shone the lights") reads as "turn on the light".
3. Skybridge's router gate lets a device on/off parse through a lists verdict when the words hold nothing a list could:
   no list noun, no list verb, no item word, and a switch verb with its on/off particle.

Negative controls (run by hand when written; each went red): dropping ``_FUSED_ON_LIGHT_RE`` fails
``test_fused_on_verb_light_command_is_a_light_command``; dropping ``|lot|lots`` from ``_MISHEARD_LIGHT_EXT`` fails
``test_lot_is_a_misheard_light_in_a_switch_frame``; dropping the ``device_command_not_list`` branch fails
``test_gate_lets_a_device_command_through_a_lists_verdict``. Synthetic utterances only (ci_safe).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import skybridge_service as sky


@pytest.fixture
def guard_on(monkeypatch):
    monkeypatch.setenv("ZOE_DEVICE_MISHEAR_GUARD", "1")


@pytest.fixture
def guard_off(monkeypatch):
    monkeypatch.delenv("ZOE_DEVICE_MISHEAR_GUARD", raising=False)


def _two_stage(domain, *, conf=0.98):
    return {"two_stage": {"domain": domain, "tool": "show_list" if domain == "lists" else "x", "head_conf": conf,
                          "gated": False, "reason": None, "head_top": domain, "shortlist": [domain, "smart_home"]}}


FUSED = ["Shone the lots.", "shown the light", "Shone the lights please.", "okay, shown the lamp", "Shawn the lot!"]


@pytest.mark.parametrize("text", FUSED)
def test_fused_on_verb_light_command_is_a_light_command(guard_on, text):
    got = sky.classify_skybridge_intent(text)
    assert got is not None and got.domain == "smart_home" and got.action == "turn_on", (text, got)
    assert got.query in ("light", "lights", "lamp"), got.query


@pytest.mark.parametrize("text,action,query", [
    ("turn on the lot", "turn_on", "light"),
    ("switch off the lots", "turn_off", "lights"),
    ("turn the lot off", "turn_off", "light"),
])
def test_lot_is_a_misheard_light_in_a_switch_frame(guard_on, text, action, query):
    got = sky.classify_skybridge_intent(text)
    assert got is not None and got.domain == "smart_home" and got.action == action and got.query == query, (text, got)


@pytest.mark.parametrize("text", [
    "I've shown the lot.",                 # a report, not a command
    "shown the lot of them",               # more was said: not the bare two-word command
    "shown the list",                      # a list word: never a light
    "turn the lot on the shelf",           # a trailing clause
    "don't turn on the lot",               # a negation
    "we sold the lot",
])
def test_fused_repair_negative_controls(guard_on, text):
    got = sky.classify_skybridge_intent(text)
    assert got is None or got.domain != "smart_home", (text, got)


@pytest.mark.parametrize("text", ["Shone the lots.", "turn on the lot"])
def test_flag_off_is_unchanged(guard_off, text):
    got = sky.classify_skybridge_intent(text)
    assert got is None or got.query not in ("light", "lights"), (text, got)
    assert sky._repair_light_mishear("turn on the white") == "turn on the light"   # the live repair is untouched


@pytest.mark.parametrize("text", ["Shone the lots.", "turn on the light", "switch the lamp off", "can you turn off the lights"])
def test_gate_lets_a_device_command_through_a_lists_verdict(guard_on, text):
    intent = sky.classify_skybridge_intent(text)
    assert intent is not None and intent.domain == "smart_home", (text, intent)
    decision, reason, *_ = sky.skybridge_router_gate(text, intent, _two_stage("lists"))
    assert (decision, reason) == ("allow", "device_command_not_list"), (text, reason)


@pytest.mark.parametrize("text", ["turn on the light", "Shone the lots."])
def test_gate_keeps_the_veto_when_the_flag_is_off(guard_off, monkeypatch, text):
    intent = sky.SkybridgeIntent(domain="smart_home", action="turn_on", query="light")
    assert sky.skybridge_router_gate(text, intent, _two_stage("lists"))[:2] == ("veto", "router_disagrees")


@pytest.mark.parametrize("text,router", [
    ("turn on the light and add bulbs to the shopping list", "lists"),   # a list word: the lists verdict stands
    ("turn on the light", "music"),                                      # only a lists verdict is second-guessed
    ("turn on the light", "chat"),                                       # a chat verdict still vetoes
])
def test_gate_guard_negative_controls(guard_on, text, router):
    intent = sky.SkybridgeIntent(domain="smart_home", action="turn_on", query="light")
    decision, reason, *_ = sky.skybridge_router_gate(text, intent, _two_stage(router))
    assert reason != "device_command_not_list", (text, router, reason)
    assert decision == "veto"


def test_gate_guard_ignores_a_non_switch_smart_home_action(guard_on):
    intent = sky.SkybridgeIntent(domain="smart_home", action="set_brightness", query="light")
    assert sky.skybridge_router_gate("dim the light", intent, _two_stage("lists"))[1] != "device_command_not_list"
