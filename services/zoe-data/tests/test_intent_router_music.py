import asyncio
import inspect

import pytest

import intent_router
from intent_router import Intent

pytestmark = pytest.mark.ci_safe


_REAL_MUSIC_DB_HELPERS = (
    intent_router._music_top_recent_genre,
    intent_router._music_recent_repeat_count,
    intent_router._music_recent_skip_count,
)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    posts = []
    status_code = 200

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(self.status_code)

    async def get(self, url, headers=None):
        return _FakeResponse(200, {})


@pytest.fixture(autouse=True)
def _music_fakes(monkeypatch):
    _FakeAsyncClient.posts = []
    _FakeAsyncClient.status_code = 200
    monkeypatch.setattr(intent_router, "_music_top_recent_genre", lambda _user_id: asyncio.sleep(0, result=None))
    monkeypatch.setattr(intent_router, "_music_recent_skip_count", lambda _user_id: asyncio.sleep(0, result=0))
    monkeypatch.setattr(intent_router, "_music_recent_repeat_count", lambda *_args: asyncio.sleep(0, result=0))
    monkeypatch.setattr(asyncio, "sleep", lambda *_args, result=None, **_kwargs: _immediate(result))

    import database

    async def fake_log_music_event(**_kwargs):
        return None

    monkeypatch.setattr(database, "log_music_event", fake_log_music_event)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)


async def _immediate(result=None):
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "expected", "expected_action"),
    [
        (Intent("music_play", {"query": "Daft Punk"}), "Playing Daft Punk.", "play_media"),
        (Intent("music_control", {"command": "next"}), "Skipped to next.", "media_next_track"),
        (Intent("music_volume", {"level": 35}), "Volume set to 35%.", "volume_set"),
    ],
)
async def test_music_success_strings_and_ha_side_effects_are_unchanged(intent, expected, expected_action):
    result = await intent_router._execute_music_intent(intent, "jason")

    assert result == expected
    assert _FakeAsyncClient.posts[0]["url"] == "http://127.0.0.1:8007/devices/control"
    assert _FakeAsyncClient.posts[0]["json"]["action"] == expected_action


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent",
    [
        Intent("music_play", {"query": "Daft Punk"}),
        Intent("music_control", {"command": "pause"}),
        Intent("music_volume", {"level": 35}),
    ],
)
async def test_music_ha_bridge_http_failure_surfaces_instead_of_success(intent):
    _FakeAsyncClient.status_code = 503

    result = await intent_router._execute_music_intent(intent, "jason")

    assert result == "I couldn't control the music because the Home Assistant bridge returned HTTP 503."


@pytest.mark.asyncio
async def test_unknown_music_command_falls_through_to_chat_fallback():
    result = await intent_router._execute_music_intent(Intent("music_control", {"command": "rewind"}), "jason")

    assert result is None


def test_music_async_path_has_no_sync_psycopg2_connect():
    sources = "\n".join(
        inspect.getsource(obj)
        for obj in (intent_router._execute_music_intent, *_REAL_MUSIC_DB_HELPERS)
    )

    assert "psycopg2" not in sources
    assert ".connect(" not in sources
