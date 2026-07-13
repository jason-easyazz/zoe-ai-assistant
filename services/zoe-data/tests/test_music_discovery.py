"""music_discovery unit tests — taste seed, playlist bridge, intent alias.

Slim-dep safe (httpx import only, every MA call faked), so ci_safe: runs in
the GitHub-hosted marker lane; the Jetson catch-all covers it too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import music_discovery as md  # noqa: E402
import music_history as mh  # noqa: E402
import music_service as ms  # noqa: E402

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _no_journal(monkeypatch):
    """Default every test to an empty journal (no DB in slim CI); individual
    tests override top_artists to exercise the journal-first path."""
    async def empty(user_id=None, days=90, limit=10):
        return []

    monkeypatch.setattr(mh, "top_artists", empty)


def _fake_ma(responses: dict[str, object]):
    """command → canned response; commands may repeat (returns same)."""
    calls: list[tuple[str, dict]] = []

    async def fake(command: str, **args):
        calls.append((command, args))
        return responses.get(command)

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


# ── intent alias ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "my discovery playlist", "discovery playlist", "the discovery playlist",
    "zoe discovery", "my discovery mix", "discovery", "my discoveries",
    "Zoe's discovery playlist", "my music discovery picks",
])
def test_discovery_alias_matches(query):
    assert md.is_discovery_playlist_query(query)


@pytest.mark.parametrize("query", [
    "discover weekly", "jazz", "my playlist", "discovery channel documentary",
    "", "some music", "daft punk discovery",  # the ALBUM Discovery needs the artist form
])
def test_discovery_alias_rejects(query):
    assert not md.is_discovery_playlist_query(query)


# ── taste seed ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_taste_seed_ranks_recent_artists(monkeypatch):
    recent = [{"uri": f"yt://track/{i}", "media_type": "track"} for i in range(4)]
    items = {
        "yt://track/0": {"artists": [{"name": "Iron & Wine"}]},
        "yt://track/1": {"artists": [{"name": "Bon Iver"}]},
        "yt://track/2": {"artists": [{"name": "Bon Iver"}]},
        "yt://track/3": {"artists": [{"name": "Céline Dion, Someone Else"}]},
    }

    async def fake(command, **args):
        if command == "music/recently_played_items":
            return recent
        if command == "music/item_by_uri":
            return items[args["uri"]]
        if command == "music/artists/library_items":
            return []
        raise AssertionError(command)

    monkeypatch.setattr(ms, "_ma", fake)
    seed = await md.taste_seed(max_artists=3)
    assert seed[0] == "Bon Iver"  # 2 plays outranks 1
    assert "Iron" in seed[1] or "Céline" in seed[1]
    assert "Céline Dion" in seed  # multi-credit collapsed to first artist


@pytest.mark.asyncio
async def test_taste_seed_backfills_from_library_play_count(monkeypatch):
    async def fake(command, **args):
        if command == "music/recently_played_items":
            return []
        if command == "music/artists/library_items":
            assert args["order_by"] == "play_count_desc"
            return [{"name": "Radiohead", "play_count": 12},
                    {"name": "Never Played", "play_count": 0}]
        raise AssertionError(command)

    monkeypatch.setattr(ms, "_ma", fake)
    assert await md.taste_seed() == ["Radiohead"]


@pytest.mark.asyncio
async def test_taste_seed_ma_down_returns_empty(monkeypatch):
    async def fake(command, **args):
        return None  # _ma's unreachable shape

    monkeypatch.setattr(ms, "_ma", fake)
    assert await md.taste_seed() == []


def test_build_mood_query_seeded_and_fallback():
    q = md.build_mood_query(["Bon Iver", "Iron & Wine"])
    assert "Bon Iver" in q and "Iron & Wine" in q
    assert "family-friendly" in md.build_mood_query([])
    assert md.build_mood_query([], "custom mood") == "custom mood"


# ── recommendation → track resolution ────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_recommendation_filters_wrong_artists(monkeypatch):
    hits = {"tracks": [
        {"uri": "yt://t/1", "artists": [{"name": "Gregory Alan Isakov"}]},
        {"uri": "yt://t/2", "artists": [{"name": "Somebody Else"}]},
        {"uri": "yt://t/3", "artists": [{"name": "Gregory Alan Isakov"}]},
    ]}

    async def fake(command, **args):
        assert command == "music/search"
        return hits

    monkeypatch.setattr(ms, "_ma", fake)
    uris = await md.resolve_recommendation_tracks(
        {"artistName": "Gregory Alan Isakov"}, per_artist=3)
    assert uris == ["yt://t/1", "yt://t/3"]


@pytest.mark.asyncio
async def test_resolve_recommendation_unresolvable(monkeypatch):
    async def fake(command, **args):
        return {"tracks": []}

    monkeypatch.setattr(ms, "_ma", fake)
    assert await md.resolve_recommendation_tracks({"artistName": "The Luminesce"}) == []


# ── playlist bridge ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replace_playlist_creates_when_missing(monkeypatch):
    created = {}

    async def fake(command, **args):
        if command == "music/playlists/library_items":
            return []
        if command == "music/playlists/create_playlist":
            created.update(args)
            return {"item_id": 42, "name": args["name"], "uri": "library://playlist/42"}
        raise AssertionError(command)

    async def fake_ok(command, timeout_s=0, **args):
        assert command == "music/playlists/add_playlist_tracks"
        assert args["db_playlist_id"] == "42"
        assert args["uris"] == ["yt://t/1"]
        return True

    monkeypatch.setattr(ms, "_ma", fake)
    monkeypatch.setattr(ms, "_ma_ok", fake_ok)
    res = await md.replace_discovery_playlist(["yt://t/1"])
    assert res["ok"] and res["added"] == 1
    assert created["name"] == md.DISCOVERY_PLAYLIST_NAME


@pytest.mark.asyncio
async def test_replace_playlist_clears_existing_by_position(monkeypatch):
    ok_calls = []

    async def fake(command, **args):
        if command == "music/playlists/library_items":
            return [{"item_id": 7, "name": "zoe discovery", "uri": "library://playlist/7"}]
        if command == "music/playlists/playlist_tracks":
            return [{"position": 0}, {"position": 1}]
        raise AssertionError(command)

    async def fake_ok(command, timeout_s=0, **args):
        ok_calls.append((command, args))
        return True

    monkeypatch.setattr(ms, "_ma", fake)
    monkeypatch.setattr(ms, "_ma_ok", fake_ok)
    res = await md.replace_discovery_playlist(["yt://t/9"])
    assert res["ok"] and res["playlist_id"] == "7"
    assert ok_calls[0][0] == "music/playlists/remove_playlist_tracks"
    assert ok_calls[0][1]["positions_to_remove"] == [0, 1]
    assert ok_calls[1][0] == "music/playlists/add_playlist_tracks"


@pytest.mark.asyncio
async def test_replace_playlist_empty_uris_refuses():
    res = await md.replace_discovery_playlist([])
    assert not res["ok"]


# ── play_discovery ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_play_discovery_no_playlist_yet(monkeypatch):
    async def fake(command, **args):
        return []

    monkeypatch.setattr(ms, "_ma", fake)
    res = await md.play_discovery()
    assert not res["ok"]


@pytest.mark.asyncio
async def test_play_discovery_plays_playlist_uri(monkeypatch):
    async def fake(command, **args):
        return [{"item_id": 7, "name": "Zoe Discovery", "uri": "library://playlist/7"}]

    played = {}

    async def fake_play(uri, player_id="", zoe_user_id=""):
        played["uri"] = uri
        return {"ok": True, "uri": uri}

    monkeypatch.setattr(ms, "_ma", fake)
    monkeypatch.setattr(ms, "play_media", fake_play)
    res = await md.play_discovery()
    assert res["ok"] and played["uri"] == "library://playlist/7"


# ── batch script pure helpers ────────────────────────────────────────────────

def test_batch_meminfo_parser():
    import importlib.util
    script = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "music_discovery_batch.py"
    spec = importlib.util.spec_from_file_location("music_discovery_batch", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = "MemTotal: 16000000 kB\nMemAvailable:  2048000 kB\n"
    assert mod.parse_meminfo_available_mb(text) == 2000
    assert mod.parse_meminfo_available_mb("MemTotal: 1 kB\n") == 0


# ── journal-first seed + attribution choke point ─────────────────────────────

@pytest.mark.asyncio
async def test_taste_seed_prefers_journal(monkeypatch):
    async def journal(user_id=None, days=90, limit=10):
        return ["Journal Artist %d" % i for i in range(limit)]

    async def boom(command, **args):  # MA must not even be consulted
        raise AssertionError("MA fallback used despite full journal")

    monkeypatch.setattr(mh, "top_artists", journal)
    monkeypatch.setattr(ms, "_ma", boom)
    seed = await md.taste_seed(max_artists=4)
    assert seed == ["Journal Artist %d" % i for i in range(4)]


@pytest.mark.asyncio
async def test_taste_seed_per_user_never_falls_back_to_household_ma_data(monkeypatch):
    async def journal(user_id=None, days=90, limit=10):
        assert user_id == "jason"
        return ["Bon Iver"]

    async def boom(command, **args):
        raise AssertionError("per-user seed must not mix in unattributed MA data")

    monkeypatch.setattr(mh, "top_artists", journal)
    monkeypatch.setattr(ms, "_ma", boom)
    assert await md.taste_seed(user_id="jason") == ["Bon Iver"]


@pytest.mark.parametrize("raw,expected", [
    ("jason", "jason"), (" jason ", "jason"),
    ("", mh.GUEST_USER_ID), (None, mh.GUEST_USER_ID),
    ("guest", mh.GUEST_USER_ID), ("voice-guest", mh.GUEST_USER_ID),
    ("Guest", mh.GUEST_USER_ID),
])
def test_resolve_music_user_choke_point(raw, expected):
    assert mh.resolve_music_user(raw) == expected


def test_media_fields_normalizes_ma_item():
    fields = mh.media_fields({
        "name": "Ashes", "uri": "yt://track/1", "provider": "ytmusic--x",
        "media_type": "track",
        "artists": [{"name": "Céline Dion"}, {"name": "Other"}],
        "album": {"name": "Deadpool 2"},
    })
    assert fields == {"track": "Ashes", "artist": "Céline Dion, Other",
                      "album": "Deadpool 2", "provider": "ytmusic--x",
                      "uri": "yt://track/1", "media_type": "track"}
    assert mh.media_fields({})["track"] == ""
