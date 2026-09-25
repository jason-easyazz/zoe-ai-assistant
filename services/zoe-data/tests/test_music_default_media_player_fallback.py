"""`ZOE_DEFAULT_MEDIA_PLAYER` pointing at a non-existent HA entity must fail SOFT.

2026-09-25 audit §2.4: the live env says `media_player.living_room`, HA has no
such entity (only `media_player.lva_88a29e0a953f_media_player`), so every
play/pause/volume that fell through to HA 404ed. The env value is operator
config; the code now resolves it against what HA actually lists, warns ONCE
naming the real entities, and uses the first real media_player instead.

Negative control: the pre-fix behaviour (post the configured id verbatim) is
asserted to be what we no longer do, and the bridge-offline path is shown to
keep the old behaviour so no new failure mode is added.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import intent_router
from intent_router import Intent

pytestmark = pytest.mark.ci_safe

_REAL = "media_player.lva_88a29e0a953f_media_player"
_STALE = "media_player.living_room"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    posts: list = []
    gets: list = []
    entities: list = [{"entity_id": _REAL, "state": "idle"}]
    get_raises: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json})
        return _FakeResponse(200)

    async def get(self, url, headers=None):
        self.gets.append(url)
        if self.get_raises is not None:
            raise self.get_raises
        return _FakeResponse(200, {"entities": list(self.entities), "count": len(self.entities)})


async def _immediate(result=None):
    return result


@pytest.fixture(autouse=True)
def _fakes(monkeypatch):
    _FakeAsyncClient.posts = []
    _FakeAsyncClient.gets = []
    _FakeAsyncClient.entities = [{"entity_id": _REAL, "state": "idle"}]
    _FakeAsyncClient.get_raises = None
    monkeypatch.setattr(intent_router, "_MEDIA_PLAYER_CACHE", {"configured": None, "resolved": None, "expires": 0.0})
    monkeypatch.setattr(intent_router, "_MEDIA_PLAYER_WARNED", set())
    monkeypatch.setattr(intent_router, "_music_top_recent_genre", lambda _u: asyncio.sleep(0, result=None))
    monkeypatch.setattr(intent_router, "_music_recent_skip_count", lambda _u: asyncio.sleep(0, result=0))
    monkeypatch.setattr(intent_router, "_music_recent_repeat_count", lambda *_a: asyncio.sleep(0, result=0))
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, result=None, **_k: _immediate(result))

    import database

    async def _no_log(**_kwargs):
        return None

    monkeypatch.setattr(database, "log_music_event", _no_log)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setenv("ZOE_DEFAULT_MEDIA_PLAYER", _STALE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "expected_action"),
    [
        (Intent("music_play", {"query": "Daft Punk"}), "play_media"),
        (Intent("music_control", {"command": "pause"}), "media_pause"),
        (Intent("music_volume", {"level": 35}), "volume_set"),
    ],
)
async def test_stale_default_player_falls_back_to_a_real_ha_entity(intent, expected_action, caplog):
    caplog.set_level(logging.WARNING, logger=intent_router.logger.name)

    result = await intent_router._execute_music_intent(intent, "jason")

    assert result is not None and "couldn't" not in result
    post = _FakeAsyncClient.posts[0]
    assert post["url"] == "http://127.0.0.1:8007/devices/control"
    assert post["json"]["action"] == expected_action
    assert post["json"]["entity_id"] == _REAL          # negative control: pre-fix == _STALE
    assert post["json"]["entity_id"] != _STALE
    assert _FakeAsyncClient.gets == ["http://127.0.0.1:8007/entities?domain=media_player"]
    warn = [r for r in caplog.records if r.levelno == logging.WARNING and "ZOE_DEFAULT_MEDIA_PLAYER" in r.getMessage()]
    assert len(warn) == 1
    assert _STALE in warn[0].getMessage() and _REAL in warn[0].getMessage()


@pytest.mark.asyncio
async def test_warning_fires_once_and_resolution_is_cached(caplog):
    caplog.set_level(logging.WARNING, logger=intent_router.logger.name)

    await intent_router._execute_music_intent(Intent("music_control", {"command": "pause"}), "jason")
    await intent_router._execute_music_intent(Intent("music_control", {"command": "next"}), "jason")
    await intent_router._execute_music_intent(Intent("music_volume", {"level": 20}), "jason")

    assert [p["json"]["entity_id"] for p in _FakeAsyncClient.posts] == [_REAL, _REAL, _REAL]
    assert len(_FakeAsyncClient.gets) == 1, "entity list is cached, not fetched per command"
    warn = [r for r in caplog.records if "ZOE_DEFAULT_MEDIA_PLAYER" in r.getMessage()]
    assert len(warn) == 1


@pytest.mark.asyncio
async def test_configured_player_that_exists_is_used_unchanged(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=intent_router.logger.name)
    monkeypatch.setenv("ZOE_DEFAULT_MEDIA_PLAYER", _REAL)
    _FakeAsyncClient.entities = [{"entity_id": "media_player.other"}, {"entity_id": _REAL}]

    await intent_router._execute_music_intent(Intent("music_control", {"command": "pause"}), "jason")

    assert _FakeAsyncClient.posts[0]["json"]["entity_id"] == _REAL
    assert not [r for r in caplog.records if "ZOE_DEFAULT_MEDIA_PLAYER" in r.getMessage()]


@pytest.mark.asyncio
async def test_default_all_target_skips_the_lookup(monkeypatch):
    monkeypatch.delenv("ZOE_DEFAULT_MEDIA_PLAYER", raising=False)

    await intent_router._execute_music_intent(Intent("music_control", {"command": "pause"}), "jason")

    assert _FakeAsyncClient.posts[0]["json"]["entity_id"] == "media_player.all"
    assert _FakeAsyncClient.gets == []


@pytest.mark.asyncio
async def test_bridge_entity_list_unavailable_keeps_old_behaviour():
    """No new failure mode: if HA cannot be asked, post the configured id as before."""
    _FakeAsyncClient.get_raises = ConnectionError("bridge down")

    await intent_router._execute_music_intent(Intent("music_control", {"command": "pause"}), "jason")

    assert _FakeAsyncClient.posts[0]["json"]["entity_id"] == _STALE


@pytest.mark.asyncio
async def test_ha_with_no_media_players_keeps_configured_id():
    _FakeAsyncClient.entities = []

    await intent_router._execute_music_intent(Intent("music_control", {"command": "pause"}), "jason")

    assert _FakeAsyncClient.posts[0]["json"]["entity_id"] == _STALE
