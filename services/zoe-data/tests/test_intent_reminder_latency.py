import sys
import time
import types
from datetime import datetime

import pytest

pytestmark = pytest.mark.ci_safe


@pytest.mark.asyncio
async def test_simple_reminder_slots_skip_llm_extractor(monkeypatch):
    module = types.ModuleType("nlu_extractor")

    async def fail_extract(_intent_name, _raw):
        raise AssertionError("simple reminder should not call the LLM slot extractor")

    module.extract_slots_for_intent = fail_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    started = time.perf_counter()
    intent = await detect_and_extract_intent("remind me to call mum tomorrow at 10am", user_id="guest")
    latency_ms = (time.perf_counter() - started) * 1000

    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots["title"] == "call mum"
    assert intent.slots["time"] == "10:00"
    assert intent.slots["date"]
    assert latency_ms < 100


@pytest.mark.asyncio
async def test_bare_simple_reminder_defaults_to_today_without_llm(monkeypatch):
    module = types.ModuleType("nlu_extractor")

    async def fail_extract(_intent_name, _raw):
        raise AssertionError("bare simple reminder should not call the LLM slot extractor")

    module.extract_slots_for_intent = fail_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent("remind me to check the oven", user_id="guest")

    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots["title"] == "check the oven"
    assert intent.slots["date"]
    assert "time" not in intent.slots


@pytest.mark.asyncio
async def test_leading_relative_reminder_skips_llm_extractor(monkeypatch):
    module = types.ModuleType("nlu_extractor")

    async def fail_extract(_intent_name, _raw):
        raise AssertionError("safe relative reminder should not call the LLM slot extractor")

    module.extract_slots_for_intent = fail_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    import intent_router
    from intent_router import detect_and_extract_intent

    monkeypatch.setattr(intent_router, "_relative_reminder_now", lambda: datetime(2026, 6, 15, 20, 5))
    intent = await detect_and_extract_intent("remind me in 5 minutes to check the oven", user_id="guest")

    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "check the oven", "date": "2026-06-15", "time": "20:10"}


@pytest.mark.asyncio
async def test_trailing_relative_reminder_skips_llm_extractor(monkeypatch):
    module = types.ModuleType("nlu_extractor")

    async def fail_extract(_intent_name, _raw):
        raise AssertionError("safe trailing relative reminder should not call the LLM slot extractor")

    module.extract_slots_for_intent = fail_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    import intent_router
    from intent_router import detect_and_extract_intent

    monkeypatch.setattr(intent_router, "_relative_reminder_now", lambda: datetime(2026, 6, 15, 20, 0))
    text = "remind me to check the oven in 30 minutes"
    intent = await detect_and_extract_intent(text, user_id="guest")

    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "check the oven", "date": "2026-06-15", "time": "20:30"}


@pytest.mark.parametrize(
    ("text", "expected_date", "expected_time"),
    [
        ("remind me to call mum in a few days", "2026-06-18", "20:00"),
        ("remind me to call mum in two hours", "2026-06-15", "22:00"),
        ("remind me to check the filter in a couple of weeks", "2026-06-29", "20:00"),
        ("remind me to check the oven in an hour", "2026-06-15", "21:00"),
        ("remind me to check the oven in half an hour", "2026-06-15", "20:30"),
        ("remind me to call mum in a day", "2026-06-16", "20:00"),
    ],
)
@pytest.mark.asyncio
async def test_word_relative_reminder_skips_llm_extractor(monkeypatch, text, expected_date, expected_time):
    module = types.ModuleType("nlu_extractor")

    async def fail_extract(_intent_name, _raw):
        raise AssertionError("safe word-relative reminder should not call the LLM slot extractor")

    module.extract_slots_for_intent = fail_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    import intent_router
    from intent_router import detect_and_extract_intent

    monkeypatch.setattr(intent_router, "_relative_reminder_now", lambda: datetime(2026, 6, 15, 20, 0))
    intent = await detect_and_extract_intent(text, user_id="guest")

    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots["date"] == expected_date
    assert intent.slots["time"] == expected_time
    assert intent.slots["title"]


@pytest.mark.parametrize(
    "text",
    [
        "remind me in 1000 weeks to rotate credentials",
        "remind me to rotate credentials in 99999 days",
    ],
)
@pytest.mark.asyncio
async def test_absurd_relative_reminder_still_uses_existing_extractor(monkeypatch, text):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "rotate credentials", "date": "2026-06-22"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent(text, user_id="guest")

    assert calls == [("reminder_create", text)]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "rotate credentials", "date": "2026-06-22"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "remind me to call mum next Tuesday",
        "remind me to pick up kids this Saturday",
        "remind me to submit the form coming Monday",
    ],
)
async def test_modifier_day_reminder_still_uses_existing_extractor(monkeypatch, text):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "modifier day reminder", "date": "2026-06-23"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent(text, user_id="guest")

    assert calls == [("reminder_create", text)]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "modifier day reminder", "date": "2026-06-23"}


@pytest.mark.parametrize(
    "text",
    [
        "remind me to call mum every Monday",
        "remind me to water the garden every week",
    ],
)
@pytest.mark.asyncio
async def test_recurring_reminder_still_uses_existing_extractor(monkeypatch, text):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "recurring reminder", "date": "2026-06-22", "recurrence": "weekly"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent(text, user_id="guest")

    assert calls == [("reminder_create", text)]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "recurring reminder", "date": "2026-06-22", "recurrence": "weekly"}


@pytest.mark.parametrize(
    "text",
    [
        "remind me to call mum at noon",
        "remind me to stretch in the morning",
        "remind me to lock the door at bedtime",
        "remind me to lock the door tonight",
    ],
)
@pytest.mark.asyncio
async def test_named_time_reminder_still_uses_existing_extractor(monkeypatch, text):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "named time reminder", "date": "2026-06-15", "time": "12:00"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent(text, user_id="guest")

    assert calls == [("reminder_create", text)]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "named time reminder", "date": "2026-06-15", "time": "12:00"}


@pytest.mark.parametrize(
    "text",
    [
        "remind me about tomorrow",
        "remind me about Monday",
        "remind me about tomorrow at 10am",
        "remind me about Monday at 5pm",
    ],
)
@pytest.mark.asyncio
async def test_standalone_day_reminder_still_uses_existing_extractor(monkeypatch, text):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "standalone day reminder", "date": "2026-06-16"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent(text, user_id="guest")

    assert calls == [("reminder_create", text)]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "standalone day reminder", "date": "2026-06-16"}


@pytest.mark.parametrize(
    "text",
    [
        "remind me to pick up the package on the 15th",
        "remind me to call mum on her birthday",
    ],
)
@pytest.mark.asyncio
async def test_unsupported_on_date_phrase_still_uses_existing_extractor(monkeypatch, text):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "unsupported on date reminder", "date": "2026-07-15"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent(text, user_id="guest")

    assert calls == [("reminder_create", text)]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "unsupported on date reminder", "date": "2026-07-15"}


@pytest.mark.parametrize(
    "text",
    [
        "remind me to prepare for Black Friday",
        "remind me to shop Cyber Monday",
        "remind me to call mum Tuesday",
        "remind me to call mum Tuesday at 5pm",
        "remind me to call mum Friday 9am",
    ],
)
def test_bare_trailing_weekday_reminder_is_not_fast_path(text):
    from intent_router import _extract_simple_reminder_slots

    assert _extract_simple_reminder_slots(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "remind me to call mum at 5",
        "remind me to call mum at 5:30",
    ],
)
@pytest.mark.asyncio
async def test_bare_numeric_time_reminder_still_uses_existing_extractor(monkeypatch, text):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "bare numeric time reminder", "date": "2026-06-15", "time": "17:00"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent(text, user_id="guest")

    assert calls == [("reminder_create", text)]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "bare numeric time reminder", "date": "2026-06-15", "time": "17:00"}


@pytest.mark.asyncio
async def test_time_before_day_reminder_skips_llm_extractor(monkeypatch):
    module = types.ModuleType("nlu_extractor")

    async def fail_extract(_intent_name, _raw):
        raise AssertionError("time-before-day simple reminder should not call the LLM slot extractor")

    module.extract_slots_for_intent = fail_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent("remind me to call mum at 10am tomorrow", user_id="guest")

    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots["title"] == "call mum"
    assert intent.slots["time"] == "10:00"
    assert intent.slots["date"]


@pytest.mark.parametrize(
    ("text", "expected_time"),
    [
        ("remind me to call mum on Tuesday", None),
        ("remind me to call mum at 10am on Tuesday", "10:00"),
    ],
)
@pytest.mark.asyncio
async def test_on_day_reminder_skips_llm_extractor_without_stray_on(monkeypatch, text, expected_time):
    module = types.ModuleType("nlu_extractor")

    async def fail_extract(_intent_name, _raw):
        raise AssertionError("on-day simple reminder should not call the LLM slot extractor")

    module.extract_slots_for_intent = fail_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent(text, user_id="guest")

    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots["title"] == "call mum"
    assert intent.slots["date"]
    if expected_time:
        assert intent.slots["time"] == expected_time
    else:
        assert "time" not in intent.slots
