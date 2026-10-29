"""Resemblyzer (and with it torch, ~360 MB RSS when loaded) is RETIRED from zoe-data; ResNet34 is the only speaker model.

Pinned here, each with a negative control (the same call with the one guard flipped behaves differently, so a green test is the
GUARD working, not an easy fixture):

* ``ZOE_SPEAKER_MODEL`` = ``resemblyzer`` / ``dual`` / a typo FAIL LOUD (``UnsupportedSpeakerModel``), at start-up before the
  service touches its database - while unset / empty / ``resnet34`` run unchanged;
* a leftover ``model_id = 'resemblyzer'`` voiceprint is IGNORED, never a crash: not synced, not matched, not claimable, listed
  as "not in use", and its owner counts as not enrolled (so Zoe offers the ResNet34 enrolment);
* the server embeds no audio any more (enrol audio -> 409, identify without a vector -> 400);
* no zoe-data runtime module imports ``resemblyzer`` / ``torch`` and the manifests no longer ship them.

No model, no audio, no network, no live DB: a SQLite table shaped like the repo's migrations on a tmp path.
"""
from __future__ import annotations

import ast
import base64
import contextlib
import sys
import types
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

import speaker_models as sm
from test_speaker_adaptation import World, at_cos

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


def add_r34(world: World, uid: str, vec) -> None:
    world.db.conn.execute(
        "INSERT INTO speaker_profiles (id, user_id, display_name, embedding_blob, sample_count, consent_at, model_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("pid-r34-" + uid, uid, uid.title(), np.asarray(vec, dtype=np.float32).tobytes(), 5, "2026-01-01 00:00:00", R34))
    world.db.conn.commit()


@pytest.fixture(autouse=True)
def _member_signed_in_at_the_panel(jason_signed_in_at_the_panel):
    """A device-token enrolment writes ONLY a member the server verified at the panel (biometric_scope, 2026-10-19)."""


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("ZOE_SPEAKER_MODEL", raising=False)
    monkeypatch.delenv("ZOE_SPEAKER_ADAPT", raising=False)
    return World(tmp_path)          # one consented, pre-0051 (model_id NULL = resemblyzer) voiceprint for jason


# -- ZOE_SPEAKER_MODEL: one supported value, the rest fail loud ---------------------------------------------------------

@pytest.mark.parametrize("raw", [None, "", "   ", "resnet34", "RESNET34 ", "ResNet34"])
def test_resnet34_unset_and_empty_all_run_resnet34_unchanged(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("ZOE_SPEAKER_MODEL", raising=False)
    else:
        monkeypatch.setenv("ZOE_SPEAKER_MODEL", raw)
    assert sm.speaker_mode() == "resnet34" and sm.acting_model() == R34
    assert sm.enrolled_models() == (R34,) and set(sm.visible_models()) == {R34} and sm.clips_needed() == 5
    assert sm.claim_threshold(R34, 3.0) == 0.265 and sm.claim_threshold(R34, 1.0) == 0.233   # the record's FAR-1 % bars, unchanged


@pytest.mark.parametrize("raw", ["resemblyzer", "Resemblyzer", "dual", "DUAL", "turbo", "1", "resnet34,dual"])
def test_a_retired_or_unknown_value_fails_loud_with_the_fix(monkeypatch, raw):
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", raw)
    with pytest.raises(sm.UnsupportedSpeakerModel) as e:
        sm.speaker_mode()
    assert "ZOE_SPEAKER_MODEL=resnet34" in str(e.value) and "retired" in str(e.value)
    for call in (sm.acting_model, sm.enrolled_models, sm.visible_models):      # no helper quietly falls back to a model
        with pytest.raises(sm.UnsupportedSpeakerModel):
            call()


def test_startup_refuses_a_retired_value_before_it_touches_the_database(monkeypatch):
    """``main.lifespan`` checks the flag before ``init_db``: dual -> UnsupportedSpeakerModel and init_db never runs; the same
    lifespan with resnet34 reaches init_db (negative control: the sentinel below is reachable, so the first half is not vacuous)."""
    import asyncio

    import logging_setup
    import main
    import runtime_env

    monkeypatch.setattr(logging_setup, "configure_logging", lambda *a, **k: None)
    monkeypatch.setattr(runtime_env, "bootstrap_runtime_env", lambda *a, **k: None)
    reached = []

    class Sentinel(Exception):
        pass

    async def fake_init_db():
        reached.append("init_db")
        raise Sentinel()

    monkeypatch.setattr(main, "init_db", fake_init_db)

    async def enter():
        async with main.lifespan(main.app):
            pass

    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "dual")
    with pytest.raises(sm.UnsupportedSpeakerModel):
        asyncio.run(enter())
    assert reached == []
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    with pytest.raises(Sentinel):
        asyncio.run(enter())
    assert reached == ["init_db"]


# -- a leftover resemblyzer voiceprint is ignored, never a crash ------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_never_serves_a_resemblyzer_row_even_to_a_panel_that_asks_for_it(monkeypatch, world):
    from routers import voice_tts
    with compat_db(monkeypatch, world.db):
        both = await voice_tts.voice_profiles_sync(models=sm.LEGACY_MODEL_ID + "," + R34, caller=DEVICE)
        none = await voice_tts.voice_profiles_sync(models=None, caller=DEVICE)
        assert both["profiles"] == [] and none["profiles"] == [] and both["mode"] == "resnet34"
        add_r34(world, "jason", at_cos(0.9))                                   # negative control: a ResNet34 row IS served
        again = await voice_tts.voice_profiles_sync(models=R34, caller=DEVICE)
    assert [p["model_id"] for p in again["profiles"]] == [R34]


@pytest.mark.asyncio
async def test_identify_neither_matches_nor_crashes_on_a_resemblyzer_row(monkeypatch, world):
    from fastapi import HTTPException
    from routers import voice_tts
    with compat_db(monkeypatch, world.db):
        out = await voice_tts.voice_identify(dict(embedding_base64=_b64(at_cos(1.0)), model_id=R34, speech_s=3.0), caller=DEVICE)
        assert out["identified"] is False and out["reason"] == "no_profiles"
        with pytest.raises(HTTPException) as e:                                 # a query in the retired space is refused
            await voice_tts.voice_identify(dict(embedding_base64=_b64(at_cos(1.0)), model_id="resemblyzer"), caller=DEVICE)
        assert e.value.status_code == 400
        add_r34(world, "jason", at_cos(1.0))                                    # negative control: the same query now matches
        hit = await voice_tts.voice_identify(dict(embedding_base64=_b64(at_cos(1.0)), model_id=R34, speech_s=3.0), caller=DEVICE)
    assert hit["identified"] is True and hit["user_id"] == "jason"


def test_a_claim_in_the_retired_space_is_never_honoured_and_a_resnet34_claim_is(world):
    from routers import voice_tts
    legacy = {"voice_user_id": "jason", "voice_score": 0.99}                    # names no model = the old Resemblyzer claim
    assert voice_tts._accept_panel_voice_claim(legacy, DEVICE) is None
    explicit = {"voice_user_id": "jason", "voice_score": 0.99, "voice_model_id": "resemblyzer"}
    assert voice_tts._accept_panel_voice_claim(explicit, DEVICE) is None
    good = {"voice_user_id": "jason", "voice_score": 0.40, "voice_model_id": R34, "voice_speech_s": 3.0}
    assert voice_tts._accept_panel_voice_claim(good, DEVICE) == "jason"         # negative control
    low = dict(good, voice_score=0.10)
    assert voice_tts._accept_panel_voice_claim(low, DEVICE) is None


@pytest.mark.asyncio
async def test_the_profile_list_labels_the_leftover_row_not_in_use_and_puts_the_acting_one_first(monkeypatch, world):
    from routers import voice_tts
    add_r34(world, "jason", at_cos(0.9))
    with compat_db(monkeypatch, world.db):
        out = await voice_tts.voice_profiles(caller=dict(source="session", user_id="jason", role="admin"))
    mine = [p for p in out["profiles"] if p["user_id"] == "jason"]
    assert [(p["model_id"], p["model_label"], p["acting"]) for p in mine] == [(R34, "current", True), ("resemblyzer", "not in use", False)]


@pytest.mark.asyncio
async def test_a_member_with_only_a_resemblyzer_voiceprint_is_not_enrolled_and_gets_the_resnet34_offer(monkeypatch, world):
    import enrollment_state
    with compat_db(monkeypatch, world.db):
        st = await enrollment_state.get_state("jason", db=world.db)
        assert (st.voice_enrolled, st.voice_upgrade) == (False, False)
        add_r34(world, "jason", at_cos(0.9))                                    # negative control
        st2 = await enrollment_state.get_state("jason", db=world.db)
    assert st2.voice_enrolled is True


@pytest.mark.asyncio
async def test_adaptation_refuses_the_retired_model(world):
    import speaker_adaptation as spk
    out = await spk.process_adaptation(world.db, panel_id="panel1", mode="on", now=None,
                                       payload={"voice_user_id": "jason", "embedding_base64": _b64(at_cos(0.9)),
                                                "voice_score": 0.9, "speech_s": 3.0, "model_id": "resemblyzer"})
    assert not out.get("updated") and out.get("reason") not in (None, "")


# -- the server embeds no audio ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uploaded_audio_is_refused_for_enrol_and_identify_and_nothing_is_stored(monkeypatch, world):
    from fastapi import HTTPException
    from routers import voice_tts
    audio = base64.b64encode(b"RIFF....WAVE").decode()
    before = world.db.conn.execute("SELECT COUNT(*) FROM speaker_profiles").fetchone()[0]
    with compat_db(monkeypatch, world.db):
        with pytest.raises(HTTPException) as e:
            await voice_tts.voice_enroll(dict(audio_base64=audio, user_id="jason"), caller=dict(source="session", user_id="jason", role="admin"))
        assert e.value.status_code == 409 and "learn my voice" in e.value.detail
        with pytest.raises(HTTPException) as e2:
            await voice_tts.voice_identify(dict(audio_base64=audio, model_id=R34), caller=DEVICE)
        assert e2.value.status_code == 400 and "embedding_base64" in e2.value.detail
        out = await voice_tts.voice_enroll(                                     # negative control: a finished panel profile still enrols
            dict(embedding_base64=_b64(at_cos(0.8)), model_id=R34, sample_count=5, user_id="jason", panel_id="panel1", consent=True),
            caller=DEVICE)
    assert out["ok"] and out["model_id"] == R34
    assert world.db.conn.execute("SELECT COUNT(*) FROM speaker_profiles").fetchone()[0] == before + 1


# -- nothing left to load ---------------------------------------------------------------------------------------------

_RETIRED = {"resemblyzer", "torch", "torchaudio", "librosa", "webrtcvad"}


def _retired_imports(source: str) -> list:
    out = []
    for node in ast.walk(ast.parse(source)):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        out += [n for n in names if n.split(".")[0] in _RETIRED]
    return out


def test_no_zoe_data_runtime_module_imports_the_retired_stack():
    assert _retired_imports("def f():\n    from resemblyzer import VoiceEncoder\n    import torch.nn\n") == ["resemblyzer", "torch.nn"]   # control
    offenders = []
    for path in sorted(SVC.rglob("*.py")):
        rel = path.relative_to(SVC)
        if rel.parts[0] in ("tests", "alembic", ".venv", "venv") or "node_modules" in rel.parts:
            continue
        for name in _retired_imports(path.read_text(encoding="utf-8")):
            offenders.append(f"{rel}: {name}")
    assert not offenders, offenders


def test_the_manifests_no_longer_ship_the_retired_stack_and_keep_what_still_needs_scipy():
    names = {}
    for fname in ("requirements.txt", "requirements-py312.txt"):
        for line in (SVC / fname).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                import re
                names.setdefault(fname, set()).add(re.split(r"[\s=<>!~@\[;]", line, maxsplit=1)[0].lower().replace("_", "-"))
    for fname, got in names.items():
        assert got, fname
        assert not (got & {"resemblyzer", "torch", "librosa", "webrtcvad", "webrtcvad-wheels"}), (fname, got & _RETIRED)
    assert {"scikit-learn", "joblib"} <= names["requirements-py312.txt"] and "scipy" in names["requirements-py312.txt"]


def test_the_session_override_flag_for_resemblyzer_is_gone():
    import voice_session
    assert "ALLOW_RESEMBLYZER" not in Path(voice_session.__file__).read_text(encoding="utf-8")
    assert "ZOE_VOICE_SESSION_ALLOW_RESEMBLYZER" not in (SVC / "flag_registry.py").read_text(encoding="utf-8")
    assert voice_session.model_gate({"voice_model_id": "resemblyzer"}) == (False, "claim_not_acting_model")
    assert voice_session.model_gate({"voice_model_id": R34}) == (True, "resnet34")
