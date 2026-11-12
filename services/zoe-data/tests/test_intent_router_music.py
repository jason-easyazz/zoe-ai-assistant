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
_REAL_MA_PLAY = intent_router._music_assistant_play
_REAL_MA_ACTIVE = intent_router._music_assistant_active_player


async def _ma_unavailable(_query, _user_id, _room=None):
    return "unavailable", None


async def _ma_idle(_room=None):  # the HA-path tests: the music engine plays nothing
    return None


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
    # The HA-path tests below pin the FALLBACK: Zoe's own music engine played nothing.
    monkeypatch.setattr(intent_router, "_music_assistant_play", _ma_unavailable)
    monkeypatch.setattr(intent_router, "_music_assistant_active_player", _ma_idle)

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


# ── voice "play X" goes through Zoe's own music engine first (2026-11-12) ─────
# Live, HA listed no available media_player, so every voice "play X" ended in
# "I can't find an available speaker" and never reached Music Assistant.
# Negative control: drop the _music_assistant_play call from the music_play
# branch and the first test goes red (no search, an HA play_media post instead).


@pytest.fixture
def ma(monkeypatch):
    """A mocked music_service: nothing here can start playback or wake MA."""
    import music_service

    state = {"up": True, "players": [{"player_id": "kitchen", "display_name": "Kitchen"}],
             "hit": {"name": "One More Time", "artist": "Daft Punk"}, "calls": []}

    async def ensure_running():
        return state["up"]

    async def get_players():
        return list(state["players"])

    async def search_and_play(query, player_id="", radio_mode=False, zoe_user_id=""):
        state["calls"].append({"query": query, "player_id": player_id, "user": zoe_user_id})
        return state["hit"]

    monkeypatch.setattr(intent_router, "_music_assistant_play", _REAL_MA_PLAY)
    monkeypatch.setattr(music_service, "ensure_running", ensure_running)
    monkeypatch.setattr(music_service, "get_players", get_players)
    monkeypatch.setattr(music_service, "search_and_play", search_and_play)
    monkeypatch.setattr(music_service, "get_preferred_player_id", lambda: "kitchen")
    return state


def _ha_posts():
    return [p for p in _FakeAsyncClient.posts if p["url"].endswith("/devices/control")]


@pytest.mark.asyncio
async def test_voice_play_uses_the_music_engine_before_home_assistant(ma):
    result = await intent_router._execute_music_intent(Intent("music_play", {"query": "Daft Punk"}), "member-a")

    assert result == "Playing One More Time by Daft Punk."
    assert ma["calls"] == [{"query": "Daft Punk", "player_id": "kitchen", "user": "member-a"}]
    assert _ha_posts() == []  # Home Assistant is not touched when the engine played it


@pytest.mark.asyncio
async def test_voice_play_names_a_room_the_engine_has(ma):
    ma["players"] = [{"player_id": "lounge-1", "display_name": "Lounge"},
                     {"player_id": "kitchen", "display_name": "Kitchen"}]
    await intent_router._execute_music_intent(Intent("music_play", {"query": "jazz", "room": "lounge"}), "member-a")
    assert ma["calls"][0]["player_id"] == "lounge-1"


@pytest.mark.asyncio
async def test_nothing_found_and_no_ha_speaker_says_nothing_was_found(ma, monkeypatch):
    ma["hit"] = None

    async def no_player(_url, room=None):
        return None

    monkeypatch.setattr(intent_router, "_resolve_media_player_entity", no_player)
    result = await intent_router._execute_music_intent(Intent("music_play", {"query": "Daft Punk"}), "member-a")

    assert result == "I couldn't find Daft Punk to play."
    assert _ha_posts() == []


@pytest.mark.asyncio
async def test_nothing_found_falls_back_to_home_assistant(ma):
    ma["hit"] = None
    result = await intent_router._execute_music_intent(Intent("music_play", {"query": "Daft Punk"}), "member-a")

    assert result == "Playing Daft Punk."  # the HA path's own reply, unchanged
    assert _ha_posts()[0]["json"]["action"] == "play_media"


@pytest.mark.asyncio
async def test_engine_that_cannot_wake_says_music_is_unavailable(ma, monkeypatch):
    ma["up"] = False

    async def no_player(_url, room=None):
        return None

    monkeypatch.setattr(intent_router, "_resolve_media_player_entity", no_player)
    result = await intent_router._execute_music_intent(Intent("music_play", {"query": "Daft Punk"}), "member-a")

    assert result == intent_router._MUSIC_ENGINE_DOWN_MSG
    assert ma["calls"] == []  # never searched a sleeping engine


@pytest.mark.asyncio
async def test_engine_up_with_no_speakers_is_unavailable_not_nothing_found(ma, monkeypatch):
    ma["hit"], ma["players"] = None, []

    async def no_player(_url, room=None):
        return None

    monkeypatch.setattr(intent_router, "_resolve_media_player_entity", no_player)
    result = await intent_router._execute_music_intent(Intent("music_play", {"query": "Daft Punk"}), "member-a")
    assert result == intent_router._MUSIC_ENGINE_DOWN_MSG


@pytest.mark.asyncio
async def test_a_refused_turn_never_reaches_the_music_engine(ma, monkeypatch):
    import panel_principal

    monkeypatch.delenv("ZOE_HA_CONTROL_SCOPE", raising=False)
    token = panel_principal._home_control_turn.set(False)
    try:
        result = await intent_router._execute_music_intent(Intent("music_play", {"query": "Daft Punk"}), "guest")
    finally:
        panel_principal._home_control_turn.reset(token)

    assert result == panel_principal.HOME_CONTROL_REFUSAL
    assert ma["calls"] == []


# Follow-up voice commands go to the engine actually playing (Codex P1 on this
# PR): "play X" played through Music Assistant, so "pause" must reach MA, not an
# unrelated HA media player, and never claim success MA did not give.


@pytest.fixture
def ma_active(ma, monkeypatch):
    import music_service

    ma["players"] = [{"player_id": "kitchen", "display_name": "Kitchen", "playback_state": "playing"}]
    ma["ok"], ma["sent"] = True, []

    async def ma_ok(command, timeout_s=None, **args):
        ma["sent"].append((command, args))
        return ma["ok"]

    async def now_playing(player_id=""):
        return {"player_id": player_id, "state": "playing", "title": "One More Time", "artist": "Daft Punk"}

    monkeypatch.setattr(intent_router, "_music_assistant_active_player", _REAL_MA_ACTIVE)
    monkeypatch.setattr(music_service, "_ma_ok", ma_ok)
    monkeypatch.setattr(music_service, "now_playing", now_playing)
    return ma


@pytest.mark.asyncio
@pytest.mark.parametrize(("cmd", "expected", "sent"), [
    ("pause", "Paused.", ("player_queues/pause", {"queue_id": "kitchen"})),
    ("next", "Skipped to next.", ("player_queues/next", {"queue_id": "kitchen"})),
    ("unmute", "Unmuted.", ("players/cmd/volume_mute", {"player_id": "kitchen", "muted": False})),
])
async def test_control_after_an_engine_play_reaches_the_engine(ma_active, cmd, expected, sent):
    result = await intent_router._execute_music_intent(Intent("music_control", {"command": cmd}), "member-a")

    assert result == expected
    assert ma_active["sent"] == [sent]
    assert _ha_posts() == []  # no unrelated HA player is touched


@pytest.mark.asyncio
async def test_volume_after_an_engine_play_reaches_the_engine(ma_active):
    result = await intent_router._execute_music_intent(Intent("music_volume", {"level": 35}), "member-a")

    assert result == "Volume set to 35%."
    assert ma_active["sent"] == [("players/cmd/volume_set", {"player_id": "kitchen", "volume_level": 35})]
    assert _ha_posts() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", [
    Intent("music_control", {"command": "pause"}),
    Intent("music_volume", {"level": 35}),
])
async def test_engine_refusal_is_never_reported_as_success(ma_active, intent):
    ma_active["ok"] = False

    result = await intent_router._execute_music_intent(intent, "member-a")

    assert result == intent_router._MUSIC_CONTROL_FAILED_MSG
    assert _ha_posts() == []  # nor "fixed" by pausing a player that is not the one playing


@pytest.mark.asyncio
async def test_now_playing_reads_the_engine_playing(ma_active):
    result = await intent_router._execute_music_intent(Intent("music_control", {"command": "now_playing"}), "member-a")

    assert result == "Playing One More Time by Daft Punk."


@pytest.mark.asyncio
async def test_idle_engine_leaves_control_to_home_assistant(ma_active):
    # Negative control: the engine plays nothing, so the HA fallback still runs.
    ma_active["players"][0]["playback_state"] = "idle"

    result = await intent_router._execute_music_intent(Intent("music_control", {"command": "pause"}), "member-a")

    assert result == "Paused."
    assert ma_active["sent"] == []
    assert _ha_posts()[0]["json"]["action"] == "media_pause"
