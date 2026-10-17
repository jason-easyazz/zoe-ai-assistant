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
    assert [r["label"] for r in run(_review(rid))] == ["you"]                         # and never reaches the day review


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


def test_a_voice_erase_failure_is_loud_and_never_blocks_erasing_audio(env, monkeypatch, caplog):
    rid = transcribe(env, monkeypatch, turns((0, 6, "other", 0.0, TV)))

    async def boom(db, uid):
        raise RuntimeError("voice tables unreachable")
    monkeypatch.setattr(ov, "forget_user", boom)
    caplog.set_level("ERROR")
    assert run(omi.forget_user(A)) == 1
    assert env.db.q("SELECT status FROM omi_recordings WHERE id = ?", (rid,)) == [("deleted",)]
    assert "OMI_VOICES_ERASE_INCOMPLETE" in caplog.text


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


def test_someone_else_clears_the_suggestion_and_learns_nothing(env, monkeypatch):
    cid = _sam_cluster(env, monkeypatch)
    env.client.post(f"/api/omi/voices/unknown/{cid}/tag", json={"name": "Sam"})
    rid = transcribe(env, monkeypatch, turns((0, 3, "you", 0.6, WEARER), (3, 6, "other", 0.0, near(SAM, 9, 0.3))))
    (seg,) = [s for s in run(_segs(rid)) if s["speaker"] == "other"]
    assert env.client.post(f"/api/omi/segments/{seg['id']}/suggestion", json={"answer": "maybe"}).status_code == 400
    assert env.client.post(f"/api/omi/segments/{seg['id']}/suggestion", json={"answer": "someone_else"}).status_code == 200
    assert env.db.q("SELECT rejections, n_segments FROM learned_voiceprints") == [(1, 2)]
    assert segs_of(env, rid)[1][2:4] == (None, None)
