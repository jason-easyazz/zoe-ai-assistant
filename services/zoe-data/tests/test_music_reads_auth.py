"""Music READS need a member or the panel, like the writes (owner decision 2026-10-18; follows #2108).

Until now ``GET /api/music/{now-playing,queue,players,recently-played,playlists,...}`` answered any device on the home network,
including a visitor's phone on the guest Wi-Fi. Now every read takes ``require_member_or_panel``; the phone jukebox page keeps
exactly two reads (``/now-playing``, ``/search``) behind its short-lived ``X-Jukebox-Token``.

Real HTTP, the REAL ``get_current_user`` + device-token resolution (zoe-auth and the panel registry are faked, as in
test_arch_a2_auth_gaps.py). Invented data; TEST-NET addresses. The static twin is test_music_routes_declare_auth.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
import music_jukebox
import music_service
import panel_principal
import routers.music as music
import routers.music_setup as music_setup_router
import routers.panel_auth as panel_auth

pytestmark = pytest.mark.ci_safe

PANEL_IP, PHONE_IP, PROXY = "192.0.2.50", "192.0.2.77", "127.0.0.1"
MEMBER_SID, GUEST_SID, PANEL_TOKEN = "sid-member", "sid-guest", "tok-kitchen-panel"

STRANGERS = [
    (PHONE_IP, {}),                                                  # anonymous, straight at :8000 on the LAN
    (PHONE_IP, {"X-Real-IP": PANEL_IP}),                            # spoofed X-Real-IP from an untrusted peer
    (PROXY, {"X-Session-ID": GUEST_SID, "X-Real-IP": PHONE_IP}),    # a minted guest session is not the panel
    (PROXY, {"X-Real-IP": PANEL_IP, "cf-ray": "abc"}),              # the panel's address, but over Cloudflare
    (PHONE_IP, {"X-Device-Token": "not-a-real-token"}),             # an unknown device token is a guest
]
PANEL = [(PROXY, {"X-Real-IP": PANEL_IP}), (PHONE_IP, {"X-Device-Token": PANEL_TOKEN})]
MEMBER = [(PHONE_IP, {"X-Session-ID": MEMBER_SID})]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("ZOE_HA_CONTROL_SCOPE", "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_CREDENTIAL", "ZOE_DEVICE_TOKEN_BINDS_MEMBER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()

    async def _validate(sid):
        role = {MEMBER_SID: ("casey", "user"), GUEST_SID: ("guest", "guest")}.get(sid)
        return {"user_id": role[0], "role": role[1], "username": role[0], "permissions": []} if role else None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _validate)
    monkeypatch.setitem(panel_auth._token_cache, panel_auth._hash_token(PANEL_TOKEN),
                        {"panel_id": "kitchen-panel", "role": "voice-daemon", "expires_at": None, "revoked": 0})

    async def _with_registry(_db, check, value):  # the registry: one active, guest-allowing kiosk at PANEL_IP
        return value in {PANEL_IP, "kitchen-panel"}

    monkeypatch.setattr(panel_principal, "_with_registry", _with_registry)
    yield
    auth._session_cache.clear()


@pytest.fixture
def ma_calls(monkeypatch):
    """Every Music Assistant read, faked and recorded: a refused request must never reach one (no network)."""
    calls: list[str] = []

    def _async(name, ret):
        async def fn(*a, **k):
            calls.append(name)
            return ret
        return fn

    for name, ret in (("now_playing", None), ("search", {"results": {}}), ("get_speaker_groups", None), ("list_playlists", []),
                      ("playlist_tracks", []), ("get_recommendations", {"items": []}), ("get_recently_played", {"items": []}),
                      ("provider_catalogue", []), ("provider_setup_form", None)):
        monkeypatch.setattr(music_service, name, _async(name, ret))
    monkeypatch.setattr(music_service, "get_preferred_player_id", lambda: calls.append("get_preferred_player_id") or "")
    for name, ret in (("_get_info", None), ("_get_providers", []), ("_get_players", []), ("_get_queue_items", [])):
        monkeypatch.setattr(music, name, _async(name, ret))
    return calls


def _client(router, peer):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, client=(peer, 50000))


def _url(path: str) -> str:
    return path.replace("{queue_id}", "player-1")


# Every music GET the router declares, minus the single-use QR handle (a capability, allowlisted in the static guard).
READS = sorted(r.path for r in music.router.routes
               if "GET" in getattr(r, "methods", set()) and "/jukebox/qr/" not in r.path)
JUKEBOX_READS = ["/api/music/now-playing", "/api/music/search"]
MEMBER_ONLY_READS = [p for p in READS if p not in JUKEBOX_READS]
SETUP_READ = "/api/music/setup/catalogue"


def test_the_read_table_is_what_the_audit_found():
    assert len(READS) == 13 and "/api/music/now-playing" in READS and "/api/music/queue/{queue_id}" in READS, READS


@pytest.mark.parametrize("path", [_url(p) for p in READS])
@pytest.mark.parametrize("peer,headers", STRANGERS)
def test_strangers_cannot_read_the_music(ma_calls, path, peer, headers):
    r = _client(music.router, peer).get(path, headers=headers)
    assert r.status_code == 403, (path, headers, r.text)
    assert ma_calls == []  # refused BEFORE the handler: Music Assistant was never touched


@pytest.mark.parametrize("path", [_url(p) for p in READS])
@pytest.mark.parametrize("peer,headers", PANEL + MEMBER)
def test_the_panel_and_members_read_the_music(ma_calls, path, peer, headers):
    assert _client(music.router, peer).get(path, headers=headers).status_code == 200, path


@pytest.mark.parametrize("peer,headers", STRANGERS)
def test_strangers_cannot_see_the_add_music_catalogue_or_start_a_setup(ma_calls, peer, headers):
    c = _client(music_setup_router.router, peer)
    assert c.get(SETUP_READ, headers=headers).status_code == 403
    assert c.post("/api/music/setup/start", json={"provider": "spotify"}, headers=headers).status_code == 403
    assert ma_calls == []


@pytest.mark.parametrize("peer,headers", PANEL + MEMBER)
def test_the_panel_and_members_use_the_add_music_flow(ma_calls, peer, headers):
    c = _client(music_setup_router.router, peer)
    assert c.get(SETUP_READ, headers=headers).status_code == 200
    assert c.post("/api/music/setup/start", json={"provider": "spotify"}, headers=headers).status_code == 200


# ── the phone jukebox keeps exactly two reads, and only with its token ───────────────────────────────────────────────────────

@pytest.fixture
def token():
    return music_jukebox.mint("kitchen-panel")["token"]


@pytest.mark.parametrize("path", JUKEBOX_READS)
def test_the_scanned_phone_sees_what_is_playing_and_searches(ma_calls, token, path):
    r = _client(music.router, PHONE_IP).get(path, headers={"X-Jukebox-Token": token})
    assert r.status_code == 200, path
    assert _client(music.router, PHONE_IP).get(path).status_code == 403  # the same phone without the token is a stranger


@pytest.mark.parametrize("path", [_url(p) for p in MEMBER_ONLY_READS])
def test_the_jukebox_token_opens_no_other_read(ma_calls, token, path):
    assert _client(music.router, PHONE_IP).get(path, headers={"X-Jukebox-Token": token}).status_code == 403, path
    assert ma_calls == []  # no queue, history, playlists, players or provider state for a visitor


def test_the_jukebox_reads_refuse_a_forged_expired_or_tunnelled_token(ma_calls, token, monkeypatch):
    def get(tok, extra=None):
        return _client(music.router, PHONE_IP).get("/api/music/now-playing", headers={"X-Jukebox-Token": tok, **(extra or {})})

    assert get(token, {"cf-ray": "abc"}).status_code == 403          # never over the Cloudflare tunnel
    assert get(token[:-2] + "xx").status_code == 403                 # forged
    monkeypatch.setattr(music_jukebox, "ttl_s", lambda: -10)
    assert get(music_jukebox.mint("kitchen-panel")["token"]).status_code == 403  # expired
    assert ma_calls == []


# ── NEGATIVE CONTROL: the strangers tests above would go red if the dependency were removed ───────────────────────────────────

@pytest.mark.parametrize("path", ["/api/music/players", "/api/music/queue/{queue_id}", "/api/music/now-playing",
                                  "/api/music/recently-played"])
def test_negative_control_removing_the_dependency_opens_the_route(ma_calls, monkeypatch, path):
    route = next(r for r in music.router.routes if r.path == path and "GET" in r.methods)
    assert route.dependencies, "the route declares a principal dependency"
    assert _client(music.router, PHONE_IP).get(_url(path)).status_code == 403
    monkeypatch.setattr(route, "dependencies", [])  # what a refactor that drops the dependency looks like
    assert _client(music.router, PHONE_IP).get(_url(path)).status_code == 200, "the guard is what refuses the stranger"
