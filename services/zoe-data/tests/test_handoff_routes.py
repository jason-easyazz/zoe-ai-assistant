"""routers/handoff — who may start, read and send a handoff, and the QR route.

Negative control: swap ``require_signed_in`` back to ``get_current_user`` in
``routers/handoff.py`` and the anonymous/guest tests go red (a credential-less
kiosk would mint a phone link for adding an account).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import importlib.util
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import auth_handoff
import music_service
import push
import setup_qr
from routers import handoff as handoff_router

SVC = Path(__file__).resolve().parents[1]
_SESSIONS = {
    "sess-jason": {"user_id": "jason", "role": "member", "username": "jason"},
    "sess-amy": {"user_id": "amy", "role": "member", "username": "amy"},
    "sess-guest": {"user_id": "guest", "role": "guest", "username": "guest"},
}
J = {"X-Session-ID": "sess-jason"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mig_0031r", SVC / "alembic/versions/0031_auth_handoffs.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    path = str(tmp_path / "h.db")
    con = sqlite3.connect(path)
    con.execute(mig._TABLE_DDL)
    con.execute("CREATE TABLE user_preferences (user_id TEXT PRIMARY KEY, prefs TEXT, updated_at TEXT)")
    con.commit()
    con.close()

    @asynccontextmanager
    async def ctx():
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    async def fake_validate(session_id):
        return dict(_SESSIONS[session_id]) if session_id in _SESSIONS else None

    async def fake_form(provider):
        return {"spotify": {"auth": "oauth"}, "radiobrowser": {"auth": "free"}}.get(provider)

    saved = []

    async def fake_save(provider, values, instance_id=None):
        saved.append(provider)
        return {"name": provider}

    class _B:
        async def broadcast_to_panel(self, *a):
            return 1

    monkeypatch.setattr(auth, "_validate_with_auth_service", fake_validate)
    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()
    monkeypatch.setattr(auth_handoff, "_db_ctx", ctx)
    monkeypatch.setattr(push, "broadcaster", _B())
    monkeypatch.setattr(music_service, "provider_setup_form", fake_form)
    monkeypatch.setattr(music_service, "save_provider", fake_save)
    monkeypatch.setattr(setup_qr, "render_svg", lambda url: b"<svg/>")  # segno not in the slim venv
    monkeypatch.delenv("ZOE_TELEGRAM_BOT_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(handoff_router.router)
    c = TestClient(app)
    c.saved = saved
    return c


def _start(client, headers=J, **body):
    return client.post("/api/handoff/start", headers=headers,
                       json={"kind": "music", "provider": "spotify", "panel_id": "zoe-touch-pi", **body})


@pytest.mark.parametrize("headers,code", [({}, 403), ({"X-Session-ID": "sess-guest"}, 403),
                                          ({"X-Session-ID": "bogus"}, 401)])
def test_start_requires_a_member_session(client, headers, code):
    assert _start(client, headers).status_code == code


def test_member_start_returns_a_qr_path_and_no_secret(client):
    r = _start(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["status"] == "pending" and body["auth"] == "oauth"
    assert body["qr_path"].startswith(f"/api/handoff/{body['id']}/qr/")
    assert "phone_url" not in body and "qr_handle" not in body
    assert "setup-music" not in r.text and "#provider" not in r.text  # the token-bearing link stays server-side


def test_free_provider_connects_immediately(client):
    r = _start(client, provider="radiobrowser").json()
    assert r == {"ok": True, "provider": "radiobrowser", "immediate": True} and client.saved == ["radiobrowser"]


@pytest.mark.parametrize("body", [{"kind": "calendar"}, {"provider": "nope"}, {"panel_id": ""},
                                  {"panel_id": "a b/../c"}])
def test_start_rejects_bad_input(client, body):
    assert _start(client, **body).status_code == 400


def test_status_and_send_are_owner_only(client):
    hid = _start(client).json()["id"]
    amy = {"X-Session-ID": "sess-amy"}
    assert client.get(f"/api/handoff/{hid}/status", headers=amy).status_code == 403
    assert client.post(f"/api/handoff/{hid}/send-to-phone", headers=amy).status_code == 403
    assert client.get(f"/api/handoff/{hid}/status").status_code == 403  # anonymous
    assert client.get("/api/handoff/nope/status", headers=J).status_code == 404
    st = client.get(f"/api/handoff/{hid}/status", headers=J).json()
    assert st["ok"] and st["status"] == "pending"
    sent = client.post(f"/api/handoff/{hid}/send-to-phone", headers=J).json()
    assert sent["status"] == "unavailable"  # no bot token / linked chat → QR only


def test_qr_route_is_single_use(client):
    body = _start(client).json()
    first = client.get(body["qr_path"])
    assert first.status_code == 200 and first.headers["content-type"].startswith("image/svg")
    assert first.headers["cache-control"] == "no-store"
    assert client.get(body["qr_path"]).status_code == 404


def test_the_card_is_voice_and_touch_only():
    """The panel card has no text input (VISION principle 8) and listens for the push."""
    home = (SVC.parent / "zoe-ui/dist/touch/home.html").read_text()
    i = home.index("function authCard(")
    card = home[home.index("// ---- authCard"):home.index("window.addEventListener('zoe:handoff'", i)]
    assert "<input" not in card and "<textarea" not in card and "contenteditable" not in card
    assert "/api/handoff/start" in card and "send-to-phone" in card
    executor = (SVC.parent / "zoe-ui/dist/js/touch-ui-executor.js").read_text()
    assert "'handoff_update'" in executor and "zoe:handoff" in executor
    # Sources → Connect/Reconnect goes through the card, not the old static-Done view.
    connect = home[home.index("function startProviderConnect("):home.index("// ---- authCard")]
    assert "authCard(" in connect and "/api/music/setup/start" not in connect


def test_one_client_qr_library():
    dist = SVC.parent / "zoe-ui/dist"
    assert not (dist / "js/qrcode.min.js").exists(), "the second client QR library came back"
    assert "/js/qrcode.min.js" not in (dist / "touch/home.html").read_text()
