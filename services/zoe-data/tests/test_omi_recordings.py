"""The Zoe side of "record on the pendant, sync when in range": ``omi_recordings.py`` + ``routers/omi_recordings.py`` (migration 0045,
flag ``ZOE_OMI_SYNC_ENABLED`` default OFF).

Groups (the flag/gate tests carry their own on/off control arm; source mutations worth re-running by Edit: skip the sha256
check in ``store_recording``; drop the tombstone branch; drop the night-window term in ``may_transcribe_now``; drop
``_require_owner``; catch ``RuntimeError`` instead of ``GateClosed`` in ``_transcribe_one`` - that last one was a real bug
the retry test caught while writing this file):

  flag + auth      off = routes ABSENT; a device token (never a session) uploads; no owner configured = refused; owner-only views
  custody answer   the 200 body is what lets the Pi free the pendant: it echoes the digest of the bytes STORED; a truncated body,
                   a bad WAV or a changed body under a known key is never confirmed
  idempotency      the same key twice = one row, one file; a deleted recording's key is acknowledged (tombstone), not resurrected
  transcription    Moonshine chunk by chunk, silence skipped, audio deleted after (unless kept), never in the night window / during a
                   voice turn / under the RAM floor, retried then failed, and the gate closing mid-job puts it back
  retention        a kept WAV still expires; delete removes audio + transcript + times
  forgetting       forget-user wipes it; forget-name redacts the transcript and drops the audio; a transcript landing later is
                   redacted against the ledger; an erase mid-upload wins
  kept out         NOTHING else reads the table: no fact extraction, recall or digest (a static guard over the module tree)

Synthetic data only. The DB edge is a real SQLite file built by migration 0045 behind ``db_compat``; Moonshine is a fake at
``omi_recordings._transcribe_file``; no model, no network, no hardware.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sqlite3
import struct
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import db_compat
import omi_recordings as omi
import user_erase_gate as erase_gate
from db_pool import _Cursor, _ExecResult
from routers import omi_recordings as omi_router

SVC = Path(__file__).resolve().parents[1]
REPO = SVC.parents[1]
OWNER, OTHER = "owner-a", "member-b"
DEVICE = "dev0123456789abc"
TOKEN = {"X-Device-Token": "tok"}
UTC = timezone.utc


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)      # TestClient runs the app in its own thread
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


def _migrate(engine, fname="0045_omi_recordings.py", down=False):
    spec = importlib.util.spec_from_file_location("mig_omi", SVC / "alembic/versions" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        (mod.downgrade if down else mod.upgrade)()


def make_wav(seconds: float, amp: int = 6000, hz: int = 220) -> bytes:
    import math
    n = int(16000 * seconds)
    pcm = b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * hz * i / 16000))) for i in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


def headers(wav: bytes, *, key="r1.0-39", started="2026-10-10T01:00:00Z", duration=3.0, quality="device", gap=0, device=DEVICE, sha=None):
    return {**TOKEN, "Content-Type": "audio/wav", "X-Omi-Device": device, "X-Omi-Key": key, "X-Omi-Started-At": started,
            "X-Omi-Duration-S": str(duration), "X-Omi-Time-Quality": quality, "X-Omi-Gap-Before": str(gap),
            "X-Omi-Sha256": sha if sha is not None else hashlib.sha256(wav).hexdigest()}


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    _migrate(engine)
    db = _Sqlite(path)
    db.conn.execute("CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT, type TEXT, title TEXT, message TEXT, data TEXT, "
                    "delivered INTEGER DEFAULT 0, created_at TEXT)")
    db.conn.create_function("NOW", 0, lambda: "2026-10-10 06:00:00+00")
    db.conn.commit()

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setenv("ZOE_OMI_SYNC_ENABLED", "1")
    monkeypatch.setenv("ZOE_OMI_OWNER_USER_ID", OWNER)
    monkeypatch.setenv("ZOE_OMI_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("ZOE_OMI_MIN_MEM_MB", "0")
    monkeypatch.setenv("NIGHT_DIR", str(tmp_path / "night"))          # never read the live ~/.zoe/night-window marker
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.delenv("ZOE_OMI_KEEP_AUDIO", raising=False)
    monkeypatch.delenv("ZOE_OMI_AUDIO_RETENTION_DAYS", raising=False)
    monkeypatch.setattr(omi, "voice_busy", lambda now_mono=None: False)
    # The night-window gate reads the WALL CLOCK when called without ``now`` (``transcribe_pending`` does), and the household-local
    # 02:30-04:30 window is 18:30-20:30 UTC - so a run landing there (CI is UTC and runs at any hour) silently deferred every
    # transcription and ~11 tests failed with ``'pending' == 'done'``. Pin the default to midday household time; an explicit ``now``
    # still goes through the real function (test_night_window_clock_and_marker), so the gate itself stays covered.
    real_night_window = omi.night_window
    midday = datetime(2026, 10, 10, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(omi, "night_window", lambda now=None: real_night_window(now or midday))
    erase_gate.reset()
    omi._deferred_logged.clear()
    omi._clear_deferred()

    calls: list[str] = []
    texts = {"n": 0}

    async def fake_stt(path):
        calls.append(path)
        texts["n"] += 1
        return f"words{texts['n']}"

    monkeypatch.setattr(omi, "_transcribe_file", fake_stt)

    import routers.panel_auth as pa
    monkeypatch.setattr(pa, "lookup_device_token", lambda raw: {"panel_id": "p1"} if raw == "tok" else None)

    app = FastAPI()
    assert omi_router.register(app) is True
    app.dependency_overrides[auth.require_signed_in] = lambda: {"user_id": OWNER, "role": "member"}
    client = TestClient(app)
    yield type("Env", (), {"db": db, "client": client, "app": app, "stt": calls, "tmp": tmp_path, "engine": engine})
    erase_gate.reset()


def upload(env, wav=None, **kw):
    wav = wav if wav is not None else make_wav(3.0)
    return env.client.post("/api/omi/recordings", content=wav, headers=headers(wav, **kw))


def rows(env, sql="SELECT * FROM omi_recordings ORDER BY created_at"):
    return env.db.rows(sql)


def run(coro):
    return asyncio.run(coro)


# --- flag + auth ------------------------------------------------------------------------------------------------
def test_flag_off_routes_are_absent(monkeypatch):
    monkeypatch.delenv("ZOE_OMI_SYNC_ENABLED", raising=False)
    app = FastAPI()
    assert omi_router.register(app) is False
    c = TestClient(app)
    assert c.post("/api/omi/recordings", content=b"x", headers=TOKEN).status_code == 404
    assert c.get("/api/omi/recordings").status_code == 404


def test_upload_needs_a_device_token_and_a_session_is_not_enough(env):
    wav = make_wav(1.0)
    no_tok = {k: v for k, v in headers(wav).items() if k != "X-Device-Token"}
    assert env.client.post("/api/omi/recordings", content=wav, headers=no_tok).status_code == 401
    bad = {**headers(wav), "X-Device-Token": "wrong"}
    assert env.client.post("/api/omi/recordings", content=wav, headers=bad).status_code == 401
    assert rows(env) == []        # (the dependency override that signs the OWNER in did not let a tokenless upload through)


def test_no_owner_configured_means_no_upload(env, monkeypatch):
    monkeypatch.delenv("ZOE_OMI_OWNER_USER_ID")
    r = upload(env)
    assert r.status_code == 503 and r.json()["error"] == "owner not configured"
    assert rows(env) == []


def test_recordings_are_owner_only_even_for_an_admin(env):
    rid = upload(env).json()["id"]
    env.app.dependency_overrides[auth.require_signed_in] = lambda: {"user_id": OTHER, "role": "admin"}
    assert env.client.get("/api/omi/recordings").status_code == 403
    assert env.client.get(f"/api/omi/recordings/{rid}").status_code == 403
    assert env.client.delete(f"/api/omi/recordings/{rid}").status_code == 403
    assert env.client.patch(f"/api/omi/recordings/{rid}", json={"reviewed": True}).status_code == 403
    assert len(rows(env)) == 1 and rows(env)[0]["status"] != "deleted"


# --- the custody answer + provenance ------------------------------------------------------------------------------------
def test_a_stored_recording_is_confirmed_with_the_digest_of_the_stored_bytes(env):
    wav = make_wav(3.0)
    r = upload(env, wav, started="2026-10-10T01:00:05Z", duration=3.0, quality="corrected", gap=4)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["duplicate"] is False and body["sha256"] == hashlib.sha256(wav).hexdigest()
    (row,) = rows(env)
    assert row["user_id"] == OWNER and row["source"] == "omi" and row["provenance"] == "recording"
    assert row["started_at"] == "2026-10-10T01:00:05Z" and row["ended_at"] == "2026-10-10T01:00:08Z"     # the PENDANT's time, not created_at
    assert row["time_quality"] == "corrected" and row["gap_before"] == 4
    assert row["speaker_state"] == "unverified" and row["reviewed"] == 0 and row["status"] == "pending"
    assert Path(row["audio_path"]).read_bytes() == wav and (Path(row["audio_path"]).stat().st_mode & 0o777) == 0o600
    exp = datetime.strptime(row["audio_expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert timedelta(days=6, hours=23) < exp - datetime.now(UTC) < timedelta(days=7, hours=1)           # 7-day audio retention


def test_the_pi_side_classifier_accepts_exactly_this_response(env):
    """Both halves of the contract in one test: omi_sync.uploader.classify(<this server's real answer>) is CONFIRMED."""
    sys.path.insert(0, str(REPO / "services" / "omi-sync"))
    try:
        from omi_sync.uploader import Outcome, classify
    finally:
        sys.path.pop(0)
    wav = make_wav(2.0)
    r = upload(env, wav)
    assert classify(r.status_code, r.content, hashlib.sha256(wav).hexdigest()).outcome is Outcome.CONFIRMED
    assert classify(r.status_code, r.content, "0" * 64).outcome is Outcome.UNAVAILABLE      # a different digest is never a confirmation
    dup = upload(env, wav)
    assert classify(dup.status_code, dup.content, hashlib.sha256(wav).hexdigest()).outcome is Outcome.CONFIRMED


@pytest.mark.parametrize("mutate,status", [
    (lambda w, h: (w[:-1000], h), 422),                                              # truncated in flight: sha256 no longer matches
    (lambda w, h: (w, {**h, "X-Omi-Sha256": "0" * 64}), 422),
    (lambda w, h: (b"RIFFnotawav" + b"\x00" * 200, None), 422),                      # digest right, not a WAV
    (lambda w, h: (w, {**h, "X-Omi-Started-At": "yesterday"}), 422),
    (lambda w, h: (w, {**h, "X-Omi-Started-At": "2099-01-01T00:00:00Z"}), 422),
    (lambda w, h: (w, {**h, "X-Omi-Key": "../../etc/passwd"}), 400),
    (lambda w, h: (w, {**h, "X-Omi-Device": "x"}), 400),
    (lambda w, h: (w, {**h, "X-Omi-Time-Quality": "perfect"}), 400),
    (lambda w, h: (w, {**h, "X-Omi-Duration-S": "abc"}), 400),
])
def test_a_bad_upload_is_refused_and_nothing_is_stored(env, mutate, status):
    wav = make_wav(3.0)
    body, hdr = mutate(wav, headers(wav))
    hdr = hdr if hdr is not None else headers(body)
    r = env.client.post("/api/omi/recordings", content=body, headers=hdr)
    assert r.status_code == status and r.json()["ok"] is False
    assert rows(env) == [] and not list((env.tmp / "audio").glob("*.wav"))


def test_a_wav_that_is_not_16k_mono_is_refused(env):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x01\x00" * 44100)
    wav = buf.getvalue()
    assert upload(env, wav).status_code == 422


# --- idempotency + tombstone -----------------------------------------------------------------------------------------------
def test_the_same_key_twice_is_one_recording_and_one_file(env):
    wav = make_wav(3.0)
    first, second = upload(env, wav).json(), upload(env, wav).json()
    assert second["duplicate"] is True and second["id"] == first["id"]
    assert len(rows(env)) == 1 and len(list((env.tmp / "audio").glob("*.wav"))) == 1


def test_changed_bytes_under_a_known_key_are_a_conflict_not_an_overwrite(env):
    a, b = make_wav(3.0), make_wav(3.0, hz=330)
    assert upload(env, a).status_code == 200
    r = upload(env, b)
    assert r.status_code == 409
    (row,) = rows(env)
    assert Path(row["audio_path"]).read_bytes() == a


def test_a_deleted_recording_is_acknowledged_and_never_resurrected(env):
    wav = make_wav(3.0)
    rid = upload(env, wav).json()["id"]
    assert env.client.delete(f"/api/omi/recordings/{rid}").status_code == 200
    (row,) = rows(env)
    assert row["status"] == "deleted" and row["transcript"] == "" and row["audio_path"] == "" and row["started_at"] == ""
    assert not list((env.tmp / "audio").glob("*.wav"))
    assert env.client.get(f"/api/omi/recordings/{rid}").status_code == 404
    assert env.client.get("/api/omi/recordings").json()["recordings"] == []
    again = upload(env, wav)                                       # the Pi never saw the confirmation and retries
    assert again.json() == {"ok": True, "deleted": True, "key": "r1.0-39"}
    assert not list((env.tmp / "audio").glob("*.wav")) and len(rows(env)) == 1


def test_an_erase_during_an_upload_wins(env):
    wav = make_wav(2.0)
    stale = erase_gate.generation(OWNER)
    run(omi.forget_user(OWNER))                                    # bumps the generation
    with pytest.raises(omi.StoreError) as ei:
        run(omi.store_recording(OWNER, device_id=DEVICE, key="r1.0-9", sha256=hashlib.sha256(wav).hexdigest(),
                                started_at="2026-10-10T01:00:00Z", duration_s=2.0, time_quality="device", gap_before=0, wav=wav,
                                scheduled_at=stale))
    assert ei.value.status == 409 and rows(env) == []


# --- list / get / patch -------------------------------------------------------------------------------------------------------
def test_list_get_and_patch(env):
    upload(env, make_wav(2.0), key="r1.0-9", started="2026-10-10T01:00:00Z")
    upload(env, make_wav(2.0, hz=300), key="r1.10-19", started="2026-10-10T02:00:00Z")
    lst = env.client.get("/api/omi/recordings").json()["recordings"]
    assert [x["recording_key"] for x in lst] == ["r1.10-19", "r1.0-9"]            # newest recording first
    assert all("transcript" not in x for x in lst)
    rid = lst[0]["id"]
    run(omi.transcribe_pending(limit=2, pause_s=0))
    got = env.client.get(f"/api/omi/recordings/{rid}").json()
    assert got["status"] == "done" and got["transcript"].startswith("words") and got["reviewed"] is False
    patched = env.client.patch(f"/api/omi/recordings/{rid}", json={"reviewed": True}).json()
    assert patched["reviewed"] is True


# --- transcription ---------------------------------------------------------------------------------------------------------------
def test_transcription_files_the_transcript_and_deletes_the_audio(env):
    wav = make_wav(45.0)
    rid = upload(env, wav, duration=45.0).json()["id"]
    path = rows(env)[0]["audio_path"]
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 1
    (row,) = rows(env)
    assert row["status"] == "done" and row["transcript"] == "words1 words2 words3"      # 45 s -> three ~20 s chunks, in order
    assert len(env.stt) == 3
    assert row["audio_path"] == "" and not Path(path).exists()                           # deleted right after transcription
    assert row["speaker_state"] == "unverified" and row["reviewed"] == 0                 # still unreviewed, still unverified
    assert env.client.get(f"/api/omi/recordings/{rid}").json()["has_audio"] is False


def test_keep_audio_keeps_the_wav_until_its_retention_expires(env, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO", "1")
    upload(env, make_wav(3.0))
    path = rows(env)[0]["audio_path"]
    run(omi.transcribe_pending(limit=1, pause_s=0))
    assert rows(env)[0]["status"] == "done" and Path(path).exists()
    assert run(omi.purge_expired_audio(now=datetime.now(UTC) + timedelta(days=6))) == 0 and Path(path).exists()
    assert run(omi.purge_expired_audio(now=datetime.now(UTC) + timedelta(days=8))) == 1
    assert not Path(path).exists() and rows(env)[0]["audio_path"] == "" and rows(env)[0]["transcript"].startswith("words")   # text outlives the audio


def test_patching_keep_audio_off_drops_an_already_transcribed_wav(env, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO", "1")
    rid = upload(env, make_wav(3.0)).json()["id"]
    path = rows(env)[0]["audio_path"]
    run(omi.transcribe_pending(limit=1, pause_s=0))
    assert Path(path).exists()
    assert env.client.patch(f"/api/omi/recordings/{rid}", json={"keep_audio": False}).json()["has_audio"] is False
    assert not Path(path).exists()


def test_silence_is_not_sent_to_moonshine_and_its_audio_is_kept(env, caplog):
    import logging
    upload(env, make_wav(30.0, amp=20))                                   # room noise: peak 20 << the 300 floor
    path = rows(env)[0]["audio_path"]
    with caplog.at_level(logging.WARNING, logger="omi_recordings"):
        run(omi.transcribe_pending(limit=1, pause_s=0))
    (row,) = rows(env)
    assert row["status"] == "empty_transcript" and row["transcript"] == "" and env.stt == []
    # THE SAFETY RULE: no text is not a success, so the only copy of the audio is NOT deleted ...
    assert Path(path).exists() and row["audio_path"] == path and row["audio_expires_at"]
    # ... and the WARNING carries what the audio actually was, so "deaf pipeline" and "quiet room" are tellable apart
    (msg,) = [r.getMessage() for r in caplog.records if "OMI_EMPTY_TRANSCRIPT" in r.getMessage()]
    assert "seconds=30.0" in msg and "peak=20" in msg and "rate=16000" in msg and "voiced_chunks=0/2" in msg and "below_silence_floor" in msg
    # surfaced to the owner: the status on the row, the held count on the list
    lst = env.client.get("/api/omi/recordings").json()
    assert lst["empty_transcript_held"] == 1 and lst["recordings"][0]["status"] == "empty_transcript" and lst["recordings"][0]["has_audio"] is True


def test_voiced_audio_that_moonshine_returns_nothing_for_is_kept_too(env, monkeypatch, caplog):
    import logging

    async def says_nothing(path):
        return "   "

    monkeypatch.setattr(omi, "_transcribe_file", says_nothing)
    upload(env, make_wav(3.0))                                            # a loud tone: it DOES reach Moonshine
    path = rows(env)[0]["audio_path"]
    with caplog.at_level(logging.WARNING, logger="omi_recordings"):
        assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 1
    (row,) = rows(env)
    assert row["status"] == "empty_transcript" and Path(path).exists()
    (msg,) = [r.getMessage() for r in caplog.records if "OMI_EMPTY_TRANSCRIPT" in r.getMessage()]
    assert "voiced_chunks=1/1" in msg and "voiced_but_no_text" in msg and "peak=6000" in msg


def test_negative_control_text_still_deletes_the_audio(env):
    """The empty-transcript rule must not have turned the normal path into 'keep everything': real text still deletes the WAV."""
    upload(env, make_wav(3.0))
    path = rows(env)[0]["audio_path"]
    run(omi.transcribe_pending(limit=1, pause_s=0))
    (row,) = rows(env)
    assert row["status"] == "done" and row["transcript"] == "words1" and row["audio_path"] == "" and not Path(path).exists()
    assert env.client.get("/api/omi/recordings").json()["empty_transcript_held"] == 0


def test_an_empty_transcript_wav_still_expires_with_its_retention(env):
    upload(env, make_wav(5.0, amp=10))
    path = rows(env)[0]["audio_path"]
    run(omi.transcribe_pending(limit=1, pause_s=0))
    assert Path(path).exists() and rows(env)[0]["status"] == "empty_transcript"
    assert run(omi.purge_expired_audio(now=datetime.now(UTC) + timedelta(days=6))) == 0 and Path(path).exists()
    assert run(omi.purge_expired_audio(now=datetime.now(UTC) + timedelta(days=8))) == 1          # bounded: never kept forever
    assert not Path(path).exists() and rows(env)[0]["status"] == "empty_transcript"


def test_the_owner_can_still_drop_or_delete_an_empty_transcript_wav(env):
    a = upload(env, make_wav(5.0, amp=10), key="r1.0-9").json()["id"]
    b = upload(env, make_wav(5.0, amp=10), key="r1.10-19", started="2026-10-10T02:00:00Z").json()["id"]
    run(omi.transcribe_pending(limit=2, pause_s=0))
    pa, pb = [r["audio_path"] for r in rows(env)]
    assert Path(pa).exists() and Path(pb).exists()
    assert env.client.patch(f"/api/omi/recordings/{a}", json={"keep_audio": False}).json()["has_audio"] is False
    assert not Path(pa).exists()
    assert env.client.delete(f"/api/omi/recordings/{b}").status_code in (200, 204) and not Path(pb).exists()


def test_a_wrong_rate_or_stereo_wav_is_refused_never_stored_or_confirmed(env):
    """An 8 kHz stream labelled/uploaded as a recording (or stereo) is the 'mis-decoded audio' failure: the Pi must hear a 4xx."""
    def wav_with(rate, channels):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"\x10\x10" * rate * channels)
        return buf.getvalue()

    for rate, ch in ((8000, 1), (48000, 1), (16000, 2)):
        r = upload(env, wav_with(rate, ch), key=f"bad{rate}{ch}")
        assert r.status_code == 422 and r.json().get("ok") is False
    assert rows(env) == [] and not list((env.tmp / "audio").glob("*.wav"))
    assert upload(env, wav_with(16000, 1), key="good").json()["ok"] is True          # control: the right format is accepted


def test_measure_wav_reports_what_the_audio_is():
    tone = omi.measure_wav(make_wav(2.0, amp=6000))
    assert tone["seconds"] == 2.0 and tone["peak"] == 6000 and 4200 < tone["rms"] < 4300 and tone["rate"] == 16000 and tone["channels"] == 1
    silent = omi.measure_wav(make_wav(2.0, amp=0))
    assert silent["peak"] == 0 and silent["rms"] == 0.0 and silent["seconds"] == 2.0
    assert omi.measure_wav(b"not a wav") == {"seconds": 0.0, "peak": 0, "rms": 0.0, "rate": 0, "channels": 0, "width": 0,
                                            "clipped": 0, "rms_median": 0.0, "active_s": 0.0}
    assert tone["clipped"] == 0 and tone["active_s"] == 2.0 and 4200 < tone["rms_median"] < 4300


# --- the pendant's wake transient vs sustained sound (synthetic; measured shape 2026-10-16, numbers only) ---------------------------------
def _pcm_to_wav(samples) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, int(v)))) for v in samples))
    return buf.getvalue()


def _room(seconds: float, rms: float = 60.0, seed: int = 7) -> list:
    import random
    rnd = random.Random(seed)
    return [rnd.gauss(0.0, rms) for _ in range(int(16000 * seconds))]


def wake_transient_wav(seconds: float = 11.0, at_s: float = 0.1) -> bytes:
    """What 27 of the 32 kept empty recordings are: ~10 s of room noise (100 ms-window RMS ~60) opened by ONE full-scale click -
    a ~10 ms slab clipped at -32768 that decays over ~50 ms (the pendant mic's wake transient + the firmware's 10 s VAD hold)."""
    x = _room(seconds)
    k = int(16000 * at_s)
    for j in range(160):
        x[k + j] = -32768.0                                   # the clipped slab
    for j in range(800):                                      # recovery, sign-flipping like the measured ringing
        x[k + 160 + j] += 12000.0 * (0.993 ** j) * (1 if j % 2 else -1)
    return _pcm_to_wav(x)


def speechlike_wav(seconds: float = 6.0, *, bursts: int = 8) -> bytes:
    """Syllable-like bursts (a 140 Hz voice with harmonics, ~250 ms on, amplitude ~4000) over the same room noise: sustained sound."""
    import math
    x = _room(seconds, seed=11)
    for b in range(bursts):
        start = int(16000 * (0.4 + b * 0.6))
        for j in range(4000):
            if start + j >= len(x):
                break
            t = (start + j) / 16000
            x[start + j] += math.sin(math.pi * j / 4000) * 4000 * sum(math.sin(2 * math.pi * 140 * h * t) / h for h in (1, 2, 3, 4))
    return _pcm_to_wav(x)


def test_measure_wav_tells_a_wake_click_from_sustained_sound():
    click = omi.measure_wav(wake_transient_wav())
    # the whole-file numbers LOOK loud and clipped - exactly what made quiet recordings read as "voiced, peak 32768, rms 700"
    assert click["peak"] == 32768 and click["clipped"] >= 160 and click["rms"] > 500
    # ... the transient-robust ones say what it is: a room at ~60 RMS and under 0.5 s of sustained sound
    assert click["rms_median"] < 100 and click["active_s"] < omi.TRANSIENT_ONLY_S
    speech = omi.measure_wav(speechlike_wav())
    assert speech["clipped"] == 0 and speech["active_s"] >= 1.0
    room = omi.measure_wav(_pcm_to_wav(_room(5.0)))                         # negative control: nothing at all
    assert room["active_s"] == 0.0 and room["clipped"] == 0 and room["peak"] < omi.SILENCE_PEAK


def test_empty_reason_labels():
    click, speech = omi.measure_wav(wake_transient_wav()), omi.measure_wav(speechlike_wav())
    assert omi.empty_reason({"voiced": 0, "chunks": 1}, click) == "below_silence_floor"
    assert omi.empty_reason({"voiced": 1, "chunks": 1}, click) == "transient_only"
    assert omi.empty_reason({"voiced": 1, "chunks": 1}, speech) == "voiced_but_no_text"
    assert omi.empty_reason({"voiced": 1, "chunks": 1}, {}) == "transient_only"          # unmeasurable: never claims "voiced"


def test_a_click_only_recording_is_labelled_transient_only_and_kept(env, monkeypatch, caplog):
    import logging

    async def says_nothing(path):
        return ""

    monkeypatch.setattr(omi, "_transcribe_file", says_nothing)
    upload(env, wake_transient_wav(), duration=11.0)
    path = rows(env)[0]["audio_path"]
    with caplog.at_level(logging.WARNING, logger="omi_recordings"):
        assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 1
    (row,) = rows(env)
    assert row["status"] == "empty_transcript" and Path(path).exists()                  # still kept: the label is not a verdict
    (msg,) = [r.getMessage() for r in caplog.records if "OMI_EMPTY_TRANSCRIPT" in r.getMessage()]
    assert "reason=transient_only" in msg and "peak=32768" in msg and "voiced_chunks=1/1" in msg
    assert re.search(r"active_s=0\.\d ", msg) and re.search(r"rms_median=\d{2}\.\d ", msg)


def test_sustained_sound_with_no_words_is_still_voiced_but_no_text(env, monkeypatch, caplog):
    import logging

    async def says_nothing(path):
        return ""

    monkeypatch.setattr(omi, "_transcribe_file", says_nothing)
    upload(env, speechlike_wav(), duration=6.0)
    with caplog.at_level(logging.WARNING, logger="omi_recordings"):
        run(omi.transcribe_pending(limit=1, pause_s=0))
    (msg,) = [r.getMessage() for r in caplog.records if "OMI_EMPTY_TRANSCRIPT" in r.getMessage()]
    assert "reason=voiced_but_no_text" in msg


def test_the_transient_label_is_never_a_gate_a_click_recording_still_reaches_moonshine(env):
    """Negative control for the label: Moonshine runs its own VAD, so a short word after a click must not be skipped by ours."""
    upload(env, wake_transient_wav(), duration=11.0)
    run(omi.transcribe_pending(limit=1, pause_s=0))
    (row,) = rows(env)
    assert len(env.stt) == 1 and row["status"] == "done" and row["transcript"] == "words1"


# --- requeue: held recordings can be transcribed again inside their retention ---------------------------------------------------------
def _held(env, monkeypatch, n=2):
    async def says_nothing(path):
        return ""

    monkeypatch.setattr(omi, "_transcribe_file", says_nothing)
    ids = [upload(env, make_wav(3.0), key=f"r1.{i * 10}-{i * 10 + 9}", started=f"2026-10-10T0{i}:00:00Z").json()["id"] for i in range(n)]
    run(omi.transcribe_pending(limit=n, pause_s=0))
    assert {r["status"] for r in rows(env)} == {"empty_transcript"}
    return ids


def _status(env) -> dict:
    return {r["id"]: r["status"] for r in rows(env)}


def test_the_owner_requeues_held_recordings_and_a_fixed_pipeline_files_text(env, monkeypatch):
    ids = _held(env, monkeypatch)
    before = {r["id"]: r["audio_expires_at"] for r in rows(env)}
    r = env.client.post("/api/omi/recordings/retranscribe")
    assert r.status_code == 200 and r.json() == {"ok": True, "queued": 2}
    got = {r["id"]: r for r in rows(env)}
    assert all(got[i]["status"] == "pending" and got[i]["attempts"] == 0 and got[i]["transcribed_at"] is None for i in ids)
    assert {i: got[i]["audio_expires_at"] for i in ids} == before                         # retention NOT extended

    async def fixed(path):
        return "now heard"

    monkeypatch.setattr(omi, "_transcribe_file", fixed)
    assert run(omi.transcribe_pending(limit=2, pause_s=0)) == 2
    assert set(_status(env).values()) == {"done"} and all(r["audio_path"] == "" for r in rows(env))
    assert env.client.post("/api/omi/recordings/retranscribe").json()["queued"] == 0     # nothing held any more


def test_requeue_one_and_what_is_not_requeueable(env, monkeypatch):
    a, b = _held(env, monkeypatch)
    assert env.client.post(f"/api/omi/recordings/{a}/retranscribe").json() == {"ok": True, "queued": 1}
    assert _status(env) == {a: "pending", b: "empty_transcript"}
    assert env.client.post(f"/api/omi/recordings/{a}/retranscribe").status_code == 404   # already pending
    assert env.client.post("/api/omi/recordings/nope/retranscribe").status_code == 404
    Path(env.db.rows("SELECT audio_path FROM omi_recordings WHERE id = ?", (b,))[0]["audio_path"]).unlink()
    assert env.client.post(f"/api/omi/recordings/{b}/retranscribe").status_code == 404   # the WAV is gone: nothing to transcribe
    assert env.client.delete(f"/api/omi/recordings/{a}").status_code == 200
    assert run(omi.requeue_held(None)) == 0                                               # a tombstone is never resurrected
    assert _status(env) == {a: "deleted", b: "empty_transcript"}


def test_requeue_is_per_member_and_a_failed_row_counts(env, monkeypatch):
    (a,) = _held(env, monkeypatch, n=1)
    env.db.conn.execute("UPDATE omi_recordings SET user_id = ? WHERE id = ?", (OTHER, a))
    env.db.conn.commit()
    assert env.client.post("/api/omi/recordings/retranscribe").json()["queued"] == 0      # the owner cannot requeue another member's row
    assert env.client.post(f"/api/omi/recordings/{a}/retranscribe").status_code == 404
    env.db.conn.execute("UPDATE omi_recordings SET status = 'failed', attempts = 3, user_id = ? WHERE id = ?", (OWNER, a))
    env.db.conn.commit()
    assert env.client.post("/api/omi/recordings/retranscribe").json()["queued"] == 1
    assert rows(env)[0]["status"] == "pending" and rows(env)[0]["attempts"] == 0


def test_a_pipeline_change_requeues_held_recordings_once(env, monkeypatch):
    _held(env, monkeypatch)
    marker = omi._pipeline_marker()
    assert not marker.exists()
    assert run(omi.requeue_after_pipeline_change()) == 2 and marker.read_text().strip() == omi.TRANSCRIBE_PIPELINE
    run(omi.transcribe_pending(limit=2, pause_s=0))                                       # still nothing heard: held again
    assert set(_status(env).values()) == {"empty_transcript"}
    assert run(omi.requeue_after_pipeline_change()) == 0                                  # ONCE per pipeline: no requeue loop
    monkeypatch.setattr(omi, "TRANSCRIBE_PIPELINE", omi.TRANSCRIBE_PIPELINE + ".next")
    assert run(omi.requeue_after_pipeline_change()) == 2                                  # the next change reaches them again


def test_a_marker_that_cannot_be_written_requeues_nothing_so_it_can_never_loop(env, monkeypatch):
    """Negative control for the once-per-pipeline guarantee: claim-then-requeue. With the marker unwritable, requeue-then-write would
    requeue on EVERY loop pass (each pass re-transcribing whatever came back empty) - here no pass requeues anything."""
    _held(env, monkeypatch)

    def no_write(marker, value):
        raise PermissionError("read-only")

    monkeypatch.setattr(omi, "_write_marker", no_write)
    for _ in range(3):
        assert run(omi.requeue_after_pipeline_change()) == 0
        assert set(_status(env).values()) == {"empty_transcript"}
    assert env.client.post("/api/omi/recordings/retranscribe").json()["queued"] == 2       # the owner route still works


def test_a_failed_requeue_restores_the_marker_so_the_next_pass_retries(env, monkeypatch):
    _held(env, monkeypatch)
    marker = omi._pipeline_marker()
    omi._write_marker(marker, "older-pipeline")
    real = omi.requeue_held

    async def db_down(*a, **k):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(omi, "requeue_held", db_down)
    with pytest.raises(RuntimeError):
        run(omi.requeue_after_pipeline_change())
    assert marker.read_text().strip() == "older-pipeline"                                  # put back: not silently "done"
    monkeypatch.setattr(omi, "requeue_held", real)
    assert run(omi.requeue_after_pipeline_change()) == 2 and marker.read_text().strip() == omi.TRANSCRIBE_PIPELINE


def test_a_requeue_cancelled_part_way_restores_the_marker_and_the_next_pass_requeues_the_rest(env, monkeypatch):
    """asyncio.CancelledError is a BaseException: an ``except Exception`` restore would leave the marker claimed with only part of
    the rows requeued, and the rest would never be requeued for this pipeline."""
    a, b = _held(env, monkeypatch)
    marker = omi._pipeline_marker()
    omi._write_marker(marker, "older-pipeline")
    real = omi.requeue_held

    async def cancelled_part_way(user_id=None, *, rid=None):
        assert await real(user_id, rid=a) == 1               # one row requeued ...
        raise asyncio.CancelledError()                        # ... then the loop task is cancelled (shutdown)

    monkeypatch.setattr(omi, "requeue_held", cancelled_part_way)
    with pytest.raises(asyncio.CancelledError):
        run(omi.requeue_after_pipeline_change())
    assert marker.read_text().strip() == "older-pipeline"
    assert _status(env) == {a: "pending", b: "empty_transcript"}
    monkeypatch.setattr(omi, "requeue_held", real)
    assert run(omi.requeue_after_pipeline_change()) == 1                                  # the remainder, and only the remainder
    assert set(_status(env).values()) == {"pending"} and marker.read_text().strip() == omi.TRANSCRIBE_PIPELINE


def test_the_marker_write_fsyncs_its_directory(env, monkeypatch):
    """A rename is not durable until its directory is fsynced: without it a crash can bring the old marker back (a second requeue)."""
    import os
    import stat
    marker = omi._pipeline_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    dir_ino = marker.parent.stat().st_ino
    real_fsync = os.fsync
    synced: list[tuple[bool, int]] = []

    def spy(fd):
        st = os.fstat(fd)
        synced.append((stat.S_ISDIR(st.st_mode), st.st_ino))
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    omi._write_marker(marker, "p1")
    assert (True, dir_ino) in synced and (False, marker.stat().st_ino) in synced           # the file AND its directory
    synced.clear()
    omi._write_marker(marker, None)                                                       # clearing it is a directory change too
    assert (True, dir_ino) in synced and not marker.exists()


def test_a_row_past_its_retention_is_never_requeued_even_while_its_wav_waits_for_the_purge(env, monkeypatch):
    """Negative control for retention: between ``audio_expires_at`` and the next purge pass the WAV is still on disk. Requeueing it
    then would transcribe past retention (or leave a pending row whose audio the purge removes). Neither route nor the automatic pass
    may pick it up; the live row beside it still requeues (so the filter is not just 'requeue nothing')."""
    live, expired = _held(env, monkeypatch)
    past = (datetime.now(UTC) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    env.db.conn.execute("UPDATE omi_recordings SET audio_expires_at = ? WHERE id = ?", (past, expired))
    env.db.conn.commit()
    exp_path = env.db.rows("SELECT audio_path FROM omi_recordings WHERE id = ?", (expired,))[0]["audio_path"]
    assert Path(exp_path).exists()                                                        # the WAV is still there: only the clock says no
    assert env.client.post(f"/api/omi/recordings/{expired}/retranscribe").status_code == 404
    assert env.client.post("/api/omi/recordings/retranscribe").json()["queued"] == 1
    assert _status(env) == {live: "pending", expired: "empty_transcript"}
    env.db.conn.execute("UPDATE omi_recordings SET status = 'empty_transcript' WHERE id = ?", (live,))
    env.db.conn.commit()
    assert run(omi.requeue_after_pipeline_change()) == 1
    assert _status(env) == {live: "pending", expired: "empty_transcript"}
    # the guarded UPDATE re-checks it too: a row that expires between the SELECT and the UPDATE stays held
    env.db.conn.execute("UPDATE omi_recordings SET status = 'empty_transcript' WHERE id = ?", (live,))
    env.db.conn.commit()
    later = datetime.now(UTC) + timedelta(days=30)                                        # every row is past retention by then
    real_fetch = omi._fetch

    async def select_ignoring_expiry(db, sql, params=()):
        if sql.startswith("SELECT id, audio_path FROM omi_recordings WHERE status IN"):
            return await real_fetch(db, "SELECT id, audio_path FROM omi_recordings WHERE audio_path != ''")
        return await real_fetch(db, sql, params)

    monkeypatch.setattr(omi, "_fetch", select_ignoring_expiry)
    assert run(omi.requeue_held(None, now=later)) == 0
    assert set(_status(env).values()) == {"empty_transcript"}


def test_an_expired_held_recording_is_purged_not_requeued(env, monkeypatch):
    _held(env, monkeypatch, n=1)
    assert run(omi.purge_expired_audio(now=datetime.now(UTC) + timedelta(days=8))) == 1
    assert run(omi.requeue_held(None)) == 0 and rows(env)[0]["status"] == "empty_transcript"


def test_the_night_window_blocks_transcription(env, monkeypatch):
    upload(env, make_wav(3.0))
    monkeypatch.setattr(omi, "night_window", lambda now=None: True)
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 0
    assert env.stt == [] and rows(env)[0]["status"] == "pending"
    monkeypatch.setattr(omi, "night_window", lambda now=None: False)      # control: the same row transcribes once the window is shut
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 1 and len(env.stt) == 1


def test_night_window_clock_and_marker(env, tmp_path):
    perth = lambda h, m: datetime(2026, 10, 10, h, m, tzinfo=timezone(timedelta(hours=8)))          # noqa: E731
    assert [omi.night_window(perth(*hm)) for hm in ((2, 29), (2, 30), (3, 0), (4, 29), (4, 30), (12, 0))] == [False, True, True, True, False, False]
    marker = tmp_path / "night"
    marker.mkdir(exist_ok=True)
    (marker / "WINDOW_OPEN").write_text("units")
    assert omi.night_window(perth(12, 0)) is True                         # the 12B window marker counts even at noon


def test_a_live_voice_turn_defers_transcription(env, monkeypatch):
    upload(env, make_wav(3.0))
    monkeypatch.setattr(omi, "voice_busy", lambda now_mono=None: True)
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 0 and env.stt == []


def test_the_ram_floor_defers_transcription(env, monkeypatch):
    upload(env, make_wav(3.0))
    monkeypatch.setenv("ZOE_OMI_MIN_MEM_MB", "1500")
    monkeypatch.setattr(omi, "mem_available_mb", lambda: 900.0)
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 0 and env.stt == []


def test_the_flag_off_transcribes_nothing(env, monkeypatch):
    upload(env, make_wav(3.0))
    monkeypatch.delenv("ZOE_OMI_SYNC_ENABLED")
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 0 and env.stt == []


def test_the_gate_closing_mid_job_puts_the_recording_back_untouched(env, monkeypatch):
    upload(env, make_wav(45.0), duration=45.0)
    state = {"n": 0}
    real = omi.may_transcribe_now

    def gate(now=None, **_kw):
        state["n"] += 1
        return (True, "") if state["n"] <= 3 else (False, "night window")        # open for the pre-check + 2 chunks, then closes

    monkeypatch.setattr(omi, "may_transcribe_now", gate)
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 0
    (row,) = rows(env)
    assert row["status"] == "pending" and row["transcript"] == "" and row["attempts"] == 0 and Path(row["audio_path"]).exists()
    monkeypatch.setattr(omi, "may_transcribe_now", real)
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 1


def test_a_failing_stt_is_retried_then_marked_failed_and_the_audio_kept(env, monkeypatch):
    upload(env, make_wav(3.0))

    async def boom(path):
        raise RuntimeError("moonshine exploded")

    monkeypatch.setattr(omi, "_transcribe_file", boom)
    for expected in ("pending", "pending", "failed"):
        run(omi.transcribe_pending(limit=1, pause_s=0))
        assert rows(env)[0]["status"] == expected
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 0              # no fourth attempt
    assert Path(rows(env)[0]["audio_path"]).exists()                         # nothing lost: the audio waits for its retention expiry


def test_a_recording_deleted_while_it_is_being_transcribed_stays_deleted(env, monkeypatch):
    rid = upload(env, make_wav(3.0)).json()["id"]

    async def slow_then_delete(path):
        run_delete = await omi.delete_recording(OWNER, rid)
        assert run_delete
        return "late words"

    monkeypatch.setattr(omi, "_transcribe_file", slow_then_delete)
    run(omi.transcribe_pending(limit=1, pause_s=0))
    (row,) = rows(env)
    assert row["status"] == "deleted" and row["transcript"] == ""


# --- chunking (pure) ----------------------------------------------------------------------------------------------------------------
def test_chunks_cover_the_audio_contiguously_and_cut_at_the_quietest_point():
    import math
    n = 16000 * 50
    pcm = bytearray()
    for i in range(n):
        in_gap = 18.40 <= i / 16000 < 18.60                                  # a 200 ms pause inside the last 3 s of chunk 1
        pcm += struct.pack("<h", 0 if in_gap else int(8000 * math.sin(i / 7.0)))
    chunks = omi.chunk_pcm(bytes(pcm))
    assert chunks[0][0] == 0 and chunks[-1][1] == len(pcm)
    assert all(a[1] == b[0] for a, b in zip(chunks, chunks[1:])) and all(b > a for a, b in chunks)
    assert 18.40 <= chunks[0][1] / 2 / 16000 <= 18.60                        # the cut landed in the pause, not mid-word at 20.0 s
    assert omi.chunk_pcm(b"") == []


# --- forgetting ----------------------------------------------------------------------------------------------------------------------------
def test_forget_user_wipes_audio_transcript_and_times(env):
    upload(env, make_wav(3.0), key="r1.0-9")
    upload(env, make_wav(3.0, hz=300), key="r1.10-19")
    run(omi.transcribe_pending(limit=1, pause_s=0))
    assert run(omi.forget_user(OWNER)) == 2
    for r in rows(env):
        assert (r["status"], r["transcript"], r["audio_path"], r["started_at"]) == ("deleted", "", "", "")
    assert not list((env.tmp / "audio").glob("*.wav"))


def test_forgetting_a_name_redacts_the_transcript_and_drops_the_audio(env, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO", "1")
    upload(env, make_wav(3.0))

    async def says_a_name(path):
        return "I saw Dana at the shop and Dana said hello"

    monkeypatch.setattr(omi, "_transcribe_file", says_a_name)
    run(omi.transcribe_pending(limit=1, pause_s=0))
    path = rows(env)[0]["audio_path"]
    assert Path(path).exists()
    assert run(omi.erase_entity(OWNER, re.compile(r"\bDana\b"))) == 1
    (row,) = rows(env)
    assert row["transcript"] == "I saw [forgotten] at the shop and [forgotten] said hello"
    assert row["audio_path"] == "" and not Path(path).exists() and row["keep_audio"] == 0


def test_a_transcript_that_lands_after_a_forget_is_redacted_against_the_ledger(env, monkeypatch):
    upload(env, make_wav(3.0))

    async def says_a_name(path):
        return "Dana called"

    import memory_forgotten as mf

    async def fake_spans(user_id, text, *, near=True):
        i = text.find("Dana")
        return [(i, i + 4)] if i >= 0 and user_id == OWNER else []

    monkeypatch.setattr(omi, "_transcribe_file", says_a_name)
    monkeypatch.setattr(mf, "spans", fake_spans)
    run(omi.transcribe_pending(limit=1, pause_s=0))
    assert rows(env)[0]["transcript"] == "[forgotten] called"


def test_the_cascade_and_the_delete_user_path_call_this_module():
    """The forget entry points reach us (a string guard: the two hooks must exist, and are the only callers)."""
    assert "omi_recordings.erase_entity(user_id, rx, strict=True)" in (SVC / "memory_forget_cascade.py").read_text()
    assert "omi_recordings.forget_user(user_id)" in (SVC / "memory_service.py").read_text()


# --- kept out of everything else -----------------------------------------------------------------------------------------------------------------
def test_nothing_else_reads_pendant_transcripts():
    """A pendant transcript is unreviewed and unverified: no fact extraction, recall, digest or prompt may read the table.
    Static guard: the only modules naming it are the module itself, its router, main (mount + loop), the two forget hooks and
    ``omi_devices.py`` (the one-time registry seed reads device_id + user_id only - never a transcript), and ``omi_day_review.py`` (the
    owner's day review: it PROPOSES candidates the owner approves; ``test_omi_day_review`` pins that it calls no write path)."""
    allowed = {"omi_recordings.py", "routers/omi_recordings.py", "main.py", "memory_service.py", "memory_forget_cascade.py",
               "omi_devices.py", "omi_day_review.py"}      # the day review (0054): proposals the owner approves, never a write of its own
    hits = set()
    for p in SVC.rglob("*.py"):
        rel = str(p.relative_to(SVC))
        if rel.startswith(("tests/", "alembic/", ".venv")):
            continue
        txt = p.read_text(errors="replace")
        if "omi_recordings" in txt or "FROM omi_recordings" in txt:
            hits.add(rel)
    assert hits <= allowed, f"unexpected readers of the pendant transcripts: {sorted(hits - allowed)}"


# --- the notice + the migration ---------------------------------------------------------------------------------------------------------------------------
def test_the_sync_notice_is_one_short_bell_row(env, monkeypatch):
    sent = []

    @contextlib.asynccontextmanager
    async def fake_ctx():
        yield env.db

    class _B:
        async def broadcast(self, channel, event, data, user_id=None):
            sent.append((channel, event, data, user_id))

    import db_pool
    import push
    monkeypatch.setattr(db_pool, "get_db_ctx", fake_ctx)
    monkeypatch.setattr(push, "broadcaster", _B())
    assert run(omi.notify_synced(OWNER, 3)) is True
    (n,) = env.db.rows("SELECT * FROM notifications")
    assert (n["user_id"], n["type"], n["message"]) == (OWNER, "omi_sync", "I've synced 3 recordings from your pendant.")
    assert sent and sent[0][3] == OWNER and sent[0][1] == "notification_created"
    assert omi.notice_text(1)[1] == "I've synced 1 recording from your pendant."
    assert run(omi.notify_synced(OWNER, 0)) is False


def test_the_notice_route_rate_limits_and_needs_the_device_token(env, monkeypatch):
    got = []

    async def fake_notify(uid, count):
        got.append((uid, count))
        return True

    monkeypatch.setattr(omi, "notify_synced", fake_notify)
    monkeypatch.setattr(omi_router, "_last_notice", {})
    assert env.client.post("/api/omi/sync-notice", json={"count": 2}).status_code == 401
    assert env.client.post("/api/omi/sync-notice", json={"count": 2}, headers=TOKEN).json() == {"ok": True, "sent": True}
    assert env.client.post("/api/omi/sync-notice", json={"count": 2}, headers=TOKEN).json() == {"ok": True, "sent": False}
    assert got == [(OWNER, 2)]


def test_migration_is_reversible_and_rerunnable(env):
    _migrate(env.engine)                                      # IF NOT EXISTS: a rerun is safe
    _migrate(env.engine, down=True)
    assert env.db.rows("SELECT name FROM sqlite_master WHERE name = 'omi_recordings'") == []


# === the RAM floor, deferral visibility, streamed reads (fix/omi-transcribe-floor) =======================================================
# Live failure this pins: MemAvailable floats around the floor, the per-chunk gate aborted a running job on the first dip, and the
# job restarted from chunk 0 each time - 34 of ~86 recordings in 2 h. Mutations worth re-running by Edit: make the mid-job gate use the
# START floor; drop the hold in ``_patient_gate``; make ``note_deferred`` log every call; read the file whole in ``_transcribe_one``.
import logging
import random
import tracemalloc
from array import array


def tone_wav_file(path: Path, seconds: int, amp: int = 6000) -> Path:
    one = struct.pack("<16000h", *[int(amp * ((i % 73) / 73.0 - 0.5)) for i in range(16000)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        for _ in range(seconds):
            w.writeframes(one)
    return path


def noise_wav_bytes(seconds: float, seed: int = 7) -> bytes:
    rng = random.Random(seed)
    a = array("h", (rng.randint(-8000, 8000) for _ in range(int(16000 * seconds))))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(a.tobytes())
    return buf.getvalue()


def test_the_default_floor_is_the_measured_one_and_the_boundary_is_inclusive(env, monkeypatch):
    monkeypatch.delenv("ZOE_OMI_MIN_MEM_MB")
    assert omi.min_mem_mb() == omi.DEFAULT_MIN_MEM_MB and omi.continue_floor_mb() == omi.DEFAULT_MIN_MEM_MB // 2
    floor = omi.min_mem_mb()
    monkeypatch.setattr(omi, "mem_available_mb", lambda: float(floor - 1))
    ok, why = omi.may_transcribe_now()
    assert not ok and omi.reason_code(why) == "low_memory"
    monkeypatch.setattr(omi, "mem_available_mb", lambda: float(floor))            # control: exactly the floor is enough
    assert omi.may_transcribe_now() == (True, "")


def test_a_running_job_yields_only_below_half_the_floor_but_a_new_one_needs_all_of_it(env, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_MIN_MEM_MB", "1000")
    monkeypatch.setattr(omi, "mem_available_mb", lambda: 700.0)
    assert omi.may_transcribe_now()[0] is False                                    # start: 700 < 1000
    assert omi.may_transcribe_now(continuing=True) == (True, "")                  # continue: 700 >= 500 - the dip does not kill the job
    monkeypatch.setattr(omi, "mem_available_mb", lambda: 499.0)
    assert omi.may_transcribe_now(continuing=True)[0] is False                    # negative control: a real shortage still stops it


def test_a_new_recording_waits_for_an_idle_panel_but_a_running_one_only_for_a_live_turn(env, monkeypatch):
    monkeypatch.delenv("ZOE_OMI_IDLE_S", raising=False)
    monkeypatch.setattr(omi, "last_voice_age_s", lambda now_mono=None: 60.0)      # a turn a minute ago: not live (45 s), not idle (120 s)
    ok, why = omi.may_transcribe_now()
    assert not ok and omi.reason_code(why) == "panel_not_idle"
    assert omi.may_transcribe_now(continuing=True) == (True, "")
    monkeypatch.setattr(omi, "last_voice_age_s", lambda now_mono=None: 121.0)     # control: idle long enough
    assert omi.may_transcribe_now() == (True, "")
    monkeypatch.setattr(omi, "last_voice_age_s", lambda now_mono=None: None)      # no turn since boot
    assert omi.may_transcribe_now() == (True, "")


def test_last_voice_age_reads_the_session_table_without_importing_it(monkeypatch):
    fake = type("M", (), {"_VOICE_SESSIONS": {"a": {"last_at": 90.0}, "b": {"last_at": 40.0}}})
    monkeypatch.setitem(sys.modules, "routers.voice_tts", fake)
    assert omi.last_voice_age_s(now_mono=100.0) == 10.0                            # the NEWEST session
    assert omi.voice_busy(now_mono=100.0) is True and omi.voice_busy(now_mono=200.0) is False
    monkeypatch.setitem(sys.modules, "routers.voice_tts", type("M", (), {"_VOICE_SESSIONS": {}}))
    assert omi.last_voice_age_s(now_mono=100.0) is None


def _deferred_lines(caplog):
    return [r.getMessage() for r in caplog.records if "OMI_TRANSCRIBE_DEFERRED" in r.getMessage()]


def test_deferral_is_logged_at_info_once_per_ten_minutes_per_reason(env, monkeypatch, caplog):
    upload(env, make_wav(3.0))
    upload(env, make_wav(3.0), key="r1.0-40")
    monkeypatch.setenv("ZOE_OMI_MIN_MEM_MB", "1500")
    monkeypatch.setattr(omi, "mem_available_mb", lambda: 1208.4)
    why = "MemAvailable 1208 MB < 1500 MB"
    with caplog.at_level(logging.INFO, logger="omi_recordings"):
        for t in (0.0, 30.0, 599.0):
            run(omi.note_deferred(why, now_mono=t))
        assert _deferred_lines(caplog) == ["OMI_TRANSCRIBE_DEFERRED reason=low_memory avail=1208 floor=1500 backlog=2"]
        run(omi.note_deferred(why, now_mono=600.0))                                 # control: the window elapses, it speaks again
        run(omi.note_deferred(omi.WHY_VOICE, now_mono=601.0))                       # a different reason has its own clock
    lines = _deferred_lines(caplog)
    assert len(lines) == 3 and lines[1].startswith("OMI_TRANSCRIBE_DEFERRED reason=low_memory") and "reason=voice_active" in lines[2]
    assert all(r.levelno == logging.INFO for r in caplog.records if "OMI_TRANSCRIBE_DEFERRED" in r.getMessage())


def test_deferral_is_silent_with_an_empty_backlog_and_for_the_expected_closures(env, monkeypatch, caplog):
    monkeypatch.setattr(omi, "mem_available_mb", lambda: 900.0)
    with caplog.at_level(logging.INFO, logger="omi_recordings"):
        run(omi.note_deferred("MemAvailable 900 MB < 1500 MB", now_mono=0.0))      # nothing waiting: not news
        upload(env, make_wav(3.0))
        run(omi.note_deferred("night window", now_mono=0.0))                        # state, never log noise
        assert omi.deferred_state()["reason"] == "night_window"
        run(omi.note_deferred("flag off", now_mono=0.0))
        assert not _deferred_lines(caplog)
        run(omi.note_deferred("MemAvailable 900 MB < 1500 MB", now_mono=0.0))      # control: with a backlog, memory does log
        assert len(_deferred_lines(caplog)) == 1


def test_a_gate_closed_pass_logs_and_the_list_shows_counts_and_the_reason_only(env, monkeypatch):
    upload(env, make_wav(3.0))
    upload(env, make_wav(3.0), key="r1.0-40")
    body = env.client.get("/api/omi/recordings").json()
    assert body["transcription"]["backlog"] == 2 and body["transcription"]["transcribing"] == 0
    assert body["transcription"]["deferred"] is False and body["transcription"]["reason"] is None
    monkeypatch.setenv("ZOE_OMI_MIN_MEM_MB", "1500")
    monkeypatch.setattr(omi, "mem_available_mb", lambda: 1100.0)
    assert run(omi.transcribe_pending(limit=3, pause_s=0)) == 0
    t = env.client.get("/api/omi/recordings").json()["transcription"]
    assert t["backlog"] == 2 and t["deferred"] is True and t["reason"] == "low_memory" and t["since"]
    assert t["mem_available_mb"] == 1100 and t["mem_floor_mb"] == 1500
    assert set(t) == {"backlog", "transcribing", "deferred", "reason", "since", "mem_available_mb", "mem_floor_mb"}      # counts, no content
    monkeypatch.setattr(omi, "mem_available_mb", lambda: 4000.0)                      # control: the gate opens, the state clears, work drains
    assert run(omi.transcribe_pending(limit=3, pause_s=0)) == 2
    t = env.client.get("/api/omi/recordings").json()["transcription"]
    assert t["backlog"] == 0 and t["deferred"] is False


def test_backlog_counts_pending_only_for_that_member_and_not_exhausted_rows(env):
    upload(env, make_wav(3.0))
    upload(env, make_wav(3.0), key="r1.0-40")
    assert run(omi.backlog_count(OWNER)) == 2 and run(omi.backlog_count(OTHER)) == 0 and run(omi.backlog_count()) == 2
    run(omi.transcribe_pending(limit=1, pause_s=0))
    assert run(omi.backlog_count(OWNER)) == 1                                        # done is not backlog
    env.db.conn.execute("UPDATE omi_recordings SET attempts = ? WHERE status = 'pending'", (omi.MAX_ATTEMPTS,))
    assert run(omi.backlog_count(OWNER)) == 0                                        # out of attempts is not waiting either


def test_a_transient_dip_mid_job_is_waited_out_not_restarted_from_zero(env, monkeypatch):
    upload(env, make_wav(45.0), duration=45.0)                                       # three chunks
    monkeypatch.setattr(omi, "HOLD_POLL_S", 0.0)
    monkeypatch.setenv("ZOE_OMI_MIN_MEM_MB", "1500")
    seen = {"n": 0}

    def mem():
        seen["n"] += 1
        return 100.0 if 3 <= seen["n"] <= 5 else 4000.0                              # open to start, then a short dip below HALF the floor

    monkeypatch.setattr(omi, "mem_available_mb", mem)
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 1
    (row,) = rows(env)
    assert row["status"] == "done" and row["transcript"] == "words1 words2 words3" and len(env.stt) == 3   # each chunk transcribed ONCE
    assert seen["n"] > 5                                                              # the dip was really observed and polled through


def test_the_hold_is_bounded_and_the_recording_goes_back_untouched(env, monkeypatch):
    upload(env, make_wav(45.0), duration=45.0)
    monkeypatch.setattr(omi, "HOLD_POLL_S", 0.0)
    monkeypatch.setattr(omi, "HOLD_MAX_S", 0.0)
    state = {"open": True}
    monkeypatch.setattr(omi, "mem_available_mb", lambda: 4000.0 if state["open"] else 10.0)
    monkeypatch.setenv("ZOE_OMI_MIN_MEM_MB", "1500")
    orig = omi._transcribe_file

    async def close_after_first(path):
        state["open"] = False                                                         # RAM collapses for good after chunk 1
        return await orig(path)

    monkeypatch.setattr(omi, "_transcribe_file", close_after_first)
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 0
    (row,) = rows(env)
    assert row["status"] == "pending" and row["attempts"] == 0 and Path(row["audio_path"]).exists() and row["transcript"] == ""


def test_a_decision_closure_mid_job_aborts_at_once_and_never_waits(env, monkeypatch):
    upload(env, make_wav(45.0), duration=45.0)
    monkeypatch.setattr(omi, "HOLD_POLL_S", 5.0)
    monkeypatch.setattr(omi, "HOLD_MAX_S", 3600.0)                                    # if the night window were waited on, this would time out
    flips = {"n": 0}

    def night(now=None):
        flips["n"] += 1
        return flips["n"] > 2

    monkeypatch.setattr(omi, "night_window", night)
    assert run(asyncio.wait_for(omi.transcribe_pending(limit=1, pause_s=0), timeout=10)) == 0
    assert rows(env)[0]["status"] == "pending"


# --- streamed reads ---------------------------------------------------------------------------------------------------------
class _Spy:
    """A wave reader that records the biggest read it was ever asked for."""
    def __init__(self, w):
        self.w, self.max_frames = w, 0

    def getframerate(self):
        return self.w.getframerate()

    def getnframes(self):
        return self.w.getnframes()

    def readframes(self, n):
        self.max_frames = max(self.max_frames, n)
        return self.w.readframes(n)


@pytest.mark.parametrize("seconds", [0.5, 19.9, 20.0, 20.1, 41.0, 65.3])
def test_the_streamed_chunks_are_exactly_the_whole_buffer_chunks(seconds):
    wav = noise_wav_bytes(seconds)
    with wave.open(io.BytesIO(wav)) as w:
        pcm = w.readframes(w.getnframes())
    expect = [pcm[a:b] for a, b in omi.chunk_pcm(pcm)]
    with wave.open(io.BytesIO(wav)) as w:
        got = list(omi.iter_wave_chunks(w))
    assert got == expect and b"".join(got) == pcm


def test_the_reader_never_asks_for_more_than_one_chunk(tmp_path):
    p = tone_wav_file(tmp_path / "long.wav", 120)
    with wave.open(str(p)) as w:
        spy = _Spy(w)
        n = sum(1 for _ in omi.iter_wave_chunks(spy))
        total = w.getnframes()
    assert n >= 6 and 0 < spy.max_frames <= int(omi.CHUNK_S * 16000)               # 120 s file, never more than 20 s requested at once
    assert total > 5 * spy.max_frames                                                # control: the file really is much bigger than one read


def test_a_wav_shorter_than_its_header_claims_terminates(tmp_path):
    p = tone_wav_file(tmp_path / "t.wav", 30)
    p.write_bytes(p.read_bytes()[:-400_000])                                         # header still promises 30 s
    with wave.open(str(p)) as w:
        got = b"".join(omi.iter_wave_chunks(w))
    assert 0 < len(got) < 30 * 32000


def test_transcribing_a_five_minute_recording_holds_no_whole_file_in_python(env, tmp_path):
    """tracemalloc over the PRODUCTION path (claim -> stream -> Moonshine seam -> write): the old path peaked at ~3x the file."""
    wav = tone_wav_file(tmp_path / "five.wav", 300).read_bytes()                     # 9.6 MB
    upload(env, wav, duration=300.0)
    tracemalloc.start()
    try:
        assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 1
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(env.stt) >= 15 and rows(env)[0]["status"] == "done"
    assert peak_bytes < len(wav) // 3, f"peak {peak_bytes} B for a {len(wav)} B file: something holds the whole recording"


def test_negative_control_the_whole_file_read_does_trip_the_memory_bound(tmp_path):
    """The instrument above must be able to fail: the OLD read-everything-then-chunk shape peaks at several times the file."""
    p = tone_wav_file(tmp_path / "five.wav", 300)
    size = p.stat().st_size
    tracemalloc.start()
    try:
        wav = p.read_bytes()
        with wave.open(io.BytesIO(wav)) as w:
            pcm = w.readframes(w.getnframes())
        _ = [pcm[a:b] for a, b in omi.chunk_pcm(pcm)]
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak_bytes > size


def test_measure_wav_file_equals_measure_wav_and_never_raises(tmp_path):
    wav = noise_wav_bytes(3.5)
    p = tmp_path / "m.wav"
    p.write_bytes(wav)
    assert omi.measure_wav_file(str(p)) == omi.measure_wav(wav)
    assert omi.measure_wav_file(str(tmp_path / "missing.wav"))["seconds"] == 0.0


def test_a_flag_flip_clears_a_stale_deferred_state_and_ends_a_running_job(env, monkeypatch):
    upload(env, make_wav(45.0), duration=45.0)
    monkeypatch.setattr(omi, "night_window", lambda now=None: True)
    run(omi.transcribe_pending(limit=1, pause_s=0))
    assert omi.deferred_state()["reason"] == "night_window"
    monkeypatch.delenv("ZOE_OMI_SYNC_ENABLED")
    run(omi.note_deferred("flag off"))
    assert omi.deferred_state()["deferred"] is False                                 # control: the night-window state above was real
    monkeypatch.setenv("ZOE_OMI_SYNC_ENABLED", "1")
    monkeypatch.setattr(omi, "night_window", lambda now=None: False)
    monkeypatch.setattr(omi, "HOLD_MAX_S", 3600.0)
    orig = omi._transcribe_file

    async def flip_after_first(path):
        monkeypatch.delenv("ZOE_OMI_SYNC_ENABLED")                                    # the flag goes off mid-job: ends it, no waiting
        return await orig(path)

    monkeypatch.setattr(omi, "_transcribe_file", flip_after_first)
    assert run(asyncio.wait_for(omi.transcribe_pending(limit=1, pause_s=0), timeout=10)) == 0
    assert rows(env)[0]["status"] == "pending" and rows(env)[0]["attempts"] == 0
