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


# ── "Hey Zoe, I like this song" -> favourite the current track ───────────────

@pytest.mark.parametrize("phrase", [
    "I like this song",
    "i love this track",
    "favourite this",
    "favorite this song",
    "hey zoe, I like this",
    "thumbs up this song",
    "like it",
])
def test_favorite_phrases_detect_music_favorite(phrase):
    det = intent_router.detect_intent(phrase, log_miss=False)
    assert det is not None and det.name == "music_favorite", f"{phrase!r} -> {det}"


@pytest.mark.parametrize("phrase", [
    "I like jazz",                 # a taste statement, not "this" — must NOT fire
    "play something I like",       # a play request
    "I like the weather today",
])
def test_taste_statements_do_not_favourite(phrase):
    det = intent_router.detect_intent(phrase, log_miss=False)
    assert det is None or det.name != "music_favorite", f"{phrase!r} wrongly -> music_favorite"


@pytest.mark.asyncio
async def test_favorite_execution_speaks_the_track(monkeypatch):
    import music_service
    async def fake_fav(player_id=""):
        return {"ok": True, "title": "Meet Joe Black", "artist": "Thomas Newman"}
    monkeypatch.setattr(music_service, "favorite_now_playing", fake_fav)
    result = await intent_router._execute_music_intent(Intent("music_favorite", {}), "jason")
    assert result == "Done — added Meet Joe Black by Thomas Newman to your favourites."


@pytest.mark.asyncio
async def test_favorite_nothing_playing_says_so(monkeypatch):
    import music_service
    async def fake_fav(player_id=""):
        return {"ok": False, "reason": "nothing playing"}
    monkeypatch.setattr(music_service, "favorite_now_playing", fake_fav)
    result = await intent_router._execute_music_intent(Intent("music_favorite", {}), "jason")
    assert result == "There's nothing playing to favourite right now."


@pytest.mark.asyncio
async def test_favorite_write_failure_does_not_claim_success(monkeypatch):
    import music_service
    async def fake_fav(player_id=""):
        return {"ok": False, "reason": "unavailable", "title": "X", "artist": "Y"}
    monkeypatch.setattr(music_service, "favorite_now_playing", fake_fav)
    result = await intent_router._execute_music_intent(Intent("music_favorite", {}), "jason")
    assert result == "I couldn't favourite that just now."


# ── the voice channel must actually reach this intent ───────────────────────

def test_music_favorite_is_reachable_from_the_voice_fast_path():
    """The whole feature is voice-triggered, so the voice gate must admit it.

    voice_tts.py's public-intent short-circuit only executes intents that are in
    guest_policy.PUBLIC_HOUSEHOLD_INTENTS. An intent missing from that set falls
    through to the brain, which has no favourite capability — so "Hey Zoe, I
    like this song" would detect correctly and then silently do nothing.

    Favouriting writes to the SHARED Music Assistant library
    (favorite_now_playing takes no user_id), so household scope is correct here
    rather than USER_SCOPED_INTENTS.
    """
    from guest_policy import PUBLIC_HOUSEHOLD_INTENTS, USER_SCOPED_INTENTS

    assert "music_favorite" in PUBLIC_HOUSEHOLD_INTENTS
    assert "music_favorite" not in USER_SCOPED_INTENTS


def test_the_favorite_phrase_detects_and_is_voice_admitted():
    """End-to-end on the two gates the phrase must clear: detect, then admit."""
    from guest_policy import PUBLIC_HOUSEHOLD_INTENTS

    for phrase in ("hey zoe i like this song", "I like this song", "I love this song"):
        intent = intent_router.detect_intent(phrase, log_miss=False)
        assert intent is not None, f"{phrase!r} did not detect"
        assert intent.name == "music_favorite", f"{phrase!r} -> {intent.name}"
        assert intent.name in PUBLIC_HOUSEHOLD_INTENTS, (
            f"{phrase!r} detects but the voice fast path would skip it"
        )
