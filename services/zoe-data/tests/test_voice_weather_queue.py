import json

from routers.voice_tts import _should_supersede_voice_weather_action


def _row(action_type: str, payload: dict, key: str = "old-key") -> dict:
    return {
        "action_type": action_type,
        "payload": json.dumps(payload),
        "idempotency_key": key,
    }


def test_supersedes_old_voice_weather_actions() -> None:
    assert _should_supersede_voice_weather_action(
        _row("panel_navigate", {"url": "/touch/weather.html"}),
        "new-nav",
        "new-card",
    )
    assert _should_supersede_voice_weather_action(
        _row("show_card", {"type": "weather"}),
        "new-nav",
        "new-card",
    )


def test_preserves_current_weather_actions() -> None:
    assert not _should_supersede_voice_weather_action(
        _row("panel_navigate", {"url": "/touch/weather.html"}, key="new-nav"),
        "new-nav",
        "new-card",
    )
    assert not _should_supersede_voice_weather_action(
        _row("show_card", {"type": "weather"}, key="new-card"),
        "new-nav",
        "new-card",
    )


def test_ignores_non_weather_actions() -> None:
    assert not _should_supersede_voice_weather_action(
        _row("panel_navigate", {"url": "/touch/calendar.html"}),
        "new-nav",
        "new-card",
    )
    assert not _should_supersede_voice_weather_action(
        _row("show_card", {"type": "calendar"}),
        "new-nav",
        "new-card",
    )
