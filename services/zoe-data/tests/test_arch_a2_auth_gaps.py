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
        return value in (PANEL_IP, "kitchen-panel")

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
            return [] if name == "get_players" else ({"ok": True} if "group" in name else True)
        return fn

    for name in ("control", "seek", "transfer", "group_players", "ungroup_player", "search_and_play", "play_media",
                 "queue_move", "queue_remove", "queue_clear", "queue_play_index", "queue_save_playlist", "playlist_add",
                 "favorite_add", "favorite_remove", "set_dont_stop_the_music", "get_players"):
        monkeypatch.setattr(music_service, name, _fake(name))
    monkeypatch.setattr(music_service, "set_preferred_player_id", lambda pid: calls.append("set_preferred_player_id"))
    return calls


WRITES = sorted(r.path for r in music.router.routes if "POST" in getattr(r, "methods", set()))


def test_the_music_router_has_the_17_writes_the_audit_found():
    assert len(WRITES) == 17, WRITES  # a new write is covered by the tests below automatically


@pytest.mark.parametrize("path", WRITES)
@pytest.mark.parametrize("peer,headers", STRANGERS)
def test_strangers_cannot_drive_the_music(ma_calls, path, peer, headers):
    r = _client(music.router, peer).post(path, json={"action": "pause", "uri": "library://track/1"}, headers=headers)
    assert r.status_code == 403, (path, headers, r.text)
    assert ma_calls == []  # refused BEFORE the handler: Music Assistant was never touched


@pytest.mark.parametrize("path", WRITES)
@pytest.mark.parametrize("peer,headers", PANEL + MEMBER)
def test_the_panel_and_members_drive_the_music(ma_calls, path, peer, headers):
    assert _client(music.router, peer).post(path, json={}, headers=headers).status_code == 200, path


def test_music_reads_stay_open(ma_calls):
    assert _client(music.router, PHONE_IP).get("/api/music/players").status_code == 200


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
