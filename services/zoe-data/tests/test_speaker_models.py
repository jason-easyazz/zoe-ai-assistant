"""Speaker model identity and the ResNet34 switch, SERVER side (``speaker_models.py``, the model-aware parts of
``routers/voice_tts.py``, ``speaker_adaptation.py``, ``enrollment_state.py``, ``voice_enrollment.py``, migration 0051).

The rules pinned here, each with a negative control (the same call with the one guard flipped behaves differently, so a green
test is the GUARD working, not an easy fixture):

* **profiles of different model_ids never compare** - sync, identify, claim acceptance, adaptation and the adapted-profile overlay;
* a claim is accepted only in the model the server ACTS on, against that model's own length-group threshold;
* the finished-profile enrolment is device-only, validated, replaces only its own model's row, and resets that row's adaptation;
* the #2002 guards are per model: the ANCHOR gate keeps a lookalike at 0 updates where a score gate alone admits it;
* the Pi module and the server mirror the same numbers (drift), and the pins in the shell script, the tool and the artifacts JSON agree.

No model, no audio, no network, no live DB: a SQLite table shaped like the repo's migrations on a tmp path.
"""
from __future__ import annotations

import base64
import contextlib
import dataclasses
import importlib.util
import json
import re
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

import speaker_adaptation as spk
import speaker_models as sm
from speaker_adaptation import AdaptConfig, AdaptState, evaluate
from test_speaker_adaptation import ANCHOR, DIM, NOW, SqliteCompat, World, at_angle, at_cos

SVC = Path(__file__).resolve().parents[1]
REPO = SVC.parents[1]
R34 = sm.RESNET34_MODEL_ID
DEVICE = dict(source="device", panel_id="panel1", via_relay=False, via_tunnel=False)


def _b64(v) -> str:
    return base64.b64encode(np.asarray(v, dtype=np.float32).tobytes()).decode()


@contextlib.contextmanager
def compat_db(monkeypatch, db):
    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    mod = types.ModuleType("db_compat")
    mod.get_compat_db = ctx
    monkeypatch.setitem(sys.modules, "db_compat", mod)
    yield


def add_r34(world: World, uid: str, vec, *, sample_count: int = 5, consent: bool = True) -> None:
    world.db.conn.execute(
        "INSERT INTO speaker_profiles (id, user_id, display_name, embedding_blob, sample_count, consent_at, model_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("pid-r34-" + uid, uid, uid.title(), np.asarray(vec, dtype=np.float32).tobytes(), sample_count,
         "2026-01-01 00:00:00" if consent else None, R34))
    world.db.conn.commit()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# -- identity ---------------------------------------------------------------------------------------------------------

def test_model_id_names_the_model_the_quantisation_and_the_centring_version():
    assert R34 == "wespeaker_resnet34+i8qdq-u8s8+c1"
    family, quant, centre = R34.split("+")
    assert (family, quant, centre) == ("wespeaker_resnet34", "i8qdq-u8s8", "c1")
    assert sm.LEGACY_MODEL_ID == "resemblyzer" and sm.is_known(R34) and not sm.is_known("wespeaker_resnet34+fp32+c1")


def test_null_empty_and_garbage_are_the_legacy_model_and_two_ids_are_never_the_same_space():
    for raw in (None, "", "   ", 7, b"x"):
        assert sm.normalize_model_id(raw) == sm.LEGACY_MODEL_ID
    assert sm.same_space(None, "resemblyzer") and not sm.same_space(R34, "resemblyzer") and not sm.same_space(R34, None)
    # an INT8 id and a hypothetical FP32 / other-centring id do not match either
    assert not sm.same_space(R34, "wespeaker_resnet34+i8qdq-u8s8+c2")


def test_mode_defaults_to_the_legacy_model_and_every_unknown_value_is_the_legacy_model(monkeypatch):
    monkeypatch.delenv("ZOE_SPEAKER_MODEL", raising=False)
    assert sm.speaker_mode() == "resemblyzer" and sm.acting_model() == sm.LEGACY_MODEL_ID
    for raw, mode in (("", "resemblyzer"), ("RESNET34 ", "resnet34"), ("turbo", "resemblyzer"), ("1", "resemblyzer"),
                      ("dual", "dual"), ("Dual", "dual")):
        monkeypatch.setenv("ZOE_SPEAKER_MODEL", raw)
        assert sm.speaker_mode() == mode, raw


@pytest.mark.parametrize("mode, acting, enrols, visible, clips", [
    ("resemblyzer", sm.LEGACY_MODEL_ID, (sm.LEGACY_MODEL_ID,), {sm.LEGACY_MODEL_ID}, 3),
    ("dual", sm.LEGACY_MODEL_ID, (sm.LEGACY_MODEL_ID, R34), {sm.LEGACY_MODEL_ID, R34}, 5),
    ("resnet34", R34, (R34,), {R34}, 5),
])
def test_what_each_mode_acts_on_enrols_serves_and_asks_for(mode, acting, enrols, visible, clips):
    assert sm.acting_model(mode) == acting and sm.enrolled_models(mode) == enrols
    assert set(sm.visible_models(mode)) == visible and sm.clips_needed(mode) == clips


# -- thresholds by length group ---------------------------------------------------------------------------------------

def test_length_groups_split_at_two_seconds_and_an_unknown_length_takes_the_stricter_group():
    assert sm.length_group(0.5) == "short" and sm.length_group(2.0) == "short"
    assert sm.length_group(2.01) == "long" and sm.length_group(9) == "long"
    for bad in (None, "x", float("nan"), float("inf"), 0, -1):
        assert sm.length_group(bad) == "long", bad


def test_resnet34_thresholds_are_the_records_and_differ_by_group(monkeypatch):
    for k in ("TIER", "THRESHOLD_SHORT", "THRESHOLD_LONG"):
        monkeypatch.delenv("ZOE_SPEAKER_RESNET34_" + k, raising=False)
    assert (sm.resnet34_threshold("short"), sm.resnet34_threshold("long")) == (0.233, 0.265)
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_TIER", "far01")
    assert (sm.resnet34_threshold("short"), sm.resnet34_threshold("long")) == (0.309, 0.351)
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "0.30")
    assert sm.resnet34_threshold("long") == 0.30 and sm.resnet34_threshold("short") == 0.309
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "nan")             # a typo can only fall back, never loosen
    assert sm.resnet34_threshold("long") == 0.351
    monkeypatch.setenv("ZOE_SPEAKER_RESNET34_TIER", "yolo")
    assert sm.resnet34_tier() == "far1"


def test_a_score_between_the_two_thresholds_passes_short_and_fails_long_and_one_global_bar_cannot_do_both(monkeypatch):
    monkeypatch.delenv("ZOE_SPEAKER_RESNET34_TIER", raising=False)
    score = 0.25                                           # 0.233 <= 0.25 < 0.265
    assert score >= sm.claim_threshold(R34, 1.5, 0.82)
    assert score < sm.claim_threshold(R34, 3.0, 0.82)
    # negative control: the legacy single global bar (0.82) rejects both, and a global 0.253 would flip one of them wrongly
    assert score < sm.claim_threshold(sm.LEGACY_MODEL_ID, 1.5, 0.82) and score < sm.claim_threshold(sm.LEGACY_MODEL_ID, 3.0, 0.82)
    assert sm.claim_threshold("not-a-model", 3.0, 0.82) is None


# -- a claim is honoured only in the model the server acts on ----------------------------------------------------------

def claim(model=None, score=0.9, speech_s=3.0, member="jason"):
    block = dict(verified=None, member=member, score=score)
    if model:
        block.update(model_id=model, speech_s=speech_s)
    return dict(voice_user_id=member, voice_score=score, speaker=block)


@pytest.mark.parametrize("mode, payload, accepted", [
    ("resemblyzer", claim(None, 0.9), True),                 # today's panel, today's gate
    ("resemblyzer", claim(R34, 0.9), False),                 # a ResNet34 claim before the flip: refused whatever its score
    ("dual", claim(None, 0.9), True),
    ("dual", claim(R34, 0.9), False),                        # dual acts on Resemblyzer ONLY
    ("resnet34", claim(R34, 0.30, 3.0), True),
    ("resnet34", claim(R34, 0.25, 1.5), True),               # short clip: 0.233
    ("resnet34", claim(R34, 0.25, 3.0), False),              # the same score on a longer clip: 0.265
    ("resnet34", claim(None, 0.99), False),                  # a Resemblyzer claim after the flip: refused
    ("resnet34", claim("wespeaker_resnet34+i8qdq-u8s8+c2", 0.9), False),     # unknown model
])
def test_claim_acceptance_matrix(monkeypatch, mode, payload, accepted):
    from routers import voice_tts
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", mode)
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.82")
    monkeypatch.delenv("ZOE_SPEAKER_RESNET34_TIER", raising=False)
    assert (voice_tts._accept_panel_voice_claim(payload, DEVICE) == "jason") is accepted


def test_a_browser_session_still_cannot_claim_a_speaker_in_any_model(monkeypatch):
    from routers import voice_tts
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    assert voice_tts._accept_panel_voice_claim(claim(R34, 0.99), dict(source="session", user_id="admin")) is None


@pytest.mark.asyncio
async def test_claim_consent_is_per_model(world, monkeypatch):
    from routers import voice_tts
    add_r34(world, "jason", at_cos(0.9))
    world.db.conn.execute("UPDATE speaker_profiles SET consent_at = NULL WHERE model_id IS NULL")     # legacy consent revoked
    world.db.conn.commit()
    with compat_db(monkeypatch, world.db):
        assert await voice_tts._voice_claim_consented("jason", R34) is True
        assert await voice_tts._voice_claim_consented("jason", sm.LEGACY_MODEL_ID) is False
        assert await voice_tts._voice_claim_consented("jason") is True             # no model asked: any consented row (old callers)


# -- the sync feed ----------------------------------------------------------------------------------------------------

async def sync(monkeypatch, world, mode, models=None):
    from routers import voice_tts
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", mode)
    monkeypatch.delenv("ZOE_SPEAKER_ADAPT", raising=False)
    with compat_db(monkeypatch, world.db):
        return await voice_tts.voice_profiles_sync(models=models, caller=DEVICE)


@pytest.fixture
def two_models(world):
    add_r34(world, "jason", at_angle(30.0))
    return world


@pytest.mark.parametrize("mode, asked, served", [
    ("resemblyzer", None, {sm.LEGACY_MODEL_ID}),
    ("resemblyzer", R34, set()),                              # flag at its default: a panel that asks for ResNet34 gets nothing
    ("dual", None, {sm.LEGACY_MODEL_ID}),                     # an older panel (no ?models=) is never handed a ResNet34 vector
    ("dual", sm.LEGACY_MODEL_ID + "," + R34, {sm.LEGACY_MODEL_ID, R34}),
    ("dual", R34, {R34}),
    ("resnet34", sm.LEGACY_MODEL_ID + "," + R34, {R34}),
    ("resnet34", None, set()),                                # resnet34 mode, panel names nothing: legacy-only panel gets no ResNet34 vector
])
@pytest.mark.asyncio
async def test_sync_serves_each_panel_only_the_models_it_asked_for_and_the_mode_exposes(monkeypatch, two_models, mode, asked, served):
    out = await sync(monkeypatch, two_models, mode, asked)
    assert set(p["model_id"] for p in out["profiles"]) == served
    assert set(out["models"]) == served and out["mode"] == mode
    if R34 in served:
        assert out["models"][R34]["thresholds"]["short"] == 0.233 and out["models"][R34]["dim"] == 256


@pytest.mark.asyncio
async def test_sync_in_the_default_mode_is_unchanged_for_legacy_rows(monkeypatch, world):
    out = await sync(monkeypatch, world, "resemblyzer", None)
    assert [p["user_id"] for p in out["profiles"]] == ["jason"]
    assert out["profiles"][0]["model_id"] == sm.LEGACY_MODEL_ID and out["threshold"] == pytest.approx(0.82)


# -- identify never crosses models ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_identify_compares_only_profiles_of_the_queried_model(monkeypatch, world):
    from routers import voice_tts
    vec = at_cos(0.95)
    add_r34(world, "ada", vec)                                  # a ResNet34 profile that is IDENTICAL to what Jason's legacy row holds
    world.db.conn.execute("UPDATE speaker_profiles SET embedding_blob = ? WHERE user_id = 'jason'",
                          (np.asarray(vec, dtype=np.float32).tobytes(),))
    world.db.conn.commit()
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "dual")
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.5")
    with compat_db(monkeypatch, world.db):
        legacy = await voice_tts.voice_identify(dict(embedding_base64=_b64(vec)), caller=DEVICE)
        r34 = await voice_tts.voice_identify(dict(embedding_base64=_b64(vec), model_id=R34, speech_s=3.0), caller=DEVICE)
    assert legacy["identified"] and legacy["user_id"] == "jason"           # not 'ada', whose ResNet34 row is the same vector
    assert r34["identified"] and r34["user_id"] == "ada" and r34["model_id"] == R34
    # negative control: remove the legacy row and the legacy query finds NOBODY instead of falling through to the ResNet34 row
    world.db.conn.execute("DELETE FROM speaker_profiles WHERE model_id IS NULL")
    world.db.conn.commit()
    with compat_db(monkeypatch, world.db):
        again = await voice_tts.voice_identify(dict(embedding_base64=_b64(vec)), caller=DEVICE)
    assert again["identified"] is False and again["reason"] == "no_profiles"


@pytest.mark.asyncio
async def test_identify_in_the_default_mode_refuses_a_resnet34_query(monkeypatch, world):
    from routers import voice_tts
    add_r34(world, "jason", at_cos(0.9))
    monkeypatch.delenv("ZOE_SPEAKER_MODEL", raising=False)
    with compat_db(monkeypatch, world.db):
        out = await voice_tts.voice_identify(dict(embedding_base64=_b64(at_cos(0.9)), model_id=R34), caller=DEVICE)
    assert out["identified"] is False and out["reason"] == "model_not_served"


# -- the finished-profile enrolment -----------------------------------------------------------------------------------

def rows(world, uid="jason"):
    return world.db.conn.execute(
        "SELECT id, COALESCE(model_id, 'resemblyzer'), sample_count, consent_at, adapted_blob, adapt_count "
        "FROM speaker_profiles WHERE user_id = ? ORDER BY 2", (uid,)).fetchall()


async def enrol(monkeypatch, world, payload, caller=DEVICE):
    from routers import voice_tts
    body = dict(user_id="jason", display_name="Jason", panel_id="panel1", consent=True)
    body.update(payload)
    with compat_db(monkeypatch, world.db):
        return await voice_tts.voice_enroll(body, caller=caller)


@pytest.mark.asyncio
async def test_a_panel_built_profile_is_stored_as_its_own_row_and_leaves_the_legacy_row_alone(monkeypatch, world):
    legacy_before = rows(world)[0]
    out = await enrol(monkeypatch, world, dict(embedding_base64=_b64(at_cos(0.8)), model_id=R34, sample_count=5))
    assert out["ok"] and out["model_id"] == R34
    r = rows(world)
    assert sorted(x[1] for x in r) == sorted([R34, sm.LEGACY_MODEL_ID])
    new = [x for x in r if x[1] == R34][0]
    assert new[2] == 5 and new[3] is not None                     # 5 clips, consent stamped (the person performed it)
    assert [x for x in r if x[1] == sm.LEGACY_MODEL_ID][0] == legacy_before


@pytest.mark.asyncio
async def test_re_enrolling_replaces_that_models_profile_and_drops_its_adaptation(monkeypatch, world):
    await enrol(monkeypatch, world, dict(embedding_base64=_b64(at_cos(0.8)), model_id=R34, sample_count=5))
    world.db.conn.execute("UPDATE speaker_profiles SET adapted_blob = ?, adapted_from_count = 5, adapt_count = 3 WHERE model_id = ?",
                          (at_cos(0.7).tobytes(), R34))
    world.db.conn.commit()
    second = at_angle(70.0)
    await enrol(monkeypatch, world, dict(embedding_base64=_b64(second), model_id=R34, sample_count=5))     # SAME count: the old guard would not notice
    mine = [x for x in rows(world) if x[1] == R34]
    assert len(mine) == 1 and mine[0][4] is None and mine[0][5] == 0       # one row, adapted profile gone
    got = np.frombuffer(world.db.conn.execute("SELECT embedding_blob FROM speaker_profiles WHERE model_id = ?", (R34,)).fetchone()[0],
                        dtype=np.float32)
    assert float(got @ second) > 0.999                                      # REPLACED, not blended with the old session


@pytest.mark.asyncio
async def test_the_finished_profile_shape_is_device_only_and_validated(monkeypatch, world):
    from fastapi import HTTPException
    good = dict(embedding_base64=_b64(at_cos(0.8)), model_id=R34)
    with pytest.raises(HTTPException) as e:
        await enrol(monkeypatch, world, good, caller=dict(source="session", user_id="jason", role="admin"))
    assert e.value.status_code == 403
    for bad in (_b64(at_cos(0.8)[:128]), _b64(at_cos(0.8) * 3.0), _b64(np.full(DIM, np.nan, dtype=np.float32)), "!!notbase64"):
        with pytest.raises(HTTPException) as e:
            await enrol(monkeypatch, world, dict(embedding_base64=bad, model_id=R34))
        assert e.value.status_code == 400, bad
    with pytest.raises(HTTPException) as e:
        await enrol(monkeypatch, world, dict(embedding_base64=_b64(at_cos(0.8)), model_id="wespeaker_resnet34+fp32+c1"))
    assert e.value.status_code == 400
    assert len(rows(world)) == 1                                           # nothing but the original legacy row was written
    out = await enrol(monkeypatch, world, good)                            # negative control: the same call, well formed, device: stored
    assert out["ok"] and len(rows(world)) == 2


@pytest.mark.asyncio
async def test_the_server_never_embeds_audio_for_resnet34(monkeypatch, world):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        await enrol(monkeypatch, world, dict(audio_base64=base64.b64encode(b"RIFF....").decode(), model_id=R34))
    assert e.value.status_code == 400 and "panel" in e.value.detail


# -- #2002 adaptation, per model --------------------------------------------------------------------------------------

R34_CFG = AdaptConfig.from_env(R34)
SCORE_ONLY = dataclasses.replace(R34_CFG, anchor_floor=R34_CFG.min_score)     # the score gate alone: what #2002 had, at ResNet34's scale


def run34(**kw):
    args = dict(embedding=at_cos(0.60), anchor=ANCHOR, adapted=None, others=[], claimed_score=None, speech_s=3.0,
                corroboration=["panel_bound"], state=AdaptState(), now=NOW, cfg=R34_CFG)
    args.update(kw)
    return evaluate(**args)


def test_resnet34_guards_are_the_records_and_the_resemblyzer_ones_never_fire_on_its_scale(monkeypatch):
    for k in ("MIN_SCORE", "ANCHOR_FLOOR"):
        monkeypatch.delenv("ZOE_SPEAKER_ADAPT_" + k, raising=False)
        monkeypatch.delenv("ZOE_SPEAKER_ADAPT_RESNET34_" + k, raising=False)
    cfg = AdaptConfig.from_env(R34)
    assert (cfg.min_score, cfg.anchor_floor, cfg.min_anchor_cos) == (0.342, 0.45, 0.92)
    legacy = AdaptConfig.from_env(sm.LEGACY_MODEL_ID)
    assert (legacy.min_score, legacy.anchor_floor) == (0.85, 0.80)
    owner_turn = at_cos(0.60)                                  # a normal owner clip on ResNet34's centred scale
    assert run34(embedding=owner_turn).allowed                # fires with the per-model guards ...
    assert run34(embedding=owner_turn, cfg=legacy).reason == "low_score"     # ... and never does with the Resemblyzer constants
    monkeypatch.setenv("ZOE_SPEAKER_ADAPT_RESNET34_ANCHOR_FLOOR", "0.5")
    assert AdaptConfig.from_env(R34).anchor_floor == 0.5 and AdaptConfig.from_env().anchor_floor == 0.80


def test_the_anchor_gate_blocks_a_lookalike_that_clears_the_score_gate():
    lookalike = at_cos(0.40)                                   # >= 0.342 (score gate) but < 0.45 (anchor gate)
    d = run34(embedding=lookalike)
    assert d.allowed is False and d.reason == "off_anchor" and d.score >= R34_CFG.min_score
    assert run34(embedding=lookalike, cfg=SCORE_ONLY).allowed is True      # negative control: the score gate ALONE admits the same clip


def _lookalike_stream(cfg, n: int = 60) -> int:
    """How many of ``n`` daily lookalike clips (cosine 0.34 - 0.42 to the anchor, random direction) the guards let in."""
    rng = np.random.default_rng(5)
    adapted, state, applied = None, AdaptState(), 0
    for i in range(n):
        c = 0.34 + 0.08 * rng.random()
        ortho = rng.standard_normal(DIM).astype(np.float32)
        ortho -= (ortho @ ANCHOR) * ANCHOR
        ortho /= np.linalg.norm(ortho)
        v = (c * ANCHOR + np.sqrt(1 - c * c) * ortho).astype(np.float32)
        d = evaluate(embedding=v, anchor=ANCHOR, adapted=adapted, others=[], claimed_score=None, speech_s=3.0,
                     corroboration=["panel_bound"], state=state, now=NOW + timedelta(days=i), cfg=cfg)
        if d.allowed:
            applied += 1
            adapted = d.new_vec
            state = AdaptState(adapt_count=applied, adapted_at=NOW + timedelta(days=i))
    return applied


def test_a_lookalike_offered_all_week_gets_zero_updates_with_the_anchor_gate_and_drags_the_profile_without_it():
    assert _lookalike_stream(R34_CFG) == 0                     # the gate: not one update
    assert _lookalike_stream(SCORE_ONLY) > 0                   # negative control: without the anchor gate the lookalike gets in


@pytest.mark.asyncio
async def test_adaptation_touches_only_the_acting_models_row(monkeypatch, world):
    add_r34(world, "jason", ANCHOR)
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    monkeypatch.delenv("ZOE_SPEAKER_ADAPT_RESNET34_MIN_SCORE", raising=False)
    offer = dict(voice_user_id="jason", embedding_base64=_b64(at_cos(0.80)), voice_score=0.80, speech_s=3.0, model_id=R34)
    out = await spk.process_adaptation(world.db, panel_id="panel1", payload=offer, now=NOW, mode="on")
    assert out["applied"] and out["model_id"] == R34
    by_model = dict((x[1], x) for x in rows(world))
    assert by_model[R34][4] is not None and by_model[R34][5] == 1       # ResNet34 row adapted ...
    assert by_model[sm.LEGACY_MODEL_ID][4] is None                      # ... the Resemblyzer row untouched
    # a Resemblyzer-space offer in resnet34 mode is refused (and so is a ResNet34 one while dual acts on Resemblyzer)
    out = await spk.process_adaptation(world.db, panel_id="panel1", payload=dict((k, v) for k, v in offer.items() if k != "model_id"), now=NOW + timedelta(hours=2), mode="on")
    assert out["reason"] == "not_acting_model"
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "dual")
    out = await spk.process_adaptation(world.db, panel_id="panel1", payload=offer, now=NOW + timedelta(hours=3), mode="on")
    assert out["reason"] == "not_acting_model"
    out = await spk.process_adaptation(world.db, panel_id="panel1", payload=dict(offer, model_id="nope"), now=NOW, mode="on")
    assert out["reason"] == "unknown_model"


@pytest.mark.asyncio
async def test_other_members_are_compared_only_within_the_same_model(monkeypatch, world):
    add_r34(world, "jason", ANCHOR)
    world.add_profile("kid", at_cos(0.80))                              # a LEGACY profile that sits right next to the offered clip
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    offer = dict(voice_user_id="jason", embedding_base64=_b64(at_cos(0.80)), voice_score=0.80, speech_s=3.0, model_id=R34)
    out = await spk.process_adaptation(world.db, panel_id="panel1", payload=offer, now=NOW, mode="on")
    assert out["applied"] and out["margin"] is None                    # the kid's Resemblyzer vector is not 'another member' here
    add_r34(world, "kid", at_cos(0.80))                                  # negative control: the same vector in the SAME model blocks it
    out = await spk.process_adaptation(world.db, panel_id="panel1", payload=offer, now=NOW + timedelta(hours=2), mode="on")
    assert out["reason"] == "close_to_other"


@pytest.mark.asyncio
async def test_the_adapted_overlay_is_keyed_by_user_and_model(monkeypatch, world):
    add_r34(world, "jason", ANCHOR, sample_count=5)
    world.db.conn.execute("UPDATE speaker_profiles SET adapted_blob = ?, adapted_from_count = sample_count WHERE model_id IS NULL",
                          (at_angle(5.0).tobytes(),))             # only the LEGACY row is adapted
    world.db.conn.commit()
    feed = [("jason", "J", ANCHOR.tobytes(), 5, R34), ("jason", "J", ANCHOR.tobytes(), 3, sm.LEGACY_MODEL_ID)]
    out = await spk.overlay_adapted(world.db, feed, user_idx=0, blob_idx=2, model_idx=4)
    assert out[0][2] == ANCHOR.tobytes()                                # the ResNet34 anchor is NOT replaced by the Resemblyzer adaptation
    assert out[1][2] == at_angle(5.0).tobytes()                         # the Resemblyzer one is


# -- who counts as enrolled, and what is asked of them -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrolled_means_every_model_the_mode_needs(monkeypatch, world):
    import enrollment_state as es
    monkeypatch.delenv("ZOE_SPEAKER_MODEL", raising=False)
    st = await es.get_state("jason", db=world.db)
    assert st.voice_enrolled is True and st.voice_upgrade is False         # default mode: exactly today
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "dual")
    st = await es.get_state("jason", db=world.db)
    assert st.voice_enrolled is False and st.voice_upgrade is True         # only the old profile: Zoe offers the re-enrolment
    add_r34(world, "jason", at_cos(0.9))
    st = await es.get_state("jason", db=world.db)
    assert st.voice_enrolled is True and st.voice_upgrade is False         # both models enrolled
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    world.db.conn.execute("DELETE FROM speaker_profiles WHERE model_id IS NULL")
    world.db.conn.commit()
    assert (await es.get_state("jason", db=world.db)).voice_enrolled is True
    world.db.conn.execute("UPDATE speaker_profiles SET consent_at = NULL")  # unconsented does not count
    world.db.conn.commit()
    st = await es.get_state("jason", db=world.db)
    assert st.voice_enrolled is False and st.voice_upgrade is False


def test_the_script_asks_for_three_phrases_unchanged_or_five_with_the_many_intro():
    import voice_enrollment as ve
    legacy = ve.script_for("en", "Ada")
    assert len(legacy["phrases"]) == 3 and "three short phrases" in legacy["intro"]
    five = ve.script_for("en", "Ada", 5)
    assert len(five["phrases"]) == 5 and five["phrases"][:3] == legacy["phrases"]
    assert "5 short phrases" in five["intro"] and "Ada" in five["intro"] and "{count}" not in five["intro"]
    assert five["retry_odd"] and ve.script_for("es", "Ada", 5)["phrases"][3:]


# -- migration 0051 ----------------------------------------------------------------------------------------------------

def test_there_is_exactly_one_alembic_head_and_0051_is_on_its_chain():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(SVC / "alembic.ini"))
    cfg.set_main_option("script_location", str(SVC / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, heads          # 0051 was the head when it landed; 0052 (calendar data model) chains onto it
    assert "0051" in {rev.revision for rev in script.walk_revisions("base", heads[0])}


def test_migration_0051_backfills_is_rerun_safe_and_adds_one_nullable_column(tmp_path):
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    spec = importlib.util.spec_from_file_location("mig_0051_t", SVC / "alembic/versions/0051_speaker_profile_model_id.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    eng = sa.create_engine("sqlite:///" + str(tmp_path / "m.db"))
    with eng.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE speaker_profiles (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, embedding_blob BLOB)")
        conn.exec_driver_sql("INSERT INTO speaker_profiles VALUES ('a', 'jason', x'00')")
        for _ in range(2):                                             # second pass over the upgraded table: a no-op
            with Operations.context(MigrationContext.configure(conn)):
                mig.upgrade()
        cols = dict((r[1], r) for r in conn.exec_driver_sql("PRAGMA table_info(speaker_profiles)").fetchall())
        assert cols["model_id"][2] == "TEXT" and cols["model_id"][3] == 0       # nullable
        assert conn.exec_driver_sql("SELECT model_id FROM speaker_profiles").fetchall() == [("resemblyzer",)]
        assert mig.revision == "0051" and mig.down_revision == "0050"
    eng.dispose()


# -- the Pi module mirrors the server, and every pin agrees --------------------------------------------------------------

def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pi():
    return _load("zoe_speaker_resnet34_drift", "scripts/setup/zoe_speaker_resnet34.py")


def test_the_panel_module_mirrors_the_servers_numbers(pi):
    assert pi.MODEL_ID == sm.RESNET34_MODEL_ID and pi.LEGACY_MODEL_ID == sm.LEGACY_MODEL_ID and pi.EMBED_DIM == sm.MODELS[R34].dim
    assert pi.SHORT_MAX_S == sm.SHORT_MAX_S and pi.THRESHOLDS == sm.RESNET34_THRESHOLDS
    assert (pi.ADAPT_MIN_SCORE, pi.ADAPT_ANCHOR_FLOOR) == (sm.RESNET34_ADAPT_MIN_SCORE, sm.RESNET34_ADAPT_ANCHOR_FLOOR)
    assert (pi.ENROLL_CLIPS, pi.ENROLL_TAU, pi.ENROLL_MIN_KEEP) == (sm.RESNET34_ENROLL_CLIPS, sm.RESNET34_ENROLL_TAU, sm.RESNET34_ENROLL_MIN_KEEP)
    for seconds in (0.4, 1.99, 2.0, 2.01, 7.0, None):
        assert pi.length_group(seconds) == sm.length_group(seconds)
    for group in ("short", "long"):
        for tier in ("far1", "far01"):
            assert pi.threshold_for(group, tier) == sm.resnet34_threshold(group, tier)


def test_the_pins_in_the_module_the_script_the_tool_and_the_artifacts_agree(pi, tmp_path):
    sh = (REPO / "scripts/setup/fetch_speaker_models.sh").read_text()
    tool = (REPO / "scripts/perf/speaker_resnet34_artifacts.py").read_text()
    for name, value in (("MODEL_SHA256", pi.MODEL_SHA256), ("FP32_SHA256", pi.FP32_SHA256)):
        assert re.search(name + '="' + value + '"', sh), name
    assert re.search('DEVCLEAN_SHA256="([0-9a-f]{64})"', sh).group(1) == re.search('DEVCLEAN_SHA256 = "([0-9a-f]{64})"', tool).group(1)
    assert pi.MODEL_FILENAME in sh and pi.FP32_URL.endswith("wespeaker_en_voxceleb_resnet34.onnx")
    art = json.loads((REPO / "scripts/setup" / pi.ARTIFACTS_FILENAME).read_text())
    assert art["model_id"] == pi.MODEL_ID and art["model"]["sha256"] == pi.MODEL_SHA256 and art["model"]["fp32_sha256"] == pi.FP32_SHA256
    assert art["centre"]["version"] == pi.CENTRE_VERSION and len(art["centre"]["vector"]) == pi.EMBED_DIM
    assert pi.centre_digest(art["centre"]["vector"]) == pi.CENTRE_SHA256 == art["centre"]["sha256_f32le"]
    loaded = pi.load_artifacts(str(REPO / "scripts/setup" / pi.ARTIFACTS_FILENAME))                # the real pin check
    assert len(loaded["selftest"]["reference"]) == pi.EMBED_DIM
    assert "LibriSpeech" in art["centre"]["provenance"]["corpus"] and art["centre"]["provenance"]["licence"] == "CC BY 4.0"
    # negative control: one edited float and the loader refuses
    art["centre"]["vector"][0] += 0.01
    bad = tmp_path / "tampered.json"
    bad.write_text(json.dumps(art))
    with pytest.raises(pi.Unavailable, match="pinned digest"):
        pi.load_artifacts(str(bad))


def test_the_panels_fbank_is_the_harness_front_end_bit_for_bit(pi):
    ref = _load("speaker_shadow_embed_drift", "scripts/voice/speaker_shadow_embed.py")
    clip = pi.synthetic_clip(2.5)
    assert np.array_equal(pi.kaldi_fbank(clip), ref.kaldi_fbank(clip))
    assert np.array_equal(pi.mel_banks(), ref.mel_banks())
    assert pi.kaldi_fbank(clip[:300]).shape == (0, 80)


def test_the_deploy_script_ships_the_embedder_and_its_artifacts():
    deploy = (REPO / "scripts/setup/deploy-pi-voice.sh").read_text()
    for name in ("zoe_speaker_resnet34.py", "zoe_speaker_resnet34_artifacts.json", "fetch_speaker_models.sh"):
        assert name in re.search(r"SHIPPED_FILES=\(([^)]*)\)", deploy).group(1), name


@pytest.mark.asyncio
async def test_enrolment_state_counts_only_the_acting_models_profile_in_every_mode(monkeypatch, world):
    """Codex P2 on #2024: after a rollback to the default mode, a member who only has a ResNet34 row must NOT be reported enrolled
    (that would suppress the re-enrolment the acting model needs), and Zoe must not say she knows a voice she cannot recognise."""
    import enrollment_state as es
    world.db.conn.execute("DELETE FROM speaker_profiles")
    world.db.conn.commit()
    add_r34(world, "jason", at_cos(0.9))
    for mode in ("resemblyzer", "resnet34"):
        monkeypatch.setenv("ZOE_SPEAKER_MODEL", mode)
        st = await es.get_state("jason", db=world.db)
        want = mode == "resnet34"
        assert (st.voice_enrolled, st.voice_upgrade) == (want, False), mode
    # negative control: the acting (legacy) row present -> enrolled in the default mode, and in dual she knows the voice (upgrade)
    world.add_profile("jason", ANCHOR)
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resemblyzer")
    assert (await es.get_state("jason", db=world.db)).voice_enrolled is True
    world.db.conn.execute("DELETE FROM speaker_profiles WHERE model_id = ?", (R34,))
    world.db.conn.commit()
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "dual")
    st = await es.get_state("jason", db=world.db)
    assert (st.voice_enrolled, st.voice_upgrade) == (False, True)
    # resnet34 mode with only the legacy row: she can NOT recognise him, so it is not an 'upgrade' she can vouch for
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    st = await es.get_state("jason", db=world.db)
    assert (st.voice_enrolled, st.voice_upgrade) == (False, False)


@pytest.mark.asyncio
async def test_a_finished_profile_must_name_its_model_it_is_never_assumed_legacy(monkeypatch, world):
    """Codex P2 on #2024: an embedding upload without a valid string model_id is refused (4xx), never stored as Resemblyzer."""
    from fastapi import HTTPException
    vec = _b64(at_cos(0.8))
    for bad in (dict(), dict(model_id=None), dict(model_id=""), dict(model_id="   "), dict(model_id=7), dict(model_id=["resemblyzer"])):
        body = dict(embedding_base64=vec)
        body.update(bad)
        with pytest.raises(HTTPException) as e:
            await enrol(monkeypatch, world, body)
        assert 400 <= e.value.status_code < 500, bad
    assert len(rows(world)) == 1                                               # only the fixture's legacy row
    out = await enrol(monkeypatch, world, dict(embedding_base64=vec, model_id="resemblyzer"))      # negative control: named -> stored
    assert out["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode, refused", [("resnet34", True), ("dual", False), ("resemblyzer", False)])
async def test_browser_audio_enrolment_is_refused_only_when_resnet34_acts(monkeypatch, world, mode, refused):
    """Codex P2 on #2024: the settings page uploads audio with no model_id. In resnet34 mode that would store a Resemblyzer
    profile nobody uses and report success - refuse it and point at the panel. dual / default keep the old behaviour."""
    from fastapi import HTTPException
    from routers import voice_tts
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", mode)
    monkeypatch.setattr(voice_tts, "_compute_resemblyzer_embedding", lambda path: np.asarray(at_cos(0.7), dtype=np.float32).tobytes())
    world.db.conn.execute("DELETE FROM speaker_profiles")
    world.db.conn.commit()
    body = dict(audio_base64=base64.b64encode(b"RIFF....WAVE").decode())
    browser = dict(source="session", user_id="jason", role="admin")
    if refused:
        with pytest.raises(HTTPException) as e:
            await enrol(monkeypatch, world, body, caller=browser)
        assert e.value.status_code == 409 and "learn my voice" in e.value.detail and "nothing was stored" in e.value.detail
        assert rows(world) == []
    else:                                                                       # negative control: the same upload is stored
        out = await enrol(monkeypatch, world, body, caller=browser)
        assert out["ok"] and [r[1] for r in rows(world)] == [sm.LEGACY_MODEL_ID]


@pytest.mark.asyncio
async def test_resnet34_mode_still_accepts_the_panels_finished_profile(monkeypatch, world):
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    out = await enrol(monkeypatch, world, dict(embedding_base64=_b64(at_cos(0.8)), model_id=R34, sample_count=5))
    assert out["ok"] and out["model_id"] == R34


# -- an ABSENT model id is legacy; an explicit malformed one is refused (Codex P2 on #2024) -----------------------------

def test_model_id_field_absent_is_legacy_and_malformed_is_none():
    assert sm.model_id_field(dict(), "model_id") == sm.LEGACY_MODEL_ID
    assert sm.model_id_field(dict(model_id=" " + R34 + " "), "model_id") == R34
    for bad in ("", "   ", None, 7, ["resemblyzer"], dict()):
        assert sm.model_id_field(dict(model_id=bad), "model_id") is None, bad
    assert sm.normalize_model_id(None) == sm.LEGACY_MODEL_ID        # stored rows keep their NULL = legacy meaning


@pytest.mark.parametrize("payload", [
    dict(voice_user_id="jason", voice_score=0.95, speaker=dict(verified=None, member="jason", score=0.95, model_id="")),
    dict(voice_user_id="jason", voice_score=0.95, speaker=dict(verified=None, member="jason", score=0.95, model_id=None)),
    dict(voice_user_id="jason", voice_score=0.95, speaker=dict(verified=None, member="jason", score=0.95, model_id=7)),
    dict(voice_user_id="jason", voice_score=0.95, voice_model_id="  "),
])
def test_an_explicitly_malformed_claim_model_is_refused_not_read_as_legacy(monkeypatch, payload):
    from routers import voice_tts
    monkeypatch.delenv("ZOE_SPEAKER_MODEL", raising=False)
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.82")
    assert voice_tts._accept_panel_voice_claim(payload, DEVICE) is None
    # negative control: the same claim with the field ABSENT is today's legacy claim, accepted
    clean = dict(voice_user_id="jason", voice_score=0.95, speaker=dict(verified=None, member="jason", score=0.95))
    assert voice_tts._accept_panel_voice_claim(clean, DEVICE) == "jason"


@pytest.mark.asyncio
async def test_identify_refuses_an_explicitly_malformed_model_id(monkeypatch, world):
    from fastapi import HTTPException
    from routers import voice_tts
    monkeypatch.delenv("ZOE_SPEAKER_MODEL", raising=False)
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.5")
    with compat_db(monkeypatch, world.db):
        for bad in ("", " ", None, 3):
            with pytest.raises(HTTPException) as e:
                await voice_tts.voice_identify(dict(embedding_base64=_b64(ANCHOR), model_id=bad), caller=DEVICE)
            assert e.value.status_code == 400, bad
        ok = await voice_tts.voice_identify(dict(embedding_base64=_b64(ANCHOR)), caller=DEVICE)    # negative control: absent
    assert ok["identified"] and ok["user_id"] == "jason"


@pytest.mark.asyncio
async def test_adaptation_refuses_a_malformed_model_id(world):
    offer = dict(voice_user_id="jason", embedding_base64=_b64(at_cos(0.90)), voice_score=0.90, speech_s=3.0, model_id="")
    out = await spk.process_adaptation(world.db, panel_id="panel1", payload=offer, now=NOW, mode="shadow")
    assert out["reason"] == "unknown_model"
    offer.pop("model_id")                                                      # negative control: absent = legacy, evaluated
    out = await spk.process_adaptation(world.db, panel_id="panel1", payload=offer, now=NOW, mode="shadow")
    assert out["reason"] != "unknown_model"


# -- the settings list names each model's profile and orders them (Codex P2 on #2024) ------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("mode, first, labels", [
    ("dual", sm.LEGACY_MODEL_ID, ["current", "new model, testing"]),
    ("resnet34", R34, ["current", "not in use"]),
    ("resemblyzer", sm.LEGACY_MODEL_ID, ["current", "not in use"]),
])
async def test_profile_list_labels_each_model_and_puts_the_acting_one_first(monkeypatch, world, mode, first, labels):
    from routers import voice_tts
    add_r34(world, "jason", at_cos(0.9))
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", mode)
    with compat_db(monkeypatch, world.db):
        out = await voice_tts.voice_profiles(caller=dict(source="session", user_id="jason", role="admin"))
    mine = [p for p in out["profiles"] if p["user_id"] == "jason"]
    assert [p["model_id"] for p in mine][0] == first and [p["model_label"] for p in mine] == labels
    assert [p["acting"] for p in mine] == [True, False]


def test_the_settings_page_shows_the_model_and_the_delete_names_it():
    html = (REPO / "services/zoe-ui/dist/settings.html").read_text()
    assert "Voice profile (${p.model_label})" in html and "data-label=" in html
    assert "deleteVoiceProfile(this.dataset.id, this.dataset.who, this.dataset.label)" in html
    assert "confirm(`Delete ${label || 'this voice profile'}" in html


# -- "do you know my voice?" follows the acting model + consent (Codex P2 on #2024) ---------------------------------------

@pytest.mark.asyncio
async def test_self_model_knows_a_voice_only_if_the_acting_model_does_rollback_case(monkeypatch, world):
    import self_model

    async def count(sql, params=(), ttl=60.0):
        if "speaker_profiles" not in sql:
            return 0                                                       # the face count: not this test's table
        return world.db.conn.execute(sql, params).fetchone()[0]

    monkeypatch.setattr(self_model, "_count", count)
    world.db.conn.execute("DELETE FROM speaker_profiles")
    world.db.conn.commit()
    add_r34(world, "jason", at_cos(0.9))                                    # enrolled under resnet34, then rolled back
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resemblyzer")
    assert (await self_model._enrolment("jason"))[0] is False             # the acting model cannot recognise him
    assert await count(*self_model._acting_voices_query()) == 0
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")                    # negative control: the model that has his profile acts
    assert (await self_model._enrolment("jason"))[0] is True
    assert await count(*self_model._acting_voices_query()) == 1
    world.db.conn.execute("UPDATE speaker_profiles SET consent_at = NULL")  # unconsented never counts
    world.db.conn.commit()
    assert (await self_model._enrolment("jason"))[0] is False


@pytest.fixture(autouse=True)
def _member_signed_in_at_the_panel(jason_signed_in_at_the_panel):
    """A device-token enrolment writes ONLY a member the server verified at the panel (biometric_scope, 2026-10-19); these tests
    are about the payload, so "jason" is PIN-signed-in. The identity rule itself: test_biometric_ownership_scope.py."""
