"""The favourite heart's un-favourite half, and the "don't stop the music" read-back.

FIXTURE PROVENANCE (a fixture that invents fields tests nothing):
every shape below is a trim of a REAL response from the live MA 2.8.7 instance
on 2026-07-19, captured while a ytmusic track was playing on a Sonos:

  player          -> `players/all`
  queue           -> `player_queues/all`  (dont_stop_the_music_enabled, elapsed_time)
  item_by_uri     -> `music/item_by_uri`, captured BOTH before and after a real
                     favourite write and then restored:
                       before -> provider=ytmusic--HemJN6vc  item_id=lahBKZIkLDM
                       after  -> provider=library            item_id=2
                     That asymmetry is the whole reason favorite_remove needs a
                     resolve step: `favorites/add_item` takes a URI but
                     `favorites/remove_item` takes (media_type, library_item_id),
                     and only the LIBRARY row carries an id it will accept.

The live now-playing payload deliberately has NO `uri` field — the panel's heart
used to read `now_playing.uri`, which never existed, so it never once reached
the API. `test_now_playing_still_has_no_uri` pins that so nobody re-adds a
read of a field the API does not return.
"""
import pytest

import music_service

pytestmark = pytest.mark.ci_safe


PLAYER = {
    "player_id": "RINCON_347E5C9BEC8F01400",
    "display_name": "Bedroom",
    "name": "Bedroom",
    "provider": "sonos",
    "available": True,
    "powered": True,
    "state": "playing",
    "playback_state": "playing",
    "volume_level": 18,
    "synced_to": None,
}

# Trimmed `player_queues/all` row. dont_stop_the_music_enabled is the flag the
# toggle reads back; it really is False on the live house.
QUEUE = {
    "queue_id": "RINCON_347E5C9BEC8F01400",
    "active": True,
    "elapsed_time": 53.8,
    "current_index": 13,
    "shuffle_enabled": False,
    "repeat_mode": "off",
    "dont_stop_the_music_enabled": False,
    "current_item": {
        "queue_item_id": "7d78677ed4314bfbb2dfdd03fe97a372",
        "name": "Thomas Newman - Mr. Bad News",
        "duration": 97,
        "media_item": {
            "uri": "ytmusic--HemJN6vc://track/pu7mh0FWO7E",
            "name": "Mr. Bad News",
            "media_type": "track",
            "item_id": "pu7mh0FWO7E",
            "provider": "ytmusic--HemJN6vc",
            "favorite": False,
            "artists": [{"name": "Thomas Newman"}],
        },
    },
}

URI = "ytmusic--HemJN6vc://track/lahBKZIkLDM"
# `music/item_by_uri` BEFORE the track was ever favourited (provider-owned).
ITEM_PROVIDER = {
    "uri": URI, "name": "Yes", "media_type": "track",
    "item_id": "lahBKZIkLDM", "provider": "ytmusic--HemJN6vc", "favorite": False,
}
# ...and AFTER a favourite write: MA forced it into the library, so it now
# resolves to the library row whose item_id remove_item accepts.
ITEM_LIBRARY = {
    "uri": URI, "name": "Yes", "media_type": "track",
    "item_id": 2, "provider": "library", "favorite": True,
}


class _MA:
    """Records every MA command and replays canned results."""

    def __init__(self, results=None, ok=True):
        self.results = results or {}
        self.ok = ok
        self.calls = []

    async def ma(self, command, **args):
        self.calls.append((command, args))
        return self.results.get(command)

    async def ma_ok(self, command, timeout_s=None, **args):
        self.calls.append((command, args))
        return self.ok

    def args_for(self, command):
        return next((a for c, a in self.calls if c == command), None)

    def commands(self):
        return [c for c, _ in self.calls]


@pytest.fixture
def ma(monkeypatch):
    fake = _MA()
    monkeypatch.setattr(music_service, "_ma", fake.ma)
    monkeypatch.setattr(music_service, "_ma_ok", fake.ma_ok)
    return fake


async def _now_playing(monkeypatch, ma, queue):
    async def players():
        return [PLAYER]
    monkeypatch.setattr(music_service, "get_players", players)
    ma.results["player_queues/all"] = [queue]
    return await music_service.now_playing()


# ── "don't stop the music" read-back ────────────────────────────────────────

@pytest.mark.asyncio
async def test_now_playing_surfaces_dont_stop_disabled(monkeypatch, ma):
    np = await _now_playing(monkeypatch, ma, QUEUE)
    assert np["dont_stop"] is False


@pytest.mark.asyncio
async def test_now_playing_surfaces_dont_stop_enabled(monkeypatch, ma):
    """The toggle renders real state on load — not a guess."""
    q = dict(QUEUE, dont_stop_the_music_enabled=True)
    np = await _now_playing(monkeypatch, ma, q)
    assert np["dont_stop"] is True


@pytest.mark.asyncio
async def test_now_playing_dont_stop_defaults_false_when_absent(monkeypatch, ma):
    """An older MA (or a queue shape without the flag) must read as OFF, not crash."""
    q = {k: v for k, v in QUEUE.items() if k != "dont_stop_the_music_enabled"}
    np = await _now_playing(monkeypatch, ma, q)
    assert np["dont_stop"] is False


@pytest.mark.asyncio
async def test_now_playing_still_has_no_uri(monkeypatch, ma):
    """Pins the bug: the panel must never again read `now_playing.uri`.

    The heart used `_music.np.uri`, which was permanently '' because this
    payload has never carried a uri — so every tap short-circuited to
    "Nothing playing" and no favourite request was ever sent.
    """
    np = await _now_playing(monkeypatch, ma, QUEUE)
    assert "uri" not in np and "media_uri" not in np


# ── un-favourite ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_favorite_remove_resolves_uri_to_library_id(ma):
    """The load-bearing asymmetry: remove takes (media_type, library_item_id)."""
    ma.results["music/item_by_uri"] = ITEM_LIBRARY
    assert await music_service.favorite_remove(URI) is True
    assert ma.args_for("music/item_by_uri") == {"uri": URI}
    assert ma.args_for("music/favorites/remove_item") == {
        "media_type": "track", "library_item_id": 2,
    }


@pytest.mark.asyncio
async def test_favorite_remove_never_sends_the_uri_as_the_id(ma):
    """Passing the uri (or the provider item id) is the obvious wrong fix."""
    ma.results["music/item_by_uri"] = ITEM_LIBRARY
    await music_service.favorite_remove(URI)
    args = ma.args_for("music/favorites/remove_item")
    assert args["library_item_id"] not in (URI, "lahBKZIkLDM")


@pytest.mark.asyncio
async def test_favorite_remove_noops_for_a_never_favourited_item(ma):
    """No library row = not a favourite = already in the desired state.

    That is success, not failure — the heart is already off, so the UI must not
    flash an error and revert.
    """
    ma.results["music/item_by_uri"] = ITEM_PROVIDER
    assert await music_service.favorite_remove(URI) is True
    assert "music/favorites/remove_item" not in ma.commands()


@pytest.mark.asyncio
async def test_favorite_remove_rejects_empty_uri(ma):
    assert await music_service.favorite_remove("") is False
    assert ma.calls == []


@pytest.mark.asyncio
async def test_favorite_remove_handles_unresolvable_uri(ma):
    """MA down, or a uri it cannot parse -> honest False, never an exception."""
    ma.results["music/item_by_uri"] = None
    assert await music_service.favorite_remove(URI) is False


@pytest.mark.asyncio
async def test_favorite_add_still_sends_the_uri(ma):
    """The other half stays uri-shaped — the two commands are NOT symmetric."""
    assert await music_service.favorite_add(URI) is True
    assert ma.args_for("music/favorites/add_item") == {"item": URI}
