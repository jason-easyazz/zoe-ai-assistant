"""YouTube Music one-tap sign-in — session state machine, teardown, refresh, and
the token-gated router endpoints. The rig (Xvfb/x11vnc/websockify/Chromium) is
stubbed, so nothing here launches a real browser or process."""
import pytest

import music_service
import music_setup
import ytmusic_signin as ys
from routers import music_setup as ms_router


GOOD_COOKIES = [
    {"name": "__Secure-3PAPISID", "value": "secretval", "domain": ".youtube.com"},
    {"name": "SID", "value": "x", "domain": ".google.com"},
    {"name": "ignore_me", "value": "z", "domain": ".example.com"},  # dropped (not auth domain)
]
BAD_COOKIES = [{"name": "SID", "value": "x", "domain": ".google.com"}]  # no __Secure-3PAPISID


class _FakePage:
    async def evaluate(self, script):
        return ""  # username derivation best-effort → falls back to a label

    async def goto(self, url, **kw):
        return None


class _FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies
        self.closed = False
        self.pages = []

    async def cookies(self):
        return list(self._cookies)

    async def new_page(self):
        p = _FakePage()
        self.pages.append(p)
        return p

    async def close(self):
        self.closed = True


class _FakeProc:
    def __init__(self):
        self.terminated = False
        self.waited = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def kill(self):
        self.killed = True


@pytest.fixture(autouse=True)
def _reset_session(monkeypatch):
    # Isolate the module-global single-session slot + speed the watcher up.
    monkeypatch.setattr(ys, "_SESSION", None)
    monkeypatch.setattr(ys, "_POLL_S", 0.01)
    yield
    ys._SESSION = None


def _stub_rig(monkeypatch, cookies):
    """Make _bring_up_rig install a fake context + fake procs (no real browser)."""
    ctx = _FakeContext(cookies)
    procs = [_FakeProc(), _FakeProc(), _FakeProc()]

    async def fake_bring_up(session):
        session["context"] = ctx
        session["procs"] = procs
        session["view_url"] = "http://192.168.1.9:6080/vnc.html?autoconnect=1"

    monkeypatch.setattr(ys, "_bring_up_rig", fake_bring_up)
    return ctx, procs


# ── session state machine ────────────────────────────────────────────────────

async def test_connect_harvests_saves_and_tears_down(monkeypatch):
    ctx, procs = _stub_rig(monkeypatch, GOOD_COOKIES)
    saved = {}

    async def fake_save(domain, values, instance_id=None):
        saved["domain"] = domain
        saved["values"] = values
        return {"name": "YouTube Music"}

    monkeypatch.setattr(music_service, "save_provider", fake_save)
    monkeypatch.setattr(ys, "_store_username", lambda u: None)

    async def _no_instance(prov):
        return None
    monkeypatch.setattr(music_service, "provider_instance_id", _no_instance)

    res = await ys.start_session()
    assert res["ok"] and res["view_url"].endswith("autoconnect=1")
    await ys._SESSION["watcher"]  # let the watcher run to completion

    st = ys.session_status(res["session_id"])
    assert st["state"] == "connected"
    # cookie assembled from auth domains only + the required key present
    assert saved["domain"] == "ytmusic"
    assert "__Secure-3PAPISID=secretval" in saved["values"]["cookie"]
    assert "ignore_me" not in saved["values"]["cookie"]
    assert saved["values"]["username"]  # a label was supplied
    # browser torn down: context closed, every rig process terminated, view gone
    assert ctx.closed is True
    assert all(p.terminated for p in procs)
    assert st["view_url"] is None


async def test_missing_required_cookie_times_out_and_tears_down(monkeypatch):
    ctx, procs = _stub_rig(monkeypatch, BAD_COOKIES)
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 0.05)
    calls = {"n": 0}

    async def fake_save(domain, values, instance_id=None):
        calls["n"] += 1
        return {"name": "x"}

    monkeypatch.setattr(music_service, "save_provider", fake_save)

    res = await ys.start_session()
    await ys._SESSION["watcher"]

    st = ys.session_status(res["session_id"])
    assert st["state"] == "timeout"
    assert calls["n"] == 0  # never saved a cookie without __Secure-3PAPISID
    assert ctx.closed is True and all(p.terminated for p in procs)


async def test_one_session_at_a_time(monkeypatch):
    _stub_rig(monkeypatch, BAD_COOKIES)  # stays 'awaiting_login' (no valid cookie)
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 30)

    res1 = await ys.start_session()
    assert res1["ok"]
    res2 = await ys.start_session()
    assert res2["ok"] is False and res2["reason"] == "busy"

    await ys.cancel_session(res1["session_id"])  # tear the first one down


async def test_rig_failure_returns_error_and_tears_down(monkeypatch):
    async def boom(session):
        raise RuntimeError("missing sign-in binaries: Xvfb")

    monkeypatch.setattr(ys, "_bring_up_rig", boom)
    res = await ys.start_session()
    assert res["ok"] is False and res["reason"] == "rig_failed"


async def test_cancel_tears_down(monkeypatch):
    ctx, procs = _stub_rig(monkeypatch, BAD_COOKIES)
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 30)
    res = await ys.start_session()
    out = await ys.cancel_session(res["session_id"])
    assert out["ok"]
    assert ctx.closed is True and all(p.terminated for p in procs)


# ── refresh_now (anti-expiry, headless) ──────────────────────────────────────

async def test_refresh_opens_headless_harvests_saves_and_closes(monkeypatch, tmp_path):
    ctx = _FakeContext(GOOD_COOKIES)
    seen = {}

    async def fake_launch(headless=False):
        seen["headless"] = headless
        return ctx

    monkeypatch.setattr(ys, "_launch_browser", fake_launch)
    monkeypatch.setattr(ys, "PROFILE_DIR", tmp_path)  # exists() → True
    monkeypatch.setattr(ys, "_stored_username", lambda: "me@example.com")
    saved = {}

    async def fake_save(domain, values, instance_id=None):
        saved["domain"] = domain
        saved["values"] = values
        return {"name": "YouTube Music"}

    monkeypatch.setattr(music_service, "save_provider", fake_save)

    async def _existing_instance(prov):
        return "ytmusic--abc123"
    monkeypatch.setattr(music_service, "provider_instance_id", _existing_instance)

    r = await ys.refresh_now()
    assert r["ok"] is True
    assert seen["headless"] is True  # never a resident/headful browser
    assert ctx.closed is True  # closed promptly
    assert saved["domain"] == "ytmusic"
    assert saved["values"]["username"] == "me@example.com"
    assert "__Secure-3PAPISID=secretval" in saved["values"]["cookie"]


async def test_refresh_skips_when_signin_active(monkeypatch):
    ys._SESSION = {"state": "awaiting_login"}
    called = {"n": 0}

    async def fake_launch(headless=False):
        called["n"] += 1
        return _FakeContext(GOOD_COOKIES)

    monkeypatch.setattr(ys, "_launch_browser", fake_launch)
    r = await ys.refresh_now()
    assert r.get("skipped") == "signin_in_progress"
    assert called["n"] == 0  # never fought the sign-in for the profile lock


async def test_refresh_no_profile_returns_not_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(ys, "PROFILE_DIR", tmp_path / "does-not-exist")
    r = await ys.refresh_now()
    assert r["ok"] is False


# ── router endpoints (token-gated) ───────────────────────────────────────────

async def test_browser_start_gated_by_token(monkeypatch):
    async def up(url):
        return True

    monkeypatch.setattr(music_service, "_potoken_reachable", up)
    started = {"n": 0}

    async def fake_start():
        started["n"] += 1
        return {"ok": True, "session_id": "sid", "view_url": "http://lan:6080/x", "expires_in": 300}

    monkeypatch.setattr(ys, "start_session", fake_start)

    # invalid token → refused, rig never started
    r = await ms_router.browser_start({"token": "bad", "provider": "ytmusic"})
    assert r["ok"] is False and started["n"] == 0

    # valid token → rig started, view url returned
    tok = music_setup.mint("ytmusic")["token"]
    r2 = await ms_router.browser_start({"token": tok, "provider": "ytmusic"})
    assert r2["ok"] is True and r2["session_id"] == "sid" and started["n"] == 1

    # wrong provider for a browser sign-in → refused
    tok_sp = music_setup.mint("spotify")["token"]
    r3 = await ms_router.browser_start({"token": tok_sp, "provider": "spotify"})
    assert r3["ok"] is False and started["n"] == 1


async def test_browser_start_blocks_when_potoken_down(monkeypatch):
    async def down(url):
        return False

    monkeypatch.setattr(music_service, "_potoken_reachable", down)
    started = {"n": 0}

    async def fake_start():
        started["n"] += 1
        return {"ok": True, "session_id": "sid", "view_url": "x"}

    monkeypatch.setattr(ys, "start_session", fake_start)
    tok = music_setup.mint("ytmusic")["token"]
    r = await ms_router.browser_start({"token": tok, "provider": "ytmusic"})
    assert r["ok"] is False and "helper isn't running" in r["reason"] and started["n"] == 0


async def test_browser_status_consumes_token_on_connected(monkeypatch):
    monkeypatch.setattr(
        ys, "session_status",
        lambda sid: {"ok": True, "state": "connected", "name": "YouTube Music", "view_url": None})
    tok = music_setup.mint("ytmusic")["token"]
    st = await ms_router.browser_status("sid", tok)
    assert st["state"] == "connected"
    assert music_setup.verify(tok) is None  # single-use token spent on success


async def test_browser_status_does_not_consume_while_pending(monkeypatch):
    monkeypatch.setattr(
        ys, "session_status",
        lambda sid: {"ok": True, "state": "awaiting_login", "view_url": "x"})
    tok = music_setup.mint("ytmusic")["token"]
    st = await ms_router.browser_status("sid", tok)
    assert st["state"] == "awaiting_login"
    assert music_setup.verify(tok) is not None  # still valid until connected
