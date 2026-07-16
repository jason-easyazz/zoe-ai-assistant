import asyncio
import pytest

from intent_router import _execute_daily_briefing

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def clear_daily_briefing_cache():
    from intent_router import _DAILY_BRIEFING_RESPONSE_CACHE

    _DAILY_BRIEFING_RESPONSE_CACHE.clear()
    yield
    _DAILY_BRIEFING_RESPONSE_CACHE.clear()


@pytest.mark.asyncio
async def test_daily_briefing_runs_direct_calls_concurrently(monkeypatch):
    calls = []
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def fake_direct(key, payload):
        calls.append(key)
        if len(calls) == 3:
            all_started.set()
        await release.wait()
        return payload

    monkeypatch.setattr(
        "intent_router._daily_briefing_weather",
        lambda user_id: fake_direct("weather", {"temp": 18.5, "city": "Perth", "description": "clear"}),
    )
    monkeypatch.setattr(
        "intent_router._daily_briefing_calendar",
        lambda user_id: fake_direct("calendar", {"events": [{"title": "Standup", "start_time": "09:00"}]}),
    )
    monkeypatch.setattr(
        "intent_router._daily_briefing_reminders",
        lambda user_id: fake_direct("reminders", {"reminders": [{"title": "pay invoice", "due_time": "17:00"}]}),
    )

    task = asyncio.create_task(_execute_daily_briefing("family-admin"))
    await asyncio.wait_for(all_started.wait(), timeout=1.0)
    assert len(calls) == 3
    assert not task.done()

    release.set()
    result = await asyncio.wait_for(task, timeout=1.0)

    assert "Weather: 18.5" in result
    assert "Standup at 09:00" in result
    assert "pay invoice at 17:00" in result
    assert set(calls) == {"weather", "calendar", "reminders"}


@pytest.mark.asyncio
async def test_daily_briefing_preserves_partial_results_when_one_call_fails(monkeypatch):
    async def fake_weather(user_id):
        await asyncio.sleep(0)
        return {"temp": 19, "city": "Perth", "description": "cloudy"}

    async def fake_calendar(user_id):
        await asyncio.sleep(0)
        raise RuntimeError("calendar unavailable")

    async def fake_reminders(user_id):
        await asyncio.sleep(0)
        return {"reminders": [{"title": "check oven"}]}

    monkeypatch.setattr("intent_router._daily_briefing_weather", fake_weather)
    monkeypatch.setattr("intent_router._daily_briefing_calendar", fake_calendar)
    monkeypatch.setattr("intent_router._daily_briefing_reminders", fake_reminders)

    result = await _execute_daily_briefing("family-admin")

    assert "Weather: 19" in result
    assert "No events on the calendar today." in result
    assert "check oven" in result


@pytest.mark.asyncio
async def test_daily_briefing_does_not_use_mcporter(monkeypatch):
    async def fail_mcporter(_cmd):
        raise AssertionError("daily briefing should use direct in-process fulfillment")

    monkeypatch.setattr("intent_router._run_mcporter", fail_mcporter)
    monkeypatch.setattr("intent_router._daily_briefing_weather", lambda user_id: asyncio.sleep(0, result=None))
    monkeypatch.setattr("intent_router._daily_briefing_calendar", lambda user_id: asyncio.sleep(0, result={"events": []}))
    monkeypatch.setattr("intent_router._daily_briefing_reminders", lambda user_id: asyncio.sleep(0, result={"reminders": []}))

    result = await _execute_daily_briefing("family-admin")

    assert result == "Here\'s your day:\n\nNo events on the calendar today."

@pytest.mark.asyncio
async def test_daily_briefing_natural_phrases_are_deterministic():
    from intent_router import detect_and_extract_intent

    for phrase in [
        "give me my daily briefing",
        "give me a morning update",
        "what is coming up today",
    ]:
        intent = await detect_and_extract_intent(phrase, user_id="guest")
        assert intent is not None
        assert intent.name == "daily_briefing"
        assert intent.slots == {}


@pytest.mark.asyncio
async def test_daily_briefing_uses_short_response_cache(monkeypatch):
    calls = []

    async def fake_weather(user_id):
        calls.append("weather")
        return {"temp": 18.5, "city": "Perth", "description": "clear"}

    monkeypatch.setattr("intent_router._daily_briefing_weather", fake_weather)
    monkeypatch.setattr("intent_router._daily_briefing_calendar", lambda user_id: asyncio.sleep(0, result={"events": []}))
    monkeypatch.setattr("intent_router._daily_briefing_reminders", lambda user_id: asyncio.sleep(0, result={"reminders": []}))
    monkeypatch.setattr("intent_router._DAILY_BRIEFING_CACHE_TTL_SECONDS", 120)
    from intent_router import _DAILY_BRIEFING_RESPONSE_CACHE

    _DAILY_BRIEFING_RESPONSE_CACHE.clear()
    first = await _execute_daily_briefing("family-admin")
    second = await _execute_daily_briefing("family-admin")

    assert first == second
    assert calls == ["weather"]
    _DAILY_BRIEFING_RESPONSE_CACHE.clear()


def test_daily_briefing_cache_evicts_oldest_when_capped(monkeypatch):
    from intent_router import (
        _DAILY_BRIEFING_RESPONSE_CACHE,
        _daily_briefing_cache_set,
    )

    _DAILY_BRIEFING_RESPONSE_CACHE.clear()
    monkeypatch.setattr("intent_router._DAILY_BRIEFING_CACHE_TTL_SECONDS", 120)
    monkeypatch.setattr("intent_router._DAILY_BRIEFING_CACHE_MAX_USERS", 2)

    try:
        _daily_briefing_cache_set("user-1", "one")
        _daily_briefing_cache_set("user-2", "two")
        _daily_briefing_cache_set("user-3", "three")

        assert set(_DAILY_BRIEFING_RESPONSE_CACHE) == {"user-2", "user-3"}
    finally:
        _DAILY_BRIEFING_RESPONSE_CACHE.clear()


@pytest.mark.asyncio
async def test_daily_briefing_weather_uses_router_weather_cache(monkeypatch):
    """A fresh keyed-cache reading for the user's resolved coords is served
    without a provider fetch — the briefing rides _get_current's cache
    short-circuit instead of the old flat-slot peek."""
    from intent_router import _daily_briefing_weather
    import routers.weather as weather

    class _Cursor:
        async def fetchone(self):
            return None  # no stored prefs → _resolve_location falls to defaults

    class _FakeDB:
        async def execute(self, *_a, **_k):
            return _Cursor()

    # #1108 converted the briefing path to `async with get_db_ctx()` (leak
    # sweep 3/3) — fake the context manager, not the old get_db generator.
    import contextlib

    @contextlib.asynccontextmanager
    async def _fake_get_db_ctx():
        yield _FakeDB()

    monkeypatch.setattr("database.get_db_ctx", _fake_get_db_ctx)
    monkeypatch.setattr(weather, "_weather_cache", {})
    # Seed the keyed cache at the coords _resolve_location resolves to
    # (no prefs → the Geraldton defaults).
    weather._cache_put(
        "current", weather.DEFAULT_LAT, weather.DEFAULT_LON,
        {"temp": 22, "city": "Geraldton", "description": "clear sky"},
    )

    async def fail_fetch(*_args, **_kwargs):
        raise AssertionError("cached daily briefing weather should not refetch")

    monkeypatch.setattr(weather, "_fetch_openmeteo_current", fail_fetch)
    monkeypatch.setattr(weather, "_fetch_owm_current", fail_fetch)

    result = await _daily_briefing_weather("family-admin")

    assert result == {"temp": 22, "city": "Geraldton", "description": "clear sky"}

@pytest.mark.asyncio
async def test_daily_briefing_does_not_cache_degraded_partial_result(monkeypatch):
    calls = []

    async def fake_weather(user_id):
        calls.append("weather")
        if len(calls) == 1:
            raise RuntimeError("weather unavailable")
        return {"temp": 20, "city": "Perth", "description": "clear"}

    monkeypatch.setattr("intent_router._daily_briefing_weather", fake_weather)
    monkeypatch.setattr("intent_router._daily_briefing_calendar", lambda user_id: asyncio.sleep(0, result={"events": []}))
    monkeypatch.setattr("intent_router._daily_briefing_reminders", lambda user_id: asyncio.sleep(0, result={"reminders": []}))
    monkeypatch.setattr("intent_router._DAILY_BRIEFING_CACHE_TTL_SECONDS", 120)

    first = await _execute_daily_briefing("family-admin")
    second = await _execute_daily_briefing("family-admin")

    assert "Weather:" not in first
    assert "Weather: 20" in second
    assert calls == ["weather", "weather"]
