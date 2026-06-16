import asyncio
import json
import pytest

from intent_router import _execute_daily_briefing


@pytest.mark.asyncio
async def test_daily_briefing_runs_mcporter_calls_concurrently(monkeypatch):
    calls = []
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def fake_run_mcporter(cmd):
        calls.append(cmd)
        if len(calls) == 3:
            all_started.set()
        await release.wait()
        if "weather_current" in cmd:
            return json.dumps({"temp": 18.5, "city": "Perth", "description": "clear"})
        if "calendar_today" in cmd:
            return json.dumps({"events": [{"title": "Standup", "start_time": "09:00"}]})
        if "reminder_list" in cmd:
            return json.dumps({"reminders": [{"title": "pay invoice", "due_time": "17:00"}]})
        return None

    monkeypatch.setattr("intent_router._run_mcporter", fake_run_mcporter)

    task = asyncio.create_task(_execute_daily_briefing("family-admin"))
    await asyncio.wait_for(all_started.wait(), timeout=1.0)
    assert len(calls) == 3
    assert not task.done()

    release.set()
    result = await asyncio.wait_for(task, timeout=1.0)

    assert "Weather: 18.5" in result
    assert "Standup at 09:00" in result
    assert "pay invoice at 17:00" in result


@pytest.mark.asyncio
async def test_daily_briefing_preserves_partial_results_when_one_call_fails(monkeypatch):
    async def fake_run_mcporter(cmd):
        await asyncio.sleep(0)
        if "weather_current" in cmd:
            return json.dumps({"temp": 19, "city": "Perth", "description": "cloudy"})
        if "calendar_today" in cmd:
            raise RuntimeError("calendar unavailable")
        if "reminder_list" in cmd:
            return json.dumps({"reminders": [{"title": "check oven"}]})
        return None

    monkeypatch.setattr("intent_router._run_mcporter", fake_run_mcporter)

    result = await _execute_daily_briefing("family-admin")

    assert "Weather: 19" in result
    assert "No events on the calendar today." in result
    assert "check oven" in result
