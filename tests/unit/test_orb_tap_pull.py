"""Orb tap with the "I have something" dot showing = collect it, spoken (owner request 2026-11-02).

  * daemon: POST /pull (same loopback guard as /activate) queues a pull turn; the turn is a fixed "what's up" TRANSCRIPT sent through
    ``_do_single_turn`` (no audio, no claim, no STT) to /api/voice/command and played like any turn; /activate is unchanged.
  * kiosk: real home.html JS run under node - the orb click POSTs /pull when the orb has the dot, /activate when it has not.
"""
from __future__ import annotations

import base64
import http.client
import importlib.util
import json
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.ci_safe

_REPO = Path(__file__).resolve().parents[2]
_DAEMON = _REPO / "scripts" / "setup" / "zoe_voice_daemon.py"
_HOME = _REPO / "services" / "zoe-ui" / "dist" / "touch" / "home.html"


@pytest.fixture()
def daemon(monkeypatch):
    for var in ("PANEL_PLATFORM", "ACTIVATE_GUARD", "ACTIVATE_ALLOWED_ORIGINS", "ZOE_URL", "VOICE_ROUTE_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEVICE_TOKEN", "tok-test")
    stubs = {n: MagicMock() for n in ("pyaudio",) if n not in sys.modules}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("zoe_voice_daemon_orb_pull_under_test", _DAEMON)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for n in stubs:
            sys.modules.pop(n, None)
    monkeypatch.setattr(mod, "ZOE_URL", "https://192.168.1.218")
    return mod


def _post(d, path, headers=None):
    srv = socketserver.TCPServer(("127.0.0.1", 0), d._HealthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        d._orb_activate_event.clear()
        d._orb_pull_event.clear()
        conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        conn.request("POST", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, body, d._orb_activate_event.is_set(), d._orb_pull_event.is_set()
    finally:
        srv.shutdown()
        srv.server_close()


def test_pull_queues_a_pull_turn_not_a_listen(daemon):
    status, body, activate, pull = _post(daemon, "/pull")
    assert status == 200 and json.loads(body)["triggered"] == "pull"
    assert (activate, pull) == (False, True)


def test_activate_is_unchanged(daemon):
    status, body, activate, pull = _post(daemon, "/activate")
    assert status == 200 and json.loads(body)["triggered"] == "wake"
    assert (activate, pull) == (True, False)


def test_pull_has_the_same_kiosk_only_guard_as_activate(daemon):
    assert _post(daemon, "/pull", {"Origin": "https://evil.example"})[0::2] == (403, False)
    assert _post(daemon, "/pull", {"Origin": "https://192.168.1.218"})[3] is True
    assert _post(daemon, "/pull", {"Host": "evil.example"})[0] == 403


def test_pull_on_the_bridge_route_is_still_a_pull(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "VOICE_ROUTE_MODE", "ha_bridge")
    _status, _body, activate, pull = _post(daemon, "/pull")
    assert (activate, pull) == (False, True)


def test_a_throttled_pull_stays_queued_and_runs_when_due(daemon, monkeypatch):
    """Cross-review: a tap inside MIN_WAKE_INTERVAL_S of the last wake was cleared and dropped. It must wait, then run once."""
    clock = [100.0]
    monkeypatch.setattr(daemon.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(daemon, "_last_wake_at", 99.0)
    daemon._orb_pull_event.set()
    assert daemon._pull_request_due() is False and daemon._orb_pull_event.is_set()
    clock[0] = 99.0 + daemon.MIN_WAKE_INTERVAL_S + 0.1
    assert daemon._pull_request_due() is True and not daemon._orb_pull_event.is_set()
    assert daemon._pull_request_due() is False       # consumed once
    assert daemon._last_wake_at == clock[0]


def test_the_main_loop_runs_the_pull_turn_when_due():
    src = _DAEMON.read_text()
    loop = src.split("if _pull_request_due():", 1)[1].split("continue", 1)[0]
    assert "_run_pull_turn(pa)" in loop


def test_the_pull_turn_runs_guarded_and_under_a_barge_monitor(daemon, monkeypatch):
    """Cross-review: the pull turn must (1) raise the turn-active flag BEFORE dispatch so announcements defer, (2) run the turn under a
    barge monitor so speech interrupts the reply, and (3) arm the wake cooldown after, releasing the flag."""
    seen = {}

    class Mon:
        def __init__(self, pa):
            seen["order"] = ["init"]

        def start(self):
            seen["order"].append("start")

        def stop(self):
            seen["order"].append("stop")

    def turn(pa, wav, **kw):
        seen["order"].append("turn")
        seen["active"] = daemon._recording_active.is_set()
        seen["kw"] = kw
        return True

    monkeypatch.setattr(daemon, "_BargeMonitor", Mon)
    monkeypatch.setattr(daemon, "_do_single_turn", turn)
    monkeypatch.setattr(daemon, "_ignore_wake_until", 0.0)
    daemon._recording_active.clear()
    daemon._run_pull_turn(None)
    assert seen["order"] == ["init", "start", "turn", "stop"]
    assert seen["active"] is True and seen["kw"] == {"prompt_on_empty": False, "text": daemon.PULL_PHRASE}
    assert not daemon._recording_active.is_set() and daemon._ignore_wake_until > 0.0


def test_a_failing_pull_turn_still_stops_the_monitor_and_releases_the_flag(daemon, monkeypatch):
    stopped = []

    class Mon:
        def __init__(self, pa): ...
        def start(self): ...
        def stop(self): stopped.append(True)

    monkeypatch.setattr(daemon, "_BargeMonitor", Mon)
    monkeypatch.setattr(daemon, "_do_single_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        daemon._run_pull_turn(None)
    assert stopped == [True] and not daemon._recording_active.is_set()


def test_the_pull_phrase_is_a_pull_to_the_server():
    sys.path.insert(0, str(_REPO / "services" / "zoe-data"))
    import proactive.pull as pl
    src = _DAEMON.read_text()
    phrase = src.split('PULL_PHRASE = "', 1)[1].split('"', 1)[0]
    assert pl.classify(phrase) == "pull"


def test_text_turn_goes_through_the_same_send_and_playback_path(daemon, monkeypatch):
    """The pull turn is a TEXT turn: /api/voice/command, the fixed phrase, no audio and NO voice claim / speaker block
    (a tap proves nothing about who touched it), and the reply audio is played by the ordinary player."""
    posts, played = [], []
    monkeypatch.setattr(daemon, "_api_post", lambda path, data, **k: posts.append((path, data)) or
                        {"ok": True, "reply": "r", "audio_base64": base64.b64encode(b"RIFF").decode(), "content_type": "audio/wav"})
    monkeypatch.setattr(daemon, "play_audio_b64", lambda b64, ct="audio/wav": played.append(b64) or True)
    monkeypatch.setattr(daemon, "_speaker_claim_to_attach", lambda w: pytest.fail("a pull must not score a voice"))
    assert daemon._do_single_turn(None, b"", prompt_on_empty=False, text=daemon.PULL_PHRASE) is True
    (path, data), = posts
    assert path == "/api/voice/command"
    assert data == {"text": daemon.PULL_PHRASE, "panel_id": daemon.PANEL_ID}
    assert len(played) == 1


def test_a_recorded_turn_still_uses_the_audio_endpoint(daemon, monkeypatch):
    posts = []
    monkeypatch.setattr(daemon, "_api_post", lambda path, data, **k: posts.append((path, data)) or {"ok": True, "text": "hi"})
    monkeypatch.setattr(daemon, "_speaker_claim_to_attach", lambda w: None)
    daemon._do_single_turn(None, b"RIFFwav", prompt_on_empty=False)
    assert posts[0][0] == "/api/voice/turn" and "audio_base64" in posts[0][1] and "text" not in posts[0][1]


_ORB_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const body = 'var _orbListenT=null' + src.split('var _orbListenT=null')[1].split("// pull, not push: the orb's quiet")[0];
const has = process.argv[2] === 'has';
const cls = new Set(has ? ['has'] : []);
const orb = {classList: {add: c => cls.add(c), remove: c => cls.delete(c), contains: c => cls.has(c), toggle(c, on){ on ? cls.add(c) : cls.delete(c); }}, addEventListener(e, f){ this.click = f; }};
const cmdbar = {classList: {add(){}, remove(){}}}, cmdin = {focus(){}};
const calls = [];
const fetch = (url, opts) => { calls.push(url); return Promise.resolve({type: 'opaque'}); };
const AbortSignal = {timeout: () => undefined};
const window = {__kiosk: true}, toast = () => {}, toastHide = () => {};
let _convLive = false;
eval(body);
orb.click();
setTimeout(() => { console.log(JSON.stringify({calls, busy: cls.has('busy'), listening: cls.has('listening')})); process.exit(0); }, 20);
"""


def _tap(mode):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    out = subprocess.run([node, "-e", _ORB_HARNESS, str(_HOME), mode], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_orb_tap_with_the_dot_posts_pull_and_shows_busy():
    r = _tap("has")
    assert r["calls"] == ["http://localhost:7777/pull"] and r["busy"] is True and r["listening"] is False


def test_orb_tap_without_the_dot_still_activates():
    r = _tap("plain")
    assert r["calls"] == ["http://localhost:7777/activate"] and r["listening"] is True
