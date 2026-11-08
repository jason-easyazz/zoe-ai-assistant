"""The panel daemon's half of named guests: guests are scored BESIDE the members and ride as a separate field, never as the member
claim; a voice closer to a guest never adapts a member's profile.
Invented names only (the repo is public)."""
from __future__ import annotations

import base64
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rn = _load("zoe_speaker_resnet34", SETUP / "zoe_speaker_resnet34.py")


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(rn.EMBED_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def b64(v) -> str:
    return base64.b64encode(np.asarray(v, dtype=np.float32).tobytes()).decode()


@pytest.fixture(scope="module")
def daemon():
    stubs = dict((n, MagicMock()) for n in ("pyaudio",) if n not in sys.modules)
    saved = dict((n, sys.modules.get(n)) for n in stubs)
    sys.modules.update(stubs)
    try:
        yield _load("zoe_voice_daemon_guest_test", SETUP / "zoe_voice_daemon.py")
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


@pytest.fixture
def rig(daemon, monkeypatch):
    quinn = unit(21)
    monkeypatch.setattr(daemon, "_rn34", rn)
    monkeypatch.setitem(daemon._profile_cache, "guests", [dict(guest_id="g-quinn", display_name="Quinn", embedding_base64=b64(quinn),
                                                               model_id=rn.MODEL_ID)])
    adapted = []
    monkeypatch.setattr(daemon, "_adapt_offer", lambda *a, **k: adapted.append(a))
    state = types.SimpleNamespace(quinn=quinn, adapted=adapted, member_score=0.30, vec=quinn)

    def fake_score(wav):
        return dict(model_id=rn.MODEL_ID, source="local", user_id="member-a", score=state.member_score, n_compared=1, seconds=3.0,
                    _vec=state.vec)
    monkeypatch.setattr(daemon, "_score_resnet34", fake_score)
    return state


def test_a_guest_voice_rides_as_the_guest_field_and_never_adapts_the_member(daemon, rig):
    claim = daemon._identify_speaker_resnet34(b"wav")
    assert tuple(claim) == ("member-a", 0.30)                       # the member candidate is unchanged (the server judges it)
    assert claim.guest[0] == "g-quinn" and claim.guest[1] > 0.99
    block = daemon._speaker_field(claim)
    assert block["member"] == "member-a" and block["guest"] == "g-quinn" and block["guest_score"] > block["score"]
    assert rig.adapted == []


def test_a_member_voice_still_adapts_and_carries_a_lower_guest_score(daemon, rig):
    rig.vec, rig.member_score = unit(22), 0.80                      # not Quinn
    claim = daemon._identify_speaker_resnet34(b"wav")
    assert claim.guest is not None and claim.guest[1] < 0.80
    assert len(rig.adapted) == 1


def test_no_guests_synced_means_no_guest_field(daemon, rig, monkeypatch):
    monkeypatch.setitem(daemon._profile_cache, "guests", [])
    block = daemon._speaker_field(daemon._identify_speaker_resnet34(b"wav"))
    assert "guest" not in block


def test_a_guest_from_another_model_space_is_never_scored(daemon, rig, monkeypatch):
    monkeypatch.setitem(daemon._profile_cache, "guests", [dict(guest_id="g-quinn", embedding_base64=b64(rig.quinn), model_id="resemblyzer")])
    assert daemon._identify_speaker_resnet34(b"wav").guest is None


def test_forget_command_drops_the_guest_from_the_cache(daemon, rig, monkeypatch):
    monkeypatch.setattr(daemon, "_persist_profile_cache", lambda: None)
    monkeypatch.setattr(daemon, "_sync_speaker_profiles", lambda force=False: None)
    assert daemon._purge_speaker_profile("g-quinn") is True
    assert daemon._profile_cache["guests"] == []
