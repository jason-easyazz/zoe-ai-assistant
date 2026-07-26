"""Catalogue health + degraded search — the jukebox "won't find anything" fix.

When YouTube Music (the only song/album source) drops out of MA ("User does not
have Youtube Music Premium"), search returns only radio. `catalog_health()` +
the `degraded` flag on `search()` let the jukebox say so instead of a misleading
"no results". MA is mocked; no network.
"""
import pytest

import music_service

pytestmark = pytest.mark.ci_safe

# ── catalogue health / degraded search (the jukebox "won't find anything") ────
# YouTube Music (the only song/album source) can drop out of MA ("User does not
# have Youtube Music Premium"), leaving search to return only radio. The server
# flags `degraded` so the jukebox says so instead of a misleading "no results".

def _ma_stub(monkeypatch, *, configs, providers, search_by_type=None):
    async def fake_ma(command, **args):
        if command == "config/providers":
            return configs
        if command == "providers":
            return providers
        if command == "music/search":
            mt = (args.get("media_types") or [None])[0]
            return (search_by_type or {}).get(mt, {})
        return None
    monkeypatch.setattr(music_service, "_ma", fake_ma)


@pytest.mark.asyncio
async def test_catalog_health_degraded_when_ytmusic_enabled_but_absent(monkeypatch):
    _ma_stub(monkeypatch,
        configs=[{"domain": "ytmusic", "enabled": True, "last_error": "User does not have Youtube Music Premium"},
                 {"domain": "radiobrowser", "enabled": True, "last_error": None}],
        providers=[{"domain": "radiobrowser", "available": True},
                   {"domain": "builtin", "available": True}])
    h = await music_service.catalog_health()
    assert h["degraded"] is True
    assert h["reason"] == "User does not have Youtube Music Premium"
    assert h["providers"] == ["ytmusic"]


@pytest.mark.asyncio
async def test_catalog_health_ok_when_streaming_provider_loaded(monkeypatch):
    _ma_stub(monkeypatch,
        configs=[{"domain": "ytmusic", "enabled": True, "last_error": None}],
        providers=[{"domain": "ytmusic", "available": True}])
    assert (await music_service.catalog_health())["degraded"] is False


@pytest.mark.asyncio
async def test_catalog_health_ignores_a_down_PLAYER_provider(monkeypatch):
    # A player provider (sonos) being offline is not a SEARCH problem — only
    # streaming catalogues count, or every speaker outage would cry "degraded".
    _ma_stub(monkeypatch,
        configs=[{"domain": "sonos", "enabled": True, "last_error": "unreachable"},
                 {"domain": "ytmusic", "enabled": True, "last_error": None}],
        providers=[{"domain": "ytmusic", "available": True}])
    assert (await music_service.catalog_health())["degraded"] is False


@pytest.mark.asyncio
async def test_search_flags_degraded_only_when_catalogue_empty(monkeypatch):
    # ytmusic down: track/album/artist empty, only radio matches -> degraded.
    _ma_stub(monkeypatch,
        configs=[{"domain": "ytmusic", "enabled": True, "last_error": "User does not have Youtube Music Premium"}],
        providers=[{"domain": "radiobrowser", "available": True}],
        search_by_type={"radio": {"radio": [{"name": "Beatles Radio", "uri": "radiobrowser://x", "media_type": "radio"}]}})
    r = await music_service.search("beatles", limit=4)
    assert r.get("degraded") is True
    assert r.get("degraded_reason") == "User does not have Youtube Music Premium"
    assert len(r["results"]["radio"]) == 1 and not r["results"]["tracks"]


@pytest.mark.asyncio
async def test_search_not_degraded_when_tracks_return(monkeypatch):
    # A healthy search must never carry the degraded flag (and must not even call
    # catalog_health — but at minimum, no flag).
    _ma_stub(monkeypatch,
        configs=[{"domain": "ytmusic", "enabled": True, "last_error": "stale error"}],  # would be "degraded" if checked
        providers=[{"domain": "ytmusic", "available": True}],
        search_by_type={"track": {"tracks": [{"name": "Come Together", "uri": "ytmusic://t/1", "media_type": "track"}]}})
    r = await music_service.search("beatles", media_types=["track"], limit=4)
    assert "degraded" not in r
    assert len(r["results"]["tracks"]) == 1



# ── Greptile #1545: don't over-report degradation ────────────────────────────

@pytest.mark.asyncio
async def test_catalog_health_ok_when_ANOTHER_streaming_provider_is_up(monkeypatch):
    # ytmusic down but Spotify healthy → catalogue search still works → NOT degraded.
    _ma_stub(monkeypatch,
        configs=[{"domain": "ytmusic", "enabled": True, "last_error": "User does not have Youtube Music Premium"},
                 {"domain": "spotify", "enabled": True, "last_error": None}],
        providers=[{"domain": "spotify", "available": True}, {"domain": "radiobrowser", "available": True}])
    assert (await music_service.catalog_health())["degraded"] is False


@pytest.mark.asyncio
async def test_search_radio_only_scope_is_not_degraded(monkeypatch):
    # A radio-only search leaves the catalogue buckets empty BY SCOPE, not outage.
    _ma_stub(monkeypatch,
        configs=[{"domain": "ytmusic", "enabled": True, "last_error": "User does not have Youtube Music Premium"}],
        providers=[{"domain": "radiobrowser", "available": True}],
        search_by_type={"radio": {"radio": [{"name": "Beatles Radio", "uri": "radiobrowser://x", "media_type": "radio"}]}})
    r = await music_service.search("beatles", media_types=["radio"], limit=4)
    assert "degraded" not in r, "a radio-only search must not carry catalogue-degradation metadata"
