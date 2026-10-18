"""Panel as code (deep dive 2 V-3, 2026-10-19).

* the tracked template lists EXACTLY the keys the panel code reads (36 of the panel's 46 keys
  used to exist only on the Pi);
* the drift checker never lets a secret value out, and classifies drift correctly;
* models.lock pins the panel's wake model (in no git) and agrees with the ResNet34 pin;
* the wake-model backup rides the off-box artefacts tier;
* /health names the running build; the daemon talks to zoe-data over ONE keep-alive session.

Stdlib + numpy only, no network, no Pi: runs in the slim ``ci_safe`` lane.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
MAINT = REPO / "scripts" / "maintenance"
SETUP = REPO / "scripts" / "setup"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


drift = _load("panel_env_drift_t", MAINT / "panel_env_drift.py")
lockv = _load("models_lock_verify_t", MAINT / "models_lock_verify.py")
TEMPLATE = drift.parse_template(drift.TEMPLATE.read_text(encoding="utf-8"))
FAKE_TOKEN = "zdt_" + "q7" * 20   # a made-up token; must never reach any output


# ── the template is the configuration as code ──────────────────────────────────────────────────────────

def test_template_lists_exactly_the_keys_the_code_reads():
    primary, legacy = drift.source_keys()
    assert len(primary) > 100, "the AST scan found suspiciously few keys"
    missing = (primary | legacy) - set(TEMPLATE)
    extra = set(TEMPLATE) - (primary | legacy)
    assert not missing, f"the panel code reads keys the template does not list: {sorted(missing)}"
    assert not extra, f"the template lists keys no panel code reads: {sorted(extra)}"
    assert legacy <= {k for k, e in TEMPLATE.items() if "legacy" in e.tags}


def test_every_template_key_has_a_meaning_and_secrets_are_placeholders():
    for key, entry in TEMPLATE.items():
        assert entry.meaning, f"{key} has no one-line meaning"
        if "secret" in entry.tags:
            assert entry.value in ("", "__SECRET__"), f"{key}: a secret slot must hold a placeholder"
    assert TEMPLATE["DEVICE_TOKEN"].active and "secret" in TEMPLATE["DEVICE_TOKEN"].tags
    # The panel's tuned values are recorded (they lived only on the Pi before).
    for key, value in (("WAKEWORD_THRESHOLD", "0.45"), ("VAD_ENDPOINT_ENABLED", "1"), ("ZOE_VAD_TAIL_MS", "640"),
                       ("FOLLOW_UP_VAD_THRESHOLD", "0.50"), ("BARGE_IN_THRESHOLD", "0.75")):
        assert TEMPLATE[key].active and TEMPLATE[key].value == value, key


def test_template_holds_no_household_or_lan_detail():
    text = drift.TEMPLATE.read_text(encoding="utf-8")
    assert not re.search(r"\b(?:192\.168|10\.\d+|172\.(?:1[6-9]|2\d|3[01]))\.\d+\.\d+", text), "a LAN address"
    assert "zdt_" not in text


# ── the drift checker ───────────────────────────────────────────────────────────────────────────────────

def _live_text(**overrides) -> str:
    lines = []
    for key, entry in TEMPLATE.items():
        if entry.active:
            value = FAKE_TOKEN if key == "DEVICE_TOKEN" else entry.value
            lines.append(f"{key}={overrides.pop(key, value)}")
    lines += [f"{k}={v}" for k, v in overrides.items()]
    return "\n".join(lines) + "\n"


def test_a_panel_matching_the_template_has_no_drift(tmp_path, capsys):
    f = tmp_path / ".env.voice"
    f.write_text(_live_text())
    assert drift.main(["--live-file", str(f), "--show-values"]) == 0
    out = capsys.readouterr().out
    assert "no drift" in out and FAKE_TOKEN not in out


def test_secret_values_never_leave_the_parser(tmp_path, capsys):
    text = _live_text(NEW_API_KEY="sk-" + "z" * 30, CF_ACCESS_CLIENT_SECRET="cfsecretvalue123")
    live = drift.parse_live(text, TEMPLATE)
    assert live["DEVICE_TOKEN"] == drift.MASK_SET
    assert live["NEW_API_KEY"] == drift.MASK_SET            # unknown to the template, masked by its name
    assert live["CF_ACCESS_CLIENT_SECRET"] == drift.MASK_SET
    assert FAKE_TOKEN not in json.dumps(live) and "cfsecretvalue123" not in json.dumps(live)
    f = tmp_path / ".env.voice"
    f.write_text(text)
    for args in (["--show-values"], ["--json"], []):
        drift.main(["--live-file", str(f), *args])
        out = capsys.readouterr().out
        assert FAKE_TOKEN not in out and "cfsecretvalue123" not in out and "sk-zzz" not in out


def test_drift_is_classified():
    primary, legacy = drift.source_keys()
    live = drift.parse_live(_live_text(WAKEWORD_THRESHOLD="0.40",            # tuned value moved
                                       ZOE_URL="https://other.example",     # site: presence only
                                       WAKE_DEBOUNCE_S="1.2",               # read by nothing
                                       VAD_ENDPOINT_THRESHOLD="0.3",        # set, but commented in the template
                                       DEVICE_TOKEN=""), TEMPLATE)          # secret emptied
    del live["ZOE_STT_STREAM_UPLOAD"]                                       # active in the template, unset live
    d = drift.compare(TEMPLATE, live, primary | legacy, mode="644")
    assert d["value"] == ["WAKEWORD_THRESHOLD"]
    assert d["dead"] == ["WAKE_DEBOUNCE_S"]
    assert d["set_default"] == ["VAD_ENDPOINT_THRESHOLD"]
    assert d["unset"] == ["ZOE_STT_STREAM_UPLOAD"]
    assert d["secret"] == ["DEVICE_TOKEN"]
    assert d["mode"] == ["644"] and drift.compare(TEMPLATE, live, primary | legacy, mode="600")["mode"] == []
    assert d["undocumented"] == []


def test_unreadable_panel_is_exit_2(tmp_path):
    assert drift.main(["--live-file", str(tmp_path / "absent")]) == 2


# ── models.lock ─────────────────────────────────────────────────────────────────────────────────────────

def test_models_lock_pins_the_panel_wake_model_and_the_rocks():
    entries = lockv.load_lock()
    for e in entries:
        assert re.fullmatch(r"[0-9a-f]{64}", e["sha256"]), e["role"]
        assert e["host"] in ("jetson", "panel") and e["bytes"] > 0 and not e["path"].startswith("/")
    wake = [e for e in entries if e["path"] == ".zoe-voice/hey_zoe.onnx"]
    assert len(wake) == 1 and wake[0]["in_git"] is False and "backup" in wake[0]
    roles = " ".join(e["role"] for e in entries)
    for rock in ("brain", "STT", "TTS", "panel VAD"):
        assert rock in roles
    assert not any("functiongemma-router" in e["path"] for e in entries)   # the ratchet owns that checkpoint
    rn34 = json.loads((SETUP / "zoe_speaker_resnet34_artifacts.json").read_text())["model"]
    [pin] = [e for e in entries if e["path"].endswith(rn34["file"])]
    assert pin["sha256"] == rn34["sha256"], "models.lock and the embedder's own pin disagree"
    manifest = json.loads((REPO / ".zoe" / "manifest.json").read_text())
    assert "models.lock" in manifest["approved_root_files"]


def test_models_lock_verify_catches_a_changed_file(tmp_path):
    good, bad = tmp_path / "a.bin", tmp_path / "b.bin"
    good.write_bytes(b"model-a")
    bad.write_bytes(b"model-b")
    entries = [dict(role="a", path="a.bin", sha256=hashlib.sha256(b"model-a").hexdigest()),
               dict(role="b", path="b.bin", sha256=hashlib.sha256(b"model-a").hexdigest()),   # negative control
               dict(role="c", path="c.bin", sha256="0" * 64)]
    assert lockv.check_local(entries, tmp_path) == [("a", "ok"), ("b", "MISMATCH"), ("c", "MISSING")]


def test_the_wake_model_backup_rides_the_offbox_artefacts_tier(tmp_path):
    offbox = _load("zoe_offbox_t", MAINT / "zoe_offbox.py")
    p = offbox.Paths(home=tmp_path, checkout=tmp_path / "assistant", config_dir=tmp_path / "c",
                     state_dir=tmp_path / "s", stage_dir=tmp_path / "st")
    assert offbox.artefact_paths(p) == []
    (tmp_path / ".zoe" / "backups" / "panel").mkdir(parents=True)
    assert offbox.artefact_paths(p) == [tmp_path / ".zoe" / "backups" / "panel"]
    # ...and it is NOT in the household tier's ~/.zoe walk (the "*backup*" exclude), so it is not shipped twice.
    assert offbox.excluded(("backups", "panel", "hey_zoe.onnx"), offbox.ZOE_TREE_EXCLUDES)
    # The monthly drill restores and checksums it (its only copy off the panel), with the served GGUF.
    import fnmatch
    for name in ("hey_zoe.onnx", "functiongemma-270m-zoe-functok-r2-Q8_0.gguf"):
        assert any(fnmatch.fnmatch(name, g) for g in offbox.DRILL_ARTEFACT_GLOBS), name
    assert not any(fnmatch.fnmatch("model.safetensors", g) for g in offbox.DRILL_ARTEFACT_GLOBS)


def test_the_jetson_installer_starts_from_code_defaults(tmp_path):
    # The template's active lines are the PANEL's tuned values; another host must not inherit them.
    import subprocess
    script = (SETUP / "install-voice-service.sh").read_text()
    m = re.search(r"sed -E (-e '[^']+' -e '[^']+') \"\$ENV_EXAMPLE\"", script)
    assert m, "the installer no longer renders the template"
    out = subprocess.run(f"sed -E {m.group(1)} {drift.TEMPLATE}", shell=True, capture_output=True, text=True,
                         check=True).stdout
    active = {line.split("=", 1)[0] for line in out.splitlines() if re.match(r"^[A-Z][A-Z0-9_]*=", line)}
    assert active == {"ZOE_URL", "PANEL_ID", "DEVICE_TOKEN"}
    assert "#ZOE_SPEAKER_MODEL=resnet34" in out and "#AUDIO_DEVICE=pulse" in out


# ── the daemon: build in /health, one keep-alive session ───────────────────────────────────────────────

@pytest.fixture(scope="module")
def daemon():
    stubs = {"pyaudio": types.ModuleType("pyaudio"), "requests": types.ModuleType("requests")}
    stubs["pyaudio"].paInt16 = 8
    stubs["pyaudio"].PyAudio = object
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        yield _load("zoe_voice_daemon_pac_test", SETUP / "zoe_voice_daemon.py")
    finally:
        for n, prev in saved.items():
            if prev is not None:
                sys.modules[n] = prev
            else:
                sys.modules.pop(n, None)


def test_build_info_reads_the_deploy_file(daemon, tmp_path):
    f = tmp_path / "BUILD.json"
    assert daemon._read_build_info(str(f)) == {"sha": "unknown", "deployed_at": ""}
    f.write_text("{not json")
    assert daemon._read_build_info(str(f))["sha"] == "unreadable"
    f.write_text(json.dumps({"sha": "abc123-dirty", "deployed_at": "2026-10-19T04:00:00Z"}))
    assert daemon._read_build_info(str(f)) == {"sha": "abc123-dirty", "deployed_at": "2026-10-19T04:00:00Z"}


def test_health_names_the_running_build(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "SPEAKER_MODEL", "resemblyzer")
    monkeypatch.setattr(daemon, "_BUILD_INFO", {"sha": "f00d", "deployed_at": "t"})
    h = daemon._health_payload()
    assert h["build"] == "f00d" and h["build_deployed_at"] == "t"
    assert h["code_sha256"] == hashlib.sha256((SETUP / "zoe_voice_daemon.py").read_bytes()).hexdigest()
    assert "wake_model_sha256" in h


def test_zoe_calls_use_one_session_once_main_installs_it(daemon, monkeypatch):
    assert daemon._zoe_http() is daemon.requests            # before main(): the module calls, as before
    calls = []

    class FakeSession:
        def post(self, url, **kw):
            calls.append(("post", url))
            return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})

        def get(self, url, **kw):
            calls.append(("get", url))
            return types.SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"announcements": []})

    monkeypatch.setattr(daemon, "_HTTP_SESSION", FakeSession())
    assert daemon._api_post("/api/voice/wake", {}) == {"ok": True}
    daemon._fetch_announcements()
    assert [c[0] for c in calls] == ["post", "get"] and all(c[1].startswith(daemon.ZOE_URL) for c in calls)
    src = (SETUP / "zoe_voice_daemon.py").read_text()
    for path in ("/api/voice/profiles/sync", "/api/voice/announcements"):
        call = src[src.index(f'f"{{ZOE_URL}}{path}"') - 60:src.index(f'f"{{ZOE_URL}}{path}"')]
        assert "_zoe_http()." in call, path
    assert "_zoe_http().post(url, json=payload" in src   # the streamed turn


def test_session_refuses_cookies(daemon, monkeypatch):
    # The daemon fixture keeps an empty `requests` stub in sys.modules, and other tests may have left a
    # half-linked package behind: import the real submodules by name instead of trusting attributes.
    import importlib
    monkeypatch.delitem(sys.modules, "requests", raising=False)
    try:
        sessions = importlib.import_module("requests.sessions")
        adapters = importlib.import_module("requests.adapters")
    except ImportError:
        pytest.skip("requests unavailable")
    real = types.SimpleNamespace(Session=sessions.Session, adapters=adapters)
    orig = daemon.requests
    daemon.requests = real
    try:
        s = daemon._make_http_session()
    finally:
        daemon.requests = orig
    import http.cookiejar
    import urllib.request
    req = urllib.request.Request("https://zoe.local/api/x")
    assert not s.cookies._policy.set_ok(http.cookiejar.Cookie(
        0, "sid", "1", None, False, "zoe.local", True, False, "/", True, True, None, False, None, None, {}), req)
    assert s.get_adapter("https://zoe.local")._pool_maxsize == 8
