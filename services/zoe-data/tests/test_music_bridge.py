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
    ("move music to the kitchen", "music", "transfer"),
    ("switch music to the living room", "music", "transfer"),
    ("send the music to bedroom", "music", "transfer"),
])
def test_music_intents(q, domain, action):
    i = classify_skybridge_intent(q, None)
    assert i is not None and (i.domain, i.action) == (domain, action), q


@pytest.mark.parametrize("q,room", [
    ("move music to the kitchen", "kitchen"),
    ("switch music to the living room", "living room"),
    ("cast it to office", "office"),
])
def test_music_transfer_extracts_room(q, room):
    i = classify_skybridge_intent(q, None)
    assert i is not None and i.action == "transfer" and i.query == room, q


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
                "title": "So What", "artist": "Miles Davis", "album": "Kind of Blue",
                "image": "", "queue_id": "p1", "elapsed": 42.0, "duration": 545.0}
    monkeypatch.setattr(music_service, "now_playing", fake_np)
    r = await music_service.resolve_music(_Intent("status"))
    assert r["handled"] and "So What" in r["spoken_summary"]
    card = r["cards"][0]
    assert card["card_type"] == "now_playing" and card["content"]["state"] == "playing"
    # Canvas card carries NO transport flag (controls live on the floating bar);
    # it does carry the queue_id (for "Up next" hydration) + display progress.
    assert "transport" not in card["content"]
    assert card["content"]["queue_id"] == "p1"
    assert card["content"]["elapsed"] == 42.0 and card["content"]["duration"] == 545.0


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
    async def fake_sp(query, player_id="", radio_mode=False): return {"name": "Blue in Green", "artist": "Miles Davis"}
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


# ── Browse: structured search + play-by-URI (touch music page) ───────────────

# One MA search hit per type, shaped like the real music/search payload.
def _ma_search_payload(mt):
    if mt == "track":
        return {"tracks": [{
            "name": "Yellow", "uri": "ytmusic://track/1", "media_type": "track",
            "artists": [{"name": "Coldplay"}],
            "album": {"name": "Parachutes",
                      "metadata": {"images": [{"path": "https://lh3.googleusercontent.com/x=w60-h60-l90-rj"}]}},
        }]}
    if mt == "album":
        return {"albums": [{"name": "Parachutes", "uri": "ytmusic://album/1", "media_type": "album",
                            "artists": [{"name": "Coldplay"}],
                            "metadata": {"images": [{"path": "https://img/a.png"}]}}]}
    if mt == "artist":
        return {"artists": [{"name": "Coldplay", "uri": "ytmusic://artist/1", "media_type": "artist",
                             "metadata": {"images": [{"path": "https://img/ar.png"}]}}]}
    if mt == "playlist":
        return {"playlists": [{"name": "Chill", "uri": "ytmusic://playlist/1", "media_type": "playlist"}]}
    if mt == "radio":
        return {"radio": [{"name": "Coldplay Radio", "uri": "ytmusic://radio/1", "media_type": "radio"},
                          {"name": "No URI station"}]}  # 2nd hit has no uri → dropped
    return {}


@pytest.mark.asyncio
async def test_search_groups_and_normalizes(monkeypatch):
    async def fake_ma(command, **args):
        assert command == "music/search"
        # search() fans out one call per media type (better MA coverage).
        return _ma_search_payload(args["media_types"][0])
    monkeypatch.setattr(music_service, "_ma", fake_ma)

    r = await music_service.search("coldplay", limit=5)
    assert r["available"] is True and r["query"] == "coldplay"
    res = r["results"]
    assert [t["name"] for t in res["tracks"]] == ["Yellow"]
    track = res["tracks"][0]
    assert track["uri"] == "ytmusic://track/1"
    assert track["artist"] == "Coldplay" and track["album"] == "Parachutes"
    # Track art falls back to its album image, and Google art is bumped to a square.
    assert track["image"] == "https://lh3.googleusercontent.com/x=w544-h544"
    assert res["artists"][0]["name"] == "Coldplay"
    # A hit with no playable URI is dropped (can't act on it).
    assert [s["name"] for s in res["radio"]] == ["Coldplay Radio"]


@pytest.mark.asyncio
async def test_search_empty_query_and_narrowed_types(monkeypatch):
    async def boom(command, **args):
        raise AssertionError("MA must not be hit for an empty query")
    monkeypatch.setattr(music_service, "_ma", boom)
    r = await music_service.search("   ")
    assert r["available"] is False and all(v == [] for v in r["results"].values())

    seen = []
    async def fake_ma(command, **args):
        seen.append(args["media_types"][0]); return _ma_search_payload(args["media_types"][0])
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    r2 = await music_service.search("coldplay", media_types=["track"], limit=3)
    assert seen == ["track"]                         # only the requested bucket queried
    assert r2["results"]["tracks"] and r2["results"]["albums"] == []


@pytest.mark.asyncio
async def test_search_unavailable_when_ma_down(monkeypatch):
    async def dead(command, **args): return None
    monkeypatch.setattr(music_service, "_ma", dead)
    r = await music_service.search("anything")
    assert r["available"] is False
    assert all(v == [] for v in r["results"].values())


@pytest.mark.asyncio
async def test_play_media_targets_named_player(monkeypatch):
    async def players():
        return [{"player_id": "kitchen", "display_name": "Kitchen", "available": True, "powered": True},
                {"player_id": "bedroom", "display_name": "Bedroom", "available": True, "powered": True}]
    calls = []
    async def fake_ok(command, timeout_s=None, **args):
        calls.append((command, args)); return True
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma_ok", fake_ok)

    r = await music_service.play_media("ytmusic://track/1", player_id="bedroom")
    assert r["ok"] is True and r["player_id"] == "bedroom" and r["player_name"] == "Bedroom"
    assert calls == [("player_queues/play_media",
                      {"queue_id": "bedroom", "media": "ytmusic://track/1",
                       "option": "replace", "radio_mode": False})]


@pytest.mark.asyncio
async def test_play_media_reports_failure_when_ma_rejects(monkeypatch):
    """play_media success body is null, so a bare _ma return can't tell success
    from failure. When MA is down/times out/rejects (HTTP != 200 → _ma_ok False)
    we must report ok:False, not a false 'Playing …'."""
    async def players():
        return [{"player_id": "bedroom", "display_name": "Bedroom", "available": True, "powered": True}]
    async def down(command, timeout_s=None, **args): return False
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma_ok", down)
    r = await music_service.play_media("ytmusic://track/1", player_id="bedroom")
    assert r["ok"] is False and r["reason"] == "playback failed"


@pytest.mark.asyncio
async def test_play_media_guards(monkeypatch):
    async def players():
        return [{"player_id": "kitchen", "display_name": "Kitchen", "available": True, "powered": True}]
    async def boom(command, timeout_s=None, **args):
        raise AssertionError("MA must not be called on a guarded play_media")
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma_ok", boom)

    assert (await music_service.play_media("", player_id="kitchen"))["ok"] is False       # no uri
    # A stale/unknown player id fails loudly instead of playing on the wrong speaker.
    r = await music_service.play_media("ytmusic://track/1", player_id="garage")
    assert r["ok"] is False and r["reason"] == "unknown player"


@pytest.mark.asyncio
async def test_play_media_falls_back_to_active_player(monkeypatch):
    async def players():
        return [{"player_id": "kitchen", "available": True, "powered": True, "playback_state": "playing"},
                {"player_id": "bedroom", "available": True, "powered": True}]
    calls = []
    async def fake_ok(command, timeout_s=None, **args):
        calls.append(args.get("queue_id")); return True
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma_ok", fake_ok)
    # No explicit player → picks the active (playing) player.
    r = await music_service.play_media("ytmusic://track/1")
    assert r["ok"] is True and calls == ["kitchen"]


@pytest.mark.asyncio
async def test_search_and_play_media_endpoints_delegate(monkeypatch):
    from routers import music as music_router
    async def fake_search(q, media_types=None, limit=8):
        return {"available": True, "query": q, "results": {"tracks": [{"name": q}]},
                "_types": media_types, "_limit": limit}
    monkeypatch.setattr(music_service, "search", fake_search)
    r = await music_router.music_search(q="jazz", types="track,album", limit=5)
    assert r["query"] == "jazz" and r["_types"] == ["track", "album"] and r["_limit"] == 5

    played = {}
    async def fake_play(uri, player_id="", radio_mode=False):
        played["uri"] = uri; played["pid"] = player_id; return {"ok": True, "player_id": player_id}
    monkeypatch.setattr(music_service, "play_media", fake_play)
    ok = await music_router.music_play_media({"uri": "ytmusic://track/1", "player_id": "bedroom"})
    assert ok["ok"] is True and played == {"uri": "ytmusic://track/1", "pid": "bedroom"}
    # missing uri → refused, service never called
    called = {"n": 0}
    async def guard(uri, player_id=""): called["n"] += 1; return {"ok": True}
    monkeypatch.setattr(music_service, "play_media", guard)
    bad = await music_router.music_play_media({"player_id": "bedroom"})
    assert bad["ok"] is False and called["n"] == 0


# ── now_playing progress + hi-res art ────────────────────────────────────────

@pytest.mark.asyncio
async def test_now_playing_reports_progress_and_hires_art(monkeypatch):
    async def players():
        return [{"player_id": "p1", "display_name": "Kitchen", "playback_state": "playing",
                 "available": True, "powered": True, "volume_level": 30}]
    async def fake_ma(command, **args):
        if command == "player_queues/all":
            return [{"queue_id": "p1", "elapsed_time": 61.4, "shuffle_enabled": False,
                     "current_item": {"duration": 180, "media_item": {
                        "name": "So What", "artists": [{"name": "Miles"}],
                        "image": {"path": "https://lh3.googleusercontent.com/abc=w60-h60-l90-rj"}}}}]
        return None
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    np = await music_service.now_playing()
    assert np["title"] == "So What" and np["state"] == "playing"
    assert np["elapsed"] == 61.4 and np["duration"] == 180.0 and np["queue_id"] == "p1"
    # Google-hosted art bumped to a large square for the hero/thumb.
    assert np["image"] == "https://lh3.googleusercontent.com/abc=w544-h544"


def test_hi_res_art_leaves_other_hosts_untouched():
    assert music_service._hi_res_art("/media/cover.png") == "/media/cover.png"
    assert music_service._hi_res_art("https://cdn.example.com/a/cover.png") == "https://cdn.example.com/a/cover.png"
    assert music_service._hi_res_art("") == ""


# ── Seek (floating-bar scrubber) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_seek_dispatches_queue_command(monkeypatch):
    async def players():
        return [{"player_id": "p1", "playback_state": "playing", "available": True, "powered": True}]
    calls = []
    async def fake_ma(command, **args):
        calls.append((command, args)); return {}
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    assert await music_service.seek(42) is True
    assert calls == [("player_queues/seek", {"queue_id": "p1", "position": 42})]


@pytest.mark.asyncio
async def test_seek_guards(monkeypatch):
    async def players():
        return [{"player_id": "p1", "playback_state": "playing", "available": True, "powered": True}]
    async def boom(command, **args):
        raise AssertionError("MA must not be called on a bad seek")
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma", boom)
    assert await music_service.seek("abc") is False      # non-numeric position no-ops
    async def none_players(): return []
    monkeypatch.setattr(music_service, "get_players", none_players)
    assert await music_service.seek(10) is False          # no players → no-op


@pytest.mark.asyncio
async def test_seek_endpoint_delegates(monkeypatch):
    from routers import music as music_router
    seen = {}
    async def fake_seek(pos, player_id=""):
        seen["pos"] = pos; seen["pid"] = player_id; return True
    monkeypatch.setattr(music_service, "seek", fake_seek)
    r = await music_router.music_seek({"position_seconds": 33, "player_id": "p1"})
    assert r == {"ok": True, "position_seconds": 33} and seen == {"pos": 33, "pid": "p1"}
    # invalid position → refused, service never called
    called = {"n": 0}
    async def guard(pos, player_id=""): called["n"] += 1; return True
    monkeypatch.setattr(music_service, "seek", guard)
    r2 = await music_router.music_seek({"position_seconds": "nope"})
    assert r2["ok"] is False and called["n"] == 0


# ── Speaker transfer (music hub) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transfer_moves_active_queue_to_target(monkeypatch):
    async def players():
        return [
            {"player_id": "kitchen", "available": True, "powered": True, "playback_state": "playing"},
            {"player_id": "living", "available": True, "powered": True},
        ]
    calls = []
    async def fake_ma(command, **args):
        calls.append((command, args)); return {}
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma", fake_ma)

    # No explicit source → picks the active (playing) player as the source queue.
    assert await music_service.transfer("living") is True
    assert calls == [("player_queues/transfer",
                      {"source_queue_id": "kitchen", "target_queue_id": "living"})]


@pytest.mark.asyncio
async def test_transfer_guards(monkeypatch):
    async def players():
        return [{"player_id": "kitchen", "available": True, "powered": True, "playback_state": "playing"}]
    async def fake_ma(command, **args):
        raise AssertionError("MA must not be called on a no-op transfer")
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma", fake_ma)

    assert await music_service.transfer("") is False           # no target
    assert await music_service.transfer("kitchen") is False     # source == target (no-op)


@pytest.mark.asyncio
async def test_resolve_transfer_matches_room_by_name(monkeypatch):
    async def players():
        return [
            {"player_id": "p_kit", "display_name": "Kitchen"},
            {"player_id": "p_liv", "display_name": "Living Room"},
        ]
    moved = {}
    async def fake_transfer(target, source=""):
        moved["target"] = target; return True
    async def fake_np(player_id=""):
        return {"state": "playing", "title": "Song", "player_name": "Living Room"}
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "transfer", fake_transfer)
    monkeypatch.setattr(music_service, "now_playing", fake_np)

    res = await music_service.resolve_music(_Intent("transfer", "living room"))
    assert moved["target"] == "p_liv"                 # fuzzy name → the right player
    assert "Living Room" in res["spoken_summary"]


@pytest.mark.asyncio
async def test_resolve_transfer_unknown_speaker(monkeypatch):
    async def players():
        return [{"player_id": "p_kit", "display_name": "Kitchen"}]
    async def boom(*a, **k):
        raise AssertionError("must not transfer to an unknown speaker")
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "transfer", boom)
    res = await music_service.resolve_music(_Intent("transfer", "garage"))
    assert "couldn't find" in res["spoken_summary"].lower()


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
    async def _up(url): return True
    monkeypatch.setattr(music_service, "_potoken_reachable", _up)  # ytmusic helper "up"
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
async def test_ytmusic_setup_save_refuses_with_accurate_msg_when_generator_down(monkeypatch):
    from routers import music_setup as ms_router
    async def down(url): return False
    monkeypatch.setattr(music_service, "_potoken_reachable", down)
    saved = {"n": 0}
    async def fake_save(prov, vals): saved["n"] += 1; return {"name": prov}
    monkeypatch.setattr(music_service, "save_provider", fake_save)
    tok = music_setup.mint("ytmusic")["token"]
    r = await ms_router.setup_save({"token": tok, "provider": "ytmusic",
                                    "values": {"username": "j", "cookie": "c"}})
    # Generator down → refused with an actionable message, MA never saved.
    assert r["ok"] is False and "helper isn't running" in r["reason"] and saved["n"] == 0


@pytest.mark.asyncio
async def test_ytmusic_setup_save_ok_when_generator_up(monkeypatch):
    from routers import music_setup as ms_router
    async def up(url): return True
    monkeypatch.setattr(music_service, "_potoken_reachable", up)
    async def fake_save(prov, vals): return {"name": prov}
    monkeypatch.setattr(music_service, "save_provider", fake_save)
    tok = music_setup.mint("ytmusic")["token"]
    r = await ms_router.setup_save({"token": tok, "provider": "ytmusic",
                                    "values": {"username": "j", "cookie": "c"}})
    assert r["ok"] is True


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


# ── Radio mode + Don't stop the music + discovery (MA-native) ────────────────

@pytest.mark.parametrize("q,query", [
    ("play miles davis radio", "miles davis"),
    ("play some coldplay radio", "coldplay"),
    ("start beatles radio", "beatles"),
])
def test_radio_mode_classifies_play_with_seed(q, query):
    i = classify_skybridge_intent(q, None)
    assert i is not None and (i.domain, i.action) == ("music", "play"), q
    assert i.query == query and i.radio_mode is True, q


def test_bare_play_radio_is_not_radio_mode():
    # No seed → normal search path ("play the radio" finds a station), not radio_mode.
    i = classify_skybridge_intent("play the radio", None)
    assert i is not None and i.domain == "music" and i.radio_mode is False


@pytest.mark.parametrize("q", [
    "keep the music going",
    "don't stop the music",
    "play something like this",
    "play more songs like this",
])
def test_dont_stop_phrasings(q):
    i = classify_skybridge_intent(q, None)
    assert i is not None and (i.domain, i.action) == ("music", "dont_stop"), q


def test_stop_the_music_still_stops():
    i = classify_skybridge_intent("stop the music", None)
    assert i is not None and (i.domain, i.action) == ("music", "stop")


def test_plain_play_keeps_radio_mode_off():
    i = classify_skybridge_intent("play some jazz", None)
    assert i is not None and i.action == "play" and i.radio_mode is False


@pytest.mark.asyncio
async def test_search_and_play_passes_radio_mode(monkeypatch):
    async def players():
        return [{"player_id": "p1", "available": True, "powered": True}]
    calls = []
    async def fake_ma(command, **args):
        if command == "music/search":
            return {"tracks": [{"name": "So What", "uri": "ytmusic://track/1",
                                "artists": [{"name": "Miles Davis"}]}]}
        calls.append((command, args)); return {}
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    hit = await music_service.search_and_play("miles davis", radio_mode=True)
    assert hit is not None
    assert calls == [("player_queues/play_media",
                      {"queue_id": "p1", "media": "ytmusic://track/1",
                       "option": "replace", "radio_mode": True})]


@pytest.mark.asyncio
async def test_resolve_radio_play_speaks_radio(monkeypatch):
    seen = {}
    async def fake_sp(query, player_id="", radio_mode=False):
        seen["radio"] = radio_mode; return {"name": "Miles Davis", "artist": ""}
    async def fake_np(pid=""): return {"state": "playing", "title": "So What"}
    monkeypatch.setattr(music_service, "search_and_play", fake_sp)
    monkeypatch.setattr(music_service, "now_playing", fake_np)

    class _RadioIntent(_Intent):
        radio_mode = True
    r = await music_service.resolve_music(_RadioIntent("play", "miles davis"))
    assert seen["radio"] is True and "radio" in r["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_play_media_passes_radio_mode(monkeypatch):
    async def players():
        return [{"player_id": "p1", "available": True, "powered": True}]
    calls = []
    async def fake_ok(command, timeout_s=None, **args):
        calls.append(args.get("radio_mode")); return True
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma_ok", fake_ok)
    r = await music_service.play_media("ytmusic://track/1", radio_mode=True)
    assert r["ok"] is True and calls == [True]


@pytest.mark.asyncio
async def test_dont_stop_toggle_dispatches(monkeypatch):
    async def players():
        return [{"player_id": "p1", "playback_state": "playing", "available": True, "powered": True}]
    calls = []
    async def fake_ok(command, timeout_s=None, **args):
        calls.append((command, args)); return True
    monkeypatch.setattr(music_service, "get_players", players)
    monkeypatch.setattr(music_service, "_ma_ok", fake_ok)
    assert await music_service.set_dont_stop_the_music(True) is True
    assert calls == [("player_queues/dont_stop_the_music",
                      {"queue_id": "p1", "dont_stop_the_music_enabled": True})]


@pytest.mark.asyncio
async def test_dont_stop_guards_no_player(monkeypatch):
    async def no_players(): return []
    monkeypatch.setattr(music_service, "get_players", no_players)
    assert await music_service.set_dont_stop_the_music(True) is False


@pytest.mark.asyncio
async def test_resolve_dont_stop(monkeypatch):
    async def fake_np(pid=""): return {"state": "playing", "title": "X", "player_id": "p1"}
    monkeypatch.setattr(music_service, "now_playing", fake_np)
    flips = []
    async def fake_toggle(enabled, player_id=""):
        flips.append(enabled); return True
    monkeypatch.setattr(music_service, "set_dont_stop_the_music", fake_toggle)
    r = await music_service.resolve_music(_Intent("dont_stop"))
    assert flips == [True] and "keep the music going" in r["spoken_summary"].lower()
    # MA rejects (no SIMILAR_TRACKS provider) → honest message, not a fake ok.
    async def fake_reject(enabled, player_id=""): return False
    monkeypatch.setattr(music_service, "set_dont_stop_the_music", fake_reject)
    r2 = await music_service.resolve_music(_Intent("dont_stop"))
    assert "couldn't" in r2["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_resolve_dont_stop_nothing_playing(monkeypatch):
    async def none_np(pid=""): return None
    monkeypatch.setattr(music_service, "now_playing", none_np)
    async def boom(enabled, player_id=""):
        raise AssertionError("must not toggle with nothing playing")
    monkeypatch.setattr(music_service, "set_dont_stop_the_music", boom)
    r = await music_service.resolve_music(_Intent("dont_stop"))
    assert "no music playing" in r["spoken_summary"].lower()


@pytest.mark.asyncio
async def test_recommendations_normalized(monkeypatch):
    async def fake_ma(command, **args):
        assert command == "music/recommendations"
        return [
            {"name": "Listen again", "items": [
                {"name": "Yellow", "uri": "ytmusic://track/1", "media_type": "track",
                 "artists": [{"name": "Coldplay"}]},
                {"name": "No URI"},  # unplayable → dropped
            ]},
            {"name": "Empty shelf", "items": []},  # empty folder → dropped
        ]
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    r = await music_service.get_recommendations()
    assert r["available"] is True and len(r["folders"]) == 1
    folder = r["folders"][0]
    assert folder["name"] == "Listen again"
    assert [i["name"] for i in folder["items"]] == ["Yellow"]
    assert folder["items"][0]["artist"] == "Coldplay"


@pytest.mark.asyncio
async def test_recommendations_unavailable_when_ma_down(monkeypatch):
    async def dead(command, **args): return None
    monkeypatch.setattr(music_service, "_ma", dead)
    r = await music_service.get_recommendations()
    assert r == {"available": False, "folders": []}


@pytest.mark.asyncio
async def test_recently_played_normalized_and_clamped(monkeypatch):
    seen = {}
    async def fake_ma(command, **args):
        assert command == "music/recently_played_items"
        seen.update(args)
        return [{"name": "So What", "uri": "ytmusic://track/2", "media_type": "track",
                 "artists": [{"name": "Miles Davis"}]}]
    monkeypatch.setattr(music_service, "_ma", fake_ma)
    r = await music_service.get_recently_played(limit=999, media_types=["track"])
    assert seen == {"limit": 50, "media_types": ["track"]}  # clamped
    assert r["available"] is True and r["items"][0]["name"] == "So What"


@pytest.mark.asyncio
async def test_discovery_endpoints_delegate(monkeypatch):
    from routers import music as music_router
    async def fake_recs():
        return {"available": True, "folders": [{"name": "Mixed for you", "items": []}]}
    monkeypatch.setattr(music_service, "get_recommendations", fake_recs)
    r = await music_router.music_recommendations()
    assert r["folders"][0]["name"] == "Mixed for you"

    seen = {}
    async def fake_recent(limit=10, media_types=None):
        seen["limit"] = limit; seen["types"] = media_types
        return {"available": True, "items": []}
    monkeypatch.setattr(music_service, "get_recently_played", fake_recent)
    r2 = await music_router.music_recently_played(limit=5, types="track,album")
    assert r2["available"] is True and seen == {"limit": 5, "types": ["track", "album"]}

    toggles = {}
    async def fake_toggle(enabled, player_id=""):
        toggles["enabled"] = enabled; toggles["pid"] = player_id; return True
    monkeypatch.setattr(music_service, "set_dont_stop_the_music", fake_toggle)
    r3 = await music_router.music_dont_stop({"enabled": True, "player_id": "p1"})
    assert r3 == {"ok": True, "enabled": True} and toggles == {"enabled": True, "pid": "p1"}


@pytest.mark.asyncio
async def test_play_endpoint_accepts_radio_flag(monkeypatch):
    from routers import music as music_router
    seen = {}
    async def fake_sp(query, player_id="", radio_mode=False):
        seen["q"] = query; seen["radio"] = radio_mode
        return {"name": query}
    monkeypatch.setattr(music_service, "search_and_play", fake_sp)
    r = await music_router.music_play({"query": "miles davis", "radio": True})
    assert r["ok"] is True and seen == {"q": "miles davis", "radio": True}
    await music_router.music_play({"query": "jazz"})
    assert seen["radio"] is False  # default unchanged
