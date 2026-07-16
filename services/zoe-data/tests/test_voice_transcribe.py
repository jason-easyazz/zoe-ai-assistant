"""Voice /transcribe endpoint: auth gate and Moonshine STT contract."""

import base64
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _clear_voice_module_state():
    """Reset voice_tts' module-level caches around every test in this file.

    `_panel_idle_cache` memoises the panel idle-logout window for 30s so it
    isn't a DB read on every voice turn. That cache is keyed on nothing, so it
    outlives the test that populated it: a test that ran earlier and resolved
    the window (e.g. the 900s "fresh session is trusted" case) leaves 900
    cached, and a later test's `monkeypatch.setenv(ZOE_PANEL_SESSION_TRUST_
    WINDOW_S, ...)` is then silently ignored — the cache short-circuits before
    the env is ever read. That made `test_recent_panel_session_user_stale_is_
    not_trusted` pass alone but fail in file order, i.e. a real staleness
    assertion was only ever green by accident of ordering. Clearing the cache
    makes each test observe the window it actually sets.
    """
    from routers import voice_tts

    def _reset():
        voice_tts._VOICE_SESSIONS.clear()
        voice_tts._panel_idle_cache["value"] = None
        voice_tts._panel_idle_cache["expires"] = 0.0

    _reset()
    yield
    _reset()


@pytest.fixture
def client():
    from routers import voice_tts

    def _fake_auth():
        return {"source": "test", "panel_id": "test-panel", "user_id": "u1"}

    app = FastAPI()
    app.include_router(voice_tts.router)
    app.dependency_overrides[voice_tts._require_voice_auth] = _fake_auth
    with TestClient(app) as c:
        yield c


def test_transcribe_ok_with_mock_whisper(client, monkeypatch):
    from routers import voice_tts

    async def _fake_run(path: str) -> str:
        return "hello world"

    # Patch the Moonshine entry point so the test is deterministic and model-free.
    monkeypatch.setattr(voice_tts, "_transcribe_audio", _fake_run)
    wav = b"RIFF" + b"\x00" * 12  # minimal header-ish payload for temp file
    body = {"audio_base64": base64.b64encode(wav).decode(), "panel_id": "p1"}
    r = client.post("/api/voice/transcribe", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data.get("ok") is True
    assert data.get("text") == "hello world"


def test_transcribe_writes_stt_audit_log(client, monkeypatch, tmp_path):
    from routers import voice_tts

    async def _fake_run(path: str) -> str:
        return "hello audit"

    log_path = tmp_path / "voice_stt.jsonl"
    monkeypatch.setenv("ZOE_VOICE_STT_LOG", str(log_path))
    monkeypatch.setattr(voice_tts, "_transcribe_audio", _fake_run)
    wav = b"RIFF" + b"\x00" * 12

    r = client.post(
        "/api/voice/transcribe",
        json={"audio_base64": base64.b64encode(wav).decode(), "panel_id": "p-audit"},
    )

    assert r.status_code == 200
    record = json.loads(log_path.read_text().strip())
    assert record["route"] == "transcribe"
    assert record["panel_id"] == "p-audit"
    assert record["transcript"] == "hello audit"
    # Moonshine is the only STT engine — the audit log reflects that, not whisper.
    assert record["model"] == "moonshine"
    assert record["audio_bytes"] == len(wav)
    assert record["moonshine_arch"] == "MEDIUM_STREAMING"


def test_transcribe_missing_body_400(client):
    r = client.post("/api/voice/transcribe", json={})
    assert r.status_code == 400


def test_transcribe_503_when_moonshine_fails(client, monkeypatch, tmp_path):
    from routers import voice_tts

    async def _boom(path: str) -> str:
        raise RuntimeError("moonshine unavailable")

    log_path = tmp_path / "voice_stt.jsonl"
    monkeypatch.setenv("ZOE_VOICE_STT_LOG", str(log_path))
    monkeypatch.setattr(voice_tts, "_transcribe_audio", _boom)
    wav = b"RIFF" + b"\x01" * 20
    r = client.post(
        "/api/voice/transcribe",
        json={"audio_base64": base64.b64encode(wav).decode()},
    )
    assert r.status_code == 503
    record = json.loads(log_path.read_text().strip())
    assert record["route"] == "transcribe"
    assert record["audio_bytes"] == len(wav)
    assert record["transcript"] == ""
    assert "moonshine unavailable" in record["error"]


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("show whether", "show weather"),
        ("open the whether", "open weather"),
        ("what's the whether", "what is the weather"),
        ("how is the whether", "how is the weather"),
        ("whether tomorrow", "weather tomorrow"),
        ("whether or not it rains, remind me", "whether or not it rains, remind me"),
    ],
)
def test_normalize_voice_command_text_weather_homophones(raw, normalized):
    from routers import voice_tts

    assert voice_tts._normalize_voice_command_text(raw) == normalized


def test_stt_audit_log_rotates_when_capped(monkeypatch, tmp_path):
    from routers import voice_tts

    log_path = tmp_path / "voice_stt.jsonl"
    log_path.write_text("x" * 50)
    monkeypatch.setenv("ZOE_VOICE_STT_LOG", str(log_path))
    monkeypatch.setenv("ZOE_VOICE_STT_LOG_MAX_BYTES", "10")

    voice_tts._log_voice_stt_sample(
        route="transcribe",
        panel_id="p-rotate",
        audio_bytes=1,
        suffix=".wav",
    )

    rotated = tmp_path / "voice_stt.jsonl.1"
    assert rotated.read_text() == "x" * 50
    record = json.loads(log_path.read_text().strip())
    assert record["panel_id"] == "p-rotate"


# Panel-session trust tests are pure logic (fake DB objects, no STT/Jetson deps),
# so they opt into validate.yml's slim `-m ci_safe` lane and gate every PR — not
# just the post-merge Jetson catch-all. The rest of this file exercises the
# Moonshine STT contract and stays Jetson-only. See tests/AGENTS.md.
@pytest.mark.ci_safe
def test_recent_panel_session_user_is_trusted(monkeypatch):
    from routers import voice_tts

    monkeypatch.setenv("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "900")

    class _Cursor:
        def __init__(self, row):
            self._row = row

        async def fetchone(self):
            return self._row

    class _Db:
        def __init__(self, row):
            self._row = row

        async def execute(self, _query, _params):
            return _Cursor(self._row)

    row = {
        "user_id": "alice",
        "last_seen_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }
    resolved = asyncio.run(
        voice_tts._resolve_recent_panel_session_user("panel-a", _Db(row))
    )
    assert resolved == "alice"


@pytest.mark.ci_safe
def test_recent_panel_session_user_stale_is_not_trusted(monkeypatch):
    from routers import voice_tts

    monkeypatch.setenv("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "60")

    class _Cursor:
        def __init__(self, row):
            self._row = row

        async def fetchone(self):
            return self._row

    class _Db:
        def __init__(self, row):
            self._row = row

        async def execute(self, _query, _params):
            return _Cursor(self._row)

    row = {
        "user_id": "alice",
        "last_seen_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
    }
    resolved = asyncio.run(
        voice_tts._resolve_recent_panel_session_user("panel-a", _Db(row))
    )
    assert resolved is None


def test_voice_session_rollover_preserves_bound_user():
    from routers import voice_tts

    old_session = "voice-panel-p1-old"
    voice_tts._VOICE_SESSIONS["p1"] = {
        "session_id": old_session,
        "last_at": time.monotonic() - voice_tts._VOICE_SESSION_TTL_S - 5,
        "bound_user_id": "user-123",
    }
    try:
        new_session = voice_tts._get_or_create_voice_session("p1")
        assert new_session != old_session
        assert voice_tts._VOICE_SESSIONS["p1"].get("bound_user_id") == "user-123"
    finally:
        voice_tts._VOICE_SESSIONS.pop("p1", None)


@pytest.mark.ci_safe
def test_panel_session_trust_window_parsing(monkeypatch):
    from routers import voice_tts

    monkeypatch.setenv("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "not-a-number")
    assert voice_tts._panel_session_trust_window_s() == 900

    monkeypatch.setenv("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "-5")
    assert voice_tts._panel_session_trust_window_s() == 0

    monkeypatch.setenv("ZOE_PANEL_SESSION_TRUST_WINDOW_S", "99999999")
    assert voice_tts._panel_session_trust_window_s() == 24 * 60 * 60


# ── Moonshine-only STT + wake-word bleed fix ─────────────────────────────────


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        # Wake word on its own leading line -> dropped, command intact.
        (["Hey Zoe.", "What time is it?"], "What time is it?"),
        (["Hey, Zoe.", "Show me the weather."], "Show me the weather."),
        (["Hey zoe", "Add bread to the shopping list"], "Add bread to the shopping list"),
        # Bare name line (no "hey") -> dropped.
        (["Zoe.", "Where is Egypt?"], "Where is Egypt?"),
        # Inline wake prefix on the same line as the command -> prefix stripped.
        (["Hey Zoe, what's on my calendar?"], "What's on my calendar?"),
        (["Hey Zoe, set a timer for five minutes."], "Set a timer for five minutes."),
        # Wake-word homophones the mishearing produces.
        (["Hey joey", "Show me my lists."], "Show me my lists."),
        # No wake word at all -> untouched.
        (["What time is it?"], "What time is it?"),
        (["Add soup to the chopping list."], "Add soup to the chopping list."),
    ],
)
def test_strip_wake_word_removes_wake_keeps_command(lines, expected):
    from routers import voice_tts

    assert voice_tts._strip_wake_word(lines) == expected


def test_strip_wake_word_never_clips_command_words():
    """The fix must never eat a real command word — only the wake token."""
    from routers import voice_tts

    # "What" must survive (the bug was trimming it to "time is it?").
    assert voice_tts._strip_wake_word(["Hey Zoe.", "What time is it?"]) == "What time is it?"
    # A command that *is* just a single word after the wake line stays.
    assert voice_tts._strip_wake_word(["Hey Zoe", "Stop."]) == "Stop."


def test_strip_wake_word_wake_only_clip_not_emptied():
    """A clip that is ONLY the wake word returns it as-is (caller handles empty),
    never an empty string from over-trimming."""
    from routers import voice_tts

    assert voice_tts._strip_wake_word(["Hey Zoe."]) == "Hey Zoe."
    assert voice_tts._strip_wake_word([]) == ""
    assert voice_tts._strip_wake_word(["", "  "]) == ""


def test_transcribe_audio_impl_is_moonshine_only(monkeypatch):
    """The live path uses ONLY Moonshine; whisper must never be called."""
    from routers import voice_tts

    async def _moon(path: str) -> str:
        return "moonshine result"

    monkeypatch.setattr(voice_tts, "_run_moonshine", _moon)

    assert asyncio.run(voice_tts._transcribe_audio_impl("/tmp/x.wav")) == "moonshine result"


def test_transcribe_audio_impl_empty_returns_empty_no_whisper(monkeypatch):
    """On Moonshine-empty, return '' (caller re-prompts) — do NOT fall to whisper."""
    from routers import voice_tts

    async def _moon_empty(path: str) -> str:
        return ""

    monkeypatch.setattr(voice_tts, "_run_moonshine", _moon_empty)

    assert asyncio.run(voice_tts._transcribe_audio_impl("/tmp/x.wav")) == ""


def test_transcribe_audio_impl_moonshine_error_raises(monkeypatch):
    """A Moonshine BACKEND error RAISES so callers can tell a real failure apart
    from silence (Greptile #854) — and whisper is never reached as a rescue."""
    from routers import voice_tts

    async def _moon_boom(path: str) -> str:
        raise RuntimeError("moonshine boom")

    monkeypatch.setattr(voice_tts, "_run_moonshine", _moon_boom)

    raised = False
    try:
        asyncio.run(voice_tts._transcribe_audio_impl("/tmp/x.wav"))
    except RuntimeError as exc:
        raised = "moonshine boom" in str(exc)
    assert raised, "Moonshine backend error must surface (raise), not be masked as silence"


# ── Moonshine audio prep (mono / 16 kHz guarantee, no per-sample edits) ──────


def test_prepare_audio_passes_16k_through_unchanged():
    """Already-16 kHz audio must reach Moonshine byte-for-byte unchanged.

    This is the zero-regression guarantee: replaying the operator's corpus showed
    Moonshine is so input-sensitive that even DC-offset removal flips transcripts,
    so the prep MUST be an identity for the live 16 kHz path."""
    from routers import voice_tts

    audio = [0.0, 0.5, -0.5, 0.25, -0.25, 0.0]
    out_audio, out_sr = voice_tts._prepare_audio_for_moonshine(audio, 16000)

    assert out_sr == 16000
    # Same object, not just equal values — no copy, no edit.
    assert out_audio is audio


def test_prepare_audio_resamples_off_rate_to_16k():
    """A non-16 kHz capture is resampled to 16 kHz (Moonshine's expected rate)."""
    import numpy as np
    from routers import voice_tts

    sr = 48000
    # 1.0s of a 440 Hz tone at 48 kHz -> expect ~16000 samples at 16 kHz.
    t = np.arange(sr, dtype=np.float32) / sr
    tone = (0.3 * np.sin(2 * np.pi * 440.0 * t)).astype(np.float32)

    out_audio, out_sr = voice_tts._prepare_audio_for_moonshine(tone.tolist(), sr)

    assert out_sr == 16000
    # 48k -> 16k is a clean 3:1 decimation; length scales with the ratio.
    assert abs(len(out_audio) - sr // 3) <= 2
    # Still bounded audio, not garbage.
    assert max(abs(x) for x in out_audio) < 1.0


def test_prepare_audio_downmixes_stereo_to_mono():
    """A 2-D (stereo) array is averaged to mono so the C transcribe call is safe."""
    import numpy as np
    from routers import voice_tts

    stereo = np.array([[0.4, 0.0], [0.0, 0.4], [0.2, 0.2]], dtype=np.float32)
    out_audio, out_sr = voice_tts._prepare_audio_for_moonshine(stereo, 16000)

    assert out_sr == 16000
    mono = np.asarray(out_audio, dtype=np.float32)
    assert mono.ndim == 1
    assert mono.tolist() == pytest.approx([0.2, 0.2, 0.2])


def test_prepare_audio_empty_is_noop():
    from routers import voice_tts

    out_audio, out_sr = voice_tts._prepare_audio_for_moonshine([], 44100)
    assert out_sr == 16000
    assert list(out_audio) == []


def test_run_moonshine_rejects_invalid_rate_before_transcribe(monkeypatch, tmp_path):
    """A corrupt WAV whose metadata yields rate 0/None must NEVER reach the C
    transcribe call — _run_moonshine returns empty instead (Greptile #886)."""
    import asyncio
    from routers import voice_tts

    class _ExplodingTr:
        def transcribe_without_streaming(self, audio, sr):
            raise AssertionError(f"invalid rate {sr!r} reached Moonshine")

    monkeypatch.setattr(voice_tts, "_ensure_moonshine", lambda: _ExplodingTr())

    import sys
    import types
    fake_utils = types.SimpleNamespace(load_wav_file=lambda p: ([0.1, 0.2, 0.3], 0))
    monkeypatch.setitem(sys.modules, "moonshine_voice.utils", fake_utils)
    monkeypatch.setitem(sys.modules, "moonshine_voice", types.SimpleNamespace(utils=fake_utils))

    wav = tmp_path / "corrupt.wav"
    wav.write_bytes(b"RIFF....")
    assert asyncio.run(voice_tts._run_moonshine(str(wav))) == ""


def test_prepare_audio_resample_needs_no_scipy(monkeypatch):
    """The off-rate resample must be numpy-only — scipy is NOT a declared
    zoe-data runtime dependency, so importing it would break the live path on a
    deployment that installs only requirements.txt (Greptile #886)."""
    import builtins
    import numpy as np
    from routers import voice_tts

    real_import = builtins.__import__

    def _no_scipy(name, *args, **kwargs):
        if name == "scipy" or name.startswith("scipy."):
            raise ModuleNotFoundError("No module named 'scipy' (blocked by test)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_scipy)

    tone = (0.2 * np.sin(np.linspace(0, 20, 48000))).astype(np.float32)
    out_audio, out_sr = voice_tts._prepare_audio_for_moonshine(tone.tolist(), 48000)

    assert out_sr == 16000
    assert abs(len(out_audio) - 16000) <= 2


def test_prepare_audio_unknown_rate_not_coerced_to_16k():
    """A falsey/invalid native rate must NOT be silently relabelled 16 kHz — that
    would make malformed audio look valid to Moonshine (Greptile #886). The rate
    is handed back unchanged so the caller can surface the bad metadata."""
    from routers import voice_tts

    audio = [0.1, -0.1, 0.2, -0.2]
    for bad in (0, None):
        out_audio, out_sr = voice_tts._prepare_audio_for_moonshine(audio, bad)
        # Not relabelled as the valid 16 kHz rate.
        assert out_sr != 16000
        assert list(out_audio) == audio


def test_maybe_capture_stt_saves_corpus_without_whisper_ab(monkeypatch, tmp_path):
    """Corpus capture still saves the WAV, but fires NO whisper A/B."""
    from routers import voice_tts

    src = tmp_path / "utt.wav"
    src.write_bytes(b"RIFF" + b"\x00" * 64)
    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("ZOE_VOICE_SAVE_AUDIO", "1")
    monkeypatch.setenv("ZOE_VOICE_SAMPLE_DIR", str(sample_dir))

    spawned = []
    monkeypatch.setattr(voice_tts, "_spawn_bg", lambda coro: spawned.append(coro))

    asyncio.run(voice_tts._maybe_capture_stt(str(src), "moonshine text"))

    saved = list(sample_dir.glob("*.wav"))
    assert len(saved) == 1  # corpus capture kept
    assert spawned == []  # no background whisper A/B scheduled
