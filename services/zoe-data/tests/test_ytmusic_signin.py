"""YouTube Music one-tap sign-in — session state machine, teardown, refresh, and
the token-gated router endpoints. The rig (Xvfb/x11vnc/websockify/Chromium) is
stubbed, so nothing here launches a real browser or process."""
import pytest

import music_service
import music_setup
import ytmusic_signin as ys
from routers import music_setup as ms_router

pytestmark = pytest.mark.ci_safe


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


async def test_cancel_requires_exact_session_id(monkeypatch):
    # An empty / wrong id must NEVER tear down the active session (a token holder
    # who doesn't know the id can't kill someone else's sign-in).
    ctx, procs = _stub_rig(monkeypatch, BAD_COOKIES)
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 30)
    res = await ys.start_session()
    await ys.cancel_session("")             # empty → no-op
    assert ctx.closed is False and not any(p.terminated for p in procs)
    await ys.cancel_session("ytm-wrongid")  # wrong id → no-op
    assert ctx.closed is False
    await ys.cancel_session(res["session_id"])  # exact id → tears down
    assert ctx.closed is True and all(p.terminated for p in procs)


async def test_router_cancel_rejects_empty_session_id(monkeypatch):
    canceled = {"n": 0}

    async def fake_cancel(sid):
        canceled["n"] += 1
        return {"ok": True, "state": "x"}

    monkeypatch.setattr(ys, "cancel_session", fake_cancel)
    tok = music_setup.mint("ytmusic")["token"]
    # valid token but no session id → refused, cancel_session never called
    r = await ms_router.browser_cancel({"token": tok, "session_id": ""})
    assert r["ok"] is False and canceled["n"] == 0
    # valid token + id → forwarded
    r2 = await ms_router.browser_cancel({"token": tok, "session_id": "ytm-abc"})
    assert canceled["n"] == 1 and r2["ok"] is True


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


# ── the LAN viewer is locked to a per-session secret (auth audit 2026-09-27) ──
# Before: x11vnc -nopw behind a websockify that routed EVERY connection to the
# local VNC port — for ~5 min any LAN client could watch or drive the browser
# the user was typing their Google password into.

def test_websockify_has_no_fixed_target_only_the_token_file(tmp_path):
    argv = ys._websockify_argv("192.168.1.9", tmp_path / "tok", web="/usr/share/novnc")
    assert argv[argv.index("--token-plugin") + 1] == "TokenFile"
    assert argv[argv.index("--token-source") + 1] == str(tmp_path / "tok")
    # A positional target would route token-less connections straight to VNC.
    assert f"127.0.0.1:{ys._VNC_PORT}" not in argv
    assert argv[-1] == f"192.168.1.9:{ys._NOVNC_PORT}"


def test_view_url_carries_the_secret_only_in_the_fragment():
    from urllib.parse import parse_qs, unquote, urlsplit
    url = ys._view_url("192.168.1.9", "s3cr3t-Value_x")
    parts = urlsplit(url)
    assert "s3cr3t" not in parts.path and parts.query == ""  # nothing in the request line
    frag = parse_qs(parts.fragment)
    assert unquote(frag["path"][0]) == "websockify?token=s3cr3t-Value_x"
    assert frag["autoconnect"] == ["1"]


def test_viewer_token_file_is_private_and_removed(monkeypatch, tmp_path):
    import os
    import stat
    monkeypatch.setattr(ys, "SECRET_DIR", tmp_path)
    monkeypatch.setattr(ys, "_VIEWER_TOKEN_FILE", tmp_path / "viewer-token")
    path = ys._write_viewer_token("abc123")
    assert path.read_text() == f"abc123: 127.0.0.1:{ys._VNC_PORT}\n"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    ys._clear_viewer_token()
    assert not path.exists()
    ys._clear_viewer_token()  # idempotent


async def test_bring_up_mints_a_fresh_secret_and_teardown_revokes_it(monkeypatch, tmp_path):
    monkeypatch.setattr(ys, "SECRET_DIR", tmp_path)
    monkeypatch.setattr(ys, "_VIEWER_TOKEN_FILE", tmp_path / "viewer-token")
    monkeypatch.setattr(ys, "_require_binaries", lambda: [])
    monkeypatch.setattr(ys, "_lan_ip", lambda: "192.168.1.9")
    seen = {}

    def fake_stack(bind, token_file):
        seen["token_file"] = token_file
        return [_FakeProc()]

    async def fake_launch(headless=False):
        return _FakeContext(BAD_COOKIES)

    monkeypatch.setattr(ys, "_start_display_stack", fake_stack)
    monkeypatch.setattr(ys, "_launch_browser", fake_launch)

    urls = []
    for _ in range(2):
        session = {}
        await ys._bring_up_rig(session)
        secret = session["view_url"].rsplit("token%3D", 1)[1]
        assert seen["token_file"].read_text().startswith(secret + ": ")
        urls.append(session["view_url"])
        await ys._teardown(session)
        assert not seen["token_file"].exists()
        assert session["view_url"] is None
    assert urls[0] != urls[1]  # per-session, never reused


def _websockify_upgrade_status(port: int, query: str) -> str:
    """Send a WebSocket upgrade and return the status line ('' if refused)."""
    import socket
    req = (f"GET /websockify{query} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
           "Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n"
           "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
    with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
        s.sendall(req.encode())
        try:
            data = s.recv(256)
        except (ConnectionResetError, socket.timeout):
            data = b""
    return data.split(b"\r\n", 1)[0].decode(errors="replace")


def test_real_websockify_refuses_a_viewer_without_the_secret(monkeypatch, tmp_path):
    """End to end against the real websockify, with the argv and token file the
    rig uses: no secret / a wrong secret gets no WebSocket upgrade (the
    connection is dropped before any 101); the right secret is upgraded.
    Skips where websockify or loopback is unavailable (CI's slim venv,
    `unshare -rn`); run it on the box for the live proof."""
    import shutil
    import socket
    import subprocess
    import time
    if shutil.which("websockify") is None:
        pytest.skip("websockify not installed")
    try:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
    except OSError:
        pytest.skip("no loopback in this namespace")
    monkeypatch.setattr(ys, "SECRET_DIR", tmp_path)
    monkeypatch.setattr(ys, "_VIEWER_TOKEN_FILE", tmp_path / "viewer-token")
    monkeypatch.setattr(ys, "_NOVNC_PORT", port)
    token_file = ys._write_viewer_token("right-secret")
    proc = subprocess.Popen(ys._websockify_argv("127.0.0.1", token_file),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not ys._wait_port("127.0.0.1", port, timeout=10):
            pytest.skip("websockify did not start")
        assert "101" not in _websockify_upgrade_status(port, "")
        assert "101" not in _websockify_upgrade_status(port, "?token=wrong")
        assert "101" in _websockify_upgrade_status(port, "?token=right-secret")
        ys._clear_viewer_token()  # teardown revokes even a running websockify
        assert "101" not in _websockify_upgrade_status(port, "?token=right-secret")
    finally:
        proc.terminate()
        proc.wait(timeout=5)
