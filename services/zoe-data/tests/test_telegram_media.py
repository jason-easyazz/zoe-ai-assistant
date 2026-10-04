"""routers/telegram_media.py — the Telegram voice-note contracts (flag-dark).

Pins: flag off = routes ABSENT (404); the internal-token gate; the ffmpeg argv
shape for decode (16 kHz mono s16 WAV, no shell) and encode (libopus OGG);
the duration cap measured on the DECODED audio with STT never reached
(negative control); structured errors for undecodable / timed-out input; no
corpus capture unless ZOE_TELEGRAM_STT_CAPTURE_DIR is set; Kokoro-down = 503
with ffmpeg never spawned. ffmpeg, Moonshine and Kokoro are all replaced by
fakes at the module's seams — no binary, no model, no sidecar, no network.
"""
import os
import struct

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
from routers import telegram_media as tm

TOKEN = {"X-Internal-Token": "tok"}


def _wav_bytes(seconds: float) -> bytes:
    """A minimal 16 kHz mono s16 RIFF of `seconds` of silence."""
    n = int(16000 * seconds) * 2
    return b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16
    ) + b"data" + struct.pack("<I", n) + b"\x00" * n


def _fake_ffmpeg(calls, *, rc=0, output=None, write_seconds=2.0):
    async def run(argv, timeout_s):
        calls.append((list(argv), timeout_s))
        if rc == 0:
            dst = argv[-1]
            with open(dst, "wb") as fh:
                fh.write(output if output is not None else _wav_bytes(write_seconds))
        return rc, "" if rc == 0 else "boom"

    return run


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_TELEGRAM_MEDIA", "1")
    monkeypatch.setenv("ZOE_TELEGRAM_FFMPEG", "/fake/bin/ffmpeg")
    monkeypatch.delenv("ZOE_TELEGRAM_STT_CAPTURE_DIR", raising=False)
    monkeypatch.delenv("ZOE_TELEGRAM_VOICE_MAX_S", raising=False)
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    app = FastAPI()
    assert tm.register(app) is True
    return TestClient(app)


# ─── flag + auth ──────────────────────────────────────────────────────────────


def test_flag_off_routes_absent(monkeypatch):
    monkeypatch.delenv("ZOE_TELEGRAM_MEDIA", raising=False)
    app = FastAPI()
    assert tm.register(app) is False
    c = TestClient(app)
    assert c.post("/api/system/telegram/transcribe", content=b"OggS", headers=TOKEN).status_code == 404
    assert c.post("/api/system/telegram/synthesize", json={"text": "hi"}, headers=TOKEN).status_code == 404


def test_flag_on_mounts_both_routes(client):
    # Present (the gate answers 403), not absent (404) — the mirror of the flag-off control.
    assert client.post("/api/system/telegram/transcribe", content=b"OggS").status_code == 403
    assert client.post("/api/system/telegram/synthesize", json={"text": "hi"}).status_code == 403


def test_internal_token_required(client, monkeypatch):
    # TestClient's peer is "testclient", not loopback, so the token is the only way in.
    assert client.post("/api/system/telegram/transcribe", content=b"OggS").status_code == 403
    assert client.post("/api/system/telegram/synthesize", json={"text": "hi"}).status_code == 403
    assert client.post("/api/system/telegram/transcribe", content=b"OggS", headers={"X-Internal-Token": "wrong"}).status_code == 403


# ─── transcribe ───────────────────────────────────────────────────────────────


def test_transcribe_decodes_then_runs_moonshine_capture_off(client, monkeypatch):
    calls = []
    monkeypatch.setattr(tm, "_run_ffmpeg", _fake_ffmpeg(calls, write_seconds=2.0))
    seen = []

    async def fake_stt(path):
        seen.append(path)
        return " what time is it "

    monkeypatch.setattr(tm, "_transcribe_wav", fake_stt)

    r = client.post("/api/system/telegram/transcribe", content=b"OggS-fake-opus", headers=TOKEN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["text"] == "what time is it"
    assert abs(body["duration_s"] - 2.0) < 0.01

    argv, timeout_s = calls[0]
    assert argv[0] == "/fake/bin/ffmpeg" and "-nostdin" in argv
    assert argv[argv.index("-ac") + 1] == "1" and argv[argv.index("-ar") + 1] == "16000"
    assert argv[argv.index("-sample_fmt") + 1] == "s16" and argv[argv.index("-f") + 1] == "wav"
    assert argv[argv.index("-t") + 1] == "61"  # max_s + 1 clip, default 60
    assert all(isinstance(a, str) for a in argv) and timeout_s >= 1.0
    # STT ran on the DECODED wav (ffmpeg's output), not on the upload.
    assert seen == [argv[-1]] and seen[0].endswith("out.wav")


def test_transcribe_rejects_over_long_audio_before_stt(client, monkeypatch):
    monkeypatch.setenv("ZOE_TELEGRAM_VOICE_MAX_S", "2")
    calls = []
    monkeypatch.setattr(tm, "_run_ffmpeg", _fake_ffmpeg(calls, write_seconds=3.0))
    stt_called = []

    async def fake_stt(path):
        stt_called.append(path)
        return "never"

    monkeypatch.setattr(tm, "_transcribe_wav", fake_stt)
    r = client.post("/api/system/telegram/transcribe", content=b"OggS", headers=TOKEN)
    assert r.status_code == 413 and r.json() == {"ok": False, "error": "audio too long", "max_s": 2}
    assert stt_called == [], "an over-long note must never reach Moonshine"
    assert calls[0][0][calls[0][0].index("-t") + 1] == "3"  # clip follows the flag


def test_transcribe_structured_errors(client, monkeypatch):
    assert client.post("/api/system/telegram/transcribe", content=b"", headers=TOKEN).status_code == 400

    monkeypatch.setattr(tm, "MAX_UPLOAD_BYTES", 10)
    r = client.post("/api/system/telegram/transcribe", content=b"x" * 11, headers=TOKEN)
    assert r.status_code == 413 and r.json()["error"] == "audio too large"
    monkeypatch.setattr(tm, "MAX_UPLOAD_BYTES", 8 * 1024 * 1024)

    monkeypatch.setattr(tm, "_run_ffmpeg", _fake_ffmpeg([], rc=1))
    r = client.post("/api/system/telegram/transcribe", content=b"not audio", headers=TOKEN)
    assert r.status_code == 422 and r.json() == {"ok": False, "error": "undecodable audio"}

    monkeypatch.setattr(tm, "_run_ffmpeg", _fake_ffmpeg([], rc=-1))
    r = client.post("/api/system/telegram/transcribe", content=b"slow", headers=TOKEN)
    assert r.status_code == 504 and r.json()["error"] == "decode timeout"

    monkeypatch.setattr(tm, "_run_ffmpeg", _fake_ffmpeg([]))

    async def boom(path):
        raise RuntimeError("moonshine exploded")

    monkeypatch.setattr(tm, "_transcribe_wav", boom)
    r = client.post("/api/system/telegram/transcribe", content=b"OggS", headers=TOKEN)
    assert r.status_code == 502 and r.json() == {"ok": False, "error": "stt failed"}


def test_transcribe_no_ffmpeg_is_503_not_500(client, monkeypatch):
    monkeypatch.setattr(tm, "ffmpeg_binary", lambda: None)
    r = client.post("/api/system/telegram/transcribe", content=b"OggS", headers=TOKEN)
    assert r.status_code == 503 and r.json()["error"] == "ffmpeg unavailable"


def test_capture_only_when_dir_is_set(client, monkeypatch, tmp_path):
    monkeypatch.setattr(tm, "_run_ffmpeg", _fake_ffmpeg([], write_seconds=1.0))

    async def fake_stt(path):
        return "hello"

    monkeypatch.setattr(tm, "_transcribe_wav", fake_stt)
    cap = tmp_path / "tg-corpus"

    # Default: nothing written anywhere.
    assert client.post("/api/system/telegram/transcribe", content=b"OggS", headers=TOKEN).status_code == 200
    assert not cap.exists()

    monkeypatch.setenv("ZOE_TELEGRAM_STT_CAPTURE_DIR", str(cap))
    assert client.post("/api/system/telegram/transcribe", content=b"OggS", headers=TOKEN).status_code == 200
    names = sorted(p.name for p in cap.iterdir())
    assert len(names) == 2 and names[0].endswith(".txt") and names[1].endswith(".wav")
    assert (cap / names[0]).read_text() == "hello\n"


# ─── synthesize ───────────────────────────────────────────────────────────────


def test_synthesize_kokoro_then_libopus_ogg(client, monkeypatch):
    calls = []
    ogg = b"OggS\x00fake-opus-page"
    monkeypatch.setattr(tm, "_run_ffmpeg", _fake_ffmpeg(calls, output=ogg))
    spoken = []

    async def fake_tts(text):
        spoken.append(text)
        return _wav_bytes(0.5)

    monkeypatch.setattr(tm, "_synthesize_wav", fake_tts)
    r = client.post("/api/system/telegram/synthesize", json={"text": "  It is ten past three. "}, headers=TOKEN)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/ogg") and r.content == ogg
    assert spoken == ["It is ten past three."]
    argv, _ = calls[0]
    assert argv[0] == "/fake/bin/ffmpeg" and "-nostdin" in argv
    assert argv[argv.index("-c:a") + 1] == "libopus" and argv[argv.index("-f") + 1] == "ogg"
    assert argv[argv.index("-ar") + 1] == "48000" and argv[argv.index("-ac") + 1] == "1"
    assert argv[-1].endswith("reply.ogg") and argv[argv.index("-i") + 1].endswith("reply.wav")


def test_synthesize_kokoro_down_is_503_and_ffmpeg_never_runs(client, monkeypatch):
    calls = []
    monkeypatch.setattr(tm, "_run_ffmpeg", _fake_ffmpeg(calls))

    async def down(text):
        return None

    monkeypatch.setattr(tm, "_synthesize_wav", down)
    r = client.post("/api/system/telegram/synthesize", json={"text": "hi"}, headers=TOKEN)
    assert r.status_code == 503 and r.json() == {"ok": False, "error": "tts unavailable"}
    assert calls == []


def test_synthesize_bounds(client, monkeypatch):
    assert client.post("/api/system/telegram/synthesize", json={"text": "   "}, headers=TOKEN).status_code == 400
    r = client.post("/api/system/telegram/synthesize", json={"text": "x" * (tm.MAX_TTS_CHARS + 1)}, headers=TOKEN)
    assert r.status_code == 413 and r.json()["error"] == "text too long"


# ─── helpers ──────────────────────────────────────────────────────────────────


def test_ffmpeg_binary_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_TELEGRAM_FFMPEG", "/opt/ffmpeg")
    assert tm.ffmpeg_binary() == "/opt/ffmpeg"
    monkeypatch.delenv("ZOE_TELEGRAM_FFMPEG")
    monkeypatch.setattr(tm.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert tm.ffmpeg_binary() is None
    local = tmp_path / ".local" / "bin"
    local.mkdir(parents=True)
    (local / "ffmpeg").write_bytes(b"#!")
    assert tm.ffmpeg_binary() == str(local / "ffmpeg")


def test_max_seconds_default_and_garbage(monkeypatch):
    monkeypatch.delenv("ZOE_TELEGRAM_VOICE_MAX_S", raising=False)
    assert tm.max_seconds() == 60
    monkeypatch.setenv("ZOE_TELEGRAM_VOICE_MAX_S", "nope")
    assert tm.max_seconds() == 60
    monkeypatch.setenv("ZOE_TELEGRAM_VOICE_MAX_S", "0")
    assert tm.max_seconds() == 1
