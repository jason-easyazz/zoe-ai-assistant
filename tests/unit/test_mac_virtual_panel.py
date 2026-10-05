"""The Mac "virtual panel" kit: scripts/setup/mac_panel/*, mac_virtual_panel.sh,
mac-requirements.txt. (The daemon-side selection + Pi byte-identity lives in
test_voice_daemon_platform.py.)

There is no Mac in CI or on the dev box, so everything macOS-specific is proven
against fakes: PyAudio is a recording fake, `brew`/`afplay` are never run, and the
install script is only exercised through its side-effect-free subcommands. What this
therefore does NOT prove is listed in docs/knowledge/mac-virtual-panel.md
("Unverified").
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_REPO = Path(__file__).resolve().parents[2]
_SETUP = _REPO / "scripts" / "setup"
_MAC = _SETUP / "mac_panel"
_SCRIPT = _SETUP / "mac_virtual_panel.sh"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclass-free modules, but keep import machinery honest
    spec.loader.exec_module(mod)
    return mod


mb = _load("mac_backend_under_test", _MAC / "mac_backend.py")
pre = _load("mac_preflight_under_test", _MAC / "preflight.py")
lab = _load("mac_lab_under_test", _MAC / "barge_lab_summary.py")


class _Stream:
    def __init__(self, kw):
        self.kw, self.writes, self.closed, self.stopped, self.gate = kw, [], False, False, None

    def write(self, data):
        if self.gate is not None:
            self.gate.wait(10)
        self.writes.append(bytes(data))

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True


class _PA:
    paInt16 = 8

    def __init__(self, devices=None, refuse=()):
        self.devices = devices or [{"name": "Built-in Output", "maxOutputChannels": 2,
                                    "maxInputChannels": 0, "defaultSampleRate": 48000.0}]
        self.refuse, self.streams, self.gate = set(refuse), [], None

    def PyAudio(self):
        return self

    def open(self, **kw):
        if kw.get("rate") in self.refuse:
            raise OSError(-9997, "Invalid sample rate")
        s = _Stream(kw)
        s.gate = self.gate
        self.streams.append(s)
        return s

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, i):
        return dict(self.devices[i], index=i)

    def get_default_output_device_info(self):
        return dict(self.devices[0], index=0)


class _Log:
    def __init__(self):
        self.warnings = []

    def warning(self, fmt, *args):
        self.warnings.append(fmt % args if args else fmt)


def _backend(pa=None, **kw):
    pa = pa or _PA()
    return mb.MacBackend(pyaudio_module=pa, **kw), pa


def _wait_for(pred, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return False


def _pcm(value: int, frames: int, ch: int = 1) -> bytes:
    import numpy as np
    return np.full(frames * ch, value, dtype="<i2").tobytes()


def _samples(stream) -> "list[int]":
    import numpy as np
    return np.frombuffer(b"".join(stream.writes), dtype="<i2").tolist()


# ── MacPlayer ────────────────────────────────────────────────────────────────

def test_mono_pcm_plays_as_stereo_in_20ms_blocks_and_exits_zero():
    be, pa = _backend()
    p = be.start_pcm_stream(16000, 1, 2)
    p.stdin.write(_pcm(1000, 1600))  # 100 ms
    p.stdin.close()
    assert p.wait(3) == 0 and p.poll() == 0
    (s,) = pa.streams
    assert s.kw["channels"] == 2 and s.kw["rate"] == 16000 and s.kw["output"] is True
    assert [len(w) for w in s.writes] == [320 * 4] * 5  # 20 ms of stereo int16 each
    assert set(_samples(s)) == {1000} and len(_samples(s)) == 3200  # L == R == the mono sample
    assert s.stopped and s.closed  # drained, then closed


def test_stereo_input_passes_through():
    be, pa = _backend()
    p = be.start_pcm_stream(16000, 2, 2)
    p.stdin.write(_pcm(7, 320, ch=2))
    p.stdin.close()
    assert p.wait(3) == 0 and len(_samples(pa.streams[0])) == 640


def test_gain_scales_samples_and_ramps_block_by_block():
    be, pa = _backend()
    p = be.start_pcm_stream(16000, 1, 2)
    pa.gate = None
    p.set_gain(0.25, ramp_ms=60)  # 3 blocks of 20 ms: 0.75, 0.5, 0.25
    p.stdin.write(_pcm(10000, 320 * 4))
    p.stdin.close()
    assert p.wait(3) == 0
    per_block = [max(__import__("numpy").frombuffer(w, dtype="<i2")) for w in pa.streams[0].writes]
    assert per_block == [7500, 5000, 2500, 2500]


def test_terminate_stops_at_once_and_breaks_the_pipe():
    be, pa = _backend()
    pa.gate = threading.Event()  # the "speaker" never drains
    p = be.start_pcm_stream(16000, 1, 2)
    p.stdin.write(_pcm(5, 800))
    p.terminate()
    assert p.poll() == -15  # immediate, like a terminated process
    with pytest.raises(BrokenPipeError):
        p.stdin.write(_pcm(5, 10))
    pa.gate.set()
    assert p.wait(1) == -15
    assert _wait_for(lambda: pa.streams and pa.streams[0].closed)
    p.terminate()  # idempotent
    p2 = be.start_pcm_stream(16000, 1, 2)
    p2.kill()
    assert p2.poll() == -9


def test_a_finished_player_keeps_its_exit_code_when_terminated_late():
    be, _ = _backend()
    p = be.start_pcm_stream(16000, 1, 2)
    p.stdin.close()
    assert p.wait(3) == 0
    p.terminate()
    assert p.poll() == 0


def test_stdin_write_blocks_like_a_full_pipe_until_the_speaker_drains():
    be, pa = _backend()
    pa.gate = threading.Event()
    p = be.start_pcm_stream(16000, 1, 2)
    done = threading.Event()
    threading.Thread(target=lambda: (p.stdin.write(b"\x01\x00" * 150000), done.set()), daemon=True).start()
    assert not done.wait(0.4)  # 300 kB into a 64 kB buffer behind a stalled speaker
    pa.gate.set()
    assert done.wait(5)
    p.kill()


def test_wait_times_out_like_popen():
    be, pa = _backend()
    pa.gate = threading.Event()
    p = be.start_pcm_stream(16000, 1, 2)
    p.stdin.write(_pcm(1, 800))
    p.stdin.close()
    with pytest.raises(subprocess.TimeoutExpired):
        p.wait(0.1)
    pa.gate.set()
    assert p.wait(3) == 0


def test_a_device_that_refuses_the_rate_is_reopened_at_its_own_rate_and_resampled():
    log = _Log()
    be, pa = _backend(_PA(refuse={24000}), log=log)
    p = be.start_pcm_stream(24000, 1, 2)
    p.stdin.write(_pcm(3000, 2400))  # 100 ms
    p.stdin.close()
    assert p.wait(3) == 0
    assert pa.streams[0].kw["rate"] == 48000
    n = len(_samples(pa.streams[0]))
    assert abs(n - 9600) <= 4 * 2 * 2  # ~100 ms of 48 kHz stereo, to within block rounding
    assert set(_samples(pa.streams[0])) == {3000}
    assert any("resampling to 48000" in w for w in log.warnings)


def test_an_unopenable_device_ends_the_player_with_an_error_not_an_exception():
    class _Dead(_PA):
        def open(self, **kw):
            raise OSError(-9996, "Invalid output device")

    log = _Log()
    be, _ = _backend(_Dead(), log=log)
    p = be.start_pcm_stream(24000, 1, 2)
    assert p.wait(3) == 1
    assert any("playback failed" in w for w in log.warnings)


def test_wav_files_play_and_unsupported_formats_are_refused(tmp_path):
    import wave
    f = tmp_path / "a.wav"
    with wave.open(str(f), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(_pcm(900, 640))
    be, pa = _backend()
    p, name = be.start_file_player(str(f), "wav")
    assert name == "pyaudio" and p.wait(3) == 0 and set(_samples(pa.streams[0])) == {900}
    f24 = tmp_path / "b.wav"
    with wave.open(str(f24), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(3); wf.setframerate(16000); wf.writeframes(b"\0\0\0" * 100)
    with pytest.raises(NotImplementedError):
        be.start_file_player(str(f24), "wav")
    with pytest.raises(NotImplementedError):
        be.start_pcm_stream(24000, 1, 3)
    with pytest.raises(NotImplementedError):
        be.start_pcm_stream(24000, 6, 2)


def test_blocking_play_times_out_and_stops_the_player(tmp_path):
    import wave
    f = tmp_path / "a.wav"
    with wave.open(str(f), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(_pcm(1, 640))
    be, pa = _backend()
    pa.gate = threading.Event()
    with pytest.raises(subprocess.TimeoutExpired):
        be.play_file_blocking(str(f), timeout=0.1)
    pa.gate.set()


def test_mp3_goes_to_afplay_and_cannot_be_ducked(monkeypatch):
    seen = []
    monkeypatch.setattr(mb.subprocess, "Popen", lambda cmd, **kw: seen.append(cmd) or types.SimpleNamespace(pid=9))
    be, _ = _backend()
    proc, name = be.start_file_player("/tmp/x.mp3", "mp3")
    assert seen == [["afplay", "/tmp/x.mp3"]] and name == "afplay"
    d = be.make_ducker(proc)
    assert d.duck() is False and d.ducked is False and d.baseline is None


def test_ducker_follows_the_daemons_current_duck_settings():
    knobs = {"v": (-15.0, 0)}
    be, _ = _backend(duck_params=lambda: knobs["v"])
    p = be.start_pcm_stream(16000, 1, 2)
    d = be.make_ducker(p)
    assert d.duck() and d.duck() is False  # once
    assert p.gain == pytest.approx(0.1778, abs=1e-3)
    d.restore()
    d.restore()
    assert p.gain == 1.0 and d.ducked is False
    knobs["v"] = (-6.0, 0)
    d2 = be.make_ducker(p)
    d2.duck()
    assert p.gain == pytest.approx(0.501, abs=1e-3)
    p.kill()


def test_output_device_resolution():
    devs = [{"name": "MacBook Pro Microphone", "maxInputChannels": 1, "maxOutputChannels": 0, "defaultSampleRate": 48000.0},
            {"name": "MacBook Pro Speakers", "maxInputChannels": 0, "maxOutputChannels": 2, "defaultSampleRate": 48000.0},
            {"name": "Jabra SPEAK 750", "maxInputChannels": 1, "maxOutputChannels": 2, "defaultSampleRate": 16000.0}]
    assert _backend(_PA(devs), output_device="default")[0].output_device_index() is None
    assert _backend(_PA(devs), output_device="jabra")[0].output_device_index() == 2     # case-insensitive substring
    assert _backend(_PA(devs), output_device="1")[0].output_device_index() == 1         # PortAudio index
    log = _Log()
    be, _ = _backend(_PA(devs), output_device="MacBook Pro Microphone", log=log)       # a MIC name (the daemon's default chain)
    assert be.output_device_index() is None and be.output_device_index() is None
    assert len(log.warnings) == 1 and "default output" in log.warnings[0]               # once, loudly
    log2 = _Log()
    be2, _ = _backend(_PA(devs), output_device="0", log=log2)                           # index of an input-only device
    assert be2.output_device_index() is None and len(log2.warnings) == 1


# ── preflight ────────────────────────────────────────────────────────────────

def test_headers_match_what_the_daemon_builds():
    assert pre.build_headers({"DEVICE_TOKEN": "t"}) == {"X-Device-Token": "t", "Content-Type": "application/json"}
    both = {"DEVICE_TOKEN": "t", "CF_ACCESS_CLIENT_ID": "i", "CF_ACCESS_CLIENT_SECRET": "s"}
    assert pre.build_headers(both) == {"X-Device-Token": "t", "Content-Type": "application/json",
                                       "CF-Access-Client-Id": "i", "CF-Access-Client-Secret": "s"}
    assert "CF-Access-Client-Id" not in pre.build_headers({"DEVICE_TOKEN": "t", "CF_ACCESS_CLIENT_ID": "i"})


@pytest.mark.parametrize("status,loc,ctype,body,sent,ok,needle", [
    (200, "", "application/json", "", True, True, "accepted the device token"),
    (200, "", "text/html", "", True, False, "not JSON"),
    (302, "https://team.cloudflareaccess.com/cdn-cgi/access/login/x", "", "", False, False, "set CF_ACCESS_CLIENT_ID"),
    (302, "https://team.cloudflareaccess.com/cdn-cgi/access/login/x", "", "", True, False, "DESPITE the service token"),
    (302, "https://elsewhere.example/", "", "", True, False, "unexpected redirect"),
    (403, "", "text/html", "<html>cloudflare access denied</html>", True, False, "refused the request"),
    (401, "", "application/json", "", True, False, "device token"),
    (502, "", "", "", True, False, "origin unreachable"),
    (524, "", "", "", True, False, "origin unreachable"),
    (418, "", "", "", True, False, "unexpected HTTP 418"),
])
def test_classify_probe(status, loc, ctype, body, sent, ok, needle):
    got_ok, why = pre.classify_probe(status, loc, ctype, body, sent_access_headers=sent)
    assert got_ok is ok and needle in why


class _Resp:
    def __init__(self, status=200, headers=None, text="", payload=None):
        self.status_code, self.headers, self.text, self._p = status, headers or {}, text, payload

    def json(self):
        return self._p


def test_probe_server_round_trip_and_never_follows_redirects():
    import base64
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        return _Resp(200, {"Content-Type": "application/json"}, payload={"audio_base64": base64.b64encode(b"RIFFx").decode()})

    env = {"ZOE_URL": "https://zoe.example/", "DEVICE_TOKEN": "t", "CF_ACCESS_CLIENT_ID": "i",
           "CF_ACCESS_CLIENT_SECRET": "s", "VERIFY_SSL": "true"}
    ok, why, audio = pre.probe_server(env, post)
    assert ok and audio == b"RIFFx" and "5 bytes" in why
    assert seen["url"] == "https://zoe.example/api/voice/speak" and seen["allow_redirects"] is False
    assert seen["verify"] is True and seen["headers"]["CF-Access-Client-Id"] == "i"
    env["VERIFY_SSL"] = "false"
    pre.probe_server(env, post)
    assert seen["verify"] is False


def test_probe_server_diagnoses_without_a_traceback():
    base = {"ZOE_URL": "https://zoe.example", "DEVICE_TOKEN": "t"}
    ok, why, _ = pre.probe_server(base, lambda *a, **k: _Resp(302, {"Location": "https://t.cloudflareaccess.com/x"}))
    assert not ok and "CF_ACCESS_CLIENT_ID" in why
    ok, why, _ = pre.probe_server(base, lambda *a, **k: (_ for _ in ()).throw(ConnectionError("boom")))
    assert not ok and "ConnectionError" in why and "zoe.example" in why
    assert pre.probe_server({"ZOE_URL": "zoe.example", "DEVICE_TOKEN": "t"}, None)[0] is False
    ok, why, _ = pre.probe_server({"ZOE_URL": "https://z"}, None)
    assert not ok and "DEVICE_TOKEN is empty" in why


def test_level_report_names_the_macos_microphone_permission_trap():
    ok, why = pre.level_report(0, 0.0, 2.0)
    assert not ok and "permission" in why and "Microphone" in why
    assert not pre.level_report(40, 2.0, 2.0)[0]
    assert pre.level_report(9000, 1200.0, 2.0)[0]


def test_list_devices_marks_defaults():
    devs = [{"name": "Mic", "maxInputChannels": 1, "maxOutputChannels": 0, "defaultSampleRate": 48000.0},
            {"name": "Spk", "maxInputChannels": 0, "maxOutputChannels": 2, "defaultSampleRate": 44100.0}]

    class PA(_PA):
        def get_default_input_device_info(self):
            return {"index": 0}

        def get_default_output_device_info(self):
            return {"index": 1}

    rows = pre.list_devices(PA(devs))
    assert rows[0][5] == "default-in" and rows[1][5] == "default-out" and rows[1][3] == 2


def test_tone_is_a_valid_wav():
    import io, wave
    with wave.open(io.BytesIO(pre.tone_wav()), "rb") as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, 16000) and wf.getnframes() > 1000


# ── lab summary ──────────────────────────────────────────────────────────────

def test_lab_summary_counts_outcomes_and_medians():
    lines = [
        "x Barge-in detected during playback (monitor, prob=0.99, th=0.75, t+1120ms, window=...) — ducked -15dB, deciding",
        "x BARGE_DECIDE outcome=commit ms=960 speech_ms=960 duck_db=-15.0 heard_chunks=1 heard_ms=1000 (monitor)",
        "x BARGE_DECIDE outcome=commit ms=1040 speech_ms=1040 duck_db=-15.0 heard_chunks=2 heard_ms=3000 (monitor)",
        "x BARGE_DECIDE outcome=resume ms=640 speech_ms=240 duck_db=-15.0 heard_chunks=-1 heard_ms=-1 (queue)",
        "x Barge-in duck unavailable (no PulseAudio sink-input for pid 5) — hard stop",
        "unrelated line",
    ]
    s = lab.summarise(lines)
    assert (s["detected"], s["duck_unavailable"], s["decisions"]) == (1, 1, 3)
    assert s["outcomes"]["commit"] == {"n": 2, "median_ms_from_onset": 1000, "median_speech_ms": 1000, "median_heard_ms": 2000}
    assert s["outcomes"]["resume"]["median_heard_ms"] is None
    text = lab.format_summary(s)
    assert "commit" in text and "resume" in text
    assert "no BARGE_DECIDE lines" in lab.format_summary(lab.summarise([]))


# ── mac-requirements.txt ─────────────────────────────────────────────────────

def _req_names(path: Path) -> "set[str]":
    names = set()
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            names.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower().replace("_", "-"))
    return names


def test_mac_requirements_are_pi_requirements_minus_pi_only_packages():
    pi, mac = _req_names(_SETUP / "pi-requirements.txt"), _req_names(_SETUP / "mac-requirements.txt")
    assert mac - pi == {"torch"}  # the one deliberate addition (the daemon loads Silero via torch.hub)
    dropped = pi - mac
    assert {"resemblyzer", "opencv-python-headless", "silero-vad"} <= dropped
    assert {"pyaudio", "numpy", "openwakeword", "onnxruntime", "requests"} <= mac


# ── mac_virtual_panel.sh ─────────────────────────────────────────────────────

def _sh(*args, home, stdin="", env_extra=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "ZOE_MAC_PANEL_HOME": str(home / "panel")}
    env.update(env_extra or {})
    return subprocess.run(["bash", str(_SCRIPT), *args], input=stdin, capture_output=True, text=True,
                          env=env, timeout=30)


def test_script_is_valid_bash_and_executable():
    assert subprocess.run(["bash", "-n", str(_SCRIPT)], capture_output=True).returncode == 0
    assert os.access(_SCRIPT, os.X_OK)


def test_env_template_has_the_documented_keys_and_every_key_is_one_the_daemon_reads(tmp_path):
    out = _sh("env-template", home=tmp_path)
    assert out.returncode == 0
    keys = dict(re.findall(r'^([A-Z][A-Z0-9_]+)="(.*)"', out.stdout, flags=re.M))
    assert keys["PANEL_PLATFORM"] == "mac" and keys["PANEL_ID"] == "mac-dev"
    assert keys["VERIFY_SSL"] == "true" and keys["HEALTH_BIND"] == "127.0.0.1"
    assert keys["DEVICE_TOKEN"] == "" and keys["CF_ACCESS_CLIENT_ID"] == "" and keys["CF_ACCESS_CLIENT_SECRET"] == ""
    assert keys["SPEAKER_ID_ENABLED"] == "false" and keys["AMBIENT_CAPTURE_ENABLED"] == "false"
    assert keys["ZOE_URL"].startswith("https://")
    daemon_src = (_SETUP / "zoe_voice_daemon.py").read_text()
    for key in keys:
        assert re.search(rf'["\']{key}["\']', daemon_src), f"{key} is in the template but the daemon never reads it"


def test_env_file_is_created_0600_and_never_overwritten(tmp_path):
    assert _sh("env", home=tmp_path).returncode == 0
    f = tmp_path / "panel" / ".env.voice"
    assert f.stat().st_mode & 0o777 == 0o600
    f.write_text(f.read_text().replace('PANEL_ID="mac-dev"', 'PANEL_ID="edited"'))
    again = _sh("env", home=tmp_path)
    assert again.returncode == 0 and "kept existing" in again.stdout
    assert 'PANEL_ID="edited"' in f.read_text()


def test_configure_writes_secrets_silently_and_idempotently(tmp_path):
    r = _sh("configure", home=tmp_path, stdin="tok-abc_123\nid.access\nsec-ret\n")
    assert r.returncode == 0
    for secret in ("tok-abc_123", "id.access", "sec-ret"):
        assert secret not in r.stdout + r.stderr  # nothing echoed
    f = tmp_path / "panel" / ".env.voice"
    text = f.read_text()
    assert 'DEVICE_TOKEN="tok-abc_123"' in text and 'CF_ACCESS_CLIENT_ID="id.access"' in text
    assert 'CF_ACCESS_CLIENT_SECRET="sec-ret"' in text and f.stat().st_mode & 0o777 == 0o600
    _sh("configure", home=tmp_path, stdin="\n\nnew-secret\n")  # Enter keeps; one key changes
    text2 = f.read_text()
    assert 'DEVICE_TOKEN="tok-abc_123"' in text2 and 'CF_ACCESS_CLIENT_SECRET="new-secret"' in text2
    assert text2.count("DEVICE_TOKEN=") == 1 and text2.count("CF_ACCESS_CLIENT_SECRET=") == 1
    # the file stays a sourceable shell file with the right values
    got = subprocess.run(["bash", "-c", f'set -a; . "{f}"; printf %s "$DEVICE_TOKEN|$PANEL_ID"'],
                         capture_output=True, text=True).stdout
    assert got == "tok-abc_123|mac-dev"


def test_configure_refuses_values_that_could_break_the_env_file(tmp_path):
    r = _sh("configure", home=tmp_path, stdin='bad"quote\n\n\n')
    assert r.returncode == 5 and "refusing" in r.stderr


def test_macos_only_commands_refuse_on_other_systems(tmp_path):
    if sys.platform == "darwin":
        pytest.skip("this host IS a Mac")
    for cmd in ("install", "run", "preflight", "devices"):
        r = _sh(cmd, home=tmp_path)
        assert r.returncode == 2 and "macOS only" in r.stderr, cmd


def test_uninstall_needs_yes_and_only_deletes_the_panel_home(tmp_path):
    (tmp_path / "panel").mkdir()
    (tmp_path / "panel" / "marker").write_text("x")
    (tmp_path / "keep").write_text("y")
    assert _sh("uninstall", home=tmp_path).returncode == 2
    assert (tmp_path / "panel" / "marker").exists()
    assert _sh("uninstall", "--yes", home=tmp_path).returncode == 0
    assert not (tmp_path / "panel").exists() and (tmp_path / "keep").exists()
    refused = _sh("uninstall", "--yes", home=tmp_path, env_extra={"ZOE_MAC_PANEL_HOME": str(tmp_path)})
    assert refused.returncode == 2 and tmp_path.exists()


def test_script_has_no_pi_paths_and_the_deploy_ship_set_is_unchanged():
    text = _SCRIPT.read_text()
    assert "/home/pi" not in text and "zoe-pi" not in text
    deploy = (_SETUP / "deploy-pi-voice.sh").read_text()
    assert "SHIPPED_FILES=(zoe_voice_daemon.py zoe_voice_announce.py pi-requirements.txt)" in deploy
    assert "mac_backend" not in deploy and "mac_panel" not in deploy  # the Pi ships nothing new
