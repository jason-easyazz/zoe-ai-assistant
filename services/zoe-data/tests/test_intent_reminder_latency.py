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


@pytest.mark.asyncio
async def test_modifier_day_reminder_still_uses_existing_extractor(monkeypatch):
    calls = []
    module = types.ModuleType("nlu_extractor")

    async def fake_extract(intent_name, raw):
        calls.append((intent_name, raw))
        return {"title": "call mum", "date": "2026-06-23"}

    module.extract_slots_for_intent = fake_extract
    monkeypatch.setitem(sys.modules, "nlu_extractor", module)

    from intent_router import detect_and_extract_intent

    text = "remind me to call mum next Tuesday"
    intent = await detect_and_extract_intent(text, user_id="guest")

    assert calls == [("reminder_create", text)]
    assert intent is not None
    assert intent.name == "reminder_create"
    assert intent.slots == {"title": "call mum", "date": "2026-06-23"}
