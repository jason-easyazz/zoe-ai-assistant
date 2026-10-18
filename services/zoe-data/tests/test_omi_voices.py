"""Speaker turns + learned voices in pendant recordings (``omi_diarize.py``, ``omi_voices.py``, ``routers/omi_voices.py``, migration
0055). Design ``docs/architecture/memories-space.md`` 2.6 / 3 / 10 (cells V1-V10, T4, T7). Synthetic audio and synthetic 256-d
embeddings only - no model, no household data. Each guardrail test names the source mutation that turns it red (re-run by Edit):

  V1  suggestions are not tags          count ``suggested_person_id`` as a tag in ``review_segments`` / ``tag``
  V2  my voices are mine                drop ``owner_user_id = ?`` / ``user_id = ?`` from a list / tag / suggestion query
  V3  never auth                        name ``learned_voiceprints`` from ``auth.py`` or ``voice_session.py``
  V4  forget erases everything          skip ``omi_voices.purge_people`` in ``contacts_store.forget_voiceprints``
  V6  unconfirmed prints expire         drop the voiceprint loop in ``sweep``
  V7  no wearer / no flag = nothing     store every segment in ``learn`` (drop ``learnable``)
  V7b the wearer's unsure never leaks   drop the ``LOW_MARGIN`` rule
  V8  children                          drop ``person_may_be_child`` from ``tag`` / ``manner_block.eligible`` from ``may_learn``
  V9  un-learning on delete             skip ``drop_recording`` in ``_tombstone``
  V10 backups                           drop ``--exclude-table-data`` from the nightly backup
  guest                                 replace ``_require_owner`` with ``get_current_user`` on a route
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
import contextlib
import importlib.util
import io
import math
import re
import sqlite3
import struct
import sys
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import contacts_store
import db_compat
import manner_block
import omi_diarize as od
import omi_recordings as omi
import omi_voices as ov
import user_erase_gate as erase_gate
from db_pool import _Cursor, _ExecResult
from routers import omi_recordings as omi_router

SVC = Path(__file__).resolve().parents[1]
REPO = SVC.parents[1]
A, B, KID = "member-a", "member-b", "member-kid"
MID = "wespeaker_resnet34+i8qdq-u8s8+c1"
UTC = timezone.utc
BASE = """
CREATE TABLE people (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT, email TEXT, phone TEXT, birthday TEXT,
    notes TEXT, preferences TEXT, visibility TEXT NOT NULL DEFAULT 'family', deleted INTEGER DEFAULT 0,
    is_partial INTEGER NOT NULL DEFAULT 0, context TEXT, circle TEXT, how_we_met TEXT, first_met_date TEXT,
    introduced_by_person_id TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE person_relationships (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, person_a_id TEXT NOT NULL, person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL,
    rel_a_to_b TEXT NOT NULL, rel_b_to_a TEXT NOT NULL, rel_group TEXT NOT NULL, notes TEXT, valid_from TEXT, valid_to TEXT,
    superseded_by TEXT, authority TEXT, origin TEXT, close_reason TEXT, turn_id TEXT, quote_span TEXT, speaker_rank INTEGER,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE person_activities (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, user_id TEXT NOT NULL);
CREATE TABLE person_gift_ideas (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, user_id TEXT NOT NULL);
CREATE TABLE person_bucket_list (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, user_id TEXT NOT NULL);
CREATE TABLE person_important_dates (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, user_id TEXT NOT NULL, label TEXT NOT NULL,
    date_type TEXT NOT NULL DEFAULT 'birthday', month INTEGER, day INTEGER, year INTEGER, reminder_days_before INTEGER NOT NULL DEFAULT 7,
    mem_id TEXT, created_at TEXT);
CREATE TABLE speaker_profiles (id TEXT PRIMARY KEY, user_id TEXT, display_name TEXT, embedding_blob BLOB, panel_id TEXT,
    consent_at TEXT, sample_count INTEGER, model_id TEXT);
"""


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
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

    def q(self, sql, params=()):
        return [tuple(r) for r in self.conn.execute(sql, params).fetchall()]


def _migrate(engine, fname, down=False):
    spec = importlib.util.spec_from_file_location(f"mig_{fname[:4]}", SVC / "alembic/versions" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        (mod.downgrade if down else mod.upgrade)()


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(256)
    return (v / np.linalg.norm(v)).astype(np.float32)


def near(v: np.ndarray, seed: int, noise: float = 0.35) -> np.ndarray:
    w = v + noise * unit(10_000 + seed)
    return (w / np.linalg.norm(w)).astype(np.float32)


WEARER, SAM, TV = unit(1), unit(2), unit(3)


def make_wav(seconds: float) -> bytes:
    n = int(16000 * seconds)
    pcm = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 220 * i / 16000))) for i in range(n))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return buf.getvalue()


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    db = _Sqlite(path)
    db.conn.executescript(BASE)
    engine = sa.create_engine(f"sqlite:///{path}")
    for f in ("0045_omi_recordings.py", "0053_contacts_data_model.py", "0055_omi_segments_voices.py"):
        _migrate(engine, f)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    for k, v in {"ZOE_OMI_SYNC_ENABLED": "1", "ZOE_OMI_OWNER_USER_ID": A, "ZOE_OMI_DEVICE_OWNERS": "1", "ZOE_OMI_MIN_MEM_MB": "0",
                 "ZOE_OMI_AUDIO_DIR": str(tmp_path / "audio"), "NIGHT_DIR": str(tmp_path / "night"), "ZOE_OMI_SPEAKER_TAG": "shadow",
                 "ZOE_OMI_LEARN_VOICES": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(omi, "voice_busy", lambda now_mono=None: False)
    monkeypatch.setattr(omi, "night_window", lambda now=None: False)

    async def adult(uid, db=None):
        return uid in (A, B)
    monkeypatch.setattr(manner_block, "eligible", adult)
    erase_gate.reset()

    async def fake_stt(p):
        with wave.open(p) as w:
            return f"said{w.getnframes() // 16000}s"
    monkeypatch.setattr(omi, "_transcribe_file", fake_stt)
    who = {"user": {"user_id": A, "role": "member"}}
    app = FastAPI()
    assert omi_router.register(app) is True
    app.dependency_overrides[auth.require_signed_in] = lambda: who["user"]
    yield type("Env", (), {"db": db, "client": TestClient(app), "app": app, "who": who, "tmp": tmp_path, "engine": engine})
    erase_gate.reset()


def run(coro):
    return asyncio.run(coro)


def add_recording(env, uid=A, rid=None, seconds=6.0, started="2026-10-10T01:00:00Z") -> str:
    rid = rid or f"r{len(env.db.q('SELECT id FROM omi_recordings'))}"
    p = env.tmp / f"{rid}.wav"
    p.write_bytes(make_wav(seconds))
    env.db.conn.execute("INSERT INTO omi_recordings (id, user_id, device_id, recording_key, started_at, duration_s, audio_path, status, "
                        "created_at) VALUES (?, ?, 'dev0123456789abc', ?, ?, ?, ?, 'pending', ?)",
                        (rid, uid, rid, started, seconds, str(p), started))
    env.db.conn.commit()
    return rid


def turns(*spec):
    """spec: (start_s, end_s, speaker, score, embedding)"""
    return [od.Segment(int(s * 1000), int(e * 1000), spk, sc, 2, emb) for s, e, spk, sc, emb in spec]


def transcribe(env, monkeypatch, segs, uid=A, state="tagged", **kw):
    async def fake(u, p, now):
        return segs, state, MID
    monkeypatch.setattr(omi, "_diarise", fake)
    rid = add_recording(env, uid, **kw)
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 1
    return rid


def segs_of(env, rid):
    return env.db.q("SELECT speaker, text, tagged_person_id, suggested_person_id, voice_cluster_id FROM omi_segments "
                    "WHERE recording_id = ? ORDER BY seq", (rid,))


# --- diarisation (pure) --------------------------------------------------------------------------------------------------
def test_regions_bridge_pauses_and_drop_blips():
    hop = od.HOP / od.RATE
    probs = [0.9] * 30 + [0.1] * 5 + [0.9] * 30 + [0.1] * 60 + [0.9] * 3 + [0.0] * 10
    regs = od.regions(probs)
    assert len(regs) == 1 and regs[0][0] == 0 and abs(regs[0][1] - 65 * hop) < 1e-6      # 5-hop pause bridged, 3-hop blip dropped
    assert od.window_spans([(0.0, 5.0)]) == [(0.0, 2.0), (1.0, 3.0), (2.0, 4.0), (3.0, 5.0)]
    assert od.window_spans([(0.0, 0.8)]) == [] and od.window_spans([(1.0, 2.5)]) == [(1.0, 2.5)]


def test_plan_cuts_turns_by_voice_and_labels_them_against_the_wearer():
    spans = [(float(i), i + 2.0) for i in range(10)]
    embs = [near(WEARER, i) for i in range(4)] + [near(SAM, i) for i in range(4, 10)]
    scores = [float(od.l2(e) @ WEARER) for e in embs]
    segs = od.plan(spans, embs, scores, 12.0)
    assert [s.speaker for s in segs] == ["you", "other"]
    assert segs[0].start_ms == 0 and segs[-1].end_ms == 12000 and segs[0].end_ms == segs[1].start_ms      # contiguous: no audio lost
    assert 4000 <= segs[0].end_ms <= 6000
    assert float(segs[1].embedding @ SAM) > 0.8
    none = od.plan(spans, embs, [None] * 10, 12.0)                       # T4: no wearer profile -> turns still cut, all unverified
    assert [s.speaker for s in none] == ["unverified", "unverified"]
    assert od.plan([], [], [], 3.0)[0].speaker == "unverified"


def test_mixed_when_a_turn_holds_both_voices():
    spans = [(float(i), i + 2.0) for i in range(4)]
    seg = od.plan(spans, [WEARER] * 4, [0.9, 0.0, 0.9, 0.0], 5.0)
    assert [s.speaker for s in seg] == ["mixed"]


def test_diarise_reads_the_wav_and_cuts_where_the_voice_changes(tmp_path):
    """The real pass over a WAV (energy VAD, a fake embedder keyed on pitch): two 'voices' -> two turns, cut near the change."""
    def tone(hz, secs):
        t = np.arange(int(16000 * secs)) / 16000
        return (6000 * np.sin(2 * np.pi * hz * t)).astype("<i2").tobytes()
    p = tmp_path / "two.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(tone(150, 5) + b"\0\0" * 3200 + tone(600, 5))

    class Fake:
        def embed(self, x):
            hz = np.argmax(np.abs(np.fft.rfft(x))) * 16000 / len(x)
            return WEARER if hz < 300 else SAM
    segs = run(od.diarise(str(p), Fake(), WEARER, vad_factory=lambda: None))
    assert [s.speaker for s in segs] == ["you", "other"]
    assert 4800 <= segs[0].end_ms <= 5400 and segs[-1].end_ms == 10200


def test_the_energy_fallback_does_not_drift_over_a_long_recording(tmp_path):
    """Without Silero the VAD reads one-second blocks; a block is not a whole number of 512-sample hops, so dropping the remainder
    shifted every later turn 8 ms per second (2.4 s five minutes in)."""
    p = tmp_path / "late.wav"
    t = np.arange(int(16000 * 5)) / 16000
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * (16000 * 300) + (6000 * np.sin(2 * np.pi * 150 * t)).astype("<i2").tobytes())

    heard = []

    class Fake:
        def embed(self, x):
            heard.append(float(np.abs(x).max()))
            return WEARER
    run(od.diarise(str(p), Fake(), WEARER, vad_factory=lambda: None))
    assert heard and min(heard) > 0.1, heard                  # every window sits on the tone; a drifted timeline embeds silence


# --- transcription: text aligned to turns, written before the WAV goes ----------------------------------------------------------
def test_each_turn_is_transcribed_on_its_own_and_lands_with_the_transcript(env, monkeypatch):
    rid = transcribe(env, monkeypatch, turns((0, 2, "you", 0.6, WEARER), (2, 6, "other", 0.05, SAM)))
    assert [s[:2] for s in segs_of(env, rid)] == [("you", "said2s"), ("other", "said4s")]
    (row,) = env.db.q("SELECT status, transcript, speaker_state, audio_path FROM omi_recordings WHERE id = ?", (rid,))
    assert row == ("done", "said2s said4s", "tagged", "")
    assert not (env.tmp / f"{rid}.wav").exists()


def test_tag_off_writes_no_segments_and_a_tagger_failure_never_costs_the_transcript(env, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_SPEAKER_TAG", "off")
    monkeypatch.setattr(od, "load_embedder", lambda: pytest.fail("the model must not load while tagging is off"))
    rid = add_recording(env)
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 1
    assert segs_of(env, rid) == [] and env.db.q("SELECT speaker_state FROM omi_recordings WHERE id = ?", (rid,)) == [("unverified",)]
    monkeypatch.setenv("ZOE_OMI_SPEAKER_TAG", "shadow")
    monkeypatch.setattr(od, "load_embedder", lambda: object())

    async def boom(*a, **k):
        raise RuntimeError("model fell over")
    monkeypatch.setattr(od, "diarise", boom)
    rid = add_recording(env)
    assert run(omi.transcribe_pending(limit=1, pause_s=0)) == 1
    (row,) = env.db.q("SELECT status, transcript, speaker_state FROM omi_recordings WHERE id = ?", (rid,))
    assert row == ("done", "said6s", "untaggable")
    assert [s[0] for s in segs_of(env, rid)] == ["unverified"]                     # chunk-level rows: the review still has lines


# --- learning: what is stored, and for whom ---------------------------------------------------------------------------------
def test_only_bystander_turns_are_held_and_never_with_learning_off(env, monkeypatch):
    you_min = od.YOU_MIN
    spec = turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, SAM), (6, 9, "unsure", you_min - 0.01, near(WEARER, 7)),
                 (9, 12, "unsure", you_min - 0.2, TV))
    monkeypatch.setenv("ZOE_OMI_LEARN_VOICES", "0")
    rid = transcribe(env, monkeypatch, spec, seconds=12)
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(0,)]          # V7: flag off = nothing held
    monkeypatch.setenv("ZOE_OMI_LEARN_VOICES", "1")
    rid = transcribe(env, monkeypatch, spec, seconds=12)
    held = env.db.q("SELECT s.speaker, s.score FROM omi_segment_embeddings e JOIN omi_segments s ON s.id = e.segment_id "
                    "WHERE s.recording_id = ?", (rid,))
    assert sorted(h[0] for h in held) == ["other", "unsure"]
    assert all(h[1] <= you_min - ov.LOW_MARGIN for h in held if h[0] == "unsure")        # V7b: the near-wearer unsure is NOT held


def test_a_child_member_holds_no_bystander_voice(env, monkeypatch):
    rid = transcribe(env, monkeypatch, turns((0, 6, "other", 0.0, SAM)), uid=KID)
    assert segs_of(env, rid)[0][0] == "other"
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(0,)] and env.db.q("SELECT COUNT(*) FROM omi_voice_clusters") == [(0,)]


def test_one_voice_across_recordings_and_days_is_one_unknown_voice(env, monkeypatch):
    for i, day in enumerate(("2026-10-10", "2026-10-11", "2026-10-12")):
        transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, i, 0.3)),
                                           (6, 9, "other", 0.0, near(TV, i, 0.3))), seconds=9, started=f"{day}T09:00:00Z")
    r = env.client.get("/api/omi/voices/unknown").json()["voices"]
    assert sorted((v["segments"], v["recordings"], v["days"]) for v in r) == [(3, 3, 3), (3, 3, 3)]
    assert all(v["name"].startswith("Voice ") and len(v["samples"]) == 3 for v in r)


# --- tagging, suggestions, confirmation --------------------------------------------------------------------------------------
def _sam_cluster(env, monkeypatch, n=2):
    for i in range(n):
        transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, i, 0.3))))
    (cid,) = [v["id"] for v in env.client.get("/api/omi/voices/unknown").json()["voices"]]
    return cid


def test_tag_creates_my_personal_person_and_starts_a_voiceprint(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    out = env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"}).json()
    assert out["learning"] == "learning" and out["person_created"] and out["segments"] == 2
    assert env.db.q("SELECT user_id, name, visibility FROM people") == [(A, "Sam", "personal")]
    assert env.db.q("SELECT kind, provenance, provenance_ref FROM contact_fields") == [("name", "entered_by_member", f"omi_tag:{cid}")]
    (vp,) = env.client.get("/api/omi/voices").json()["voices"]
    assert (vp["name"], vp["status"], vp["segments"], vp["recordings"]) == ("Sam", "provisional", 2, 2)
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(0,)]          # used embeddings are deleted
    assert env.db.q("SELECT COUNT(*) FROM omi_voice_clusters") == [(0,)]
    assert env.db.q("SELECT COUNT(*) FROM omi_segments WHERE tagged_person_id IS NOT NULL AND tag_source = 'owner'") == [(2,)]


def test_a_suggestion_is_not_a_tag_until_the_member_says_yes(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    pid = env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"}).json()["person_id"]
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, 9, 0.3))))
    (seg,) = [s for s in run(_segs(rid)) if s["speaker"] == "other"]
    assert seg["suggested_person_id"] == pid and seg["tagged_person_id"] is None and seg["label"] == "someone_else"
    review = run(_review(rid))
    assert all("suggested_person_id" not in r and r["tagged_person_id"] is None for r in review)     # V1: the review never sees it
    assert env.client.post(f"/api/omi/segments/{seg['id']}/suggestion", json={"answer": "yes"}).status_code == 200
    (seg,) = [s for s in run(_segs(rid)) if s["speaker"] == "other"]
    assert (seg["tagged_person_id"], seg["tag_source"], seg["label"]) == (pid, "suggested_confirmed", "person")
    (vp,) = env.client.get("/api/omi/voices").json()["voices"]
    assert vp["status"] == "confirmed" and vp["segments"] == 3 and vp["last_confirmed_at"]


def test_a_contact_edited_into_a_child_after_learning_is_never_suggested(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    pid = env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"}).json()["person_id"]
    env.db.conn.execute("UPDATE people SET relationship = 'son' WHERE id = ?", (pid,))
    env.db.conn.commit()
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, 9, 0.3))))
    (seg,) = [s for s in run(_segs(rid)) if s["speaker"] == "other"]
    assert seg["suggested_person_id"] is None


def _suggested(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    pid = env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"}).json()["person_id"]
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, 9, 0.3))))
    (seg,) = [s for s in run(_segs(rid)) if s["speaker"] == "other"]
    assert seg["suggested_person_id"] == pid
    return pid, seg["id"]


def test_a_yes_after_the_contact_became_a_child_confirms_nothing_and_drops_the_print(env, monkeypatch):
    """The suggestion was made while Sam was an adult; the contact was then edited into a child. A yes is a label only: no promotion,
    no 365-day refresh, and the print learned earlier goes."""
    pid, sid = _suggested(env, monkeypatch)
    env.db.conn.execute("UPDATE people SET relationship = 'son' WHERE id = ?", (pid,))
    env.db.conn.commit()
    out = env.client.post(f"/api/omi/segments/{sid}/suggestion", json={"answer": "yes"}).json()
    assert out["learning"] == "label_only_child"
    assert env.db.q("SELECT tagged_person_id, tag_source FROM omi_segments WHERE id = ?", (sid,)) == [(pid, "suggested_confirmed")]
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprint_segments") == [(0,)]


def test_stopping_a_voice_withdraws_its_suggestions_and_a_stale_yes_cannot_refresh_it(env, monkeypatch):
    pid, sid = _suggested(env, monkeypatch)
    (vp,) = [v["id"] for v in env.client.get("/api/omi/voices").json()["voices"]]
    before = env.db.q("SELECT status, last_confirmed_at, expires_at FROM learned_voiceprints")
    assert env.client.patch(f"/api/omi/voices/{vp}", json={"learning_enabled": False}).status_code == 200
    assert env.db.q("SELECT suggested_person_id FROM omi_segments WHERE id = ?", (sid,)) == [(None,)]     # withdrawn with the voice
    # a suggestion that somehow outlived the switch (a race): the yes labels the line but confirms and refreshes nothing
    env.db.conn.execute("UPDATE omi_segments SET suggested_person_id = ? WHERE id = ?", (pid, sid))
    env.db.conn.commit()
    assert env.client.post(f"/api/omi/segments/{sid}/suggestion", json={"answer": "yes"}).status_code == 200
    assert env.db.q("SELECT status, last_confirmed_at, expires_at FROM learned_voiceprints") == before


def test_a_suggestion_answer_touches_only_the_voiceprint_of_its_own_model(env, monkeypatch):
    pid, sid = _suggested(env, monkeypatch)
    (model,) = env.db.q("SELECT model_id FROM omi_segments WHERE id = ?", (sid,))[0]
    env.db.conn.execute("INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, channel, embedding, n_segments, "
                        "n_recordings, status, learning_enabled, created_at, updated_at, expires_at) VALUES ('other-model-vp', ?, ?, "
                        "'some-other-model', 'pendant', ?, 5, 5, 'provisional', 1, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', "
                        "'2026-10-31T00:00:00Z')", (A, pid, sqlite3.Binary(np.zeros(256, dtype="<f4").tobytes())))
    env.db.conn.commit()
    env.client.post(f"/api/omi/segments/{sid}/suggestion", json={"answer": "yes"})
    assert env.db.q("SELECT status, last_confirmed_at, expires_at FROM learned_voiceprints WHERE id = 'other-model-vp'") == [
        ("provisional", None, "2026-10-31T00:00:00Z")]
    assert env.db.q("SELECT last_confirmed_at IS NOT NULL FROM learned_voiceprints WHERE model_id = ?", (model,)) == [(1,)]
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, 11, 0.3))))
    (seg,) = [s for s in run(_segs(rid)) if s["speaker"] == "other"]
    assert seg["suggested_person_id"] == pid
    env.client.post(f"/api/omi/segments/{seg['id']}/suggestion", json={"answer": "no"})
    assert env.db.q("SELECT rejections FROM learned_voiceprints WHERE id = 'other-model-vp'") == [(0,)]
    assert env.db.q("SELECT rejections FROM learned_voiceprints WHERE model_id = ?", (model,)) == [(1,)]


def test_no_raises_the_bar_and_learns_nothing(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, 9, 0.3))))
    (seg,) = [s for s in run(_segs(rid)) if s["speaker"] == "other"]
    env.client.post(f"/api/omi/segments/{seg['id']}/suggestion", json={"answer": "no"})
    assert env.db.q("SELECT rejections, n_segments FROM learned_voiceprints") == [(1, 2)]
    assert segs_of(env, rid)[1][2:4] == (None, None)


async def _segs(rid, uid=A):
    async with db_compat.get_compat_db() as db:
        return await ov.segments_for(db, uid, rid)


async def _review(rid, uid=A):
    async with db_compat.get_compat_db() as db:
        return await ov.review_segments(db, uid, [rid])


def test_another_member_never_sees_or_uses_my_voices(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    out = env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"}).json()
    vp = env.client.get("/api/omi/voices").json()["voices"][0]["id"]
    env.who["user"] = {"user_id": B, "role": "admin"}                                   # even an admin
    assert env.client.get("/api/omi/voices").json()["voices"] == []
    assert env.client.delete(f"/api/omi/voices/{vp}").status_code == 404
    assert env.client.patch(f"/api/omi/voices/{vp}", json={"learning_enabled": False}).status_code == 404
    assert env.client.post(f"/api/omi/segments/{segs_of_id(env)}/tag", json={"person_id": out["person_id"]}).status_code == 404
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, 9, 0.3))), uid=B)
    assert all(s[3] is None for s in segs_of(env, rid))                               # V2: B's recording is never suggested A's Sam
    assert env.client.get("/api/omi/voices/unknown").json()["voices"][0]["recordings"] == 1        # B's own unknown voice only


def segs_of_id(env):
    return env.db.q("SELECT id FROM omi_segments WHERE user_id = ? LIMIT 1", (A,))[0][0]


def test_a_child_person_is_a_label_only(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    env.db.conn.execute("INSERT INTO people (id, user_id, name, relationship, visibility, deleted) VALUES ('kid1', ?, 'Mia', "
                        "'daughter', 'personal', 0)", (A,))
    env.db.conn.commit()
    out = env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"person_id": "kid1"}).json()
    assert out["learning"] == "label_only_child"
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)] and env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(0,)]
    assert ov.person_may_be_child({"birthday": "2015-03-02"}) and not ov.person_may_be_child({"relationship": "friend",
                                                                                              "birthday": "1980-01-01"})


def test_not_a_person_leaves_suggestions_and_the_review_for_good(env, monkeypatch):
    for i in range(2):
        transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(TV, i, 0.3))))
    (cid,) = [v["id"] for v in env.client.get("/api/omi/voices/unknown").json()["voices"]]
    assert env.client.post(f"/api/omi/voices/unknown/{cid}/not-a-person", json={"kind": "pets"}).status_code == 400
    assert env.client.post(f"/api/omi/voices/unknown/{cid}/not-a-person", json={"kind": "tv"}).json() == {"ok": True, "segments": 2}
    assert env.client.get("/api/omi/voices/unknown").json()["voices"] == []          # never offered again
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(0,)]
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(TV, 5, 0.3))))
    assert [s["label"] for s in run(_segs(rid))] == ["you", "not_a_person"]          # a later TV line is marked on arrival
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(0,)]
    monkeypatch.setenv("ZOE_OMI_SPEAKER_TAG", "on")
    assert [r["label"] for r in run(_review(rid))] == ["you"]                         # and never reaches the day review


def test_in_shadow_mode_the_review_ignores_model_labels_but_keeps_the_members_word(env, monkeypatch):
    """ZOE_OMI_SPEAKER_TAG=shadow: the owner sees the model's labels, the day review does not use them (design 9)."""
    cid = _sam_cluster(env, monkeypatch)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, 9, 0.3))))
    assert [s["label"] for s in run(_segs(rid))] == ["you", "someone_else"]                        # the owner's page shows them
    assert [r["label"] for r in run(_review(rid))] == ["unverified", "unverified"]                  # the review does not
    sid = env.db.q("SELECT id FROM omi_segments WHERE recording_id = ? AND speaker = 'you'", (rid,))[0][0]
    env.client.patch(f"/api/omi/segments/{sid}", json={"owner_label": "you"})                       # the member's own word counts
    assert [r["label"] for r in run(_review(rid))] == ["you", "unverified"]
    monkeypatch.setenv("ZOE_OMI_SPEAKER_TAG", "on")
    assert [r["label"] for r in run(_review(rid))] == ["you", "someone_else"]


def test_a_retag_moves_what_the_first_tag_taught_to_the_right_person(env, monkeypatch):
    """The first tag moved the held embedding into Sam's print and deleted it; correcting the tag to Alex must un-learn Sam (rebuild
    or drop his print) and teach Alex - a retag that only flipped ``tagged_person_id`` left the contribution with the wrong person."""
    cid = _sam_cluster(env, monkeypatch, n=2)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    sam = env.db.q("SELECT id FROM people WHERE name = 'Sam'")[0][0]
    (seg_a, seg_b) = [r[0] for r in env.db.q("SELECT segment_id FROM learned_voiceprint_segments ORDER BY segment_id")]
    out = env.client.post(f"/api/omi/segments/{seg_a}/tag", json={"name": "Alex"}).json()
    assert out["learning"] == "learning"
    alex = env.db.q("SELECT id FROM people WHERE name = 'Alex'")[0][0]
    assert env.db.q("SELECT p.name, COUNT(*) FROM learned_voiceprint_segments c JOIN learned_voiceprints v ON v.id = c.voiceprint_id "
                    "JOIN people p ON p.id = v.people_id GROUP BY p.name ORDER BY p.name") == [("Alex", 1), ("Sam", 1)]
    assert env.db.q("SELECT n_segments FROM learned_voiceprints WHERE people_id = ?", (sam,)) == [(1,)]       # Sam's print was rebuilt
    assert env.db.q("SELECT n_segments FROM learned_voiceprints WHERE people_id = ?", (alex,)) == [(1,)]
    # the last contribution leaves Sam too: his print goes rather than keeping a clip the member said is not his
    env.client.post(f"/api/omi/segments/{seg_b}/tag", json={"person_id": alex})
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprints WHERE people_id = ?", (sam,)) == [(0,)]
    assert env.db.q("SELECT n_segments FROM learned_voiceprints WHERE people_id = ?", (alex,)) == [(2,)]


def test_a_tagged_line_marked_not_a_person_is_untagged_and_unlearned(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch, n=2)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    seg_a = env.db.q("SELECT segment_id FROM learned_voiceprint_segments ORDER BY segment_id")[0][0]
    assert env.client.patch(f"/api/omi/segments/{seg_a}", json={"not_person": "tv"}).status_code == 200
    assert env.db.q("SELECT not_person, tagged_person_id, tag_source FROM omi_segments WHERE id = ?", (seg_a,)) == [("tv", None, None)]
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprint_segments WHERE segment_id = ?", (seg_a,)) == [(0,)]
    assert env.db.q("SELECT n_segments FROM learned_voiceprints") == [(1,)]                                  # Sam's print was rebuilt


def test_lines_under_zoes_own_speech_are_not_a_person(env, monkeypatch):
    async def spoke(db, uid, rec):
        return [(2500, 6000)]
    monkeypatch.setattr(ov, "zoe_spoken_spans", spoke)
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, SAM)))
    (you, zoe) = run(_segs(rid))
    assert (you["label"], zoe["label"], zoe["not_person"]) == ("you", "not_a_person", "zoe")
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(0,)]          # Zoe's voice is never held as a bystander
    assert env.client.patch(f"/api/omi/segments/{zoe['id']}", json={"not_person": None}).status_code == 200
    assert run(_segs(rid))[1]["label"] == "someone_else"


# --- forgetting, expiry, deletion ------------------------------------------------------------------------------------------
def test_forget_name_erases_the_voice_and_everything_derived(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, 9, 0.3))))
    assert segs_of(env, rid)[1][3] is not None
    n = run(_forget(re.compile(r"\bSam\b", re.I)))
    assert n > 0
    for table in ("learned_voiceprints", "learned_voiceprint_segments"):
        assert env.db.q(f"SELECT COUNT(*) FROM {table}") == [(0,)], table
    assert env.db.q("SELECT COUNT(*) FROM omi_segments WHERE tagged_person_id IS NOT NULL OR suggested_person_id IS NOT NULL") == [(0,)]


async def _forget(rx, uid=A):
    async with db_compat.get_compat_db() as db:
        return await contacts_store.forget_voiceprints(db, uid, rx)


def test_forget_voice_keeps_the_person_and_tags_and_stop_learning_lets_go(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    vp = env.client.get("/api/omi/voices").json()["voices"][0]["id"]
    assert env.client.delete(f"/api/omi/voices/{vp}").json() == {"ok": True}
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)] and env.db.q("SELECT COUNT(*) FROM people") == [(1,)]
    assert env.db.q("SELECT COUNT(*) FROM omi_segments WHERE tagged_person_id IS NOT NULL") == [(2,)]
    transcribe(env, monkeypatch, turns((0, 6, "other", 0.0, TV)))
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(1,)]
    assert env.client.put("/api/omi/voices/settings", json={"learning_enabled": False}).json() == {"learning_enabled": False}
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(0,)] and env.db.q("SELECT COUNT(*) FROM omi_voice_clusters") == [(0,)]
    transcribe(env, monkeypatch, turns((0, 6, "other", 0.0, TV)))
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(0,)]          # stopped stays stopped


def test_unconfirmed_voices_and_held_embeddings_expire(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    transcribe(env, monkeypatch, turns((0, 6, "other", 0.0, TV)))
    now = datetime.now(UTC)
    assert run(_sweep(now + timedelta(days=13))) == {"embeddings": 0, "clusters": 0, "voiceprints": 0, "people": 0}
    assert run(_sweep(now + timedelta(days=15)))["embeddings"] == 1
    assert env.db.q("SELECT COUNT(*) FROM omi_voice_clusters") == [(0,)]
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprints") == [(1,)]
    assert run(_sweep(now + timedelta(days=31)))["voiceprints"] == 1
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprint_segments") == [(0,)]


async def _sweep(now):
    async with db_compat.get_compat_db() as db:
        return await ov.sweep(db, now=now)


def test_a_soft_deleted_person_is_purged_by_the_sweep(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    env.db.conn.execute("UPDATE people SET deleted = 1")
    env.db.conn.commit()
    assert env.client.get("/api/omi/voices").json()["voices"] == []                   # hidden at once, before the purge
    assert run(_sweep(datetime.now(UTC)))["people"] > 0
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]
    assert env.db.q("SELECT COUNT(*) FROM omi_segments WHERE tagged_person_id IS NOT NULL") == [(0,)]


def test_deleting_a_recording_unlearns_it_and_rebuilds_the_voice(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch, n=3)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    first = env.db.q("SELECT recording_id FROM learned_voiceprint_segments ORDER BY recording_id")[0][0]
    before = env.db.q("SELECT embedding FROM learned_voiceprints")[0][0]
    assert env.client.delete(f"/api/omi/recordings/{first}").status_code == 200
    assert env.db.q("SELECT n_segments, n_recordings FROM learned_voiceprints") == [(2, 2)]
    assert env.db.q("SELECT embedding FROM learned_voiceprints")[0][0] != before
    assert env.db.q("SELECT COUNT(*) FROM omi_segments WHERE recording_id = ?", (first,)) == [(0,)]


def test_deleting_a_recording_rebuilds_or_drops_the_unknown_voice_it_fed(env, monkeypatch):
    for i in range(2):
        transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, i, 0.3))))
    (r0,), (r1,) = env.db.q("SELECT id FROM omi_recordings ORDER BY id")
    before = env.db.q("SELECT centroid, n_segments FROM omi_voice_clusters")[0]
    env.client.delete(f"/api/omi/recordings/{r0}")
    after = env.db.q("SELECT centroid, n_segments FROM omi_voice_clusters")[0]
    assert after[1] == 1 and after[0] != before[0]                                   # the deleted recording's voice is out of it
    env.client.delete(f"/api/omi/recordings/{r1}")
    assert env.db.q("SELECT COUNT(*) FROM omi_voice_clusters") == [(0,)]


def test_an_expired_voice_takes_its_suggestions_with_it(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    pid = env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"}).json()["person_id"]
    rid = transcribe(env, monkeypatch, turns((0, 6, "other", 0.0, TV)))
    env.db.conn.execute("UPDATE omi_segments SET suggested_person_id = ? WHERE recording_id = ?", (pid, rid))
    env.db.conn.commit()
    run(_sweep(datetime.now(UTC) + timedelta(days=31)))
    assert env.db.q("SELECT COUNT(*) FROM omi_segments WHERE suggested_person_id IS NOT NULL") == [(0,)]


def test_a_segment_store_failure_never_costs_the_transcript(env, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(ov, "store_segments", boom)
    rid = transcribe(env, monkeypatch, turns((0, 6, "other", 0.0, SAM)))
    assert env.db.q("SELECT status, transcript, speaker_state FROM omi_recordings WHERE id = ?", (rid,)) == [("done", "said6s", "untaggable")]


def test_a_person_merge_moves_the_members_tags_to_the_survivor(env, monkeypatch):
    import person_merge
    cid = _sam_cluster(env, monkeypatch)
    stub = env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"}).json()["person_id"]
    env.db.conn.execute("INSERT INTO people (id, user_id, name, visibility, deleted, is_partial) VALUES ('real', ?, 'Sam Lee', "
                        "'personal', 0, 0)", (A,))
    env.db.conn.commit()
    run(person_merge.merge_person(env.db, A, stub, "real"))
    assert env.db.q("SELECT DISTINCT tagged_person_id FROM omi_segments WHERE tagged_person_id IS NOT NULL") == [("real",)]
    run(_sweep(datetime.now(UTC)))                                                   # the soft-deleted stub's purge loses nothing
    assert env.db.q("SELECT COUNT(*) FROM omi_segments WHERE tagged_person_id = 'real'") == [(2,)]


def test_a_voice_erase_failure_fails_closed_but_still_erases_the_audio(env, monkeypatch, caplog):
    """A learned-voice erase that fails must never read as "forgotten": forget_user RAISES (after the audio and the pendant
    registrations are done, which it never blocks) so the cascade reports the failure and the caller retries."""
    rid = transcribe(env, monkeypatch, turns((0, 6, "other", 0.0, TV)))

    async def boom(db, uid):
        raise RuntimeError("voice tables unreachable")
    monkeypatch.setattr(ov, "forget_user", boom)
    caplog.set_level("ERROR")
    with pytest.raises(omi.VoiceEraseIncomplete):
        run(omi.forget_user(A))
    assert env.db.q("SELECT status FROM omi_recordings WHERE id = ?", (rid,)) == [("deleted",)]
    assert "OMI_VOICES_ERASE_INCOMPLETE" in caplog.text


def test_a_failed_voice_cleanup_leaves_the_recording_deletable_again(env, monkeypatch):
    """If the voice cleanup fails the recording must NOT already be a tombstone (a retry would exit at "already deleted" and the
    segments / embeddings / voiceprint contributions would stay for good)."""
    cid = _sam_cluster(env, monkeypatch, n=2)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    (rid,) = env.db.q("SELECT id FROM omi_recordings ORDER BY id LIMIT 1")[0]
    real = ov.drop_recording

    async def boom(db, r):
        raise RuntimeError("transient")
    monkeypatch.setattr(ov, "drop_recording", boom)
    with pytest.raises(RuntimeError):
        run(omi.delete_recording(A, rid))
    assert env.db.q("SELECT status FROM omi_recordings WHERE id = ?", (rid,)) != [("deleted",)]
    monkeypatch.setattr(ov, "drop_recording", real)
    assert run(omi.delete_recording(A, rid)) is True
    assert env.db.q("SELECT COUNT(*) FROM omi_segments WHERE recording_id = ?", (rid,)) == [(0,)]
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprint_segments WHERE recording_id = ?", (rid,)) == [(0,)]


def test_the_expiry_sweep_runs_with_the_sync_flag_off(env, monkeypatch):
    """Turning ZOE_OMI_SYNC_ENABLED off stops the transcription loop, not the retention of biometrics already held."""
    cid = _sam_cluster(env, monkeypatch)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    env.db.conn.execute("UPDATE learned_voiceprints SET expires_at = '2000-01-01T00:00:00Z'")
    env.db.conn.commit()
    monkeypatch.setattr(omi, "enabled", lambda: False)
    monkeypatch.setattr(omi, "RETENTION_S", 0.01)

    async def go():
        try:
            await asyncio.wait_for(omi.run_retention_loop(), 1.0)
        except asyncio.TimeoutError:
            pass
    run(go())
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)]
    assert "run_retention_loop" in (SVC / "main.py").read_text()                    # and main starts it unconditionally


def test_a_tagged_line_corrected_to_you_is_untagged_and_unlearned(env, monkeypatch):
    """The member tagged a line as Sam, then says "that was me": Sam's print must stop holding the wearer's voice, and the line
    must stop reading as Sam."""
    cid = _sam_cluster(env, monkeypatch, n=2)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    seg_a = env.db.q("SELECT segment_id FROM learned_voiceprint_segments ORDER BY segment_id")[0][0]
    assert env.client.patch(f"/api/omi/segments/{seg_a}", json={"owner_label": "you"}).status_code == 200
    assert env.db.q("SELECT tagged_person_id, tag_source, tagged_at, owner_label FROM omi_segments WHERE id = ?", (seg_a,)) == [
        (None, None, None, "you")]
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprint_segments WHERE segment_id = ?", (seg_a,)) == [(0,)]
    assert env.db.q("SELECT n_segments FROM learned_voiceprints") == [(1,)]                  # Sam's print was rebuilt without it
    assert [s["label"] for s in run(_segs(env.db.q("SELECT recording_id FROM omi_segments WHERE id = ?", (seg_a,))[0][0]))
            if s["id"] == seg_a] == ["you"]


def test_a_wearer_correction_drops_the_held_embedding(env, monkeypatch):
    """"That was me" on a line held as a bystander's voice: the embedding and its place in the unknown voice go, so tagging the voice
    can never teach another person's print the wearer's voice."""
    for i in range(2):
        transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, i, 0.3))))
    sid = env.db.q("SELECT id FROM omi_segments WHERE speaker = 'other' ORDER BY id")[0][0]
    assert env.client.patch(f"/api/omi/segments/{sid}", json={"owner_label": "you"}).status_code == 200
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings WHERE segment_id = ?", (sid,)) == [(0,)]
    assert env.db.q("SELECT voice_cluster_id FROM omi_segments WHERE id = ?", (sid,)) == [(None,)]
    assert env.db.q("SELECT n_segments FROM omi_voice_clusters") == [(1,)]                           # the voice forgot that sample


def test_a_segment_marked_not_a_person_leaves_the_unknown_voice(env, monkeypatch):
    for i in range(2):
        transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, i, 0.3))))
    sid = env.db.q("SELECT id FROM omi_segments WHERE speaker = 'other' ORDER BY id")[0][0]
    assert env.client.patch(f"/api/omi/segments/{sid}", json={"not_person": "tv"}).status_code == 200
    (v,) = env.client.get("/api/omi/voices/unknown").json()["voices"]
    assert v["segments"] == 1 and sid not in [x["segment_id"] for x in v["samples"]]                                            # no longer a sample of it
    assert env.db.q("SELECT n_segments FROM omi_voice_clusters") == [(1,)]


def test_tagging_one_line_rebuilds_the_voice_it_came_from(env, monkeypatch):
    for i in range(2):
        transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, i, 0.3))))
    a, b = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE speaker = 'other' ORDER BY id")]
    before = env.db.q("SELECT centroid FROM omi_voice_clusters")[0][0]
    env.client.post(f"/api/omi/segments/{a}/tag", json={"name": "Sam"})
    assert env.db.q("SELECT n_segments FROM omi_voice_clusters") == [(1,)]
    assert env.db.q("SELECT centroid FROM omi_voice_clusters")[0][0] != before                      # no longer includes the tagged sample
    env.client.post(f"/api/omi/segments/{b}/tag", json={"name": "Sam2"})
    assert env.db.q("SELECT COUNT(*) FROM omi_voice_clusters") == [(0,)]                            # the last sample took the voice with it


def test_a_patch_with_a_bad_field_applies_nothing(env, monkeypatch):
    for i in range(2):
        transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, i, 0.3))))
    sid = env.db.q("SELECT id FROM omi_segments WHERE speaker = 'other' ORDER BY id")[0][0]
    r = env.client.patch(f"/api/omi/segments/{sid}", json={"not_person": "tv", "owner_label": "bogus"})
    assert r.status_code == 400
    assert env.db.q("SELECT not_person, owner_label FROM omi_segments WHERE id = ?", (sid,)) == [(None, None)]
    r = env.client.patch(f"/api/omi/segments/{sid}", json={"not_person": "pets", "owner_label": "you"})
    assert r.status_code == 400
    assert env.db.q("SELECT not_person, owner_label FROM omi_segments WHERE id = ?", (sid,)) == [(None, None)]


def test_erasing_the_member_erases_every_voice_row(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    transcribe(env, monkeypatch, turns((0, 6, "other", 0.0, TV)))
    assert run(omi.forget_user(A)) > 0
    for t in ("omi_segments", "omi_segment_embeddings", "omi_voice_clusters", "learned_voiceprints", "learned_voiceprint_segments"):
        assert env.db.q(f"SELECT COUNT(*) FROM {t}") == [(0,)], t


def test_forget_name_redacts_turn_text(env, monkeypatch):
    rid = transcribe(env, monkeypatch, turns((0, 6, "other", 0.0, SAM)))
    assert run(omi.erase_entity(A, re.compile("said6s"))) >= 1
    assert segs_of(env, rid)[0][1] == "[forgotten]"


# --- who may call it, and who may never read it ------------------------------------------------------------------------------
def test_guests_and_panels_cannot_call_the_voice_api(env, monkeypatch):
    env.app.dependency_overrides.pop(auth.require_signed_in)
    env.app.dependency_overrides[auth.get_current_user] = lambda: {"user_id": "guest", "role": "guest"}
    calls = [("get", "/api/omi/voices/unknown", None), ("post", "/api/omi/voices/unknown/x/tag", {"name": "Sam"}),
             ("post", "/api/omi/voices/unknown/x/not-a-person", {"kind": "tv"}), ("post", "/api/omi/segments/x/tag", {"name": "Sam"}),
             ("patch", "/api/omi/segments/x", {"owner_label": "you"}), ("post", "/api/omi/segments/x/suggestion", {"answer": "yes"}),
             ("get", "/api/omi/voices", None), ("put", "/api/omi/voices/settings", {"learning_enabled": False}),
             ("patch", "/api/omi/voices/x", {"learning_enabled": False}), ("delete", "/api/omi/voices/x", None)]
    for method, url, body in calls:
        r = getattr(env.client, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, url, r.status_code)
    env.app.dependency_overrides[auth.get_current_user] = lambda: {"user_id": A, "role": "member", "device_token_role": "panel"}
    assert env.client.get("/api/omi/voices").status_code == 403                    # a panel's device token is not the person


ALLOWED = {"omi_voices.py", "omi_diarize.py", "omi_recordings.py", "routers/omi_voices.py", "routers/omi_recordings.py", "contacts_store.py", "person_merge.py",
           "memory_forget_cascade.py",
           # the day review names the ``omi_segments`` vocabulary in its seam (``lines_for_day(segments=)``); it never touches a
           # voice table - its input is ``review_segments`` output (confirmed labels, no suggestions) handed in by the caller
           "omi_day_review.py"}
NEEDLES = ("learned_voiceprint", "omi_segment", "omi_voice_cluster", "omi_voice_settings", "omi_voices")


def test_no_auth_identity_or_session_path_can_reach_a_learned_voice():
    """V3 + "a learned print never authenticates or changes get_current_user": only the allow-listed modules name the voice tables
    or the store; auth, voice_session, the voice router (identify / profiles sync), adaptation and enrolment never do."""
    hits = set()
    for p in SVC.rglob("*.py"):
        rel = str(p.relative_to(SVC))
        if rel.startswith(("tests/", "alembic/", ".venv")):
            continue
        txt = p.read_text(errors="replace")
        if any(n in txt for n in NEEDLES):
            hits.add(rel)
    assert hits <= ALLOWED, f"unexpected readers of learned voices: {sorted(hits - ALLOWED)}"
    for must_not in ("auth.py", "voice_session.py", "routers/voice_tts.py", "speaker_adaptation.py", "voice_enrollment.py"):
        assert must_not not in hits


def test_the_nightly_backup_holds_no_biometric_row_data():
    s = (REPO / "scripts/maintenance/postgres-nightly-backup.sh").read_text()
    for t in ("learned_voiceprints", "learned_voiceprint_segments", "omi_segment_embeddings", "omi_voice_clusters"):
        assert t in s.split("BIOMETRIC_TABLES=")[1].split("\n")[0], t
    assert '--exclude-table-data=$t' in s and '"${exclude_args[@]}"' in s


def test_migration_0055_round_trips_on_sqlite(env):
    _migrate(env.engine, "0055_omi_segments_voices.py", down=True)
    names = {r[0] for r in env.db.q("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not names & {"omi_segments", "omi_segment_embeddings", "omi_voice_clusters", "learned_voiceprint_segments"}
    assert "rejections" not in {r[1] for r in env.db.q("PRAGMA table_info(learned_voiceprints)")}
    _migrate(env.engine, "0055_omi_segments_voices.py")
    _migrate(env.engine, "0055_omi_segments_voices.py")                                # rerun-safe
    assert "rejections" in {r[1] for r in env.db.q("PRAGMA table_info(learned_voiceprints)")}


def _calibrate_module():
    spec = importlib.util.spec_from_file_location("omi_voice_calibrate", REPO / "scripts/perf/omi_voice_calibrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_calibration_refuses_unless_every_gate_is_positively_open(tmp_path):
    """Fail closed: a missing log, an unreadable voice-line time, a recent turn, the night window, low RAM - each refuses."""
    cal = _calibrate_module()
    day = datetime(2026, 10, 17, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    log = tmp_path / "app.log"
    with pytest.raises(SystemExit):
        cal.preflight(0, log, now=day, mem_mb=5000)                                     # no log: cannot rule out a voice turn
    log.write_text("2026-10-17T11:59:00+0800 INFO routers.voice_tts: turn_stream start\n")
    with pytest.raises(SystemExit):
        cal.preflight(0, log, now=day, mem_mb=5000)                                     # a turn one minute ago
    log.write_text("garbled turn_stream line\n")
    with pytest.raises(SystemExit):
        cal.preflight(0, log, now=day, mem_mb=5000)                                     # unreadable time
    log.write_text("2026-10-17T11:00:00+0800 INFO routers.voice_tts: turn_stream start\n")
    cal.preflight(0, log, now=day, mem_mb=5000)                                         # an hour ago: open
    with pytest.raises(SystemExit):
        cal.preflight(0, log, now=day.replace(hour=2), mem_mb=5000)                     # the night jobs' window
    with pytest.raises(SystemExit):
        cal.preflight(600, log, now=day, mem_mb=500)                                    # not enough RAM
    with pytest.raises(SystemExit):
        cal.report_path(str(REPO / "docs" / "x.md"), "ABCD")                            # never inside the public repo
    assert cal.report_path(str(tmp_path / "r.md"), "ABCD").endswith("r.md")


def test_the_calibration_quiet_window_is_the_households_not_the_hosts(tmp_path):
    """TZ=UTC on the host: 18:00Z is 02:00 in the household zone - inside the night window, so it refuses; 12:00Z (20:00 local) is open."""
    cal = _calibrate_module()
    log = tmp_path / "app.log"
    log.write_text("2026-10-17T00:00:00+0800 INFO routers.voice_tts: turn_stream start\n")
    with pytest.raises(SystemExit):
        cal.preflight(0, log, now=datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc), mem_mb=5000)
    cal.preflight(0, log, now=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc), mem_mb=5000)


def test_the_calibration_stops_before_the_night_window_it_started_ahead_of(tmp_path):
    cal = _calibrate_module()
    log = tmp_path / "app.log"
    log.write_text("2026-10-16T00:00:00+0800 INFO routers.voice_tts: turn_stream start\n")
    start = datetime(2026, 10, 17, 1, 0, tzinfo=timezone(timedelta(hours=8)))
    deadline = cal.preflight(0, log, now=start, mem_mb=5000)
    assert (deadline.hour, deadline.minute, deadline.day) == (1, 45, 17)                     # 5 minutes before 01:50, same night
    cal.DEADLINE = deadline
    cal.check_deadline(now=start + timedelta(minutes=30))                                     # 01:30 - still running
    with pytest.raises(SystemExit):
        cal.check_deadline(now=start + timedelta(minutes=46))                                 # 01:46 - stop
    evening = datetime(2026, 10, 17, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    assert cal.quiet_deadline(evening).day == 18                                              # an evening start is bounded by tomorrow's 01:45


def test_the_calibration_reads_other_rates_skips_bad_files_and_never_retightens_a_directory(tmp_path):
    cal = _calibrate_module()
    p = tmp_path / "r24.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x10\x00" * 24000)
    assert abs(len(cal.read(str(p))) - 16000) < 50                                            # one second at 24 kHz is 16k samples at 16 kHz
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"RIFFnotawave")
    assert cal.read(str(bad)) is None and cal.SKIPPED["unreadable"] >= 1
    shared = tmp_path / "shared-reports"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    cal.write_report(str(shared / "r.md"), ["x"])
    assert (shared.stat().st_mode & 0o777) == 0o755 and ((shared / "r.md").stat().st_mode & 0o777) == 0o600
    cal.write_report(str(tmp_path / "fresh" / "r.md"), ["x"])
    assert ((tmp_path / "fresh").stat().st_mode & 0o777) == 0o700


def test_a_public_only_section_never_touches_the_panel_corpus(tmp_path, monkeypatch):
    cal = _calibrate_module()
    seen = []
    real = cal.glob.glob
    monkeypatch.setattr(cal.glob, "glob", lambda pat, *a, **k: (seen.append(pat), real(pat, *a, **k))[1])
    monkeypatch.setattr(cal, "preflight", lambda *a, **k: datetime(2999, 1, 1, tzinfo=timezone.utc))

    class Emb:
        def embed(self, x):
            return None
    import omi_diarize
    import types
    monkeypatch.setitem(sys.modules, "threadpoolctl", types.SimpleNamespace(threadpool_limits=lambda n: None))     # absent in the slim CI venv
    monkeypatch.setattr(omi_diarize, "load_embedder", lambda: Emb())
    monkeypatch.setattr(sys, "argv", ["x", "--sections", "C", "--panel-dir", str(tmp_path / "panel"), "--public-dir", str(tmp_path / "pub"),
                                      "--report", str(tmp_path / "r.md")])
    with pytest.raises(FileNotFoundError):                                                    # no public manifest: it got as far as section C
        asyncio.run(cal.main())
    assert not any("panel" in pat for pat in seen), seen


def test_someone_else_clears_the_suggestion_and_learns_nothing(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, 9, 0.3))))
    (seg,) = [s for s in run(_segs(rid)) if s["speaker"] == "other"]
    assert env.client.post(f"/api/omi/segments/{seg['id']}/suggestion", json={"answer": "maybe"}).status_code == 400
    assert env.client.post(f"/api/omi/segments/{seg['id']}/suggestion", json={"answer": "someone_else"}).status_code == 200
    assert env.db.q("SELECT rejections, n_segments FROM learned_voiceprints") == [(1, 2)]
    assert segs_of(env, rid)[1][2:4] == (None, None)


# --- the day review reads the voice labels (omi_day_review.voice_segments / segment_map) --------------------------------------
# Negative controls (re-run by Edit, watch the named test go red):
#   map           make ``someone_else`` / ``person`` read ``not_sure`` in ``_VOICE_SPEAKER``     -> the someone_else / tagged-contact tests
#   not-a-person  drop the ``not_a_person`` skip in ``segment_map``                              -> the adapter unit test
#   no fallback   pass ``()`` instead of ``have`` to ``segment_map`` in ``voice_segments``       -> the not-a-person test (joined transcript leaks)
#   shadow        return ``trusted = True`` from ``review_segments``                             -> the shadow test
#   wiring        drop the ``segments is None`` loader in ``review_day``                         -> every end-to-end test here
#   fail closed   swallow every exception in ``voice_segments``                                  -> the unreadable-segments test
import json  # noqa: E402

import omi_day_review as dr  # noqa: E402

DAY_REVIEWED = "2026-10-10"
AS_OF = datetime(2026, 10, 11, 23, 59, tzinfo=UTC)
SURFER, MARATHON, SYDNEY = "I'm a keen surfer.", "I'm training for the half marathon in December.", "Sam said he is moving to Sydney next year."
FACT_RULES = [("I'm a keen surfer", {"kind": "owner_fact", "fields": {"fact": "I'm a keen surfer."}}),
              ("I'm training for the half marathon in December", {"kind": "owner_fact", "fields": {"fact": "I'm training for the half marathon in December."}})]


def speaks(monkeypatch, table):
    async def stt(path):
        with wave.open(path) as w:
            n = w.getnframes() // 16000
        return table.get(n, f"said{n}s")
    monkeypatch.setattr(omi, "_transcribe_file", stt)


def review_day(monkeypatch, rules, *, dry=True, mode=None):
    """Run the REAL day review over the member's day with a model that points at lines by substring; returns (result, what it saw)."""
    monkeypatch.setenv("ZOE_OMI_DAY_REVIEW", "shadow")
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    if mode:
        monkeypatch.setenv("ZOE_OMI_SPEAKER_TAG", mode)

    async def no_ledger(user_id, text):
        return False
    monkeypatch.setattr(dr, "_forgotten", no_ledger)
    seen: list = []

    def llm(messages, max_tokens):
        data = messages[-1]["content"].split("DATA>>>", 1)[1].rsplit("<<<DATA", 1)[0]
        seen.append(data)
        items = []
        for line in data.strip().splitlines():
            m = re.match(r"\[(r\d+\.\d+) [\d:-]+\] (.*)$", line)
            for needle, item in rules if m else ():
                if needle in m.group(2):
                    items.append({"ids": [m.group(1)], "quote": needle, "heard_from": "conversation", "about": "me", **item})
        return json.dumps({"items": items})
    dr.set_llm(llm)
    try:
        return run(dr.review_day(A, DAY_REVIEWED, dr.Config(lane="night"), as_of=AS_OF, dry_run=dry)), "\n".join(seen)
    finally:
        dr.set_llm(None)


def surfer_and_marathon(env, monkeypatch, other="other", **kw):
    """One recording: the wearer says SURFER (3 s), another voice says MARATHON (4 s). Returns the recording id."""
    speaks(monkeypatch, {3: SURFER, 4: MARATHON})
    return transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 7, other, 0.0, near(SAM, 1, 0.3))), seconds=8, **kw)


def test_the_adapter_maps_voice_labels_to_the_review_vocabulary():
    seg = lambda rid, label, text: {"recording_id": rid, "label": label, "text": text}        # noqa: E731
    got = dr.segment_map([seg("r1", "you", "a"), seg("r1", "someone_else", "b"), seg("r1", "person", "c"), seg("r1", "not_sure", "d"),
                          seg("r1", "unverified", "e"), seg("r1", "weird", "f"), seg("r1", "not_a_person", "TV line"),
                          seg("r2", "not_a_person", "Zoe's reply")], segmented=["r1", "r2", "r3"])
    assert [tuple(t)[:2] for t in got["r1"]] == [("a", "you"), ("b", "someone_else"), ("c", "someone_else"), ("d", "not_sure"),
                                                ("e", "not_sure"), ("f", "not_sure")]
    assert all(t.person_id == "" for t in got["r1"])                 # a tag with no person row of the member's own carries nobody
    assert got["r2"] == [] and got["r3"] == []                       # a recording of only excluded lines is present and EMPTY, not absent
    assert "TV line" not in json.dumps(got) and "Zoe's reply" not in json.dumps(got)
    assert dr.segment_map([]) == {}


def test_a_line_someone_else_said_never_becomes_an_owner_fact_but_the_wearers_own_can(env, monkeypatch):
    surfer_and_marathon(env, monkeypatch)
    res, seen = review_day(monkeypatch, FACT_RULES, mode="on")
    assert SURFER in seen and MARATHON in seen                                           # both lines reach the model, with their labels
    got = {c["payload"]["fact"]: c for c in res["candidates"]}
    assert set(got) == {"I'm a keen surfer."}, got                                       # the other voice's first-person line is dropped
    assert got["I'm a keen surfer."]["speaker"] == "you" and got["I'm a keen surfer."]["confidence"] == 0.8
    assert res["counts"]["drop_not_owner_words"] == 1


def test_a_tagged_contacts_line_is_someone_else_to_the_review(env, monkeypatch):
    rid = surfer_and_marathon(env, monkeypatch)
    (sid,) = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE recording_id = ? AND speaker = 'other'", (rid,))]
    assert env.client.post(f"/api/omi/segments/{sid}/tag", json={"name": "Sam"}).status_code == 200
    res, seen = review_day(monkeypatch, FACT_RULES)                                      # shadow: the member's tag still counts
    assert MARATHON in seen
    assert {c["payload"]["fact"] for c in res["candidates"]} == {"I'm a keen surfer."}, res["candidates"]    # Sam's first person is not the wearer's fact


def test_shadow_labels_do_not_unlock_owner_facts_but_the_members_word_does(env, monkeypatch):
    rid = surfer_and_marathon(env, monkeypatch)
    res, _ = review_day(monkeypatch, FACT_RULES)                                         # ZOE_OMI_SPEAKER_TAG=shadow (the fixture)
    assert len(res["candidates"]) == 2
    assert {c["speaker"] for c in res["candidates"]} == {"not_sure"}, res["candidates"]  # a model-only "you" is not a "you"
    assert all(c["confidence"] <= 0.5 for c in res["candidates"])
    sid = {r[1]: r[0] for r in env.db.q("SELECT id, speaker FROM omi_segments WHERE recording_id = ?", (rid,))}
    assert env.client.patch(f"/api/omi/segments/{sid['you']}", json={"owner_label": "you"}).status_code == 200
    assert env.client.patch(f"/api/omi/segments/{sid['other']}", json={"owner_label": "other"}).status_code == 200
    res, _ = review_day(monkeypatch, FACT_RULES)
    assert [(c["payload"]["fact"], c["speaker"]) for c in res["candidates"]] == [("I'm a keen surfer.", "you")]


def test_not_a_person_lines_never_reach_the_model_or_become_candidates(env, monkeypatch):
    speaks(monkeypatch, {3: SURFER, 4: MARATHON, 5: SYDNEY})
    r1 = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 7, "other", 0.0, near(TV, 1, 0.3))), seconds=8,
                    started="2026-10-10T01:00:00Z")
    r2 = transcribe(env, monkeypatch, turns((0, 5, "other", 0.0, near(TV, 2, 0.3))), seconds=6, started="2026-10-10T02:00:00Z")
    for rid in (r1, r2):
        (sid,) = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE recording_id = ? AND speaker = 'other'", (rid,))]
        assert env.client.patch(f"/api/omi/segments/{sid}", json={"not_person": "tv"}).status_code == 200
    # the joined transcripts still hold the TV text: only the exclusion keeps it away from the model
    assert MARATHON in env.db.q("SELECT transcript FROM omi_recordings WHERE id = ?", (r1,))[0][0]
    assert SYDNEY in env.db.q("SELECT transcript FROM omi_recordings WHERE id = ?", (r2,))[0][0]
    res, seen = review_day(monkeypatch, FACT_RULES + [("Sydney", {"kind": "owner_fact", "fields": {"fact": "I am moving to Sydney."}})], mode="on")
    assert SURFER in seen and MARATHON not in seen and "Sydney" not in seen             # r2 has ONLY an excluded line: no joined-transcript fallback
    assert {c["payload"]["fact"] for c in res["candidates"]} == {"I'm a keen surfer."}


def test_a_recording_without_segments_keeps_the_joined_transcript(env, monkeypatch):
    env.db.conn.execute("INSERT INTO omi_recordings (id, user_id, device_id, recording_key, started_at, status, transcript, created_at) "
                        "VALUES ('plain', ?, 'dev', 'plain', '2026-10-10T01:00:00Z', 'done', ?, '2026-10-10T01:00:00Z')", (A, SURFER + " " + MARATHON))
    env.db.conn.commit()
    res, seen = review_day(monkeypatch, FACT_RULES, mode="on")
    assert SURFER in seen and MARATHON in seen
    assert {c["speaker"] for c in res["candidates"]} == {"not_sure"}                     # no labels: every line is not_sure, as before


def test_unreadable_segments_stop_the_review_before_the_claim_but_a_missing_table_does_not(env, monkeypatch):
    _migrate(env.engine, "0054_omi_day_review.py")
    surfer_and_marathon(env, monkeypatch)

    async def broken(db, user_id, ids):
        raise RuntimeError("connection reset")
    monkeypatch.setattr(ov, "review_segments", broken)
    res, seen = review_day(monkeypatch, FACT_RULES, dry=False)
    assert res["status"] == "error" and res["reason"] == "segments_RuntimeError" and seen == ""   # the model was never called
    assert env.db.q("SELECT COUNT(*) FROM omi_day_reviews") == [(0,)]                              # and no claim is left behind
    assert env.db.q("SELECT day_review_id FROM omi_recordings") == [(None,)]

    async def missing(db, user_id, ids):
        raise sqlite3.OperationalError("no such table: omi_segments")                          # a database before migration 0055
    monkeypatch.setattr(ov, "review_segments", missing)
    res, seen = review_day(monkeypatch, FACT_RULES)
    assert res["status"] == "ok" and SURFER in seen and MARATHON in seen                         # joined-transcript path


# --- follow-ups (2026-10-18). Negative controls:
#   b  drop the owner_label+not_person check in ``label_segment``                              -> the both-fields test
#   c  drop ``AND learning_enabled = 1`` from the rejection UPDATE in ``answer_suggestion``     -> the stopped-voice test
#   d  scope by ``people_id`` only (drop the model clause of ``_withdraw_orphaned_suggestions``) -> the model-scoped cleanup tests;
#      or scope by model only (drop ``model_id IS NULL``)                                       -> the safe-side assertion
#   e  return every manifest entry from ``one_variant_per_clip``                                -> the calibration leakage test
def test_a_patch_with_both_not_person_and_owner_label_is_rejected_and_applies_nothing(env, monkeypatch):
    for i in range(2):
        transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, i, 0.3))))
    sid = env.db.q("SELECT id FROM omi_segments WHERE speaker = 'other' ORDER BY id")[0][0]
    for body in ({"not_person": "tv", "owner_label": "you"}, {"not_person": "tv", "owner_label": "other"},
                 {"owner_label": None, "not_person": "radio"}):
        r = env.client.patch(f"/api/omi/segments/{sid}", json=body)
        assert r.status_code == 400, body
        assert env.db.q("SELECT not_person, owner_label FROM omi_segments WHERE id = ?", (sid,)) == [(None, None)], body
    # control: each field alone is still accepted, so the 400 is the combination and not "PATCH is broken"
    assert env.client.patch(f"/api/omi/segments/{sid}", json={"owner_label": "other"}).status_code == 200
    assert env.client.patch(f"/api/omi/segments/{sid}", json={"not_person": "tv"}).status_code == 200
    assert env.db.q("SELECT not_person FROM omi_segments WHERE id = ?", (sid,)) == [("tv",)]


def test_a_no_on_a_stopped_voice_does_not_move_its_rejection_count(env, monkeypatch):
    pid, sid = _suggested(env, monkeypatch)
    (vp,) = [v["id"] for v in env.client.get("/api/omi/voices").json()["voices"]]
    assert env.client.post(f"/api/omi/segments/{sid}/suggestion", json={"answer": "no"}).status_code == 200
    assert env.db.q("SELECT rejections FROM learned_voiceprints") == [(1,)]               # control: a live voice counts the no
    assert env.client.patch(f"/api/omi/voices/{vp}", json={"learning_enabled": False}).status_code == 200
    env.db.conn.execute("UPDATE omi_segments SET suggested_person_id = ? WHERE id = ?", (pid, sid))      # a suggestion that outlived the stop
    env.db.conn.commit()
    assert env.client.post(f"/api/omi/segments/{sid}/suggestion", json={"answer": "no"}).status_code == 200
    assert env.db.q("SELECT rejections, learning_enabled FROM learned_voiceprints") == [(1, 0)]       # frozen: not 2
    assert env.db.q("SELECT suggested_person_id FROM omi_segments WHERE id = ?", (sid,)) == [(None,)]  # the suggestion itself is still cleared


def _three_suggestions(env, monkeypatch):
    """Sam (an adult, tagged) with a print under the real model and a second print under ``other-model``; three lines suggest Sam:
    one made by the real model, one by ``other-model``, one with no recorded model."""
    pid, sid = _suggested(env, monkeypatch)
    env.db.conn.execute("INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, channel, embedding, n_segments, "
                        "n_recordings, status, learning_enabled, created_at, updated_at, expires_at) VALUES ('vp-other', ?, ?, "
                        "'other-model', 'pendant', ?, 5, 5, 'provisional', 1, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', "
                        "'2999-01-01T00:00:00Z')", (A, pid, sqlite3.Binary(np.zeros(256, dtype="<f4").tobytes())))
    spare = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE id != ? AND speaker = 'other' ORDER BY id", (sid,))]
    s_other, s_null = spare[0], spare[1]
    env.db.conn.execute("UPDATE omi_segments SET model_id = 'other-model', suggested_person_id = ? WHERE id = ?", (pid, s_other))
    env.db.conn.execute("UPDATE omi_segments SET model_id = NULL, suggested_person_id = ? WHERE id = ?", (pid, s_null))
    env.db.conn.commit()
    (vp_main,) = [r[0] for r in env.db.q("SELECT id FROM learned_voiceprints WHERE model_id = ?", (MID,))]
    return pid, {"main": sid, "other": s_other, "null": s_null}, vp_main


def _suggesting(env):
    return {r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE suggested_person_id IS NOT NULL")}


def test_forgetting_a_voice_withdraws_only_the_suggestions_its_model_made(env, monkeypatch):
    pid, seg, vp_main = _three_suggestions(env, monkeypatch)
    assert _suggesting(env) == set(seg.values())
    assert env.client.delete(f"/api/omi/voices/{vp_main}").status_code == 200
    # the real model's suggestion goes; a line with NO recorded model goes too (safe side); the other model's print still stands behind its own
    assert _suggesting(env) == {seg["other"]}
    assert env.client.delete("/api/omi/voices/vp-other").status_code == 200
    assert _suggesting(env) == set()                                                       # no print left for Sam: nothing may still suggest him


def test_an_expired_voice_withdraws_only_the_suggestions_its_model_made(env, monkeypatch):
    pid, seg, vp_main = _three_suggestions(env, monkeypatch)
    env.db.conn.execute("UPDATE learned_voiceprints SET expires_at = '2020-01-01T00:00:00Z' WHERE id = ?", (vp_main,))
    env.db.conn.commit()
    run(_sweep(datetime.now(UTC)))
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprints WHERE id = ?", (vp_main,)) == [(0,)]
    assert _suggesting(env) == {seg["other"]}                                              # same rule as forgetting it
    env.db.conn.execute("UPDATE learned_voiceprints SET expires_at = '2020-01-01T00:00:00Z' WHERE id = 'vp-other'")
    env.db.conn.commit()
    run(_sweep(datetime.now(UTC)))
    assert _suggesting(env) == set()


def test_calibration_keeps_one_variant_per_source_clip(tmp_path):
    """The public manifest lists a clip twice (clean + farfield of the same utterance); using both let a voiceprint be scored against
    its own twin. One variant per (speaker, file name): farfield preferred, the other kept only when it is all there is."""
    cal = _calibrate_module()
    man = [{"source": "libri", "speaker": "11", "variant": "farfield", "path": "libri/farfield/11_e0.wav"},
           {"source": "libri", "speaker": "11", "variant": "clean", "path": "libri/clean/11_e0.wav"},
           {"source": "libri", "speaker": "11", "variant": "clean", "path": "libri/clean/11_e1.wav"},
           {"source": "libri", "speaker": "22", "variant": "clean", "path": "libri/clean/22_e0.wav"},
           {"source": "libri", "speaker": "22", "variant": "farfield", "path": "libri/farfield/22_e0.wav"},
           {"source": "kokoro", "speaker": "k", "variant": "clean", "path": "kokoro/clean/k_e0.wav"}]
    got = cal.one_variant_per_clip(man)
    assert got == {"11": ["libri/farfield/11_e0.wav", "libri/clean/11_e1.wav"], "22": ["libri/farfield/22_e0.wav"]}
    stems = [(spk, Path(p).name) for spk, ps in got.items() for p in ps]
    assert len(stems) == len(set(stems))                                                   # no source clip appears twice
    assert cal.one_variant_per_clip(man, prefer="clean")["11"] == ["libri/clean/11_e0.wav", "libri/clean/11_e1.wav"]


# --- the Pendant page's glue (desktop/phone tagging, 2026-10-18) -------------------------------------------------------------
def test_unknown_voices_carry_their_sample_lines_so_the_page_needs_no_extra_reads(env, monkeypatch):
    """Mutation: drop ``s.text`` from ``list_unknown``'s select -> the sample has no words."""
    _sam_cluster(env, monkeypatch)
    (v,) = env.client.get("/api/omi/voices/unknown").json()["voices"]
    assert v["samples"] and all(s["text"] == "said3s" and s["started_at"] for s in v["samples"])


def test_that_voice_is_me_relabels_every_line_as_the_wearer_and_learns_nothing(env, monkeypatch):
    """The pendant can hear the wearer as 'other'. Mutation: skip the embedding delete in ``cluster_is_me`` -> the wearer's own
    voice stays held as a bystander's (red); drop ``user_id = ?`` from its cluster check -> member B can relabel A's voice (red)."""
    cid = _sam_cluster(env, monkeypatch)
    env.who["user"] = {"user_id": B, "role": "admin"}
    assert env.client.post(f"/api/omi/voices/unknown/{cid}/me").status_code == 404            # another member, even an admin
    env.who["user"] = {"user_id": A, "role": "member"}
    out = env.client.post(f"/api/omi/voices/unknown/{cid}/me").json()
    assert out == {"ok": True, "segments": 2}
    assert env.db.q("SELECT COUNT(*) FROM omi_segments WHERE owner_label = 'you' AND voice_cluster_id IS NULL") == [(2,)]
    assert env.db.q("SELECT COUNT(*) FROM omi_segment_embeddings") == [(0,)]
    assert env.db.q("SELECT COUNT(*) FROM omi_voice_clusters") == [(0,)]
    assert env.db.q("SELECT COUNT(*) FROM learned_voiceprints") == [(0,)] and env.db.q("SELECT COUNT(*) FROM people") == [(0,)]
    assert env.client.get("/api/omi/voices/unknown").json()["voices"] == []
    assert env.client.post(f"/api/omi/voices/unknown/{cid}/me").status_code == 404            # gone


def test_the_picker_lists_only_my_own_people(env, monkeypatch):
    """Mutation: drop ``user_id = ?`` from ``own_people`` -> member B's family-visible contact shows in A's picker (red)."""
    env.db.conn.executemany("INSERT INTO people (id, user_id, name, visibility, deleted) VALUES (?, ?, ?, ?, ?)",
                            [("pa", A, "Robin", "personal", 0), ("pb", B, "Casey", "family", 0), ("pg", A, "Gone", "personal", 1)])
    env.db.conn.commit()
    assert env.client.get("/api/omi/voices/people").json() == {"people": [{"id": "pa", "name": "Robin"}]}
    env.who["user"] = {"user_id": B, "role": "admin"}
    assert env.client.get("/api/omi/voices/people").json() == {"people": [{"id": "pb", "name": "Casey"}]}


def test_a_typed_name_that_is_already_my_person_links_them_and_an_ambiguous_one_asks(env, monkeypatch):
    """Mutation: drop the fold match in ``_resolve_person`` -> a second 'Sam' row is created (red)."""
    cid = _sam_cluster(env, monkeypatch)
    env.db.conn.execute("INSERT INTO people (id, user_id, name, visibility, deleted) VALUES ('p-sam', ?, 'Sam', 'personal', 0)", (A,))
    env.db.conn.commit()
    out = env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "  sam "}).json()
    assert out["person_id"] == "p-sam" and out["person_created"] is False
    assert env.db.q("SELECT COUNT(*) FROM people") == [(1,)]
    env.db.conn.execute("INSERT INTO people (id, user_id, name, visibility, deleted) VALUES ('p-sam2', ?, 'SAM', 'personal', 0)", (A,))
    env.db.conn.commit()
    (sid,) = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE speaker = 'other' LIMIT 1")]
    r = env.client.post(f"/api/omi/segments/{sid}/tag", json={"name": "Sam"})
    assert r.status_code == 409 and env.db.q("SELECT COUNT(*) FROM people") == [(2,)]


def _held(env, rid):
    (row,) = env.db.q("SELECT audio_path FROM omi_recordings WHERE id = ?", (rid,))
    return bool(row[0]) and (env.tmp / f"{rid}.wav").exists()


def test_audio_is_held_for_tagging_only_while_a_line_needs_a_name(env, monkeypatch):
    """``ZOE_OMI_KEEP_AUDIO_FOR_TAGGING``: the WAV stays while a someone-else line is unnamed and goes the moment it is named.
    Mutations: drop ``needs_name`` from the hold -> the you-only recording is held (red); skip ``_released`` on the tag route -> the
    named recording keeps its audio (red)."""
    monkeypatch.setenv("ZOE_OMI_AUDIO_DIR", str(env.tmp))
    spec = turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, SAM))
    rid_off = transcribe(env, monkeypatch, spec)                                    # flag off (default): deleted at transcription
    assert not _held(env, rid_off)
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO_FOR_TAGGING", "1")
    rid_me = transcribe(env, monkeypatch, turns((0, 6, "you", 0.6, WEARER)))
    assert not _held(env, rid_me)                                                  # nothing to name: nothing held
    rid = transcribe(env, monkeypatch, spec)
    assert _held(env, rid)
    (sid,) = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE recording_id = ? AND speaker = 'other'", (rid,))]
    clip = env.client.get(f"/api/omi/segments/{sid}/clip")
    assert clip.status_code == 200 and clip.headers["content-type"] == "audio/wav" and clip.headers["cache-control"] == "no-store"
    with wave.open(io.BytesIO(clip.content)) as w:
        assert w.getframerate() == 16000 and w.getnframes() == 3 * 16000         # exactly that turn (3 s, under the 8 s cap)
    env.who["user"] = {"user_id": B, "role": "admin"}
    assert env.client.get(f"/api/omi/segments/{sid}/clip").status_code == 404     # another member's line, even an admin
    env.who["user"] = {"user_id": A, "role": "member"}
    assert env.client.post(f"/api/omi/segments/{sid}/tag", json={"name": "Sam"}).status_code == 200
    assert not _held(env, rid)                                                     # named: the WAV is gone at once
    assert env.client.get(f"/api/omi/segments/{sid}/clip").status_code == 404


def test_held_audio_goes_on_me_or_not_a_person_and_when_the_flag_is_turned_off(env, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_AUDIO_DIR", str(env.tmp))
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO_FOR_TAGGING", "1")
    a = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, SAM)))
    b = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, TV)))
    c = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "unsure", 0.1, near(SAM, 3))))
    assert _held(env, a) and _held(env, b) and _held(env, c)
    (sa,) = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE recording_id = ? AND speaker = 'other'", (a,))]
    (sb,) = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE recording_id = ? AND speaker = 'other'", (b,))]
    assert env.client.patch(f"/api/omi/segments/{sa}", json={"owner_label": "you"}).status_code == 200
    assert env.client.patch(f"/api/omi/segments/{sb}", json={"not_person": "tv"}).status_code == 200
    assert not _held(env, a) and not _held(env, b) and _held(env, c)
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO_FOR_TAGGING", "0")
    assert run(omi.release_tagged_audio()) == 1 and not _held(env, c)             # the retention loop's backstop


def test_a_line_learn_marks_not_a_person_releases_the_held_audio_at_once(env, monkeypatch):
    """Codex review on #2118: the hold is decided before ``learn`` runs, and ``learn`` marks a line matching a voice the member
    called TV as not-a-person. Mutation: drop the post-transcription ``release_tagged_audio`` -> the WAV stays held (red)."""
    monkeypatch.setenv("ZOE_OMI_AUDIO_DIR", str(env.tmp))
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO_FOR_TAGGING", "1")
    first = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(TV, 1, 0.3))))
    (cid,) = [v["id"] for v in env.client.get("/api/omi/voices/unknown").json()["voices"]]
    assert env.client.post(f"/api/omi/voices/unknown/{cid}/not-a-person", json={"kind": "tv"}).status_code == 200
    assert not _held(env, first)
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(TV, 2, 0.3))))
    assert env.db.q("SELECT not_person FROM omi_segments WHERE recording_id = ? AND speaker = 'other'", (rid,)) == [("tv",)]
    assert not _held(env, rid)


def test_un_marking_a_tv_line_as_not_me_is_one_request_and_keeps_the_audio(env, monkeypatch):
    """Codex review on #2118: a TV-marked line the model called ``you`` is corrected to "not me". As two requests, the un-mark
    alone left nothing needing a name and released the WAV before the label made the line nameable again. One request
    ``{"not_person": null, "owner_label": "other"}`` does both, then releases. Mutation: reject that combination again (red)."""
    monkeypatch.setenv("ZOE_OMI_AUDIO_DIR", str(env.tmp))
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO_FOR_TAGGING", "1")
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, SAM)))
    (you_sid,) = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE recording_id = ? AND speaker = 'you'", (rid,))]
    (sam_sid,) = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE recording_id = ? AND speaker = 'other'", (rid,))]
    assert env.client.patch(f"/api/omi/segments/{you_sid}", json={"not_person": "tv"}).status_code == 200
    assert _held(env, rid)                                                         # Sam's line still needs a name
    env.db.conn.execute("UPDATE omi_segments SET not_person = 'radio' WHERE id = ?", (sam_sid,))   # nothing needs a name now, but
    env.db.conn.commit()                                                                          # no release has run yet
    assert env.client.patch(f"/api/omi/segments/{you_sid}", json={"not_person": None, "owner_label": "other"}).status_code == 200
    assert env.db.q("SELECT not_person, owner_label FROM omi_segments WHERE id = ?", (you_sid,)) == [(None, "other")]
    assert _held(env, rid)                                                         # the corrected line needs a name: still playable
    clip = env.client.get(f"/api/omi/segments/{you_sid}/clip")
    assert clip.status_code == 200
    r = env.client.patch(f"/api/omi/segments/{you_sid}", json={"not_person": None, "owner_label": "you"})
    assert r.status_code == 200 and env.db.q("SELECT owner_label FROM omi_segments WHERE id = ?", (you_sid,)) == [("you",)]
    assert not _held(env, rid)                                                     # now nothing needs a name: released


def test_held_audio_never_outlives_its_retention_and_a_clip_never_leaves_the_audio_dir(env, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_AUDIO_DIR", str(env.tmp))
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO_FOR_TAGGING", "1")
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, SAM)))
    (sid,) = [r[0] for r in env.db.q("SELECT id FROM omi_segments WHERE recording_id = ? AND speaker = 'other'", (rid,))]
    monkeypatch.setenv("ZOE_OMI_AUDIO_DIR", str(env.tmp / "elsewhere"))
    assert env.client.get(f"/api/omi/segments/{sid}/clip").status_code == 404     # a path outside the audio dir is never read
    monkeypatch.setenv("ZOE_OMI_AUDIO_DIR", str(env.tmp))
    assert env.client.get(f"/api/omi/segments/{sid}/clip").status_code == 200
    env.db.conn.execute("UPDATE omi_recordings SET audio_expires_at = '2000-01-01T00:00:00Z' WHERE id = ?", (rid,))
    env.db.conn.commit()
    assert env.client.get(f"/api/omi/segments/{sid}/clip").status_code == 404     # past retention: not served even before the purge
    assert run(omi.purge_expired_audio()) == 1 and not _held(env, rid)


def test_the_voices_read_says_which_tagging_mode_is_live(env, monkeypatch):
    assert env.client.get("/api/omi/voices").json()["speaker_tag"] == "shadow"
    monkeypatch.setenv("ZOE_OMI_SPEAKER_TAG", "bogus")
    assert env.client.get("/api/omi/voices").json()["speaker_tag"] == "off"
