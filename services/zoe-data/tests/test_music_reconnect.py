"""Reconnecting a music provider (the "YouTube Music needs attention" fix).

Two backend pieces make the degraded notice actionable:

- provider_catalogue() flags a configured-but-unhealthy provider as
  `needs_attention` (with MA's reason), so the UI can offer "Reconnect".
- setup_save() reconnects the EXISTING instance in place (passes instance_id).
  Without it, every reconnect minted a fresh instance — leaving a duplicate
  YouTube Music provider behind on a cookie/Premium refresh.

MA is mocked; no network.
"""
import pytest

import music_service
import music_setup
from routers import music_setup as music_setup_router

pytestmark = pytest.mark.ci_safe


def _ma_stub(monkeypatch, *, configs, providers):
    async def fake_ma(command, **args):
        if command == "config/providers":
            return configs
        if command == "providers":
            return providers
        return None
    monkeypatch.setattr(music_service, "_ma", fake_ma)


@pytest.mark.asyncio
async def test_catalogue_flags_configured_but_unhealthy_as_needs_attention(monkeypatch):
    _ma_stub(monkeypatch,
        configs=[{"domain": "ytmusic", "enabled": True, "last_error": "User does not have Youtube Music Premium", "instance_id": "ytmusic--x"},
                 {"domain": "radiobrowser", "enabled": True, "last_error": None}],
        providers=[{"domain": "radiobrowser", "available": True}, {"domain": "builtin", "available": True}])
    cat = {p["domain"]: p for p in await music_service.provider_catalogue()}
    yt = cat["ytmusic"]
    assert yt["connected"] is True and yt["needs_attention"] is True
    assert yt["reason"] == "User does not have Youtube Music Premium"
    # radiobrowser is healthy → connected, not flagged; spotify absent → neither.
    assert cat["radiobrowser"]["needs_attention"] is False
    assert cat["spotify"]["connected"] is False and cat["spotify"]["needs_attention"] is False


@pytest.mark.asyncio
async def test_catalogue_not_flagged_when_provider_is_loaded(monkeypatch):
    # A healthy, loaded streaming provider must never say "needs attention".
    _ma_stub(monkeypatch,
        configs=[{"domain": "ytmusic", "enabled": True, "last_error": None, "instance_id": "ytmusic--x"}],
        providers=[{"domain": "ytmusic", "available": True}])
    cat = {p["domain"]: p for p in await music_service.provider_catalogue()}
    assert cat["ytmusic"]["connected"] is True and cat["ytmusic"]["needs_attention"] is False


@pytest.mark.asyncio
async def test_setup_save_reconnects_in_place_with_instance_id(monkeypatch):
    """Re-auth must UPDATE the existing instance, not create a duplicate."""
    monkeypatch.setattr(music_setup, "consume", lambda t: {"p": "ytmusic"})
    async def fake_potoken(_url):
        return True
    monkeypatch.setattr(music_service, "_potoken_reachable", fake_potoken)
    async def fake_instance(provider):
        return "ytmusic--EXISTING"      # a provider is already configured
    captured = {}
    async def fake_save(provider, values, instance_id=None):
        captured["instance_id"] = instance_id
        return {"name": "YouTube Music"}
    monkeypatch.setattr(music_service, "provider_instance_id", fake_instance)
    monkeypatch.setattr(music_service, "save_provider", fake_save)

    res = await music_setup_router.setup_save(
        {"token": "t", "provider": "ytmusic", "values": {"username": "a", "cookie": "b"}})

    assert res["ok"] is True and res.get("reconnected") is True
    assert captured["instance_id"] == "ytmusic--EXISTING", "reconnect did not reuse the existing instance"


@pytest.mark.asyncio
async def test_setup_save_first_connect_has_no_instance_id(monkeypatch):
    monkeypatch.setattr(music_setup, "consume", lambda t: {"p": "spotify"})
    async def fake_instance(provider):
        return None                     # nothing configured yet
    captured = {}
    async def fake_save(provider, values, instance_id=None):
        captured["instance_id"] = instance_id
        return {"name": "Spotify"}
    monkeypatch.setattr(music_service, "provider_instance_id", fake_instance)
    monkeypatch.setattr(music_service, "save_provider", fake_save)

    res = await music_setup_router.setup_save(
        {"token": "t", "provider": "spotify", "values": {}})

    assert res["ok"] is True and res.get("reconnected") is False
    assert captured["instance_id"] is None, "a first-time connect must not reuse an instance id"
