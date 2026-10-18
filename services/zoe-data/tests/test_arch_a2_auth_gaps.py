"""A2 of the 2026-10-18 architecture deep dive: the auth gaps left after #2089 / #2099.

* the 17 ``routers/music.py`` writes took NO auth: now ``require_member_or_panel`` (the kiosk plays music);
* ``GET /api/settings`` dumped every ``system_preferences`` row to a guest: now an allowlist, and a member sees only their
  OWN per-user rows;
* the Multica webhook ran its UPDATE before checking the secret: the secret is now a route dependency;
* the fake-success ``POST /api/agent/board/cancel`` and ``POST /api/user/layout`` are gone.

Real HTTP, the REAL ``get_current_user`` + device-token resolution (zoe-auth and the panel registry are faked; the
registry SQL is pinned by test_house_state_routes_auth.py). Invented data; TEST-NET addresses.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import auth
import music_service
import panel_principal
import routers.music as music
import routers.panel_auth as panel_auth
import routers.stubs as stubs
from db_pool import get_db
from routers import system

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
ACTIVE_PANELS = {PANEL_IP, "kitchen-panel"}  # the registry; a test may retire the panel


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for var in ("ZOE_HA_CONTROL_SCOPE", "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_CREDENTIAL",
                "ZOE_DEVICE_TOKEN_BINDS_MEMBER", "MULTICA_WEBHOOK_SECRET"):
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
        return value in ACTIVE_PANELS

    monkeypatch.setattr(panel_principal, "_with_registry", _with_registry)
    yield
    auth._session_cache.clear()


def _client(router, peer, db=None):
    app = FastAPI()
    app.include_router(router)
    if db is not None:
        app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, client=(peer, 50000))


# ── music: every write is a member or the panel ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def ma_calls(monkeypatch):
    """Every Music Assistant call a music write can make, faked and recorded (no network)."""
    calls = []

    def _fake(name):
        async def fn(*a, **k):
            calls.append(name)
            return [] if name == "get_players" else ({"ok": True} if "group" in name or name == "play_media" else True)
        return fn

    for name in ("control", "seek", "transfer", "group_players", "ungroup_player", "search_and_play", "play_media",
                 "queue_move", "queue_remove", "queue_clear", "queue_play_index", "queue_save_playlist", "playlist_add",
                 "favorite_add", "favorite_remove", "set_dont_stop_the_music", "get_players"):
        monkeypatch.setattr(music_service, name, _fake(name))
    monkeypatch.setattr(music_service, "set_preferred_player_id", lambda pid: calls.append("set_preferred_player_id"))
    return calls


WRITES = sorted(r.path for r in music.router.routes if "POST" in getattr(r, "methods", set()))


def test_the_music_router_has_the_17_writes_the_audit_found_plus_the_jukebox_mint():
    assert len(WRITES) == 18 and "/api/music/jukebox/start" in WRITES, WRITES  # a new write is covered below too


@pytest.mark.parametrize("path", WRITES)
@pytest.mark.parametrize("peer,headers", STRANGERS)
def test_strangers_cannot_drive_the_music(ma_calls, path, peer, headers):
    r = _client(music.router, peer).post(path, json={"action": "pause", "uri": "library://track/1"}, headers=headers)
    assert r.status_code == 403, (path, headers, r.text)
    assert ma_calls == []  # refused BEFORE the handler: Music Assistant was never touched


@pytest.mark.parametrize("path", WRITES)
@pytest.mark.parametrize("peer,headers", PANEL + MEMBER)
def test_the_panel_and_members_drive_the_music(ma_calls, path, peer, headers):
    body = {"panel_id": "kitchen-panel"} if path.endswith("/jukebox/start") else {}
    assert _client(music.router, peer).post(path, json=body, headers=headers).status_code == 200, path


def test_music_reads_stay_open(ma_calls):
    assert _client(music.router, PHONE_IP).get("/api/music/players").status_code == 200


# ── the phone jukebox: a QR capability for play / queue-a-song only ──────────────────────────────────────────────────────────

@pytest.fixture
def jukebox(monkeypatch, ma_calls):
    """The panel shows the QR; return the token the phone gets from it (the URL the SVG encodes)."""
    import setup_qr

    encoded = []
    monkeypatch.setattr(setup_qr, "render_svg", lambda url: encoded.append(url) or b"<svg/>")  # segno is not in the slim venv
    r = _client(music.router, PROXY).post("/api/music/jukebox/start", json={"panel_id": "kitchen-panel"},
                                          headers={"X-Real-IP": PANEL_IP})
    qr = _client(music.router, PROXY).get(r.json()["qr_path"])
    assert qr.status_code == 200 and qr.headers["cache-control"] == "no-store"
    assert _client(music.router, PROXY).get(r.json()["qr_path"]).status_code == 404  # the handle is single-use
    assert encoded[0].split("#t=")[0].endswith("/jukebox.html")
    return encoded[0].split("#t=", 1)[1]


def _phone(path, token, extra=None, body=None):
    headers = {"X-Jukebox-Token": token, **(extra or {})}
    return _client(music.router, PHONE_IP).post(path, json=body or {"uri": "library://track/1", "option": "add"},
                                                headers=headers)


@pytest.mark.parametrize("path", ["/api/music/play_media", "/api/music/play"])
def test_the_scanned_phone_plays_and_queues(jukebox, path):
    assert _phone(path, jukebox).status_code == 200


@pytest.mark.parametrize("path", [p for p in WRITES if not p.endswith(("/play_media", "/play"))])
def test_the_token_opens_nothing_else(jukebox, ma_calls, path):
    assert _phone(path, jukebox).status_code == 403, path  # no queue edits, library writes, speakers, or a new token
    assert ma_calls == []


def test_the_token_is_refused_over_the_tunnel_expired_forged_or_for_a_retired_panel(jukebox, monkeypatch):
    import music_jukebox
    import music_setup

    assert _phone("/api/music/play_media", jukebox, {"cf-ray": "abc"}).status_code == 403
    assert _phone("/api/music/play_media", jukebox[:-2] + "xx").status_code == 403
    assert _phone("/api/music/play_media", music_setup.mint("spotify")["token"]).status_code == 403  # another flow's key
    other = music_jukebox._TOKENS.mint({"k": "other", "pid": "kitchen-panel"}, 60)  # this key, another purpose
    assert _phone("/api/music/play_media", other).status_code == 403
    monkeypatch.setattr(music_jukebox, "ttl_s", lambda: -10)
    assert _phone("/api/music/play_media", music_jukebox.mint("kitchen-panel")["token"]).status_code == 403
    monkeypatch.setattr(sys.modules[__name__], "ACTIVE_PANELS", {PANEL_IP})  # the panel is retired / no longer allows guests
    assert _phone("/api/music/play_media", jukebox).status_code == 403


def test_only_a_member_or_the_panel_mints_and_only_for_a_live_panel(ma_calls):
    for peer, headers in STRANGERS:
        assert _client(music.router, peer).post("/api/music/jukebox/start", json={"panel_id": "kitchen-panel"},
                                                headers=headers).status_code == 403
    peer, headers = MEMBER[0]
    assert _client(music.router, peer).post("/api/music/jukebox/start", json={"panel_id": "no-such-panel"},
                                            headers=headers).status_code == 400


# ── GET /api/settings ────────────────────────────────────────────────────────────────────────────────────────────────────────

class _SettingsDb:
    async def execute_fetchall(self, sql, params=()):
        return [{"key": "weather_default_location", "value": '{"lat": -31.95, "lon": 115.86}'},
                {"key": "panel_idle_logout_s", "value": "120"},
                {"key": "intelligence_settings:casey", "value": '{"show_orb": false}'},
                {"key": "intelligence_settings:robin", "value": '{"do_not_disturb": true}'}]


@pytest.mark.parametrize("peer,headers", STRANGERS + PANEL)
def test_guests_and_the_panel_get_only_the_public_settings(monkeypatch, peer, headers):
    monkeypatch.setenv("ZOE_HA_URL", "http://ha.example:8123")
    r = _client(stubs.router, peer, _SettingsDb()).get("/api/settings", headers=headers)
    assert r.json() == {"homeassistant_url": "http://ha.example:8123", "panel_idle_logout_s": 120}


def test_a_member_gets_the_household_rows_and_only_their_own():
    peer, headers = MEMBER[0]
    body = _client(stubs.router, peer, _SettingsDb()).get("/api/settings", headers=headers).json()
    assert body["weather_default_location"] == {"lat": -31.95, "lon": 115.86}
    assert body["intelligence_settings:casey"] == {"show_orb": False}
    assert "intelligence_settings:robin" not in body  # another member's row


# ── the Multica webhook: the secret comes before the write ───────────────────────────────────────────────────────────────────

@pytest.fixture
def webhook(monkeypatch):
    import db_pool

    writes = []

    class _Db:
        async def execute(self, sql, *args):
            writes.append(args)

    @asynccontextmanager
    async def ctx():
        yield _Db()

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    client = _client(system._agent_card_router, "127.0.0.1")
    client.writes = writes
    return client


EVENT = {"event": "issue.status_changed", "issue": {"id": "iss-1", "identifier": "ZOE-1", "status": "done"}}


@pytest.mark.parametrize("secret,headers", [
    (None, {}), (None, {"Authorization": "Bearer "}),          # no secret configured: fail closed
    ("s3cret", {}), ("s3cret", {"Authorization": "Bearer wrong"}), ("s3cret", {"X-Multica-Webhook-Token": "wrong"}),
])
def test_the_webhook_writes_nothing_without_the_secret(webhook, monkeypatch, secret, headers):
    if secret:
        monkeypatch.setenv("MULTICA_WEBHOOK_SECRET", secret)
    assert webhook.post("/api/agent/board/webhook", json=EVENT, headers=headers).status_code == 401
    assert webhook.writes == []  # the proposal status was NOT flipped


@pytest.mark.parametrize("headers", [{"Authorization": "Bearer s3cret"}, {"X-Multica-Webhook-Token": "s3cret"}])
def test_the_webhook_syncs_the_proposal_with_the_secret(webhook, monkeypatch, headers):
    monkeypatch.setenv("MULTICA_WEBHOOK_SECRET", "s3cret")
    assert webhook.post("/api/agent/board/webhook", json=EVENT, headers=headers).json() == {"ok": True}
    assert webhook.writes == [("validated", "iss-1")]


# ── fake success is gone ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_fake_success_stubs_are_gone():
    def routes(router):
        return {(m, r.path) for r in router.routes for m in getattr(r, "methods", ())}

    assert ("POST", "/api/agent/board/cancel") not in routes(system._agent_card_router)
    assert ("POST", "/api/user/layout") not in routes(stubs.router)
    assert ("GET", "/api/user/layout") in routes(stubs.router)  # the honest read ("no layout") stays
    assert "/api/agent/board/cancel" not in (ROOT / "routers" / "chat.py").read_text()  # the card no longer links it
