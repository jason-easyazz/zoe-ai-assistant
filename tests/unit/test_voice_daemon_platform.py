"""Platform backend of the panel voice daemon (`scripts/setup/zoe_voice_daemon.py`).

PANEL_PLATFORM=pi|mac|auto picks who plays audio and who ducks it. The contract
this file pins:

  * DEFAULT = THE PI, BYTE FOR BYTE. With PANEL_PLATFORM unset the daemon builds the
    exact aplay / mpg123 / espeak-ng argv, the exact request headers, the exact
    pactl ducker and binds the health server on every interface, as before the
    abstraction existed. The golden values below were read off the pre-change code;
    breaking any one of them (e.g. routing the Pi through the Mac backend) reddens
    a test here. The Mac module is not even imported on the Pi.
  * The Cloudflare Access header pair is added only when BOTH halves are set.
  * `mac` swaps the actuators and nothing else: the SAME `_BargeEpisode` /
    `_BargeDecider` / `_PlayoutLedger` run over an in-process player whose duck is
    a gain, and the scenarios of phase 1 (duck, commit, resume, ceiling, the
    VAD-failure sentinel, no-duck fallback) come out the same.

No audio device, no model, no network: PyAudio is faked, subprocess is faked.
"""
from __future__ import annotations

import base64
import importlib.util
import io
import logging
import subprocess
import sys
import threading
import time
import types
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_REPO = Path(__file__).resolve().parents[2]
_DAEMON_PATH = _REPO / "scripts" / "setup" / "zoe_voice_daemon.py"
_ENV = ("PANEL_PLATFORM", "HEALTH_BIND", "HEALTH_PORT", "CF_ACCESS_CLIENT_ID",
        "CF_ACCESS_CLIENT_SECRET", "AUDIO_DEVICE", "AUDIO_OUTPUT_DEVICE", "MIC_DEVICE_INDEX",
        "DEVICE_TOKEN", "BARGE_DUCK_ENABLED", "BARGE_DUCK_DB", "BARGE_DUCK_RAMP_MS",
        "BARGE_COMMIT_SPEECH_MS", "BARGE_RESUME_SILENCE_MS", "BARGE_DECIDE_MAX_MS",
        "BARGE_IN_THRESHOLD", "CHUNK_SIZE", "SAMPLE_RATE", "ZOE_VOICE_LOG", "WAKE_BEEP_ENABLED")


def _load_daemon(name: str):
    stubs = {n: MagicMock() for n in ("pyaudio",) if n not in sys.modules}
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, _DAEMON_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


@pytest.fixture()
def clean_env(monkeypatch):
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture()
def pi(clean_env):
    clean_env.setenv("DEVICE_TOKEN", "tok-pi")
    return _load_daemon("zoe_voice_daemon_platform_pi_under_test")


# ── fakes ────────────────────────────────────────────────────────────────────

class _FakeStream:
    def __init__(self, kw):
        self.kw, self.writes, self.closed, self.stopped = kw, [], False, False
        self.gate = None  # a threading.Event the test can hold the writer behind

    def write(self, data):
        if self.gate is not None:
            self.gate.wait(10)
        self.writes.append(bytes(data))

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _FakePA:
    paInt16 = 8

    def __init__(self, devices=None, refuse_rates=()):
        self.devices = devices or [{"name": "Built-in Output", "maxOutputChannels": 2,
                                    "maxInputChannels": 0, "defaultSampleRate": 48000.0}]
        self.refuse_rates = set(refuse_rates)
        self.streams: list[_FakeStream] = []
        self.gate = None

    def PyAudio(self):  # the module attribute the backend calls
        return self

    def open(self, **kw):
        if kw.get("rate") in self.refuse_rates:
            raise OSError(-9997, "Invalid sample rate")
        s = _FakeStream(kw)
        s.gate = self.gate
        self.streams.append(s)
        return s

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, i):
        return dict(self.devices[i], index=i)

    def get_default_output_device_info(self):
        return dict(self.devices[0], index=0)


def _wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _wav(rate=24000, ch=1, width=2, n=2400) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(ch)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(b"\x10\x00\x00"[:width] * ch * n if width != 2 else (np.full(n * ch, 800, dtype="<i2").tobytes()))
    return buf.getvalue()


# ── selection ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,system,want", [
    (None, "Linux", "pi"), ("", "Linux", "pi"), ("pi", "Linux", "pi"), ("PI", "Darwin", "pi"),
    ("mac", "Linux", "mac"), (" MAC ", "Darwin", "mac"),
    ("auto", "Darwin", "mac"), ("auto", "Linux", "pi"), ("AUTO", "Darwin", "mac"),
])
def test_platform_resolution(pi, raw, system, want):
    assert pi._resolve_panel_platform(raw, system) == want


def test_unknown_value_is_pi_and_loud(pi, caplog):
    caplog.set_level(logging.WARNING)
    assert pi._resolve_panel_platform("windows", "Linux") == "pi"
    assert any("PANEL_PLATFORM" in r.getMessage() and "windows" in r.getMessage() for r in caplog.records)


def test_unset_on_a_mac_stays_pi_but_says_why(pi, caplog):
    """The literal default is pi (owner decision); a Darwin host with nothing set is
    the one case where that can only be a mistake, so it is a WARNING, not a guess."""
    caplog.set_level(logging.WARNING)
    assert pi._resolve_panel_platform(None, "Darwin") == "pi"
    assert any("PANEL_PLATFORM=mac" in r.getMessage() for r in caplog.records)
    caplog.clear()
    assert pi._resolve_panel_platform("pi", "Darwin") == "pi"
    assert not caplog.records  # an explicit pi is the operator's call


# ── the Pi default is byte-identical (negative control for the abstraction) ───

def test_pi_default_builds_the_pre_abstraction_objects(pi):
    assert pi.PANEL_PLATFORM == "pi"
    assert type(pi._PLATFORM).__name__ == "_PiBackend" and pi._PLATFORM.name == "pi"
    assert pi.HEALTH_BIND == "" and pi._PLATFORM.has_panel_agent is True
    assert "zoe_mac_panel_backend" not in sys.modules  # the Mac module is never imported on the Pi
    assert pi._headers == {"X-Device-Token": "tok-pi", "Content-Type": "application/json"}
    assert pi._PLATFORM.local_tts_cmd("hello") == ["espeak-ng", "-s", "140", "-p", "44", "hello"]
    d = pi._PLATFORM.make_ducker(types.SimpleNamespace(pid=4242))
    assert isinstance(d, pi._SinkInputDucker) and d.pid == 4242


class _Rec:
    """Records Popen / run calls; the fake process exits 0 at once."""

    def __init__(self):
        self.calls: list[tuple] = []

    def popen(self, cmd, **kw):
        self.calls.append(("Popen", list(cmd), dict(kw)))
        p = MagicMock()
        p.poll.return_value = 0
        p.returncode = 0
        p.pid = 31337
        return p

    def run(self, cmd, **kw):
        self.calls.append(("run", list(cmd), dict(kw)))
        return subprocess.CompletedProcess(cmd, 0)


def _drive_every_call_site(d, monkeypatch, tmp_path):
    """Run each place the daemon starts a player, return what reached subprocess."""
    rec = _Rec()
    monkeypatch.setattr(d.subprocess, "Popen", rec.popen)
    monkeypatch.setattr(d.subprocess, "run", rec.run)
    monkeypatch.setattr(d, "_tts_process", None)
    monkeypatch.setattr(d, "_tts_started_at", None)
    wav_b64 = base64.b64encode(_wav()).decode()
    d.play_audio_b64(wav_b64, "audio/wav")
    d.play_audio_b64(wav_b64, "audio/mpeg")
    d.play_wake_beep()
    d.play_follow_up_beep()
    d._espeak_local("hi there")
    bufdir = tmp_path / "buffers"
    bufdir.mkdir(exist_ok=True)
    (bufdir / "buf_one.wav").write_bytes(_wav())
    monkeypatch.setattr(d, "_BUFFER_DIR", str(bufdir))
    monkeypatch.setattr(d, "_BUFFER_ENABLED", True)
    d._play_buffer_phrase()
    d._feed_pcm_chunk(None, _wav(rate=24000, ch=1))
    return rec.calls


def _normalise(calls):
    """Drop the temp-file argument (random per run) so argv can be compared."""
    out = []
    for kind, cmd, kw in calls:
        cmd = ["<FILE>" if (c.startswith("/") and c.endswith((".wav", ".mp3"))) else c for c in cmd]
        out.append((kind, cmd, kw))
    return out


def test_pi_default_argv_is_golden(pi, monkeypatch, tmp_path):
    """Every player the Pi starts, with AUDIO_OUTPUT_DEVICE unset (== "default")."""
    monkeypatch.setattr(pi, "AUDIO_OUTPUT_DEVICE", "default")
    monkeypatch.setattr(pi, "WAKE_BEEP_ENABLED", True)
    got = _normalise(_drive_every_call_site(pi, monkeypatch, tmp_path))
    assert got == [
        ("Popen", ["aplay", "-q", "<FILE>"], {}),
        ("Popen", ["mpg123", "-q", "<FILE>"], {}),
        ("run", ["aplay", "-q", "<FILE>"], {"check": False}),
        ("run", ["aplay", "-q", "<FILE>"], {"check": False, "timeout": 3}),
        ("run", ["espeak-ng", "-s", "140", "-p", "44", "hi there"], {"check": False, "timeout": 10}),
        ("Popen", ["aplay", "-q", "<FILE>"], {"stderr": subprocess.DEVNULL}),
        ("Popen", ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", "24000"],
         {"stdin": subprocess.PIPE}),
    ]


def test_pi_named_device_argv_is_golden(pi, monkeypatch, tmp_path):
    """AUDIO_OUTPUT_DEVICE=hw:2,0 (the Jabra): -D for aplay, -a for mpg123."""
    monkeypatch.setattr(pi, "AUDIO_OUTPUT_DEVICE", "hw:2,0")
    monkeypatch.setattr(pi, "WAKE_BEEP_ENABLED", True)
    got = _normalise(_drive_every_call_site(pi, monkeypatch, tmp_path))
    assert got == [
        ("Popen", ["aplay", "-q", "-D", "hw:2,0", "<FILE>"], {}),
        ("Popen", ["mpg123", "-q", "-a", "hw:2,0", "<FILE>"], {}),
        ("run", ["aplay", "-q", "-D", "hw:2,0", "<FILE>"], {"check": False}),
        ("run", ["aplay", "-q", "-D", "hw:2,0", "<FILE>"], {"check": False, "timeout": 3}),
        ("run", ["espeak-ng", "-s", "140", "-p", "44", "hi there"], {"check": False, "timeout": 10}),
        ("Popen", ["aplay", "-q", "-D", "hw:2,0", "<FILE>"], {"stderr": subprocess.DEVNULL}),
        ("Popen", ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", "24000", "-D", "hw:2,0"],
         {"stdin": subprocess.PIPE}),
    ]


def test_pi_wakes_the_panel_agent_and_binds_every_interface(pi, monkeypatch):
    posts = []
    monkeypatch.setattr(pi.requests, "post", lambda url, **kw: posts.append((url, kw)))
    pi._wake_panel_agent()
    assert posts == [("http://127.0.0.1:8765/wake", {"json": {"hold_s": 20}, "timeout": 1.0})]

    bound = []

    class _Srv:
        allow_reuse_address = False

        def __init__(self, addr, handler):
            bound.append(addr)

        def serve_forever(self):
            return None

    monkeypatch.setattr(pi.socketserver, "TCPServer", _Srv)
    pi._start_health_server()
    assert bound == [("", 7777)]


def test_pi_barge_episode_still_uses_the_pactl_sink_input_ducker(pi):
    ep = pi._BargeEpisode("monitor", types.SimpleNamespace(pid=4242), pi._BargeDetector(),
                          captured_at=10.0)
    assert isinstance(ep.ducker, pi._SinkInputDucker) and ep.ducker.pid == 4242


# ── Cloudflare Access service-token headers (flag-dark) ──────────────────────

def test_access_headers_only_when_both_halves_are_set(clean_env):
    clean_env.setenv("DEVICE_TOKEN", "tok")
    clean_env.setenv("CF_ACCESS_CLIENT_ID", "id.access")
    clean_env.setenv("CF_ACCESS_CLIENT_SECRET", "s3cret")
    d = _load_daemon("zoe_voice_daemon_cf_both_under_test")
    assert d._headers == {"X-Device-Token": "tok", "Content-Type": "application/json",
                          "CF-Access-Client-Id": "id.access", "CF-Access-Client-Secret": "s3cret"}


@pytest.mark.parametrize("env", [{"CF_ACCESS_CLIENT_ID": "only-id"}, {"CF_ACCESS_CLIENT_SECRET": "only-secret"}])
def test_a_lone_access_half_is_ignored_and_logged(clean_env, caplog, env):
    caplog.set_level(logging.WARNING)
    clean_env.setenv("DEVICE_TOKEN", "tok")
    for k, v in env.items():
        clean_env.setenv(k, v)
    d = _load_daemon("zoe_voice_daemon_cf_half_under_test")
    assert set(d._headers) == {"X-Device-Token", "Content-Type"}
    assert any("must both be set" in r.getMessage() for r in caplog.records)
    assert "only-id" not in caplog.text and "only-secret" not in caplog.text  # never log a secret


# ── the Mac backend, as the daemon loads it ──────────────────────────────────

@pytest.fixture()
def mac(clean_env):
    clean_env.setenv("PANEL_PLATFORM", "mac")
    clean_env.setenv("DEVICE_TOKEN", "tok-mac")
    clean_env.setenv("BARGE_DUCK_ENABLED", "true")
    d = _load_daemon("zoe_voice_daemon_platform_mac_under_test")
    fake = _FakePA()
    d._PLATFORM.pyaudio = fake  # the stub pyaudio -> a recording fake (no device is opened)
    d._fake_pa = fake
    return d


def test_mac_platform_swaps_the_actuators(mac, monkeypatch):
    assert mac.PANEL_PLATFORM == "mac" and mac._PLATFORM.name == "mac"
    assert mac.HEALTH_BIND == "127.0.0.1" and mac._PLATFORM.has_panel_agent is False
    assert mac._PLATFORM.local_tts_cmd("hello") == ["say", "hello"]
    posts = []
    monkeypatch.setattr(mac.requests, "post", lambda *a, **k: posts.append(a))
    mac._wake_panel_agent()
    assert posts == []  # no on-box agent on a laptop


def test_health_bind_can_be_overridden_on_the_mac(clean_env):
    clean_env.setenv("PANEL_PLATFORM", "mac")
    clean_env.setenv("HEALTH_BIND", "0.0.0.0")
    assert _load_daemon("zoe_voice_daemon_bind_under_test").HEALTH_BIND == "0.0.0.0"


def test_missing_mac_module_fails_loudly_not_silently_as_pi(clean_env, monkeypatch):
    clean_env.setenv("PANEL_PLATFORM", "mac")
    real = __import__("os").path.isfile
    monkeypatch.setattr("os.path.isfile", lambda p: False if str(p).endswith("mac_backend.py") else real(p))
    with pytest.raises(RuntimeError, match="mac_backend.py"):
        _load_daemon("zoe_voice_daemon_nomac_under_test")


def test_mac_streams_reply_chunks_through_the_in_process_player(mac):
    player = mac._feed_pcm_chunk(None, _wav(rate=24000, ch=1, n=4800))
    assert player is not None and mac._active_playback() is not None
    assert _wait_for(lambda: mac._fake_pa.streams and mac._fake_pa.streams[0].writes)
    player.stdin.close()
    assert player.wait(3) == 0 and mac._fake_pa.streams[0].kw["channels"] == 2
    assert mac._fake_pa.streams[0].stopped and mac._fake_pa.streams[0].closed


def test_a_pcm_format_the_mac_player_cannot_take_is_skipped_not_fatal(mac, caplog):
    caplog.set_level(logging.WARNING)
    assert mac._feed_pcm_chunk(None, _wav(width=3)) is None
    assert any("not playable" in r.getMessage() for r in caplog.records)


def test_play_audio_b64_on_the_mac_plays_wav_and_stops_on_barge(mac):
    barge = threading.Timer(0.15, mac._barge_in_requested.set)
    mac._fake_pa.gate = threading.Event()  # hold the speaker so the reply is "still playing"
    barge.start()
    try:
        ok = mac.play_audio_b64(base64.b64encode(_wav(n=24000)).decode(), "audio/wav")
    finally:
        mac._fake_pa.gate.set()
        barge.cancel()
    assert ok is True  # a barge-in stop counts as heard, like the Pi


# ── barge-in phase 1 over the Mac actuator ───────────────────────────────────
# The decide logic is the Pi's, unchanged; what differs is that the duck is a gain.

def _primed_detector(d):
    det = d._BargeDetector(threshold=0.5, min_chunks=3, window_chunks=6, grace_ms=0,
                           fast_prob=0.95, fast_chunks=0)
    det.new_playback(0.0)
    fired = [det.feed(0.99, k * d._CHUNK_S) for k in range(3)]
    assert fired == [False, False, True]
    return det


def _open_episode(d, player):
    return d._BargeEpisode.open("monitor", player, _primed_detector(d), 0.99, 2 * d._CHUNK_S)


def _drive(d, ep, probs, start_chunk=3):
    """Feed probs the way the monitor does; return the first outcome and its index."""
    for i, p in enumerate(probs):
        t = (start_chunk + i) * d._CHUNK_S
        outcome = ep.feed(p, t, t + d._CHUNK_S)
        if outcome:
            return outcome, i
    return "", len(probs)


@pytest.fixture()
def mac_duck(mac, monkeypatch):
    monkeypatch.setattr(mac, "_PLAYOUT", mac._PlayoutLedger())
    monkeypatch.setattr(mac, "_barge_in_requested", threading.Event())
    monkeypatch.setattr(mac, "_barge_stream_closed", threading.Event())
    resp = MagicMock()
    mac._set_turn_response(resp)
    player = mac._PLATFORM.start_pcm_stream(24000, 1, 2)
    yield types.SimpleNamespace(d=mac, player=player, resp=resp)
    mac._set_turn_response(None)
    player.kill()


def test_mac_sustained_speech_ducks_the_stream_then_commits(mac_duck, caplog):
    d, p = mac_duck.d, mac_duck.player
    caplog.set_level(logging.INFO)
    ep = _open_episode(d, p)
    assert ep is not None and ep.ducker.ducked and ep.ducker.baseline is None
    assert p.gain == pytest.approx(10 ** (-15 / 20))  # BARGE_DUCK_DB default -15
    outcome, idx = _drive(d, ep, [0.99] * 16)
    assert outcome == "commit"
    assert p.poll() == -15 and d._barge_in_requested.is_set() and mac_duck.resp.close.called
    assert p.gain == 1.0  # restored before the kill: nothing left ducked
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("BARGE_DECIDE ")]
    assert "outcome=commit" in line and "duck_db=-15.0" in line
    assert 900 <= int(line.split(" ms=")[1].split()[0]) <= 1100  # the Pi lane's bound


def test_mac_short_burst_resumes_and_restores_the_gain(mac_duck, caplog):
    d, p = mac_duck.d, mac_duck.player
    caplog.set_level(logging.INFO)
    ep = _open_episode(d, p)
    outcome, _ = _drive(d, ep, [0.01] * 12)
    assert outcome == "resume"
    assert p.poll() is None and p.gain == 1.0 and not d._barge_in_requested.is_set()
    assert not mac_duck.resp.close.called


def test_mac_ceiling_resumes(mac_duck):
    d, p = mac_duck.d, mac_duck.player
    ep = _open_episode(d, p)
    outcome, _ = _drive(d, ep, ([0.9] * 2 + [0.1] * 4) * 8)
    assert outcome == "ceiling" and p.poll() is None and p.gain == 1.0


def test_mac_vad_failure_sentinel_never_commits(mac_duck):
    """Negative control: the same stream at 0.99 commits (first test)."""
    d, p = mac_duck.d, mac_duck.player
    ep = _open_episode(d, p)
    outcome, _ = _drive(d, ep, [-1.0] * 40)
    assert outcome == "ceiling" and p.poll() is None and p.gain == 1.0


def test_mac_the_audio_itself_is_ducked_then_restored(mac_duck):
    """Not just a number: the samples reaching the output stream are scaled while
    ducked and untouched after the restore."""
    d, p = mac_duck.d, mac_duck.player
    ep = _open_episode(d, p)
    stream = lambda: d._fake_pa.streams[0]  # noqa: E731
    p.stdin.write(np.full(480, 10000, dtype="<i2").tobytes())  # exactly one 20 ms block
    assert _wait_for(lambda: d._fake_pa.streams and stream().writes)
    ducked = np.frombuffer(stream().writes[0], dtype="<i2")
    assert ducked.max() == pytest.approx(10000 * 10 ** (-15 / 20), abs=2)
    ep.ducker.restore()
    n = len(stream().writes)
    p.stdin.write(np.full(480, 10000, dtype="<i2").tobytes())
    assert _wait_for(lambda: len(stream().writes) > n)
    assert np.frombuffer(stream().writes[n], dtype="<i2").max() == 10000


def test_mac_a_player_that_cannot_be_ducked_falls_back_to_the_hard_stop(mac, caplog, monkeypatch):
    """afplay (mp3) has no mid-stream volume: duck unavailable -> today's hard stop."""
    caplog.set_level(logging.INFO)
    afplay_like = types.SimpleNamespace(pid=777, poll=lambda: None, terminate=lambda: None)
    assert _open_episode(mac, afplay_like) is None
    assert any("duck unavailable" in r.getMessage() for r in caplog.records)


def test_decide_log_format_is_what_the_lab_summary_parses(mac_duck, caplog):
    """Tie the lab tool to the daemon's real BARGE_DECIDE line: if the format
    drifts, the summary stops counting and this goes red."""
    sys.path.insert(0, str(_REPO / "scripts" / "setup" / "mac_panel"))
    try:
        import barge_lab_summary as lab
    finally:
        sys.path.pop(0)
    d, p = mac_duck.d, mac_duck.player
    caplog.set_level(logging.INFO)
    ep = _open_episode(d, p)
    _drive(d, ep, [0.99] * 16)
    s = lab.summarise([r.getMessage() for r in caplog.records])
    assert s["decisions"] == 1 and s["detected"] == 1 and s["outcomes"]["commit"]["n"] == 1
