"""Music Assistant bridge — classifier + music_service (mocked MA) + card shape."""
import pytest

import music_service
from skybridge_service import classify_skybridge_intent, skybridge_intent_requires_identity


# ── classifier (no MA needed) ────────────────────────────────────────────────

@pytest.mark.parametrize("q,domain,action", [
    ("play some jazz", "music", "play"),
    ("put on the beatles", "music", "play"),
    ("play the news", "music", "play"),
    ("play some music", "music", "status"),   # generic → show, not a bogus search
    ("what's playing", "music", "status"),
    ("show music", "music", "status"),
    ("pause the music", "music", "pause"),
    ("next song", "music", "next"),
    ("skip this song", "music", "next"),
    ("turn the music up", "music", "volume_up"),
    ("turn the music down", "music", "volume_down"),
])
def test_music_intents(q, domain, action):
    i = classify_skybridge_intent(q, None)
    assert i is not None and (i.domain, i.action) == (domain, action), q


@pytest.mark.parametrize("q", [
    "play a game", "let's play pretend", "add milk to the shopping list",
    "set a timer for 5 minutes", "what's on my calendar", "turn on the lights",
])
def test_music_does_not_over_capture(q):
    i = classify_skybridge_intent(q, None)
    assert i is None or i.domain != "music", q


def test_music_is_public_no_identity_gate():
    i = classify_skybridge_intent("play some jazz", None)
    assert skybridge_intent_requires_identity(i) is False


# ── music_service resolver (mocked MA) ───────────────────────────────────────

class _Intent:
    def __init__(self, action, query=""):
        self.domain = "music"; self.action = action; self.query = query


@pytest.mark.asyncio
async def test_status_when_playing(monkeypatch):
    async def fake_np(pid=""):
        return {"player_id": "p1", "player_name": "Kitchen", "state": "playing",
                "title": "So What", "artist": "Miles Davis", "album": "Kind of Blue", "image": ""}
    monkeypatch.setattr(music_service, "now_playing", fake_np)
    r = await music_service.resolve_music(_Intent("status"))
    assert r["handled"] and "So What" in r["spoken_summary"]
    card = r["cards"][0]
    assert card["card_type"] == "now_playing" and card["content"]["state"] == "playing"
    assert card["content"]["transport"] is True


@pytest.mark.asyncio
async def test_status_when_ma_down_is_friendly(monkeypatch):
    async def none_np(pid=""): return None
    monkeypatch.setattr(music_service, "now_playing", none_np)
    r = await music_service.resolve_music(_Intent("status"))
    assert r["handled"] and "set up" in r["spoken_summary"].lower()
    assert r["cards"][0]["card_id"] == "music-browse"  # honest browse card, not fake


@pytest.mark.asyncio
async def test_pause_calls_control(monkeypatch):
    calls = {}
    async def fake_np(pid=""): return {"state": "playing", "title": "X", "player_id": "p"}
    async def fake_control(action, player_id="", value=None): calls["a"] = action; return True
    monkeypatch.setattr(music_service, "now_playing", fake_np)
    monkeypatch.setattr(music_service, "control", fake_control)
    r = await music_service.resolve_music(_Intent("pause"))
    assert calls["a"] == "pause" and r["intent"]["action"] == "pause"


@pytest.mark.asyncio
async def test_play_searches_and_plays(monkeypatch):
    async def fake_sp(query, player_id=""): return {"name": "Blue in Green", "artist": "Miles Davis"}
    async def fake_np(pid=""): return {"state": "playing", "title": "Blue in Green", "artist": "Miles Davis"}
    monkeypatch.setattr(music_service, "search_and_play", fake_sp)
    monkeypatch.setattr(music_service, "now_playing", fake_np)
    r = await music_service.resolve_music(_Intent("play", "some miles davis"))
    assert "Blue in Green" in r["spoken_summary"]


@pytest.mark.asyncio
async def test_control_never_raises_when_ma_down(monkeypatch):
    async def no_players(): return []
    monkeypatch.setattr(music_service, "get_players", no_players)
    assert await music_service.control("pause") is False
    assert await music_service.now_playing() is None


# ── Add-source (QR→phone setup) — token + resolver + guards ──────────────────

import music_setup


def test_setup_token_roundtrip_single_use_and_tamper():
    t = music_setup.mint("ytmusic", "jason")["token"]
    p = music_setup.verify(t)
    assert p and p["p"] == "ytmusic" and p["u"] == "jason"
    assert music_setup.verify(t[:-4] + "aaaa") is None       # tampered sig rejected
    assert music_setup.consume(t) is not None                # spend once
    assert music_setup.consume(t) is None                    # not twice
    assert music_setup.verify(t) is None                     # consumed → invalid


def test_setup_token_expiry(monkeypatch):
    import time
    t = music_setup.mint("spotify")["token"]
    monkeypatch.setattr(time, "time", lambda: time.gmtime and 9999999999)  # far future
    assert music_setup.verify(t) is None


@pytest.mark.asyncio
async def test_setup_classify_and_resolver(monkeypatch):
    from skybridge_service import classify_skybridge_intent
    assert classify_skybridge_intent("connect spotify", None).action == "setup"
    assert classify_skybridge_intent("add youtube music", None).query == "ytmusic"
    # resolver: unknown/blank → catalogue; a provider → QR card
    async def fake_cat(): return [{"domain": "spotify", "name": "Spotify", "auth": "oauth", "connected": False}]
    monkeypatch.setattr(music_service, "provider_catalogue", fake_cat)
    r = await music_service.resolve_music_setup("")
    assert r["cards"][0]["content"]["mode"] == "catalogue"
    r2 = await music_service.resolve_music_setup("spotify")
    c = r2["cards"][0]["content"]
    assert c["mode"] == "qr" and c["provider"] == "spotify" and "/api/music/setup/qr" in c["qr_path"]


@pytest.mark.asyncio
async def test_setup_save_gated_by_token(monkeypatch):
    from routers import music_setup as ms_router
    saved = {"n": 0}
    async def fake_save(prov, vals): saved["n"] += 1; return {"name": prov}
    monkeypatch.setattr(music_service, "save_provider", fake_save)
    # invalid token → refused, MA never touched
    r = await ms_router.setup_save({"token": "bad", "provider": "ytmusic", "values": {"username": "x"}})
    assert r["ok"] is False and saved["n"] == 0
    # valid token → saved once, then token spent
    tok = music_setup.mint("ytmusic")["token"]
    r2 = await ms_router.setup_save({"token": tok, "provider": "ytmusic", "values": {"username": "x", "cookie": "y"}})
    assert r2["ok"] is True and saved["n"] == 1
    r3 = await ms_router.setup_save({"token": tok, "provider": "ytmusic", "values": {"username": "x"}})
    assert r3["ok"] is False and saved["n"] == 1  # single-use


# ── YouTube Music: hide PO-token field + inject local generator ──────────────

# MA's ytmusic provider config entries (shape from config/providers/get_entries).
_YTM_ENTRIES = [
    {"key": "username", "type": "string", "label": "Username", "required": True},
    {"key": "cookie", "type": "secure_string", "label": "Cookie",
     "description": "The Login cookie you grabbed from an existing session.", "required": True},
    {"key": "po_token_server_url", "type": "string", "label": "PO Token Server URL",
     "required": True, "default_value": "http://127.0.0.1:4416"},
    {"key": "library_sync_playlists", "type": "boolean", "default_value": True},
    {"key": "log_level", "type": "string", "default_value": "GLOBAL"},
]


@pytest.mark.asyncio
async def test_ytmusic_form_hides_potoken_keeps_credentials(monkeypatch):
    async def fake_ma(command, **args):
        assert command == "config/providers/get_entries" and args["provider_domain"] == "ytmusic"
        return list(_YTM_ENTRIES)
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    form = await music_service.provider_setup_form("ytmusic")
    assert form is not None and form["domain"] == "ytmusic"
    keys = {f["key"] for f in form["fields"]}
    # PO-token field hidden; library-sync/log_level already filtered generically.
    assert "po_token_server_url" not in keys
    assert keys == {"username", "cookie"}
    cookie = next(f for f in form["fields"] if f["key"] == "cookie")
    assert cookie["type"] == "secure_string" and cookie["required"] is True
    assert next(f for f in form["fields"] if f["key"] == "username")["required"] is True


@pytest.mark.asyncio
async def test_ytmusic_save_injects_local_potoken(monkeypatch):
    monkeypatch.delenv("ZOE_YTMUSIC_POTOKEN_URL", raising=False)
    captured = {}
    async def fake_ma(command, **args):
        if command == "config/providers/get_entries":
            return list(_YTM_ENTRIES)
        if command == "config/providers/save":
            captured.update(args)
            return {"name": "YouTube Music", "values": args.get("values")}
        return None
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    # User supplies only username + cookie (as the phone form allows).
    saved = await music_service.save_provider("ytmusic", {"username": "jason", "cookie": "SECRET"})
    assert saved is not None
    vals = captured["values"]
    assert vals["username"] == "jason" and vals["cookie"] == "SECRET"
    # PO-token URL filled server-side from the default, not from the phone.
    assert vals["po_token_server_url"] == "http://localhost:4416"


@pytest.mark.asyncio
async def test_ytmusic_save_potoken_url_env_override(monkeypatch):
    monkeypatch.setenv("ZOE_YTMUSIC_POTOKEN_URL", "http://10.0.0.5:4416/")
    captured = {}
    async def fake_ma(command, **args):
        if command == "config/providers/get_entries":
            return list(_YTM_ENTRIES)
        if command == "config/providers/save":
            captured.update(args); return {"name": "YouTube Music"}
        return None
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    await music_service.save_provider("ytmusic", {"username": "j", "cookie": "c"})
    # trailing slash trimmed; other providers untouched by this logic.
    assert captured["values"]["po_token_server_url"] == "http://10.0.0.5:4416"


@pytest.mark.asyncio
async def test_non_ytmusic_save_has_no_potoken_key(monkeypatch):
    captured = {}
    async def fake_ma(command, **args):
        if command == "config/providers/get_entries":
            return [{"key": "username", "type": "string", "required": True}]
        if command == "config/providers/save":
            captured.update(args); return {"name": "Qobuz"}
        return None
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    await music_service.save_provider("qobuz", {"username": "j"})
    assert "po_token_server_url" not in captured["values"]


# ── Spotify OAuth (slice 3): WS driver + token-gated endpoints ────────────────

import music_oauth


def test_oauth_values_from_entries_extracts_token():
    entries = [
        {"key": "auth", "type": "action", "value": None},
        {"key": "refresh_token_global", "type": "secure_string", "value": "RT123"},
        {"key": "library_sync_tracks", "type": "boolean", "value": True},
        {"key": "client_id", "type": "secure_string", "value": None},  # None skipped
    ]
    v = music_oauth._values_from_entries(entries)
    assert v == {"refresh_token_global": "RT123", "library_sync_tracks": True}


def test_oauth_status_unknown_for_bad_id():
    assert music_oauth.oauth_status("nope")["state"] == "unknown"


@pytest.mark.asyncio
async def test_oauth_start_endpoint_gated_by_token(monkeypatch):
    from routers import music_setup as ms
    started = {"n": 0}
    async def fake_start(provider):
        started["n"] += 1
        return {"oauth_id": "oid1", "auth_url": "https://accounts.spotify.com/authorize?x", "state": "pending"}
    monkeypatch.setattr(music_oauth, "start_oauth", fake_start)
    # bad token → refused, WS never opened
    r = await ms.oauth_start({"token": "bad", "provider": "spotify"})
    assert r["ok"] is False and started["n"] == 0
    # valid token → started, URL returned
    tok = music_setup.mint("spotify")["token"]
    r2 = await ms.oauth_start({"token": tok, "provider": "spotify"})
    assert r2["ok"] is True and r2["auth_url"].startswith("https://accounts.spotify.com") and started["n"] == 1


@pytest.mark.asyncio
async def test_oauth_status_endpoint_consumes_on_connected(monkeypatch):
    from routers import music_setup as ms
    monkeypatch.setattr(music_oauth, "oauth_status", lambda oid: {"state": "connected", "provider": "spotify"})
    tok = music_setup.mint("spotify")["token"]
    r = await ms.oauth_status(oauth_id="oid1", token=tok)
    assert r["ok"] is True and r["state"] == "connected"
    assert music_setup.verify(tok) is None  # consumed on success


@pytest.mark.asyncio
async def test_oauth_start_returns_failed_when_no_url(monkeypatch):
    from routers import music_setup as ms
    async def no_url(provider):
        return {"oauth_id": "x", "auth_url": None, "state": "failed", "error": "sign-in timed out"}
    monkeypatch.setattr(music_oauth, "start_oauth", no_url)
    tok = music_setup.mint("spotify")["token"]
    r = await ms.oauth_start({"token": tok, "provider": "spotify"})
    assert r["ok"] is False and "timed out" in (r.get("reason") or "")
