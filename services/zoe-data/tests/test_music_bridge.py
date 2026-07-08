"""Music Assistant bridge — classifier + music_service (mocked MA) + card shape."""
import pytest

import music_service
from skybridge_service import classify_skybridge_intent, skybridge_intent_requires_identity


# ── classifier (no MA needed) ────────────────────────────────────────────────

@pytest.mark.parametrize("q,domain,action", [
    ("play some jazz", "music", "play"),
    ("put on the beatles", "music", "play"),
    ("play the news", "music", "play"),
    ("play some music", "music", "status"),   # generic → show, not a bogus search
    ("what's playing", "music", "status"),
    ("show music", "music", "status"),
    ("pause the music", "music", "pause"),
    ("next song", "music", "next"),
    ("skip this song", "music", "next"),
    ("turn the music up", "music", "volume_up"),
    ("turn the music down", "music", "volume_down"),
])
def test_music_intents(q, domain, action):
    i = classify_skybridge_intent(q, None)
    assert i is not None and (i.domain, i.action) == (domain, action), q


@pytest.mark.parametrize("q", [
    "play a game", "let's play pretend", "add milk to the shopping list",
    "set a timer for 5 minutes", "what's on my calendar", "turn on the lights",
])
def test_music_does_not_over_capture(q):
    i = classify_skybridge_intent(q, None)
    assert i is None or i.domain != "music", q


def test_music_is_public_no_identity_gate():
    i = classify_skybridge_intent("play some jazz", None)
    assert skybridge_intent_requires_identity(i) is False


# ── music_service resolver (mocked MA) ───────────────────────────────────────

class _Intent:
    def __init__(self, action, query=""):
        self.domain = "music"; self.action = action; self.query = query


@pytest.mark.asyncio
async def test_status_when_playing(monkeypatch):
    async def fake_np(pid=""):
        return {"player_id": "p1", "player_name": "Kitchen", "state": "playing",
                "title": "So What", "artist": "Miles Davis", "album": "Kind of Blue", "image": ""}
    monkeypatch.setattr(music_service, "now_playing", fake_np)
    r = await music_service.resolve_music(_Intent("status"))
    assert r["handled"] and "So What" in r["spoken_summary"]
    card = r["cards"][0]
    assert card["card_type"] == "now_playing" and card["content"]["state"] == "playing"
    assert card["content"]["transport"] is True


@pytest.mark.asyncio
async def test_status_when_ma_down_is_friendly(monkeypatch):
    async def none_np(pid=""): return None
    monkeypatch.setattr(music_service, "now_playing", none_np)
    r = await music_service.resolve_music(_Intent("status"))
    assert r["handled"] and "set up" in r["spoken_summary"].lower()
    assert r["cards"][0]["card_id"] == "music-browse"  # honest browse card, not fake


@pytest.mark.asyncio
async def test_pause_calls_control(monkeypatch):
    calls = {}
    async def fake_np(pid=""): return {"state": "playing", "title": "X", "player_id": "p"}
    async def fake_control(action, player_id="", value=None): calls["a"] = action; return True
    monkeypatch.setattr(music_service, "now_playing", fake_np)
    monkeypatch.setattr(music_service, "control", fake_control)
    r = await music_service.resolve_music(_Intent("pause"))
    assert calls["a"] == "pause" and r["intent"]["action"] == "pause"


@pytest.mark.asyncio
async def test_play_searches_and_plays(monkeypatch):
    async def fake_sp(query, player_id=""): return {"name": "Blue in Green", "artist": "Miles Davis"}
    async def fake_np(pid=""): return {"state": "playing", "title": "Blue in Green", "artist": "Miles Davis"}
    monkeypatch.setattr(music_service, "search_and_play", fake_sp)
    monkeypatch.setattr(music_service, "now_playing", fake_np)
    r = await music_service.resolve_music(_Intent("play", "some miles davis"))
    assert "Blue in Green" in r["spoken_summary"]


@pytest.mark.asyncio
async def test_control_never_raises_when_ma_down(monkeypatch):
    async def no_players(): return []
    monkeypatch.setattr(music_service, "get_players", no_players)
    assert await music_service.control("pause") is False
    assert await music_service.now_playing() is None
