"""auth_handoff — the one app-connection handoff engine (B7.5).

Pins the state machine (start → awaiting_phone → completing → done | error,
forward-only, ``done`` and expiry final, a failed sign-in retryable), the
single-use QR handle, send-to-phone available/unavailable, the live panel push
(secret-free) on every change, the best-effort wake, and the music flows
end-to-end against the 2.8.7 and 2.10.3 fake Music Assistant servers.

Negative controls (in-suite, each proves its test can go red):
  * ``test_control_permissive_rules_break_finality`` — with the transition
    rules removed, the finality scenario lets an error overwrite ``done``;
  * ``test_control_no_expiry_check_keeps_pending`` — with the expiry check
    removed from ``status()``, a stale handoff stays ``pending``.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import asyncio
import importlib.util
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import aiosqlite

import auth_handoff
import music_service
import music_setup
import push
import setup_qr
import ytmusic_signin
from routers import music_setup as ms_router

SVC = Path(__file__).resolve().parents[1]
MEMBER = {"user_id": "jason", "role": "member"}


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, SVC / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MIG = _load("mig_0030", "alembic/versions/0030_auth_handoffs.py")
_REAL_WAKE = auth_handoff._wake_panel  # captured before the fixture stubs it


@pytest.fixture
def hdb(tmp_path, monkeypatch):
    path = str(tmp_path / "h.db")
    con = sqlite3.connect(path)
    con.execute(MIG._TABLE_DDL)  # the real migration DDL, not a test copy
    for stmt in MIG._INDEX_DDL:
        con.execute(stmt)
    con.execute("CREATE TABLE user_preferences (user_id TEXT PRIMARY KEY, prefs TEXT, updated_at TEXT)")
    con.execute("CREATE TABLE display_preferences (device_id TEXT PRIMARY KEY, pi_host TEXT)")
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

    sent, woke = [], []

    class _Broadcaster:
        async def broadcast_to_panel(self, panel_id, event, data):
            sent.append((panel_id, event, data))
            return 1

    async def wake(panel_id):
        woke.append(panel_id)

    monkeypatch.setattr(auth_handoff, "_db_ctx", ctx)
    monkeypatch.setattr(push, "broadcaster", _Broadcaster())
    monkeypatch.setattr(auth_handoff, "_wake_panel", wake)
    monkeypatch.delenv("ZOE_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ZOE_PUBLIC_URL", raising=False)
    auth_handoff._LINKS.clear()
    return SimpleNamespace(path=path, sent=sent, woke=woke,
                           statuses=lambda: [d["action"]["payload"]["status"] for _, _, d in sent])


def _link_telegram(path, user="jason", tid="123456789"):
    con = sqlite3.connect(path)
    con.execute("INSERT INTO user_preferences (user_id, prefs) VALUES (?, ?)",
                (user, json.dumps({"telegram_id": tid})))
    con.commit()
    con.close()


async def _start(provider="spotify", user=MEMBER, panel="zoe-touch-pi"):
    return await auth_handoff.start("music", actor_user=user, panel_id=panel, provider=provider,
                                    base_url="https://zoe.local")


def _token_of(h):
    return h["phone_url"].split("&t=", 1)[1]


# ── start ────────────────────────────────────────────────────────────────────

async def test_start_mints_a_pending_handoff_bound_to_the_token_nonce(hdb):
    h = await _start()
    assert h["status"] == "pending" and h["kind"] == "music" and h["provider"] == "spotify"
    assert h["phone_url"].startswith("https://zoe.local/setup-music.html#provider=spotify&t=")
    tok = _token_of(h)
    assert music_setup.verify(tok)["u"] == "jason"
    assert tok not in h["qr_path"] and "?" not in h["qr_path"]
    assert h["qr_path"] == f"/api/handoff/{h['id']}/qr/{h['qr_handle']}"
    assert h["channels"] == ["qr"]  # no bot token configured → QR only
    con = sqlite3.connect(hdb.path)
    ref, panel, user = con.execute("SELECT ref, panel_id, user_id FROM auth_handoffs").fetchone()
    assert ref == music_setup.verify(tok)["n"] and panel == "zoe-touch-pi" and user == "jason"
    assert tok not in json.dumps(con.execute("SELECT * FROM auth_handoffs").fetchall())  # never at rest


@pytest.mark.parametrize("user,panel,kind", [
    ({"user_id": "guest", "role": "guest"}, "p1", "music"),
    ({}, "p1", "music"),
    (MEMBER, "", "music"),
    (MEMBER, "p1", "calendar"),
])
async def test_start_refuses_guests_missing_panel_and_unknown_kinds(hdb, user, panel, kind):
    with pytest.raises(auth_handoff.HandoffError):
        await auth_handoff.start(kind, actor_user=user, panel_id=panel, provider="spotify",
                                 base_url="https://zoe.local")


# ── state machine ────────────────────────────────────────────────────────────

async def test_happy_path_pushes_every_change_to_the_panel(hdb):
    h = await _start()
    await auth_handoff._transition(h["id"], "awaiting_phone")
    await auth_handoff._transition(h["id"], "completing", "Connecting…")
    done = await auth_handoff.complete(h["id"], {"ok": True, "detail": "Spotify is connected"})
    assert done["status"] == "done" and done["detail"] == "Spotify is connected"
    assert hdb.statuses() == ["awaiting_phone", "completing", "done"]
    panel, event, data = hdb.sent[-1]
    assert panel == "zoe-touch-pi" and event == "ui_action" and data["panel_id"] == "zoe-touch-pi"
    assert data["action"]["action_type"] == "handoff_update"
    assert data["action"]["id"].startswith("push_handoff_")  # never acked into the ledger
    assert (await auth_handoff.status(h["id"]))["status"] == "done"


async def test_the_push_is_secret_free(hdb):
    h = await _start()
    await auth_handoff._transition(h["id"], "awaiting_phone")
    blob = json.dumps(hdb.sent)
    assert _token_of(h) not in blob and "phone_url" not in blob and "setup-music" not in blob


async def test_done_is_final_and_status_never_moves_backwards(hdb):
    h = await _start()
    await auth_handoff._transition(h["id"], "completing")
    assert await auth_handoff._transition(h["id"], "awaiting_phone") is None  # a phone reload
    await auth_handoff.complete(h["id"], {"ok": True})
    assert await auth_handoff.complete(h["id"], {"ok": False}) is None
    assert (await auth_handoff.status(h["id"]))["status"] == "done"
    assert hdb.statuses() == ["completing", "done"]


async def test_concurrent_completions_settle_once(hdb):
    h = await _start()
    results = await asyncio.gather(auth_handoff.complete(h["id"], {"ok": True}),
                                   auth_handoff.complete(h["id"], {"ok": False}))
    assert sum(r is not None for r in results) == 1, results  # one wins, the other is refused
    assert len([s for s in hdb.statuses() if s in ("done", "error")]) == 1


async def test_control_permissive_rules_break_finality(hdb, monkeypatch):
    monkeypatch.setattr(auth_handoff, "_allowed", lambda *a: True)
    h = await _start()
    await auth_handoff.complete(h["id"], {"ok": True})
    await auth_handoff.complete(h["id"], {"ok": False})
    assert (await auth_handoff.status(h["id"]))["status"] == "error"  # what the real rules forbid


async def test_a_failed_sign_in_can_be_retried_on_the_same_link(hdb):
    h = await _start()
    await auth_handoff.complete(h["id"], {"ok": False, "detail": "Sign-in was cancelled."})
    assert (await auth_handoff.status(h["id"]))["reason"] == "failed"
    assert (await auth_handoff._transition(h["id"], "completing"))["status"] == "completing"
    assert (await auth_handoff.complete(h["id"], {"ok": True}))["status"] == "done"


async def test_an_unchanged_transition_is_not_rebroadcast(hdb):
    h = await _start()
    await auth_handoff._transition(h["id"], "awaiting_phone", "Finish on your phone")
    await auth_handoff._transition(h["id"], "awaiting_phone", "Finish on your phone")
    assert hdb.statuses() == ["awaiting_phone"]


def _age(monkeypatch, seconds):
    import time as _time
    real = _time.time
    monkeypatch.setattr(auth_handoff.time, "time", lambda: real() + seconds)


async def test_timeout_expires_on_read_and_is_final(hdb, monkeypatch):
    h = await _start()
    _age(monkeypatch, music_setup.SETUP_TTL_S + 5)
    v = await auth_handoff.status(h["id"])
    assert v["status"] == "error" and v["reason"] == "expired" and v["expires_in"] == 0
    assert hdb.statuses() == ["error"]
    assert await auth_handoff.complete(h["id"], {"ok": True}) is None  # a late finish cannot revive it
    assert h["id"] not in auth_handoff._LINKS  # the link cannot be re-sent


async def test_control_no_expiry_check_keeps_pending(hdb, monkeypatch):
    h = await _start()
    _age(monkeypatch, music_setup.SETUP_TTL_S + 5)
    real_view = auth_handoff._view

    async def status_without_expiry(handoff_id):
        async with auth_handoff._db_ctx() as db:
            return real_view(await auth_handoff._row(db, handoff_id))
    monkeypatch.setattr(auth_handoff, "status", status_without_expiry)
    assert (await auth_handoff.status(h["id"]))["status"] == "pending"


async def test_terminal_changes_wake_the_panel_best_effort(hdb):
    h = await _start()
    await auth_handoff._transition(h["id"], "awaiting_phone")
    await asyncio.sleep(0)
    assert hdb.woke == []
    await auth_handoff.complete(h["id"], {"ok": True})
    await asyncio.sleep(0)
    assert hdb.woke == ["zoe-touch-pi"]


async def test_wake_posts_to_the_panel_agent_and_never_raises(hdb, monkeypatch):
    import agent_safety
    import httpx
    con = sqlite3.connect(hdb.path)
    con.execute("INSERT INTO display_preferences VALUES ('p1', '192.168.1.77')")
    con.commit()
    con.close()
    monkeypatch.setattr(agent_safety, "is_allowed_panel_host", lambda h: h == "192.168.1.77")
    posts = []

    class _Client:
        def __init__(self, fail):
            self.fail = fail

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            if self.fail:
                raise httpx.ConnectError("no route to host")
            posts.append((url, json))

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: _Client(False))
    await _REAL_WAKE("p1")
    assert posts == [("http://192.168.1.77:8765/wake", {"hold_s": 20})]
    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: _Client(True))
    await _REAL_WAKE("p1")  # unreachable (the Pi is off): logged, never raised
    await _REAL_WAKE("unknown-panel")  # default host is not allowed here: skipped


# ── QR handle ────────────────────────────────────────────────────────────────

async def test_qr_handle_is_single_use_and_bound_to_its_handoff(hdb):
    a, b = await _start(), await _start("tidal")
    assert auth_handoff.qr_url(b["id"], a["qr_handle"]) is None  # foreign handoff
    c = await _start("deezer")
    assert auth_handoff.qr_url(c["id"], c["qr_handle"]) == c["phone_url"]
    assert auth_handoff.qr_url(c["id"], c["qr_handle"]) is None  # spent


# ── send to my phone ─────────────────────────────────────────────────────────

async def test_send_to_phone_unavailable_without_a_bot_or_a_linked_chat(hdb, monkeypatch):
    h = await _start()
    assert (await auth_handoff.send_to_phone(h["id"]))["status"] == "unavailable"  # no bot token
    monkeypatch.setenv("ZOE_TELEGRAM_BOT_TOKEN", "123:abc")
    assert (await auth_handoff.send_to_phone(h["id"]))["status"] == "unavailable"  # no linked chat
    assert await auth_handoff.telegram_available("jason") is False


async def test_send_to_phone_delivers_the_link_to_the_linked_chat(hdb, monkeypatch):
    monkeypatch.setenv("ZOE_TELEGRAM_BOT_TOKEN", "123:abc")
    _link_telegram(hdb.path)
    calls = []

    async def fake_send(chat_id, text, url):
        calls.append((chat_id, url))
        return True
    monkeypatch.setattr(auth_handoff, "_telegram_send", fake_send)
    h = await _start()
    assert h["channels"] == ["qr", "telegram"]
    r = await auth_handoff.send_to_phone(h["id"])
    assert r == {"ok": True, "status": "sent", "detail": "Sent to your phone"}
    assert calls == [("123456789", h["phone_url"])]
    assert (await auth_handoff.status(h["id"]))["detail"].startswith("Sent to your phone")
    await auth_handoff.complete(h["id"], {"ok": True})
    assert (await auth_handoff.send_to_phone(h["id"]))["status"] == "unavailable"  # finished
    assert len(calls) == 1


async def test_send_to_phone_after_a_restart_says_start_again(hdb, monkeypatch):
    monkeypatch.setenv("ZOE_TELEGRAM_BOT_TOKEN", "123:abc")
    _link_telegram(hdb.path)
    h = await _start()
    auth_handoff._LINKS.clear()  # the process restarted: the token-bearing link is gone
    r = await auth_handoff.send_to_phone(h["id"])
    assert r["status"] == "unavailable" and "again" in r["detail"]


# ── provider hooks by nonce ──────────────────────────────────────────────────

async def test_ref_hooks_are_best_effort(hdb, monkeypatch):
    await auth_handoff.mark_ref("music", "no-such-nonce", "completing")  # unknown ref: no-op
    await auth_handoff.complete_ref("music", None, {"ok": True})
    assert hdb.sent == []

    @asynccontextmanager
    async def broken():
        raise RuntimeError("pool not initialised")
        yield  # pragma: no cover
    monkeypatch.setattr(auth_handoff, "_db_ctx", broken)
    await auth_handoff.mark_ref("music", "n", "completing")  # swallowed, never breaks a flow
    await auth_handoff.complete_ref("music", "n", {"ok": True})


# ── music flows end-to-end (fake MA 2.8.7 / 2.10.3) ──────────────────────────

MA = _load("ma_fakes", "tests/test_music_ma_setup_flow.py")


@pytest.fixture
def ma_env(monkeypatch):
    monkeypatch.delenv("ZOE_YTMUSIC_POTOKEN_URL", raising=False)
    music_service._invalidate_ma_version()

    async def up(url):
        return True
    monkeypatch.setattr(music_service, "_potoken_reachable", up)
    yield
    music_service._invalidate_ma_version()


@pytest.mark.parametrize("fake_cls", ["Fake287", "Fake210"])
async def test_music_reconnect_form_flow_end_to_end(hdb, ma_env, monkeypatch, fake_cls):
    fake = getattr(MA, fake_cls)().install(monkeypatch)
    iid = MA._ytmusic_instance(fake)
    h = await _start("ytmusic")
    tok = _token_of(h)

    async def form(provider):
        return {"name": "YouTube Music", "auth": "browser", "fields": []}
    monkeypatch.setattr(music_service, "provider_setup_form", form)
    assert (await ms_router.setup_form(token=tok, provider="ytmusic"))["ok"] is True
    r = await ms_router.setup_save({"token": tok, "provider": "ytmusic",
                                    "values": {"username": "jason", "cookie": "FRESH"}})
    assert r["ok"] is True and r["reconnected"] is True
    assert list(fake.instances) == [iid], "reconnect minted a duplicate"
    assert hdb.statuses() == ["awaiting_phone", "completing", "done"]
    v = await auth_handoff.status(h["id"])
    assert v["status"] == "done" and "connected" in v["detail"]


async def test_music_210_rejected_cookie_reports_error(hdb, ma_env, monkeypatch):
    fake = MA.Fake210().install(monkeypatch)
    MA._ytmusic_instance(fake)
    h = await _start("ytmusic")
    r = await ms_router.setup_save({"token": _token_of(h), "provider": "ytmusic",
                                    "values": {"username": "jason", "cookie": "REJECTED"}})
    assert r["ok"] is False
    v = await auth_handoff.status(h["id"])
    assert v["status"] == "error" and v["reason"] == "failed"


async def _settled(flow):
    """The attempt, and a report scheduled by ``watch`` for an attempt that had
    already ended by the time it was watched."""
    await flow["task"]
    if flow.get("report_task") is not None:
        await flow["report_task"]


async def test_music_210_oauth_finish_event_completes_the_handoff(hdb, ma_env, monkeypatch):
    fake = MA.Fake210().install(monkeypatch)
    fake.oauth_url = "https://accounts.spotify.com/authorize?x=1"
    MA._install_ws(monkeypatch, fake, MA._user_completes)
    h = await _start("spotify")
    r = await ms_router.oauth_start({"token": _token_of(h), "provider": "spotify"})
    assert r["ok"] is True and r["auth_url"] == fake.oauth_url
    import music_oauth
    await _settled(music_oauth._flows[r["oauth_id"]])
    v = await auth_handoff.status(h["id"])
    assert v["status"] == "done" and v["detail"] == "Spotify is connected", v
    assert "completing" in hdb.statuses()


async def test_music_210_oauth_socket_loss_reports_error(hdb, ma_env, monkeypatch):
    fake = MA.Fake210().install(monkeypatch)
    fake.oauth_url = "https://accounts.spotify.com/authorize?x=3"
    MA._install_ws(monkeypatch, fake)  # no script: the socket drops after the URL
    h = await _start("spotify")
    r = await ms_router.oauth_start({"token": _token_of(h), "provider": "spotify"})
    import music_oauth
    await _settled(music_oauth._flows[r["oauth_id"]])
    assert (await auth_handoff.status(h["id"]))["status"] == "error"


async def test_ytmusic_browser_sign_in_completes_the_handoff(hdb, monkeypatch):
    monkeypatch.setattr(ytmusic_signin, "_SESSION", None)
    h = await _start("ytmusic")
    ref = music_setup.verify(_token_of(h))["n"]
    ytmusic_signin._SESSION = {"id": "ytm-1", "state": "awaiting_login", "watcher": None}
    ytmusic_signin.watch("ytm-1", auth_handoff.reporter("music", ref, "YouTube Music"))
    ytmusic_signin._SESSION["state"] = "connected"
    await ytmusic_signin._report(ytmusic_signin._SESSION)
    assert (await auth_handoff.status(h["id"]))["detail"] == "YouTube Music is connected"
    ytmusic_signin._SESSION = None


async def test_ytmusic_cancel_reports_a_retryable_error(hdb, monkeypatch):
    monkeypatch.setattr(ytmusic_signin, "_SESSION", None)

    async def no_teardown(session):
        return None
    monkeypatch.setattr(ytmusic_signin, "_teardown", no_teardown)
    h = await _start("ytmusic")
    ref = music_setup.verify(_token_of(h))["n"]
    ytmusic_signin._SESSION = {"id": "ytm-2", "state": "awaiting_login", "watcher": None}
    ytmusic_signin.watch("ytm-2", auth_handoff.reporter("music", ref))
    await ytmusic_signin.cancel_session("ytm-2")
    v = await auth_handoff.status(h["id"])
    assert v["status"] == "error" and v["reason"] == "failed" and "cancelled" in v["detail"]
    ytmusic_signin._SESSION = None


async def test_legacy_start_tokens_report_nothing(hdb, ma_env, monkeypatch):
    """A token from /api/music/setup/start has no handoff — the phone flow is unchanged."""
    fake = MA.Fake287().install(monkeypatch)
    MA._ytmusic_instance(fake)
    tok = music_setup.mint("ytmusic")["token"]
    r = await ms_router.setup_save({"token": tok, "provider": "ytmusic",
                                    "values": {"username": "jason", "cookie": "FRESH"}})
    assert r["ok"] is True and hdb.sent == []
