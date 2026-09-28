"""YouTube Music one-tap sign-in — session state machine, teardown, refresh, and
the token-gated router endpoints. The rig (Xvfb/x11vnc/websockify/Chromium) is
stubbed, so nothing here launches a real browser or process."""
import pytest

import music_service
import music_setup
import ytmusic_signin as ys
from routers import music_setup as ms_router

pytestmark = pytest.mark.ci_safe

# The real validator, captured before the autouse fixture stubs it out.
_REAL_VALIDATE = ys._validate_cookie


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
    """A browser context whose cookie jar can change mid-session: the first
    ``after`` reads return ``cookies`` (what the profile held at start), later
    reads return ``then`` (a login that happened during the session)."""

    def __init__(self, cookies, then=None, after=1):
        self._cookies = list(cookies)
        self._then = None if then is None else list(then)
        self._after = after
        self.reads = 0
        self.cleared = []
        self.clear_fails = False
        self.closed = False
        self.pages = []

    async def cookies(self):
        self.reads += 1
        if self._then is not None and self.reads > self._after:
            return list(self._then)
        return list(self._cookies)

    async def clear_cookies(self, *, name=None, domain=None, path=None):
        if self.clear_fails:
            raise RuntimeError("profile locked")
        self.cleared.append(domain)
        self._cookies = [c for c in self._cookies if not domain.search(c["domain"])]

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


@pytest.fixture(autouse=True)
def _no_network_validation(monkeypatch):
    """Cookie validation is a real YouTube request — never in a unit test. A
    test that reaches it without declaring a verdict fails loudly."""
    async def _undeclared(header):
        raise AssertionError("test reached _validate_cookie without declaring a verdict")
    monkeypatch.setattr(ys, "_validate_cookie", _undeclared)


def _validator(monkeypatch, *verdicts, default=None):
    """Stub _validate_cookie: returns ``verdicts`` in order, then ``default``.
    Records the __Secure-3PAPISID value of every header it was asked about."""
    seen = []
    queue = list(verdicts)

    async def fake(header):
        seen.append(ys._cookie_value(header, ys.REQUIRED_COOKIE))
        return queue.pop(0) if queue else default

    monkeypatch.setattr(ys, "_validate_cookie", fake)
    return seen


def _no_save(monkeypatch):
    calls = []

    async def fake_save(domain, values, instance_id=None):
        calls.append(values)
        return {"name": "YouTube Music"}

    monkeypatch.setattr(music_service, "save_provider", fake_save)
    return calls


async def _let_watcher_poll(n=10):
    import asyncio
    for _ in range(n):
        await asyncio.sleep(0.01)


def _stub_rig(monkeypatch, cookies, then=None, after=1):
    """Make _bring_up_rig install a fake context + fake procs (no real browser)."""
    ctx = _FakeContext(cookies, then=then, after=after)
    procs = [_FakeProc(), _FakeProc(), _FakeProc()]

    async def fake_bring_up(session):
        session["context"] = ctx
        session["procs"] = procs
        session["view_url"] = "http://192.168.1.9:6080/vnc.html?autoconnect=1"

    monkeypatch.setattr(ys, "_bring_up_rig", fake_bring_up)
    return ctx, procs


# ── session state machine ────────────────────────────────────────────────────

async def test_connect_harvests_saves_and_tears_down(monkeypatch):
    # Clean profile at start; the person signs in; YouTube confirms the login.
    ctx, procs = _stub_rig(monkeypatch, [], then=GOOD_COOKIES)
    seen = _validator(monkeypatch, True)
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
    assert seen == ["secretval"]  # validated before it was saved


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
    _validator(monkeypatch, True)
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


# ── "cookie present" is NOT "login happened" (live incident 2026-09-28) ───────
# The persistent profile still held a rotated, dead __Secure-3PAPISID. Every
# attempt "harvested" it 13-17 s after start, saved it to MA, and tore the view
# down before the person had opened it. The watcher must only harvest a login
# that happened DURING the session and that YouTube confirms is signed in.

STALE_COOKIES = [
    {"name": "__Secure-3PAPISID", "value": "stale-old-value", "domain": ".youtube.com"},
    {"name": "SID", "value": "old-sid", "domain": ".google.com"},
    {"name": "keep_me", "value": "k", "domain": ".example.com"},  # not a Google/YouTube cookie
]
FRESH_COOKIES = [
    {"name": "__Secure-3PAPISID", "value": "fresh-new-value", "domain": ".youtube.com"},
    {"name": "SID", "value": "new-sid", "domain": ".google.com"},
]


async def test_unchanged_cookie_is_never_harvested(monkeypatch):
    # YouTube said the profile's cookie is signed out, and the wipe failed, so
    # the stale cookie is still in the jar. Even if a later check would now say
    # "signed in", an UNCHANGED cookie is not a login that happened in this
    # session: it is never re-validated and never saved.
    ctx, procs = _stub_rig(monkeypatch, STALE_COOKIES)
    ctx.clear_fails = True
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 30)
    monkeypatch.setattr(ys, "_BASELINE_RETRY_S", 0.0)
    seen = _validator(monkeypatch, False, default=True)
    saves = _no_save(monkeypatch)

    res = await ys.start_session()
    await _let_watcher_poll()

    st = ys.session_status(res["session_id"])
    assert st["state"] == "stale_cookie_cleared"
    assert st["view_url"]  # the view is still up for the person
    assert saves == []
    assert ctx.closed is False and not any(p.terminated for p in procs)
    assert ctx.reads > 3  # the watcher kept polling
    assert seen == ["stale-old-value"]  # only the start check asked
    await ys.cancel_session(res["session_id"])


async def test_inconclusive_start_check_is_retried_and_a_live_login_saved(monkeypatch):
    # A transient outage at start must not decide the session: the unchanged
    # cookie is retried and saved once YouTube confirms it is signed in.
    ctx, _procs = _stub_rig(monkeypatch, GOOD_COOKIES)
    monkeypatch.setattr(ys, "_BASELINE_RETRY_S", 0.0)
    seen = _validator(monkeypatch, None, None, True)
    saves = _no_save(monkeypatch)
    monkeypatch.setattr(ys, "_store_username", lambda u: None)

    async def _no_instance(prov):
        return None
    monkeypatch.setattr(music_service, "provider_instance_id", _no_instance)

    res = await ys.start_session()
    await ys._SESSION["watcher"]
    assert ys.session_status(res["session_id"])["state"] == "connected"
    assert len(saves) == 1 and seen == ["secretval"] * 3
    assert ctx.closed is True


async def test_inconclusive_start_check_retried_then_stale_is_cleared(monkeypatch):
    ctx, _procs = _stub_rig(monkeypatch, STALE_COOKIES)
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 30)
    monkeypatch.setattr(ys, "_BASELINE_RETRY_S", 0.0)
    _validator(monkeypatch, None, False)
    saves = _no_save(monkeypatch)
    res = await ys.start_session()
    await _let_watcher_poll()
    assert ys.session_status(res["session_id"])["state"] == "stale_cookie_cleared"
    assert [c["name"] for c in await ctx.cookies()] == ["keep_me"]
    assert saves == []
    await ys.cancel_session(res["session_id"])


async def test_inconclusive_start_check_backs_off_and_never_harvests_unverified(monkeypatch):
    ctx, procs = _stub_rig(monkeypatch, STALE_COOKIES)
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 30)
    monkeypatch.setattr(ys, "_BASELINE_RETRY_S", 3600.0)
    seen = _validator(monkeypatch, default=None)
    saves = _no_save(monkeypatch)
    res = await ys.start_session()
    await _let_watcher_poll()
    assert ys.session_status(res["session_id"])["state"] == "awaiting_login"
    assert saves == [] and seen == ["stale-old-value"]  # no retry inside the backoff
    assert ctx.closed is False and not any(p.terminated for p in procs)
    await ys.cancel_session(res["session_id"])


async def test_changed_cookie_validated_is_harvested_saved_and_torn_down(monkeypatch):
    ctx, procs = _stub_rig(monkeypatch, STALE_COOKIES, then=FRESH_COOKIES, after=3)
    seen = _validator(monkeypatch, None, True)  # start: inconclusive; fresh: signed in
    saves = _no_save(monkeypatch)
    monkeypatch.setattr(ys, "_store_username", lambda u: None)

    async def _no_instance(prov):
        return None
    monkeypatch.setattr(music_service, "provider_instance_id", _no_instance)

    res = await ys.start_session()
    await ys._SESSION["watcher"]

    assert ys.session_status(res["session_id"])["state"] == "connected"
    assert len(saves) == 1 and "__Secure-3PAPISID=fresh-new-value" in saves[0]["cookie"]
    assert "stale-old-value" not in saves[0]["cookie"]
    assert seen == ["stale-old-value", "fresh-new-value"]
    assert ctx.closed is True and all(p.terminated for p in procs)


async def test_changed_cookie_failing_validation_is_not_saved_and_stays_up(monkeypatch):
    ctx, procs = _stub_rig(monkeypatch, [], then=FRESH_COOKIES)
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 30)
    seen = _validator(monkeypatch, default=False)
    saves = _no_save(monkeypatch)

    res = await ys.start_session()
    await _let_watcher_poll()

    assert ys.session_status(res["session_id"])["state"] == "awaiting_login"
    assert saves == []
    assert ctx.closed is False and not any(p.terminated for p in procs)
    assert seen == ["fresh-new-value"]  # a rejected cookie is not re-asked every poll
    await ys.cancel_session(res["session_id"])


async def test_no_cookie_at_start_then_cookie_appears_is_harvested(monkeypatch):
    ctx, _procs = _stub_rig(monkeypatch, [], then=FRESH_COOKIES, after=2)
    seen = _validator(monkeypatch, True)
    saves = _no_save(monkeypatch)
    monkeypatch.setattr(ys, "_store_username", lambda u: None)

    async def _no_instance(prov):
        return None
    monkeypatch.setattr(music_service, "provider_instance_id", _no_instance)

    res = await ys.start_session()
    await ys._SESSION["watcher"]
    assert ys.session_status(res["session_id"])["state"] == "connected"
    assert len(saves) == 1 and seen == ["fresh-new-value"]


async def test_stale_cookie_at_start_is_cleared_and_surfaced(monkeypatch):
    ctx, procs = _stub_rig(monkeypatch, STALE_COOKIES)
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 30)
    seen = _validator(monkeypatch, False)
    saves = _no_save(monkeypatch)
    notices = []

    async def on_progress(detail):
        notices.append(detail)

    async def on_done(ok, detail):
        pass

    res = await ys.start_session()
    ys.watch(res["session_id"], on_done, on_progress=on_progress)
    await _let_watcher_poll()

    st = ys.session_status(res["session_id"])
    assert st["state"] == "stale_cookie_cleared" and st["view_url"]
    # only the Google/YouTube cookies were wiped — never the rest of the profile
    assert [c["name"] for c in await ctx.cookies()] == ["keep_me"]
    assert ctx.cleared and all(r.search("accounts.google.com") and r.search(".youtube.com")
                               and not r.search("example.com") for r in ctx.cleared)
    assert notices and notices[0] == ys.STALE_DETAIL
    assert saves == [] and seen == ["stale-old-value"]
    assert ctx.closed is False and not any(p.terminated for p in procs)
    # still one-at-a-time, and the refresh path still yields to it
    assert (await ys.start_session())["reason"] == "busy"
    assert (await ys.refresh_now()).get("skipped") == "signin_in_progress"
    await ys.cancel_session(res["session_id"])


async def test_stale_notice_reaches_a_late_watcher(monkeypatch):
    # The router registers watch() after start_session returns; if the start
    # check already wiped the cookie by then, the notice still goes out.
    _stub_rig(monkeypatch, STALE_COOKIES)
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 30)
    _validator(monkeypatch, False)
    _no_save(monkeypatch)
    res = await ys.start_session()
    await _let_watcher_poll(3)
    assert ys.session_status(res["session_id"])["state"] == "stale_cookie_cleared"
    notices = []

    async def on_progress(detail):
        notices.append(detail)

    async def on_done(ok, detail):
        pass

    ys.watch(res["session_id"], on_done, on_progress=on_progress)
    await ys._SESSION["progress_task"]
    assert notices == [ys.STALE_DETAIL]
    await ys.cancel_session(res["session_id"])


async def test_live_login_already_in_profile_is_saved(monkeypatch):
    # A profile cookie YouTube confirms is signed in IS a login — saving it
    # beats leaving the person on a signed-in page until the timeout.
    ctx, procs = _stub_rig(monkeypatch, GOOD_COOKIES)
    _validator(monkeypatch, True)
    saves = _no_save(monkeypatch)
    monkeypatch.setattr(ys, "_store_username", lambda u: None)

    async def _no_instance(prov):
        return None
    monkeypatch.setattr(music_service, "provider_instance_id", _no_instance)

    res = await ys.start_session()
    await ys._SESSION["watcher"]
    assert ys.session_status(res["session_id"])["state"] == "connected"
    assert len(saves) == 1 and ctx.closed is True


async def test_unchanged_stale_cookie_times_out_and_tears_down(monkeypatch):
    ctx, procs = _stub_rig(monkeypatch, STALE_COOKIES)
    monkeypatch.setattr(ys, "SESSION_TIMEOUT_S", 0.05)
    _validator(monkeypatch, None)
    saves = _no_save(monkeypatch)
    res = await ys.start_session()
    await ys._SESSION["watcher"]
    assert ys.session_status(res["session_id"])["state"] == "timeout"
    assert saves == []
    assert ctx.closed is True and all(p.terminated for p in procs)


async def test_refresh_never_pushes_a_signed_out_cookie(monkeypatch, tmp_path):
    ctx = _FakeContext(STALE_COOKIES)

    async def fake_launch(headless=False):
        return ctx

    monkeypatch.setattr(ys, "_launch_browser", fake_launch)
    monkeypatch.setattr(ys, "PROFILE_DIR", tmp_path)
    _validator(monkeypatch, False)
    saves = _no_save(monkeypatch)
    r = await ys.refresh_now()
    assert r["ok"] is False and "expired" in r["reason"]
    assert saves == [] and ctx.closed is True


async def test_refresh_never_pushes_an_unverified_cookie(monkeypatch, tmp_path):
    async def fake_launch(headless=False):
        return _FakeContext(STALE_COOKIES)

    monkeypatch.setattr(ys, "_launch_browser", fake_launch)
    monkeypatch.setattr(ys, "PROFILE_DIR", tmp_path)
    _validator(monkeypatch, None)
    saves = _no_save(monkeypatch)
    r = await ys.refresh_now()
    assert r["ok"] is False and saves == []


async def test_a_late_stale_notice_lands_before_the_outcome():
    # watch() fires the notice as a task; the outcome report must wait for it,
    # or the handoff card would go error → completing ("sign in again").
    import asyncio
    order = []
    gate = asyncio.Event()

    async def on_progress(detail):
        await gate.wait()
        order.append("progress")

    async def on_done(ok, detail):
        order.append("done")

    ys._SESSION = {"id": "ytm-x", "state": "stale_cookie_cleared", "watcher": None}
    ys.watch("ytm-x", on_done, on_progress=on_progress)
    await asyncio.sleep(0)  # the notice is in flight (blocked on the gate)
    ys._SESSION["state"] = "timeout"
    report = asyncio.create_task(ys._report(ys._SESSION))
    await asyncio.sleep(0.01)
    assert order == []  # the outcome is held behind the in-flight notice
    gate.set()
    await report
    assert order == ["progress", "done"]


# ── the validator: YouTube's own logged_in / yt_li flags ─────────────────────

# Trimmed from a live logged-out youtubei/v1/account/account_menu response
# (2026-09-28, sent with a bogus cookie): HTTP 200, both flags "0".
LOGGED_OUT_RESPONSE = {"responseContext": {"serviceTrackingParams": [
    {"service": "CSI", "params": [{"key": "c", "value": "WEB_REMIX"},
                                  {"key": "yt_li", "value": "0"}]},
    {"service": "GFEEDBACK", "params": [{"key": "logged_in", "value": "0"}]},
]}}


def _with_flags(yt_li, logged_in):
    import copy
    data = copy.deepcopy(LOGGED_OUT_RESPONSE)
    svc = data["responseContext"]["serviceTrackingParams"]
    svc[0]["params"][1]["value"] = yt_li
    svc[1]["params"][0]["value"] = logged_in
    return data


def test_logged_in_verdict_reads_youtubes_own_flags():
    assert ys._logged_in_verdict(LOGGED_OUT_RESPONSE) is False
    assert ys._logged_in_verdict(_with_flags("1", "1")) is True
    assert ys._logged_in_verdict(_with_flags("1", "0")) is None  # disagreement → can't tell
    assert ys._logged_in_verdict({"responseContext": {}}) is None
    assert ys._logged_in_verdict({"error": {"code": 400}}) is None
    assert ys._logged_in_verdict(None) is None


def _mock_http(monkeypatch, handler):
    import httpx
    real = httpx.AsyncClient
    requests = []

    def record(request):
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(ys.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(record), **kw))
    return requests


async def test_validate_cookie_sends_the_ma_auth_shape_and_reads_the_verdict(monkeypatch):
    import httpx
    monkeypatch.setattr(ys, "_validate_cookie", _REAL_VALIDATE)
    header = "SID=s; __Secure-3PAPISID=abc/DEF"
    reqs = _mock_http(monkeypatch, lambda r: httpx.Response(200, json=_with_flags("1", "1")))
    assert await ys._validate_cookie(header) is True
    req = reqs[0]
    assert req.url.host == "music.youtube.com" and req.url.path.endswith("/account/account_menu")
    assert req.headers["cookie"] == header
    ts, digest = req.headers["authorization"].removeprefix("SAPISIDHASH ").split("_")
    import hashlib
    assert digest == hashlib.sha1(f"{ts} abc/DEF https://music.youtube.com".encode()).hexdigest()


@pytest.mark.parametrize("status,body,expected", [
    (200, LOGGED_OUT_RESPONSE, False),
    (401, {}, False),
    (403, {}, False),
    (500, {}, None),
    (200, None, None),  # not JSON
])
async def test_validate_cookie_verdicts(monkeypatch, status, body, expected):
    import httpx
    monkeypatch.setattr(ys, "_validate_cookie", _REAL_VALIDATE)
    _mock_http(monkeypatch, lambda r: (httpx.Response(status, json=body) if body is not None
                                       else httpx.Response(status, text="<html>")))
    assert await ys._validate_cookie("__Secure-3PAPISID=v") is expected


async def test_validate_cookie_network_failure_is_inconclusive_and_no_cookie_is_invalid(monkeypatch):
    import httpx
    monkeypatch.setattr(ys, "_validate_cookie", _REAL_VALIDATE)

    def boom(request):
        raise httpx.ConnectError("no route")

    reqs = _mock_http(monkeypatch, boom)
    assert await ys._validate_cookie("__Secure-3PAPISID=v") is None
    assert await ys._validate_cookie("SID=only") is False
    assert len(reqs) == 1  # no request without the login cookie


# ── CloakBrowser's per-launch update check is off (no pypi/github on the hot path)

async def test_launch_disables_cloakbrowser_update_check(monkeypatch, tmp_path):
    import os
    import sys
    import types
    monkeypatch.delenv("CLOAKBROWSER_AUTO_UPDATE", raising=False)
    monkeypatch.setattr(ys, "SECRET_DIR", tmp_path)
    monkeypatch.setattr(ys, "PROFILE_DIR", tmp_path / "profile")
    seen = {}

    async def fake_launch(profile, **kw):
        import os
        seen["auto_update"] = os.environ.get("CLOAKBROWSER_AUTO_UPDATE")
        return _FakeContext([])

    monkeypatch.setitem(sys.modules, "cloakbrowser",
                        types.SimpleNamespace(launch_persistent_context_async=fake_launch))
    try:
        await ys._launch_browser(headless=True)
    finally:
        # _launch_browser writes os.environ directly — monkeypatch can't undo it.
        os.environ.pop("CLOAKBROWSER_AUTO_UPDATE", None)
    assert seen["auto_update"] == "false"


# ── the router surfaces the stale-cookie notice on the panel's handoff card ──

async def test_browser_start_wires_the_stale_notice_to_the_handoff(monkeypatch):
    import auth_handoff

    async def up(url):
        return True

    monkeypatch.setattr(music_service, "_potoken_reachable", up)

    async def fake_start():
        return {"ok": True, "session_id": "sid", "view_url": "http://lan:6080/x", "expires_in": 300}

    monkeypatch.setattr(ys, "start_session", fake_start)
    marks = []

    async def fake_mark(kind, ref, new, detail=""):
        marks.append((kind, new, detail))

    monkeypatch.setattr(auth_handoff, "mark_ref", fake_mark)
    registered = {}
    monkeypatch.setattr(ys, "watch", lambda sid, on_done, on_progress=None:
                        registered.update(sid=sid, on_progress=on_progress))
    tok = music_setup.mint("ytmusic")["token"]
    r = await ms_router.browser_start({"token": tok, "provider": "ytmusic"})
    assert r["ok"] is True and registered["sid"] == "sid"
    await registered["on_progress"](ys.STALE_DETAIL)
    assert marks[-1] == ("music", "completing", ys.STALE_DETAIL)
