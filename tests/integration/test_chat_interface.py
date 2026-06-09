#!/usr/bin/env python3
"""Production smoke tests for Zoe's live chat interface."""

import json

import requests


BASE_URL = "http://localhost:8000"
UI_BASE_URL = "http://localhost"


def _chat_events(message: str) -> list[dict]:
    response = requests.post(
        f"{BASE_URL}/api/chat/",
        params={"stream": "true"},
        json={"message": message},
        timeout=15,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = []
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            events.append(json.loads(line.removeprefix("data: ")))
        except json.JSONDecodeError:
            continue
    assert events, f"No SSE events received; raw response: {response.text[:500]}"
    return events


def test_chat_interface_emits_ag_ui_stream():
    events = _chat_events("Say exactly: Zoe chat integration ok")

    event_types = [event["type"] for event in events]
    assert event_types[0] == "RUN_STARTED"
    assert "TEXT_MESSAGE_START" in event_types
    assert "TEXT_MESSAGE_END" in event_types
    assert event_types[-1] == "RUN_FINISHED"

    assistant_chunks = [
        event.get("delta", "")
        for event in events
        if event["type"] == "TEXT_MESSAGE_CHUNK"
    ]
    assert any(chunk.strip() for chunk in assistant_chunks)


def test_chat_html_is_accessible():
    response = requests.get(f"{UI_BASE_URL}/chat.html", timeout=5)

    assert response.status_code == 200
    assert "Zoe - AI Chat" in response.text


if __name__ == "__main__":
    for event in _chat_events("Say exactly: Zoe chat integration ok"):
        print(event)
