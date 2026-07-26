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


# ── OAuth reconnect must ALSO refresh in place (Greptile #1559) ───────────────
# The generic "Reconnect" works for OAuth providers (Spotify/Tidal/Deezer) too;
# their save runs through music_oauth._run_flow, which must pass the existing
# instance_id or MA duplicates the provider and leaves the broken one.

@pytest.mark.asyncio
async def test_oauth_run_flow_reconnects_in_place(monkeypatch):
    import asyncio, json, secrets, sys, types, music_oauth

    monkeypatch.delenv("MUSIC_ASSISTANT_TOKEN", raising=False)  # skip the WS auth ack
    monkeypatch.setattr(secrets, "token_hex", lambda n: "FIXED")  # deterministic msg_id
    msg_id = "zoe-auth-FIXED"

    class FakeWS:
        def __init__(self):
            self._q = ["{}", json.dumps({"message_id": msg_id, "result": [
                {"key": "session_id", "value": "s"}]})]
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def send(self, _s): pass
        async def recv(self):
            return self._q.pop(0) if self._q else "{}"
    # _run_flow does a LOCAL `import websockets`, so patch sys.modules, not a
    # module attribute — the local import re-binds from sys.modules and would
    # otherwise pull the real package.
    fake_ws_mod = types.ModuleType("websockets")
    fake_ws_mod.connect = lambda *a, **k: FakeWS()
    monkeypatch.setitem(sys.modules, "websockets", fake_ws_mod)
    monkeypatch.setattr(music_oauth, "_values_from_entries", lambda r: {"token": "abc"})

    async def fake_instance(provider): return "spotify--EXISTING"
    captured = {}
    async def fake_save(provider, values, instance_id=None):
        captured["instance_id"] = instance_id
        return {"name": "Spotify"}
    monkeypatch.setattr(music_service, "provider_instance_id", fake_instance)
    monkeypatch.setattr(music_service, "save_provider", fake_save)

    oid = "o1"
    music_oauth._flows[oid] = {"state": "pending", "auth_url": None, "provider": "spotify",
                               "error": None, "created": 0, "event": asyncio.Event()}
    await music_oauth._run_flow(oid, "spotify")

    assert music_oauth._flows[oid]["state"] == "connected"
    assert captured["instance_id"] == "spotify--EXISTING", "OAuth reconnect did not reuse the instance"
    music_oauth._flows.pop(oid, None)


@pytest.mark.asyncio
async def test_reconnect_preserves_existing_provider_settings(monkeypatch):
    """A reconnect must merge the new auth over the instance's CURRENT values,
    not fresh defaults — or it silently resets unrelated settings (Greptile)."""
    saved_args = {}
    async def fake_ma(command, **args):
        if command == "config/providers/get_entries":
            return [
                {"key": "username", "type": "string", "default_value": ""},
                {"key": "cookie", "type": "secure_string", "default_value": ""},
                {"key": "library_sync", "type": "boolean", "default_value": False},
            ]
        if command == "config/providers":
            return [{"domain": "spotify", "instance_id": "sp-EX",
                     "values": {"username": "old@me", "library_sync": True}}]
        if command == "config/providers/save":
            saved_args.update(args)
            return {"name": "Spotify"}
        return None
    monkeypatch.setattr(music_service, "_ma", fake_ma)

    await music_service.save_provider("spotify", {"cookie": "fresh"}, instance_id="sp-EX")

    v = saved_args.get("values") or {}
    assert v.get("library_sync") is True, "reconnect reset a non-default setting to its default"
    assert v.get("username") == "old@me", "reconnect dropped the existing username"
    assert v.get("cookie") == "fresh", "the new auth value was not applied"
    assert saved_args.get("instance_id") == "sp-EX"


@pytest.mark.asyncio
async def test_first_connect_uses_defaults_not_a_prior_instance(monkeypatch):
    """A FIRST connect (no instance_id) starts from defaults — it must NOT pull
    another instance's values."""
    saved_args = {}
    async def fake_ma(command, **args):
        if command == "config/providers/get_entries":
            return [{"key": "library_sync", "type": "boolean", "default_value": False}]
        if command == "config/providers":
            return [{"domain": "spotify", "instance_id": "other", "values": {"library_sync": True}}]
        if command == "config/providers/save":
            saved_args.update(args); return {"name": "Spotify"}
        return None
    monkeypatch.setattr(music_service, "_ma", fake_ma)

    await music_service.save_provider("spotify", {}, instance_id=None)

    assert (saved_args.get("values") or {}).get("library_sync") is False
    assert "instance_id" not in saved_args
