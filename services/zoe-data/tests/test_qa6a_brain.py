"""QA round 6a brain fixes (2026-11-08): a cooking temperature is never the weather, a named place beats the household default,
a lazily typed possessive resolves to the known name, "who is X" is not a contacts-only search, and short acknowledgements
(k, yep, nah, nope) are social closes. All names and places are invented."""
import contextlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import expert_dispatch  # noqa: E402
import intent_router  # noqa: E402
import memory_gate  # noqa: E402
from intent_router import Intent, detect_intent  # noqa: E402
from weather_speech import classify_weather_ask, is_food_temperature, weather_place  # noqa: E402

pytestmark = pytest.mark.ci_safe


# ── 1. temp -> weather ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "and chicken, whats the safe internal temp", "and what temp", "what temp do i cook lamb at", "oven temp for a roast",
    "what temperature should the beef reach", "whats the core temp for pork", "what temp for the salmon",
    "what's the internal temperature of cooked steak", "what temp does chicken need to reach", "temperature of the roast",
    "what is the roast temperature", "what temperature should i cook chicken tonight",
])
def test_cooking_temperature_is_not_weather(text):
    assert is_food_temperature(text) is True
    assert classify_weather_ask(text) is None            # the Skybridge lane declines it too


@pytest.mark.parametrize("text", [
    "what's the temp outside", "temperature today", "what's the temperature", "how hot is it going to get today",
    "what's the weather", "what's the temperature outside for the roast tonight", "is it cold outside",
    "what temp will it be today", "what's the temperature tonight",
])
def test_negative_control_real_weather_still_weather(text):
    assert is_food_temperature(text) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["and chicken, whats the safe internal temp", "and what temp"])
async def test_expert_dispatch_sends_food_temp_to_the_brain(monkeypatch, text):
    monkeypatch.setenv("ZOE_EXPERT_ENABLED", "1")
    monkeypatch.setenv("ZOE_EXPERT_MODE", "active")
    planned = []
    monkeypatch.setattr(expert_dispatch, "_plan", lambda d, t: planned.append(t) or None)
    ctx = {"score": 0.87, "user_id": "demo_user"}      # the router head scored it as weather at 0.87, exactly as live
    assert await expert_dispatch.dispatch("weather", text, ctx) is None
    assert planned == []                               # the GUARD declined: the weather plan was never even reached
    # control: with the guard disabled the same text DOES reach the weather plan (so the assertion above can fail)
    monkeypatch.setattr(expert_dispatch, "is_food_temperature", lambda _t: False)
    await expert_dispatch.dispatch("weather", text, ctx)
    assert planned == [text]


@pytest.mark.asyncio
async def test_negative_control_real_weather_reaches_the_plan(monkeypatch):
    monkeypatch.setenv("ZOE_EXPERT_ENABLED", "1")
    monkeypatch.setenv("ZOE_EXPERT_MODE", "active")
    planned = []
    monkeypatch.setattr(expert_dispatch, "_plan", lambda d, t: planned.append(t) or None)
    await expert_dispatch.dispatch("weather", "what's the temp outside", {"score": 0.87, "user_id": "demo_user"})
    assert planned == ["what's the temp outside"]


def test_expert_plan_still_plans_real_weather():
    assert expert_dispatch._plan("weather", "what's the temp outside")[0] == "weather"
    assert expert_dispatch._plan("weather", "temperature today")[0] == "weather"


# ── 1b. a place the user names ─────────────────────────────────────────────────

@pytest.mark.parametrize("text,place", [
    ("weather in perth", "Perth"), ("what's the weather in Port Hedland tomorrow", "Port Hedland"),
    ("whats the forecast for broome", "Broome"), ("is it going to rain in albany today", "Albany"),
    ("weather in perth for tomorrow", "Perth"), ("weather in mount isa this week", "Mount Isa"),
])
def test_weather_place_is_the_named_place(text, place):
    assert weather_place(text) == place


@pytest.mark.parametrize("text", [
    "what's the weather", "weather today", "how's the weather in the kitchen", "temperature in the house",
    "is it cold in here", "oven temp for roast", "weather for roast", "weather in early morning", "weather for work tomorrow",
    "weather in living room", "weather in front yard", "weather in summer tomorrow",
])
def test_weather_place_negative_controls(text):
    assert weather_place(text) == ""


def test_named_place_reaches_the_expert_slots_and_the_chat_intent():
    assert expert_dispatch._plan("weather", "weather in perth")[1]["location"] == "Perth"
    assert "location" not in expert_dispatch._plan("weather", "what's the weather")[1]
    assert detect_intent("what's the weather in perth", log_miss=False).slots["location"] == "Perth"
    assert "location" not in detect_intent("what's the weather like", log_miss=False).slots


# ── 2. names ───────────────────────────────────────────────────────────────────

def test_lazy_possessive_resolves_to_the_known_name():
    assert memory_gate.person_names_in_question("whats theos deal with food again", ["Theo", "Ivy"]) == ["Theo"]
    assert memory_gate.person_names_in_question("whats Theo's deal with food", ["Theo"]) == ["Theo"]


def test_lazy_possessive_negative_controls():
    assert memory_gate.person_names_in_question("whats the deal with food again", ["Theo", "Ivy"]) == []
    assert memory_gate.person_names_in_question("which ones are good", ["Jo"]) == []      # 'jo' is < 3 letters: no +s match
    assert memory_gate.person_names_in_question("whats chriss deal", ["Chris"]) == []     # only the plain +s form, never a guess


class _Cur:
    def __init__(self, rows=()):
        self._rows = list(rows)

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _EmptyDB:
    async def execute(self, sql, params=()):
        return _Cur()


def _install(monkeypatch, db):
    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr("database.get_db_ctx", ctx)


def test_who_is_marks_an_identity_question():
    assert detect_intent("who is graham", log_miss=False).slots.get("via") == "who_is"
    assert detect_intent("find contact graham", log_miss=False).slots.get("via") is None


@pytest.mark.asyncio
async def test_who_is_unknown_contact_goes_to_the_brain_not_a_contacts_denial(monkeypatch):
    _install(monkeypatch, _EmptyDB())
    out = await intent_router._execute_people_search_direct(
        Intent("people_search", {"query": "graham", "via": "who_is"}), "demo_user")
    assert out is None                                    # the brain answers with the memory + people packet


@pytest.mark.asyncio
async def test_negative_control_explicit_contact_search_still_says_not_found(monkeypatch):
    _install(monkeypatch, _EmptyDB())
    out = await intent_router._execute_people_search_direct(Intent("people_search", {"query": "graham"}), "demo_user")
    assert out and "graham" in out.lower()


# ── 4. short replies ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["k", "ok", "yep", "yup", "sure", "cool", "sure thing"])
def test_short_affirmations_are_acks(text):
    i = detect_intent(text, log_miss=False)
    assert i.name == "acknowledgement" and i.slots == {"kind": "ack"}


@pytest.mark.parametrize("text", ["nah", "nope", "nah thanks", "no thanks", "nope!"])
def test_short_declines_are_a_social_close(text):
    i = detect_intent(text, log_miss=False)
    assert i.name == "acknowledgement" and i.slots == {"kind": "ack", "decline": True}
    assert memory_gate.is_emotional_turn(text) is False


@pytest.mark.asyncio
async def test_decline_reply_is_light_and_never_a_mood():
    i = Intent("acknowledgement", {"kind": "ack", "decline": True})
    for _ in range(12):
        out = await intent_router.execute_intent(i, "demo_user")
        assert out in intent_router.DECLINE_REPLIES
        assert "down" not in out.lower() and "weighing" not in out.lower()


@pytest.mark.parametrize("text", ["nope not today", "sure add milk", "no", "yes", "nah that is wrong, it was tuesday", "sure but later"])
def test_negative_control_real_content_is_not_an_ack(text):
    i = detect_intent(text, log_miss=False)
    assert i is None or i.name != "acknowledgement"


@pytest.mark.asyncio
@pytest.mark.parametrize("prev,expect", [
    ("Want me to set a reminder for the pickup?", True),      # her question/offer is hanging: the brain reads the short reply
    ("Which list should I use, the shopping one or the to-do?", True),
    ("Done, added to your list.", False),                    # nothing outstanding: the canned close
])
async def test_ack_answers_a_question_or_offer_of_hers(prev, expect):
    for text in ("k", "nah"):
        assert await intent_router.ack_answers_offer(detect_intent(text, log_miss=False), "demo_user", previous=prev) is expect
