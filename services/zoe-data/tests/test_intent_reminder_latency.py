import sys
import time
import types

import pytest


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
async def test_relative_reminder_still_uses_existing_extractor(monkeypatch):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "check the oven", "date": "2026-06-15", "time": "20:10"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent("remind me in 5 minutes to check the oven", user_id="guest")

    assert calls == [("reminder_create", "remind me in 5 minutes to check the oven")]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "check the oven", "date": "2026-06-15", "time": "20:10"}


@pytest.mark.asyncio
async def test_trailing_relative_reminder_still_uses_existing_extractor(monkeypatch):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "check the oven", "date": "2026-06-15", "time": "20:30"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    text = "remind me to check the oven in 30 minutes"
    intent = await detect_and_extract_intent(text, user_id="guest")

    assert calls == [("reminder_create", text)]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "check the oven", "date": "2026-06-15", "time": "20:30"}


@pytest.mark.parametrize(
    "text",
    [
        "remind me to call mum in a few days",
        "remind me to call mum in two hours",
        "remind me to check the filter in a couple of weeks",
        "remind me to check the oven in an hour",
        "remind me to check the oven in half an hour",
        "remind me to call mum in a day",
    ],
)
@pytest.mark.asyncio
async def test_word_relative_reminder_still_uses_existing_extractor(monkeypatch, text):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "word relative reminder", "date": "2026-06-18"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent(text, user_id="guest")

    assert calls == [("reminder_create", text)]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "word relative reminder", "date": "2026-06-18"}


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
