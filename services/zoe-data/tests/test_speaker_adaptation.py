"""Guarded speaker-profile adaptation (``speaker_adaptation.py``, ``routers/voice_speaker_adapt.py``).

What is pinned, and the Edit-based negative control for each (delete / loosen the named line in
``speaker_adaptation.py`` and the named test goes red):

* every guard - low score, off-anchor, short clip, no corroboration, close to another profile, interval and daily rate
  limit, drift bound, invalid embedding - refuses on its own (``test_each_guard_refuses_alone``); the lone happy path
  proves the scenario is otherwise accepted, so a red guard is the GUARD, not the fixture;
* the panel's claimed score can only RAISE the bar (``test_claimed_score_can_only_raise_the_bar``);
* the drift bound holds against an adversary that sends the furthest possible vector forever
  (``test_drift_bound_survives_an_adversary``);
* ``off`` touches nothing - no DB access at all (``test_off_mode_touches_nothing``); ``shadow`` runs the pipeline,
  logs, and writes nothing (``test_shadow_writes_nothing_but_logs``);
* ``on`` updates only the adapted column, never the anchor, with the audit line and no embedding values in it;
* a re-enrolment invalidates the adapted profile without the enrol route knowing adaptation exists;
* "forget my voice" (the existing profile delete) removes anchor AND adapted profile - they share a row;
* the sync / identify feeds serve the adapted profile ONLY in mode ``on`` and are otherwise byte-identical.

Real SQL: the schema is the repo's own (migration 0048 applied to a table shaped like 0001 + 0023) on SQLite; no
network, no model, no live DB.
"""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import json
import logging
import sqlite3
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = pytest.mark.ci_safe

import speaker_adaptation as spk
import speaker_models
from speaker_adaptation import AdaptConfig, AdaptState, evaluate

SVC = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)
CFG = AdaptConfig()
DIM = 256


# ── geometry helpers: unit vectors at exact angles in the (e0, e1) plane ─────

def at_angle(deg: float) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[0], v[1] = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return v


def at_cos(c: float) -> np.ndarray:
    return at_angle(float(np.degrees(np.arccos(c))))


ANCHOR = at_angle(0.0)


def run(**kw):
    """evaluate() with a happy-path default; override exactly one thing per test."""
    args = dict(embedding=at_cos(0.90), anchor=ANCHOR, adapted=None, others=[], claimed_score=None, speech_s=3.0,
                corroboration=["panel_bound"], state=AdaptState(), now=NOW, cfg=CFG)
    args.update(kw)
    return evaluate(**args)


# ── the guards, one at a time ─────────────────────────────────────────────

def test_happy_path_is_accepted_and_moves_a_small_step():
    d = run()
    assert d.allowed and d.reason == "ok"
    assert d.new_vec is not None and abs(float(np.linalg.norm(d.new_vec)) - 1.0) < 1e-5
    assert 0 < d.delta_norm < 0.1                       # alpha = 0.05: a small step
    assert d.drift_cos > CFG.min_anchor_cos
    assert d.margin is None                             # solo household: nobody to confuse it with


# angle of the offered clip relative to the anchor, built so ONLY the named guard is violated
_ADAPTED_23 = at_angle(23.0)   # a drifted-but-in-bounds adapted profile (cos 0.92 to the anchor)

GUARD_CASES = [
    ("low_score", dict(embedding=at_cos(0.84))),
    # near the drifted adapted profile (cos .96) yet unlike the enrolled voice (cos .78)
    ("off_anchor", dict(embedding=at_angle(39.0), adapted=_ADAPTED_23)),
    ("short_clip", dict(speech_s=1.0)),
    ("short_clip", dict(speech_s=None)),
    ("no_corroboration", dict(corroboration=[])),
    # another member's profile sits at cos 0.85 from the clip: margin .07 < .10
    ("close_to_other", dict(embedding=at_cos(0.92), others=[at_angle(float(np.degrees(np.arccos(0.92))) - 31.8)])),
    ("rate_interval", dict(state=AdaptState(adapt_count=1, adapted_at=NOW - timedelta(seconds=60)))),
    ("rate_daily", dict(state=AdaptState(adapt_count=5, adapted_at=NOW - timedelta(hours=2),
                                         day="2026-10-12", day_count=5))),
    ("drift_bound", dict(cfg=AdaptConfig(min_anchor_cos=0.99999))),
    ("invalid_embedding", dict(embedding=np.zeros(DIM, dtype=np.float32))),
    ("invalid_embedding", dict(embedding=np.full(DIM, np.nan, dtype=np.float32))),
    ("invalid_embedding", dict(embedding=at_cos(0.9)[:128])),
    ("invalid_embedding", dict(embedding=at_cos(0.9) * 2.0)),     # not unit-norm: not a resemblyzer embedding
]


@pytest.mark.parametrize("reason,override", GUARD_CASES, ids=[f"{r}-{i}" for i, (r, _) in enumerate(GUARD_CASES)])
def test_each_guard_refuses_alone(reason, override):
    d = run(**override)
    assert not d.allowed and d.reason == reason and d.new_vec is None


def test_daily_cap_resets_on_a_new_utc_day():
    yesterday = AdaptState(adapt_count=5, adapted_at=NOW - timedelta(days=1), day="2026-10-11", day_count=5)
    assert run(state=yesterday).allowed


def test_other_members_far_away_do_not_block():
    far = at_angle(float(np.degrees(np.arccos(0.90))) + 70.0)
    d = run(others=[far])
    assert d.allowed and d.margin > CFG.min_margin


def test_claimed_score_can_only_raise_the_bar():
    # server computes 0.90; a panel that claims LESS lowers the effective score under the bar ...
    assert run(claimed_score=0.80).reason == "low_score"
    # ... and a panel that claims MORE cannot rescue a clip the server scored low.
    assert run(embedding=at_cos(0.84), claimed_score=0.99).reason == "low_score"
    assert run(claimed_score=float("nan")).allowed   # a non-finite claim is ignored, not trusted


def test_updates_move_toward_the_clip_by_alpha():
    d = run()
    expected = (1 - CFG.alpha) * ANCHOR + CFG.alpha * at_cos(0.90)
    expected = expected / np.linalg.norm(expected)
    assert np.allclose(d.new_vec, expected, atol=1e-5)


def test_drift_bound_survives_an_adversary():
    """Guards 1-6 OFF, alpha unchanged, an adversary always pulling in one fixed direction orthogonal to the anchor
    (the direction that rotates the profile fastest; the exact opposite of the anchor would not rotate it at all) 5000
    times: the adapted profile must stop at the bound (refusal, not clamp: drift_bound is the only thing standing)."""
    cfg = AdaptConfig(min_score=-2, anchor_floor=-2, min_margin=-2, min_speech_s=0, max_per_day=10**9,
                      min_interval_s=0)
    adapted, state, reasons = None, AdaptState(), set()
    for i in range(5000):
        d = evaluate(embedding=at_angle(90.0), anchor=ANCHOR, adapted=adapted, others=[], claimed_score=None, speech_s=9.0,
                     corroboration=["panel_bound"], state=state, now=NOW + timedelta(days=i), cfg=cfg)
        reasons.add(d.reason)
        if d.allowed:
            adapted = d.new_vec
        assert adapted is None or spk.cosine(adapted, ANCHOR) >= cfg.min_anchor_cos - 1e-6
    assert "drift_bound" in reasons                      # it did try to cross, and was refused
    assert spk.cosine(adapted, ANCHOR) >= cfg.min_anchor_cos - 1e-6


# ── serving: adapted iff valid for THIS anchor ───────────────────────────

def test_serve_blob_rules():
    a, v = ANCHOR.tobytes(), at_angle(5.0).tobytes()
    assert spk.serve_blob(a, v, 3, 3) == v               # valid
    assert spk.serve_blob(a, None, None, 3) == a          # never adapted
    assert spk.serve_blob(a, v, 3, 4) == a                # re-enrolled since: stale
    assert spk.serve_blob(a, v[:512], 3, 3) == a          # wrong dimension


def test_serve_blob_ignores_unusable_adapted_vectors():
    a = ANCHOR.tobytes()
    nan = np.full(DIM, np.nan, dtype=np.float32).tobytes()
    assert spk.serve_blob(a, nan, 3, 3) == a


def test_config_defaults_env_overrides_and_safe_fallbacks(monkeypatch):
    for k in ("MIN_SCORE", "ANCHOR_FLOOR", "MIN_MARGIN", "MIN_SPEECH_S", "ALPHA", "MIN_ANCHOR_COS", "MAX_PER_DAY",
              "MIN_INTERVAL_S", "PIN_WINDOW_S", "FACE_MIN"):
        monkeypatch.delenv(f"ZOE_SPEAKER_ADAPT_{k}", raising=False)
    assert AdaptConfig.from_env() == AdaptConfig()
    assert (CFG.min_score, CFG.alpha, CFG.min_anchor_cos, CFG.max_per_day) == (0.85, 0.05, 0.92, 5)
    monkeypatch.setenv("ZOE_SPEAKER_ADAPT_MIN_SCORE", "0.9")
    monkeypatch.setenv("ZOE_SPEAKER_ADAPT_MAX_PER_DAY", "2")
    assert (AdaptConfig.from_env().min_score, AdaptConfig.from_env().max_per_day) == (0.9, 2)
    # a big alpha, a zero alpha, a NaN and a typo all fall back rather than loosening the policy
    for bad_alpha in ("0.9", "0", "-1", "nan", "fast"):
        monkeypatch.setenv("ZOE_SPEAKER_ADAPT_ALPHA", bad_alpha)
        assert AdaptConfig.from_env().alpha == 0.05, bad_alpha
    monkeypatch.setenv("ZOE_SPEAKER_ADAPT_MIN_SCORE", "nan")
    assert AdaptConfig.from_env().min_score == 0.85


# ── SQLite compat double + schema ───────────────────────────────────────

class _Cur:
    def __init__(self, rows, rowcount):
        self._rows, self.rowcount = rows, rowcount

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)


class _Exec:
    """Both ``await db.execute(...)`` and ``async with db.execute(...) as cur`` (the pool wrapper's two shapes)."""

    def __init__(self, coro):
        self._coro = coro

    def __await__(self):
        return self._coro.__await__()

    async def __aenter__(self):
        return await self._coro

    async def __aexit__(self, *exc):
        return False


class SqliteCompat:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.queries: list[str] = []
        self.before_update = None        # hook: simulate a concurrent writer

    def execute(self, sql, params=()):
        async def go():
            self.queries.append(sql)
            if sql.lstrip().upper().startswith("UPDATE SPEAKER_PROFILES SET ADAPTED_BLOB = ?") and self.before_update:
                hook, self.before_update = self.before_update, None
                hook(self.conn)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall() if cur.description else []
            return _Cur(rows, cur.rowcount)
        return _Exec(go())

    async def commit(self):
        self.conn.commit()

    def scalar(self, sql, params=()):
        r = self.conn.execute(sql, params).fetchone()
        return r[0] if r else None


def _migration_0048():
    spec = importlib.util.spec_from_file_location("mig_0048", SVC / "alembic/versions/0048_speaker_adaptation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _migration_0051():
    spec = importlib.util.spec_from_file_location("mig_0051", SVC / "alembic/versions/0051_speaker_profile_model_id.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BASE_SCHEMA = """
CREATE TABLE speaker_profiles (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, display_name TEXT NOT NULL, embedding_blob BLOB NOT NULL,
    enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, sample_count INTEGER DEFAULT 0, panel_id TEXT,
    consent_at TIMESTAMP);
CREATE TABLE panel_user_bindings (panel_id TEXT NOT NULL, user_id TEXT NOT NULL,
    binding_type TEXT NOT NULL DEFAULT 'allowed', priority INTEGER DEFAULT 0, PRIMARY KEY (panel_id, user_id));
CREATE TABLE ui_panel_sessions (panel_id TEXT PRIMARY KEY, user_id TEXT, last_seen_at TEXT, updated_at TEXT);
"""


def _build_db(path):
    eng = sa.create_engine(f"sqlite:///{path}")
    with eng.begin() as conn:
        for stmt in BASE_SCHEMA.split(";"):
            if stmt.strip():
                conn.exec_driver_sql(stmt)
        with Operations.context(MigrationContext.configure(conn)):
            _migration_0048().upgrade()
            _migration_0051().upgrade()
    eng.dispose()


def _b64(v) -> str:
    return base64.b64encode(np.asarray(v, dtype=np.float32).tobytes()).decode()


class World:
    """``model_id`` None = the pre-0051 rows (NULL = the retired Resemblyzer model); this file's own fixture builds a ResNet34 world,
    the only model the server acts on, so its offers name it."""

    def __init__(self, tmp_path, model_id=None):
        self.model_id = model_id
        self.path = str(tmp_path / "zoe.db")
        _build_db(self.path)
        self.db = SqliteCompat(self.path)
        self.add_profile("jason", ANCHOR)
        self.bind("panel1", "jason")

    def add_profile(self, uid, vec, *, consent=True, sample_count=3):
        self.db.conn.execute(
            "INSERT INTO speaker_profiles (id, user_id, display_name, embedding_blob, sample_count, consent_at, model_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (f"pid-{uid}", uid, uid.title(), np.asarray(vec, dtype=np.float32).tobytes(), sample_count,
             "2026-01-01 00:00:00" if consent else None, self.model_id))
        self.db.conn.commit()

    def bind(self, panel, uid):
        self.db.conn.execute("INSERT INTO panel_user_bindings (panel_id, user_id, binding_type) VALUES (?, ?, 'default')",
                             (panel, uid))
        self.db.conn.commit()

    def unbind(self):
        self.db.conn.execute("DELETE FROM panel_user_bindings")
        self.db.conn.commit()

    def row(self, uid="jason"):
        return self.db.conn.execute(
            "SELECT embedding_blob, adapted_blob, adapted_from_count, adapt_count, adapted_at, adapt_day, "
            "adapt_day_count FROM speaker_profiles WHERE user_id = ?", (uid,)).fetchone()

    async def offer(self, emb=None, *, mode="on", now=NOW, panel="panel1", cfg=CFG, **payload):
        body = {"voice_user_id": "jason", "embedding_base64": _b64(at_cos(0.90) if emb is None else emb),
                "voice_score": 0.90, "speech_s": 3.0}
        if self.model_id:
            body["model_id"] = self.model_id
        body.update(payload)
        return await spk.process_adaptation(self.db, panel_id=panel, payload=body, now=now, mode=mode, cfg=cfg)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path, model_id=speaker_models.RESNET34_MODEL_ID)


# ── migration ──────────────────────────────────────────────────────────

def test_migration_is_rerun_safe_and_adds_only_nullable_columns(tmp_path):
    path = str(tmp_path / "m.db")
    _build_db(path)
    eng = sa.create_engine(f"sqlite:///{path}")
    with eng.begin() as conn:                       # second upgrade over the upgraded table: a no-op
        with Operations.context(MigrationContext.configure(conn)):
            _migration_0048().upgrade()
        cols = {r[1]: r for r in conn.exec_driver_sql("PRAGMA table_info(speaker_profiles)").fetchall()}
    assert {"adapted_blob", "adapted_from_count", "adapt_count", "adapted_at", "adapt_day",
            "adapt_day_count"} <= set(cols)
    assert all(cols[c][3] == 0 for c in ("adapted_blob", "adapted_from_count"))   # nullable: old rows are unaffected
    assert _migration_0048().down_revision == "0047"


# ── mode: off / shadow / on ──────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [(None, "off"), ("", "off"), ("off", "off"), ("on", "on"),
                                          ("SHADOW", "shadow"), ("true", "off"), ("1", "off"), ("yes", "off")])
def test_mode_parsing_fails_to_off(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("ZOE_SPEAKER_ADAPT", raising=False)
    else:
        monkeypatch.setenv("ZOE_SPEAKER_ADAPT", raw)
    assert spk.adapt_mode() == expected


@pytest.mark.asyncio
async def test_off_mode_touches_nothing(world):
    before = world.row()
    out = await world.offer(mode="off")
    assert out == {"ok": True, "mode": "off", "applied": False, "reason": "off"}
    assert world.db.queries == []                      # not even a SELECT
    assert world.row() == before


@pytest.mark.asyncio
async def test_shadow_writes_nothing_but_logs(world, caplog):
    before = world.row()
    with caplog.at_level(logging.INFO, logger="zoe.speaker_adapt"):
        out = await world.offer(mode="shadow")
    assert out["applied"] is False and out["would_apply"] is True and out["reason"] == "shadow"
    assert world.row() == before                       # nothing written
    assert not [q for q in world.db.queries if q.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))]
    lines = [r.getMessage() for r in caplog.records if r.name == "zoe.speaker_adapt"]
    assert len(lines) == 1 and "would_apply" in lines[0] and "user=jason" in lines[0]


@pytest.mark.asyncio
async def test_on_updates_only_the_adapted_profile_and_audits(world, caplog):
    anchor_before = world.row()[0]
    with caplog.at_level(logging.INFO, logger="zoe.speaker_adapt"):
        out = await world.offer(mode="on")
    assert out["applied"] is True and out["reason"] == "applied"
    anchor, adapted, from_count, count, at, day, day_count = world.row()
    assert anchor == anchor_before                      # the anchor is NEVER touched
    assert adapted is not None and adapted != anchor
    assert (from_count, count, day, day_count) == (3, 1, "2026-10-12", 1) and at.startswith("2026-10-12T10:00")
    new = np.frombuffer(adapted, dtype=np.float32)
    assert abs(float(np.linalg.norm(new)) - 1.0) < 1e-4 and spk.cosine(new, ANCHOR) > CFG.min_anchor_cos
    msgs = [r.getMessage() for r in caplog.records if r.name == "zoe.speaker_adapt"]
    assert len(msgs) == 1
    for field in ("user=jason", "applied", "score=", "margin=", "delta_norm=", "drift_cos=", "count=1"):
        assert field in msgs[0], field
    assert _b64(at_cos(0.90))[:24] not in msgs[0]       # no embedding values in the audit line


@pytest.mark.asyncio
async def test_rate_limits_hold_against_the_database_state(world):
    assert (await world.offer(now=NOW))["applied"] is True
    out = await world.offer(now=NOW + timedelta(seconds=30))
    assert out["reason"] == "rate_interval" and out["applied"] is False
    for i in range(1, 5):                                # 4 more, spaced past the interval: daily total 5
        assert (await world.offer(now=NOW + timedelta(minutes=11 * i)))["applied"] is True
    out = await world.offer(now=NOW + timedelta(minutes=11 * 5))
    assert out["reason"] == "rate_daily"
    assert world.row()[3] == 5
    assert (await world.offer(now=NOW + timedelta(days=1)))["applied"] is True   # next UTC day


@pytest.mark.asyncio
async def test_a_poisoned_stream_cannot_cross_the_drift_bound_in_the_db(world):
    cfg = AdaptConfig(min_score=-2, anchor_floor=-2, min_margin=-2, min_speech_s=0, max_per_day=10**9,
                      min_interval_s=0)
    outs = [await world.offer(at_angle(90.0), now=NOW + timedelta(days=i), cfg=cfg, voice_score=1.0)
            for i in range(400)]
    assert any(o["reason"] == "drift_bound" for o in outs)
    adapted = np.frombuffer(world.row()[1], dtype=np.float32)
    assert spk.cosine(adapted, ANCHOR) >= cfg.min_anchor_cos - 1e-6


# ── corroboration is verified by the SERVER ─────────────────────────────

@pytest.mark.asyncio
async def test_no_corroboration_without_binding_session_or_face(world):
    world.unbind()
    out = await world.offer()
    assert out["reason"] == "no_corroboration" and out["applied"] is False
    assert world.row()[1] is None


@pytest.mark.asyncio
async def test_panel_bound_to_a_different_user_is_not_corroboration(world):
    world.add_profile("kiddo", at_angle(120.0))
    world.unbind()
    world.bind("panel1", "kiddo")
    assert (await world.offer())["reason"] == "no_corroboration"


@pytest.mark.asyncio
async def test_fresh_login_session_corroborates_and_stale_does_not(world):
    world.unbind()
    ins = "INSERT OR REPLACE INTO ui_panel_sessions (panel_id, user_id, last_seen_at) VALUES ('panel1', 'jason', ?)"
    world.db.conn.execute(ins, ((NOW - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),))
    out = await world.offer()
    assert out["applied"] is True and out["corroboration"] == ["pin_recent"]
    world.db.conn.execute(ins, ((NOW - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"),))
    out = await world.offer(now=NOW + timedelta(hours=1))
    assert out["reason"] == "no_corroboration"


@pytest.mark.asyncio
async def test_face_match_corroborates_only_for_the_same_user_above_threshold(world):
    world.unbind()
    ok = await world.offer(corroboration={"face_user_id": "jason", "face_score": 0.8})
    assert ok["applied"] is True and ok["corroboration"] == ["face"]
    for claim in ({"face_user_id": "kiddo", "face_score": 0.9}, {"face_user_id": "jason", "face_score": 0.3},
                  {"face_user_id": "jason"}, {"face_user_id": "jason", "face_score": "x"}):
        out = await world.offer(now=NOW + timedelta(days=2), corroboration=claim)
        assert out["reason"] == "no_corroboration", claim


@pytest.mark.asyncio
async def test_the_panel_in_the_payload_is_ignored_only_the_callers_panel_counts(world):
    world.add_profile("kiddo", at_angle(120.0))
    world.bind("panel2", "kiddo")
    out = await world.offer(panel="panel2", panel_id="panel1")          # claims panel1's binding from panel2
    assert out["reason"] == "no_corroboration"


# ── other members ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_close_to_another_enrolled_member_is_refused(world):
    world.add_profile("kiddo", at_angle(float(np.degrees(np.arccos(0.92))) - 31.8))
    out = await world.offer(at_cos(0.92), voice_score=0.92)
    assert out["reason"] == "close_to_other" and out["applied"] is False and out["margin"] < CFG.min_margin


@pytest.mark.asyncio
async def test_a_far_member_does_not_block_and_an_unconsented_one_is_ignored(world):
    world.add_profile("kiddo", at_angle(150.0))
    world.add_profile("ghost", at_angle(float(np.degrees(np.arccos(0.92))) - 31.8), consent=False)
    out = await world.offer(at_cos(0.92), voice_score=0.92)
    assert out["applied"] is True and out["margin"] > CFG.min_margin


# ── consent, missing rows, storage ───────────────────────────────────────

@pytest.mark.asyncio
async def test_no_profile_no_consent_no_storage(world):
    assert (await world.offer(voice_user_id="stranger"))["reason"] == "no_profile"
    world.db.conn.execute("UPDATE speaker_profiles SET consent_at = NULL")
    assert (await world.offer())["reason"] == "no_consent"
    world.db.conn.execute("DROP TABLE speaker_profiles")
    assert (await world.offer())["reason"] == "storage_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"voice_user_id": ""}, {"embedding_base64": "not base64!!"}, {"embedding_base64": ""},
    {"embedding_base64": base64.b64encode(b"\x00\x01\x02").decode()},
])
async def test_garbage_payloads_are_refused_not_raised(world, payload):
    out = await world.offer(**payload)
    assert out["applied"] is False and out["reason"] in {"no_user", "invalid_embedding"}


# ── re-enrolment invalidates, forget removes ─────────────────────────────

@pytest.mark.asyncio
async def test_reenrolment_invalidates_the_adapted_profile(world):
    assert (await world.offer())["applied"] is True
    anchor, adapted, *_ = world.row()
    assert spk.serve_blob(anchor, adapted, world.row()[2], 3) == adapted
    new_anchor = at_angle(10.0).tobytes()                      # what /enroll does: new averaged anchor, count + 1
    world.db.conn.execute("UPDATE speaker_profiles SET embedding_blob = ?, sample_count = 4 WHERE user_id='jason'",
                          (new_anchor,))
    anchor, adapted, from_count, *_ = world.row()
    assert spk.serve_blob(anchor, adapted, from_count, 4) == new_anchor      # stale adapted is NOT served
    out = await world.offer(at_angle(10.0 + 25.8), now=NOW + timedelta(hours=1))    # cos .90 to the NEW anchor
    assert out["applied"] is True
    anchor, adapted, from_count, count, *_ = world.row()
    assert from_count == 4 and count == 2
    assert spk.cosine(np.frombuffer(adapted, dtype=np.float32), np.frombuffer(new_anchor, dtype=np.float32)) > 0.99


@pytest.mark.asyncio
async def test_concurrent_update_loses_the_race_instead_of_double_counting(world):
    world.db.before_update = lambda conn: conn.execute(
        "UPDATE speaker_profiles SET adapt_count = 7 WHERE user_id = 'jason'")
    out = await world.offer()
    assert out["reason"] == "conflict" and out["applied"] is False
    assert world.row()[1] is None and world.row()[3] == 7


@pytest.mark.asyncio
async def test_reset_drops_only_the_adapted_profile(world):
    await world.offer()
    anchor_before = world.row()[0]
    assert await spk.reset_adaptation(world.db, "jason") is True
    anchor, adapted, from_count, count, at, day, day_count = world.row()
    assert anchor == anchor_before and adapted is None and from_count is None and count == 0 and at is None
    assert day == "2026-10-12" and day_count == 1             # the daily cap survives a reset
    assert await spk.reset_adaptation(world.db, "jason") is False


@contextlib.contextmanager
def _compat_db(monkeypatch, db):
    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    mod = types.ModuleType("db_compat")
    mod.get_compat_db = ctx
    monkeypatch.setitem(sys.modules, "db_compat", mod)
    yield


@pytest.mark.asyncio
async def test_forget_my_voice_removes_anchor_and_adapted_profile(world, monkeypatch):
    from routers import voice_tts

    await world.offer()
    assert world.row()[1] is not None
    with _compat_db(monkeypatch, world.db):
        out = await voice_tts.voice_profile_delete(
            "pid-jason", caller={"source": "session", "user_id": "jason", "role": "member"})
    assert out["ok"] is True
    assert world.db.scalar("SELECT COUNT(*) FROM speaker_profiles WHERE user_id = 'jason'") == 0
    # the adapted profile cannot outlive the row: it is a column of it, not a side table
    tables = {r[0] for r in world.db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not [t for t in tables if "adapt" in t]


# ── serving: sync and identify ───────────────────────────────────────────

async def _sync(world, monkeypatch):
    from routers import voice_tts
    with _compat_db(monkeypatch, world.db):
        return await voice_tts.voice_profiles_sync(models=speaker_models.RESNET34_MODEL_ID,
                                                   caller={"source": "device", "panel_id": "panel1", "via_relay": False, "via_tunnel": False})


@pytest.mark.asyncio
async def test_flag_off_and_shadow_sync_are_byte_identical_to_an_unadapted_profile(world, monkeypatch, tmp_path):
    baseline = json.dumps(await _sync(world, monkeypatch), sort_keys=True)
    await world.offer(mode="on")                                           # the DB now holds an adapted profile
    assert world.row()[1] is not None
    for mode in ("off", "shadow", "", "garbage"):
        monkeypatch.setenv("ZOE_SPEAKER_ADAPT", mode)
        assert json.dumps(await _sync(world, monkeypatch), sort_keys=True) == baseline, mode
    monkeypatch.setenv("ZOE_SPEAKER_ADAPT", "on")
    served = await _sync(world, monkeypatch)
    assert json.dumps(served, sort_keys=True) != baseline
    assert base64.b64decode(served["profiles"][0]["embedding_base64"]) == world.row()[1]


@pytest.mark.asyncio
async def test_sync_in_on_mode_serves_the_anchor_for_a_stale_adapted_profile(world, monkeypatch):
    await world.offer()
    world.db.conn.execute("UPDATE speaker_profiles SET sample_count = 4 WHERE user_id = 'jason'")
    monkeypatch.setenv("ZOE_SPEAKER_ADAPT", "on")
    served = await _sync(world, monkeypatch)
    assert base64.b64decode(served["profiles"][0]["embedding_base64"]) == world.row()[0]


@pytest.mark.asyncio
async def test_identify_matches_against_the_adapted_profile_only_in_on_mode(world, monkeypatch):
    from routers import voice_tts
    await world.offer()
    adapted = np.frombuffer(world.row()[1], dtype=np.float32)
    probe = _b64(adapted)
    results = {}
    for mode in ("off", "on"):
        monkeypatch.setenv("ZOE_SPEAKER_ADAPT", mode)
        with _compat_db(monkeypatch, world.db):
            results[mode] = await voice_tts.voice_identify(
                {"embedding_base64": probe, "model_id": speaker_models.RESNET34_MODEL_ID, "speech_s": 3.0},
                caller={"source": "device", "panel_id": "panel1"})
    off_score = results["off"].get("confidence", results["off"].get("best_confidence"))
    assert results["on"]["confidence"] == pytest.approx(1.0, abs=1e-3)       # the adapted profile matches itself
    assert off_score < results["on"]["confidence"]                           # the anchor-only feed does not


@pytest.mark.asyncio
async def test_overlay_failure_serves_anchors(world, monkeypatch):
    world.db.conn.execute("ALTER TABLE speaker_profiles DROP COLUMN adapted_blob")
    rows = [("jason", "Jason", ANCHOR.tobytes(), 3)]
    assert await spk.overlay_adapted(world.db, rows, user_idx=0, blob_idx=2) == rows


# ── routes ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adapt_route_is_device_only_and_off_is_a_noop(monkeypatch):
    from fastapi import HTTPException
    from routers import voice_speaker_adapt as route

    monkeypatch.delenv("ZOE_SPEAKER_ADAPT", raising=False)

    @contextlib.asynccontextmanager
    async def boom():
        raise AssertionError("flag off must not open a DB connection")
        yield
    mod = types.ModuleType("db_compat")
    mod.get_compat_db = boom
    monkeypatch.setitem(sys.modules, "db_compat", mod)

    with pytest.raises(HTTPException) as exc:
        await route.voice_adapt({}, caller={"source": "session", "user_id": "jason", "role": "admin"})
    assert exc.value.status_code == 403
    out = await route.voice_adapt({"voice_user_id": "jason"}, caller={"source": "device", "panel_id": "p"})
    assert out == {"ok": True, "mode": "off", "applied": False, "reason": "off"}


@pytest.mark.asyncio
async def test_adapt_route_end_to_end_uses_the_callers_panel(world, monkeypatch):
    from routers import voice_speaker_adapt as route

    monkeypatch.setenv("ZOE_SPEAKER_ADAPT", "on")
    body = {"voice_user_id": "jason", "embedding_base64": _b64(at_cos(0.9)), "voice_score": 0.9, "speech_s": 3.0,
            "model_id": speaker_models.RESNET34_MODEL_ID}
    with _compat_db(monkeypatch, world.db):
        out = await route.voice_adapt(dict(body), caller={"source": "device", "panel_id": "panel1"})
    assert out["applied"] is True
    world.db.conn.execute("UPDATE speaker_profiles SET adapted_blob = NULL, adapt_count = 0, adapted_at = NULL, "
                          "adapt_day_count = 0")
    with _compat_db(monkeypatch, world.db):                        # same payload from an UNBOUND panel
        out = await route.voice_adapt(dict(body), caller={"source": "device", "panel_id": "other-panel"})
    assert out["reason"] == "no_corroboration"


@pytest.mark.asyncio
async def test_reset_route_is_own_profile_only(world, monkeypatch):
    from fastapi import HTTPException
    from routers import voice_speaker_adapt as route

    await world.offer()
    me = {"source": "session", "user_id": "jason", "role": "member"}
    other = {"source": "session", "user_id": "kiddo", "role": "member"}
    with _compat_db(monkeypatch, world.db):
        with pytest.raises(HTTPException) as exc:
            await route.voice_adaptation_reset(user_id="jason", caller=other)
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException):
            await route.voice_adaptation_reset(user_id=None, caller={"source": "device", "panel_id": "p"})
        assert world.row()[1] is not None
        out = await route.voice_adaptation_reset(user_id=None, caller=me)
    assert out["reset"] is True and world.row()[1] is None


def test_router_is_registered_in_main():
    src = (SVC / "main.py").read_text()
    assert "voice_speaker_adapt_router" in src and "app.include_router(voice_speaker_adapt_router)" in src
