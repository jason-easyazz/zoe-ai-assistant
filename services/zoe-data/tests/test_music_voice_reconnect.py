"""Saying "connect youtube music" for a provider that needs signing in again.

resolve_music_setup used to answer "already connected" for ANY configured
provider, so an expired YouTube Music Premium sign-in could not be repaired by
voice. Now a connected provider with `needs_attention` gets an honest "open Music, then Sources, tap Reconnect" reply (the QR card is not rendered on voice) and
the phone's save reconnects the EXISTING instance in place (never a duplicate).
A healthy connected provider still says "already connected".
"""
import pytest

import music_service
import music_setup
from routers import music_setup as music_setup_router

pytestmark = pytest.mark.ci_safe

_BROKEN = [{"domain": "ytmusic", "enabled": True, "last_error": "User does not have Youtube Music Premium",
            "instance_id": "ytmusic--EXISTING"}]
_HEALTHY = [{"domain": "ytmusic", "enabled": True, "last_error": None, "instance_id": "ytmusic--EXISTING"}]


@pytest.fixture(autouse=True)
def _pin_ma_287(monkeypatch):
    async def info():
        return {"server_version": "2.8.7", "schema_version": 29}
    monkeypatch.setattr(music_service, "_ma_info", info)
    music_service._invalidate_ma_version()
    yield
    music_service._invalidate_ma_version()


def _ma(monkeypatch, configs, providers):
    async def fake_ma(command, **args):
        return {"config/providers": configs, "providers": providers}.get(command)
    monkeypatch.setattr(music_service, "_ma", fake_ma)


@pytest.mark.asyncio
async def test_needs_attention_offers_qr_and_reconnects_in_place(monkeypatch):
    _ma(monkeypatch, _BROKEN, [])
    minted = {}
    real_mint = music_setup.mint

    def spy_mint(provider, user_id=""):
        minted.update(real_mint(provider, user_id))
        return minted
    monkeypatch.setattr(music_setup, "mint", spy_mint)

    res = await music_service.resolve_music_setup("ytmusic")
    text = str(res)
    assert "YouTube Music needs signing in again - open Music, then Sources on the panel, and tap Reconnect." in text
    assert "scan the code" not in text  # the reply must not promise a code that voice never renders
    assert "already connected" not in text
    assert minted["token"], "a one-time QR token was not minted"
    assert "music-qr-ytmusic" in text  # the QR card, not the catalogue

    # The phone finishes it: the save must target the existing instance.
    async def potoken(_u):
        return True
    captured = {}

    async def fake_save(provider, values, instance_id=None):
        captured["instance_id"] = instance_id
        return {"name": "YouTube Music"}
    monkeypatch.setattr(music_service, "_potoken_reachable", potoken)
    monkeypatch.setattr(music_service, "save_provider", fake_save)
    out = await music_setup_router.setup_save(
        {"token": minted["token"], "provider": "ytmusic", "values": {"username": "a", "cookie": "b"}})
    assert out["ok"] is True and out["reconnected"] is True
    assert captured["instance_id"] == "ytmusic--EXISTING"


@pytest.mark.asyncio
async def test_healthy_connected_still_says_already_connected(monkeypatch):
    _ma(monkeypatch, _HEALTHY, [{"domain": "ytmusic", "available": True}])
    monkeypatch.setattr(music_setup, "mint", lambda *a, **k: pytest.fail("healthy provider must not mint a QR"))
    res = await music_service.resolve_music_setup("ytmusic")
    assert "YouTube Music is already connected." in str(res)
    assert "needs signing in again" not in str(res)


@pytest.mark.asyncio
async def test_not_connected_flow_unchanged(monkeypatch):
    _ma(monkeypatch, [], [])
    res = await music_service.resolve_music_setup("ytmusic")
    text = str(res)
    assert "To connect YouTube Music, open Music, then Sources on the panel, and tap Connect." in text
    assert "needs signing in again" not in text


@pytest.mark.asyncio
async def test_free_provider_needing_attention_refreshes_in_place(monkeypatch):
    free = next(p for p in music_service._SETUP_CATALOGUE if p["auth"] == "free")
    _ma(monkeypatch, [{"domain": free["domain"], "enabled": True, "last_error": "boom",
                       "instance_id": "free--EXISTING"}], [])
    captured = {}

    async def fake_save(provider, values, instance_id=None):
        captured["instance_id"] = instance_id
        return {"name": "x"}
    monkeypatch.setattr(music_service, "save_provider", fake_save)
    await music_service.resolve_music_setup(free["domain"])
    assert captured["instance_id"] == "free--EXISTING"
