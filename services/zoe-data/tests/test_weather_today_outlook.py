"""A weather question is answered with the fact it asked for, today's high first.

Live bug (2026-10): "how hot is it going to get today" missed the Skybridge weather
lane (no "weather"/"temperature" keyword), fell to the expert lane and got a long
five-day dump; "what's the weather going to be like today" got current conditions
with no high. Pins the classifier (positive + scoped negatives), the spoken summary
built from the REAL forecast fields (``today`` / ``daily``), the Skybridge wiring and
the expert-lane plan. Phrasings are invented examples.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import skybridge_service  # noqa: E402
import voice_stitch  # noqa: E402
from expert_dispatch import _plan  # noqa: E402
from skybridge_service import classify_skybridge_intent, resolve_skybridge_request  # noqa: E402
from weather_speech import WeatherAsk, classify_weather_ask, speak_weather  # noqa: E402

pytestmark = pytest.mark.ci_safe

CURRENT = {"temp": 22.4, "description": "clear sky", "feels_like": 21.0}
FORECAST = {
    "today": {"day": "2026-06-11", "high": 27.2, "low": 12.6, "rain_chance": 10, "description": "clear"},
    "daily": [
        {"day": "2026-06-12", "high": 25.0, "low": 14.0, "rain_chance": 60, "description": "light rain"},
        {"day": "2026-06-13", "high": 23.0, "low": 13.0, "rain_chance": 20, "description": "cloudy"},
        {"day": "2026-06-14", "high": 21.0, "low": 11.0, "rain_chance": 5, "description": "clear"},
        {"day": "2026-06-15", "high": 20.0, "low": 10.0, "rain_chance": 5, "description": "clear"},
    ],
}


# ── classifier ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,focus,day",
    [
        ("how hot is it going to get today", "high", "today"),
        ("How hot is it going to get today?", "high", "today"),
        ("how hot will it get later", "high", "today"),
        ("how hot is it going to be tomorrow", "high", "tomorrow"),
        ("how cold is it going to get tonight", "low", "tonight"),
        ("how cold will it get tonight", "low", "tonight"),
        ("what's the high today", "high", "today"),
        ("what is the high temperature for today", "high", "today"),
        ("what's today's high", "high", "today"),
        ("what's the low tonight", "low", "tonight"),
        ("what will the max be tomorrow", "high", "tomorrow"),
        ("what temperature is it going to get today", "high", "today"),
        ("will it be hot today", "high", "today"),
        ("is it going to be cold tonight", "low", "tonight"),
        ("is it going to be sunny tomorrow", "outlook", "tomorrow"),
        ("what's it going to be like today", "outlook", "today"),
        ("what's the weather going to be like today", "outlook", "today"),
        ("will it rain today", "rain", "today"),
        ("is it going to rain tomorrow", "rain", "tomorrow"),
        ("how cold tonight", "low", "tonight"),  # bare forms: no "is it"
        ("how hot tomorrow", "high", "tomorrow"),
        ("what's the high", "high", "today"),
        ("what's the low", "low", "today"),
        ("how hot is it outside", "current", ""),
        ("how cold is it", "current", ""),
        ("what's the weather", "current", ""),
        ("what's the temperature", "current", ""),
    ],
)
def test_classifier_positive(text, focus, day):
    ask = classify_weather_ask(text)
    assert ask is not None, text
    assert (ask.focus, ask.day) == (focus, day), text


@pytest.mark.parametrize(
    "text",
    [
        "how hot is the oven going to get",
        "how hot is the oven",
        "how hot is it in the fridge",
        "how cold is it in the freezer going to get today",
        "what's the high score today",
        "what's the low battery warning",
        "how hot is the water going to get today",
        "how hard is it going to get today",  # a misheard variant must NOT become weather
        "how is it going today",
        "how hot is the oven tonight",
        "what's the high score",
        "what's the low battery",
        "what's it going to be like",  # no day: could be anything
        "will it be hot",  # no day scope: not claimed
        "what time is it",
        "add milk to the shopping list",
    ],
)
def test_classifier_negative(text):
    assert classify_weather_ask(text) is None, text


@pytest.mark.parametrize(
    "text",
    [
        "what's the temperature in the bedroom",
        "how warm is it in the living room",
        "what is the temperature inside",
        "what is the current temperature in this area",
        "how is temperature my space",
        "what's the current temperature in my area",
        "how hot is it in here",
        "what's the thermostat temperature",
        "set the lamp colour temperature to 2700 K",
        "set the temperature to 21 degrees",
        "what's the temperature of the water",
        "remind me if it rains tomorrow",
        "how's the rainbow lamp",
        "wind down the lights",
    ],
)
def test_indoor_and_non_weather_temperature_is_never_weather(text):
    assert classify_weather_ask(text) is None, text
    intent = classify_skybridge_intent(text)
    assert intent is None or intent.domain != "weather", text


def test_a_command_shaped_write_is_never_answered_with_the_weather():
    # classify_weather_ask alone reads the keyword; the Skybridge write guard keeps a WRITE out of the read lane.
    intent = classify_skybridge_intent("remind me to check the weather")
    assert intent is None or intent.domain != "weather"


def test_actions():
    assert WeatherAsk("current").action == "current"
    assert WeatherAsk("high", "today").action == "today"
    assert WeatherAsk("outlook", "today").action == "today"
    assert WeatherAsk("high", "tomorrow").action == "forecast"
    assert classify_weather_ask("show weather forecast").action == "forecast"
    assert classify_weather_ask("show me the weather").action == "current"
    assert classify_weather_ask("weather for tomorrow").action == "forecast"


def test_skybridge_classifies_the_outlook_questions():
    for text, action, focus in [
        ("how hot is it going to get today", "today", "high"),
        ("what's the weather going to be like today", "today", "outlook"),
        ("how cold is it going to get tonight", "today", "low"),
        ("how hot is it going to be tomorrow", "forecast", "high"),
        ("show me the weather", "current", "current"),
    ]:
        intent = classify_skybridge_intent(text)
        assert intent is not None and intent.domain == "weather", text
        assert (intent.action, intent.query) == (action, focus), text
    for text in ("how hot is the oven going to get", "how hard is it going to get today"):
        intent = classify_skybridge_intent(text)
        assert intent is None or intent.domain != "weather", text


# ── spoken summary ───────────────────────────────────────────────────────────


def test_high_leads_and_is_whole_degrees():
    out = speak_weather(WeatherAsk("high", "today"), CURRENT, FORECAST, "Testville")
    assert out == "Today's top is 27 degrees. It's 22 now and clear sky."
    assert "point" not in out and "." not in out.replace(". ", "|").rstrip(".").replace("|", "")


def test_low_tonight_uses_the_overnight_low_not_the_past_morning_one():
    out = speak_weather(WeatherAsk("low", "tonight"), CURRENT, FORECAST, "Testville")
    assert out.startswith("Tonight's low is 14 degrees.")  # next day's early-morning low, not today's 12.6
    assert speak_weather(WeatherAsk("low", "today"), CURRENT, FORECAST, "Testville").startswith("Today's low is 13 degrees.")


def test_rain_leads_with_the_chance_and_falls_back_to_the_description():
    assert speak_weather(WeatherAsk("rain", "today"), CURRENT, FORECAST, "Testville") == "There's a 10 percent chance of rain today."
    no_pct = {"today": {"high": 20, "description": "light rain"}, "daily": []}
    assert speak_weather(WeatherAsk("rain", "today"), CURRENT, no_pct, "Testville").startswith("Rain is likely today")
    dry = {"today": {"high": 20, "description": "clear"}, "daily": []}
    assert speak_weather(WeatherAsk("rain", "today"), CURRENT, dry, "Testville").startswith("No rain is expected today")


def test_outlook_and_current_append_the_top_but_stay_short():
    for ask in (WeatherAsk("outlook", "today"), WeatherAsk("current")):
        out = speak_weather(ask, CURRENT, FORECAST, "Testville")
        assert out == "It's 22 degrees and clear sky in Testville, with a top of 27 today."
    wet = {"today": {"high": 20, "rain_chance": 70}, "daily": []}
    out = speak_weather(WeatherAsk("outlook", "today"), CURRENT, wet, "Testville")
    assert out.endswith("a top of 20 today, and a 70 percent chance of rain.")
    assert len(out) < 120


def test_no_forecast_data_degrades_to_the_plain_current_reply():
    for fc in ({}, None, {"error": "Weather provider unavailable"}, {"today": None, "daily": []}):
        assert speak_weather(WeatherAsk("high", "today"), CURRENT, fc, "Testville") == "It's 22 degrees and clear sky in Testville."


def test_forecast_speaks_real_numbers_not_just_a_heading():
    tomorrow = speak_weather(WeatherAsk("high", "tomorrow"), CURRENT, FORECAST, "Testville")
    assert tomorrow == "Tomorrow's top is 25 degrees, with a low of 14 and light rain."
    generic = speak_weather(WeatherAsk("outlook", "", wide=True), CURRENT, FORECAST, "Testville")
    assert generic.startswith("Tomorrow, a high of 25 and a low of 14 degrees, light rain.")
    assert generic.count("a high of") == 3  # three days, not a five-day wall
    assert "Here is the forecast" not in generic
    assert speak_weather(WeatherAsk("outlook", "tomorrow"), CURRENT, FORECAST, "Testville").count("a high of") == 1
    assert "couldn't get the forecast" in speak_weather(WeatherAsk("outlook", "", wide=True), CURRENT, {"daily": []}, "Testville")


# ── Skybridge wiring ─────────────────────────────────────────────────────────


class _Db:
    async def fetchrow(self, *_a, **_k):
        return None


@pytest.fixture
def fake_weather(monkeypatch):
    async def fake_default(_db):
        return {"latitude": 1.0, "longitude": 2.0, "city": "Testville", "country": "AU"}

    async def fake_current(_lat, _lon, city, country):
        return {**CURRENT, "city": city, "country": country, "_hourly_raw": {"x": 1}}

    async def fake_forecast(_lat, _lon):
        return FORECAST

    monkeypatch.setattr(skybridge_service, "_get_system_default_location", fake_default)
    monkeypatch.setattr(skybridge_service, "_get_current", fake_current)
    monkeypatch.setattr(skybridge_service, "_get_forecast", fake_forecast)


@pytest.mark.asyncio
async def test_skybridge_today_high_question_speaks_the_high_with_the_current_card(fake_weather):
    result = await resolve_skybridge_request("how hot is it going to get today", "u1", db=_Db())
    assert result["handled"] is True
    assert result["intent"] == {"domain": "weather", "action": "today"}
    assert result["spoken_summary"] == "Today's top is 27 degrees. It's 22 now and clear sky."
    card = result["cards"][0]
    assert card["content"]["source"] == "weather_current"
    assert card["content"]["forecast"]["today"]["high"] == 27.2  # the panel card carries the day's range


@pytest.mark.asyncio
async def test_skybridge_weather_like_today_includes_the_top(fake_weather):
    result = await resolve_skybridge_request("what's the weather going to be like today?", "u1", db=_Db())
    assert result["spoken_summary"] == "It's 22 degrees and clear sky in Testville, with a top of 27 today."


@pytest.mark.asyncio
async def test_skybridge_plain_weather_stays_current_with_the_top_appended(fake_weather):
    result = await resolve_skybridge_request("what's the weather", "u1", db=_Db())
    assert result["intent"]["action"] == "current"
    assert result["spoken_summary"] == "It's 22 degrees and clear sky in Testville, with a top of 27 today."
    assert result["cards"][0]["content"]["source"] == "weather_current"


@pytest.mark.asyncio
async def test_skybridge_tomorrow_uses_the_forecast_card_and_speaks_numbers(fake_weather):
    result = await resolve_skybridge_request("how hot is it going to be tomorrow", "u1", db=_Db())
    assert result["intent"]["action"] == "forecast"
    assert result["cards"][0]["content"]["source"] == "weather_forecast"
    assert result["spoken_summary"] == "Tomorrow's top is 25 degrees, with a low of 14 and light rain."


# ── expert lane plan ─────────────────────────────────────────────────────────


def test_expert_plan_leads_with_the_asked_fact_not_a_forecast_dump():
    assert _plan("weather", "how hot is it going to get today") == (
        "weather", {"focus": "high", "day": "today", "forecast": False}, "read")
    # "how cold" used to be swallowed by the jacket-advice branch.
    assert _plan("weather", "how cold is it going to get tonight")[1]["focus"] == "low"
    assert _plan("weather", "how cold tonight")[1] == {"focus": "low", "day": "tonight", "forecast": False}
    assert _plan("weather", "what's the high")[1]["focus"] == "high"
    # Explicit-object advice is untouched.
    assert _plan("weather", "do I need a jacket today")[1] == {"advice": "warmth"}
    assert _plan("weather", "should I take an umbrella")[1] == {"advice": "rain"}


@pytest.fixture
def intent_lane(monkeypatch):
    import contextlib

    import database
    from routers import weather

    class _Cursor:
        async def fetchone(self):
            return None

    class _Conn:
        async def execute(self, *_a, **_k):
            return _Cursor()

    @contextlib.asynccontextmanager
    async def ctx():
        yield _Conn()

    async def current(_lat, _lon, city, _country):
        return {"temp": 22.4, "description": "clear sky", "feels_like": 21.0, "city": city}

    async def forecast(_lat, _lon):
        return FORECAST

    monkeypatch.setattr(database, "get_db_ctx", ctx)
    monkeypatch.setattr(weather, "_get_current", current)
    monkeypatch.setattr(weather, "_get_forecast", forecast)
    monkeypatch.setattr(weather, "_resolve_location", lambda _prefs: (1.0, 2.0, "Testville", "AU"))


@pytest.mark.asyncio
async def test_intent_lane_focus_answers_short_and_leads_with_the_high(intent_lane):
    from intent_router import _execute_weather_direct

    out = await _execute_weather_direct("u1", forecast=True, focus="high", day="today")
    assert out == "Today's top is 27 degrees. It's 22 now and clear sky."
    assert len(out) < 80  # the live bug was a 400+ char multi-day reply


@pytest.mark.asyncio
async def test_intent_lane_plain_current_appends_the_top_and_forecast_caps_at_three_days(intent_lane):
    from intent_router import _execute_weather_direct

    assert await _execute_weather_direct("u1") == "It's 22 degrees and clear sky in Testville, with a top of 27 today."
    out = await _execute_weather_direct("u1", forecast=True)
    assert out.count("a high of") == 3
    # The jacket answer reasons over TODAY's range (daily[0] is tomorrow).
    assert (await _execute_weather_direct("u1", advice="warmth")).startswith("No, you should be fine")


# ── stitch shape ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plain_current_reply_with_a_top_still_stitches(monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_STITCH_ENABLED", "1")
    asked: list[str] = []

    async def synth(seg):
        asked.append(seg)
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x01\x02" * 100)
        return buf.getvalue()

    reply = speak_weather(WeatherAsk("current"), CURRENT, FORECAST, "Testville")
    assert await voice_stitch.stitch_reply(reply, synth) is not None
    assert asked == ["It's twenty two degrees", "Clear sky", "in Testville", "with a top of twenty seven today"]


# ── provider data: forecast["today"] ─────────────────────────────────────────


def _perth_now(monkeypatch, weather):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    perth = ZoneInfo("Australia/Perth")
    monkeypatch.setattr(weather, "_weather_now", lambda tz: datetime(2026, 6, 29, 7, 0, tzinfo=perth))
    monkeypatch.setattr(weather, "_weather_cache", {})


@pytest.mark.asyncio
async def test_openmeteo_forecast_carries_todays_high_low_and_rain_chance(monkeypatch):
    from routers import weather

    _perth_now(monkeypatch, weather)
    weather._cache_put("current", 1.5, 2.5, {
        "_hourly_raw": {"timezone": "Australia/Perth", "times": ["2026-06-29T08:00"], "temps": [11], "codes": [0]},
        "_daily_raw": {
            "time": ["2026-06-29", "2026-06-30"],
            "temperature_2m_max": [21.4, 22],
            "temperature_2m_min": [10, 11],
            "weathercode": [61, 1],
            "precipitation_probability_max": [80, 5],
        },
    })
    fc = await weather._fetch_openmeteo_forecast(1.5, 2.5)
    assert (fc["today"]["day"], fc["today"]["high"], fc["today"]["low"], fc["today"]["rain_chance"]) == ("2026-06-29", 21.4, 10, 80)
    assert fc["today"]["description"]
    assert [d["day"] for d in fc["daily"]] == ["2026-06-30"]  # today is still NOT in daily
    assert fc["daily"][0]["rain_chance"] == 5


@pytest.mark.asyncio
async def test_openmeteo_forecast_without_rain_probability_still_has_today(monkeypatch):
    from routers import weather

    _perth_now(monkeypatch, weather)
    weather._cache_put("current", 3.5, 4.5, {
        "_hourly_raw": {"timezone": "Australia/Perth", "times": ["2026-06-29T08:00"], "temps": [11], "codes": [0]},
        "_daily_raw": {"time": ["2026-06-29"], "temperature_2m_max": [21], "temperature_2m_min": [10], "weathercode": [0]},
    })
    fc = await weather._fetch_openmeteo_forecast(3.5, 4.5)
    assert fc["today"]["high"] == 21 and fc["today"]["rain_chance"] is None


@pytest.mark.asyncio
async def test_owm_forecast_today_uses_remaining_slots_and_pop(monkeypatch):
    from routers import weather

    _perth_now(monkeypatch, weather)

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"city": {"timezone": 8 * 3600}, "list": [
                {"dt": 1782688800, "main": {"temp": 12}, "pop": 0.2, "weather": [{"description": "clear", "icon": "01d"}]},  # 06-29 07:20
                {"dt": 1782699600, "main": {"temp": 19}, "pop": 0.7, "weather": [{"description": "rain", "icon": "10d"}]},  # 06-29 10:20
                {"dt": 1782789600, "main": {"temp": 22}, "pop": 0.1, "weather": [{"description": "cloudy", "icon": "02d"}]},  # 06-30 11:20
            ]}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *a, **k):
            return Resp()

    monkeypatch.setattr(weather.httpx, "AsyncClient", Client)
    fc = await weather._fetch_owm_forecast(5.5, 6.5)
    assert (fc["today"]["high"], fc["today"]["low"], fc["today"]["rain_chance"]) == (19, 12, 70)
    assert [d["day"] for d in fc["daily"]] == ["2026-06-30"] and fc["daily"][0]["rain_chance"] == 10
