"""The voice-signature engine failing to LOAD is loud, explained, and worked around (owner's failed enrolment, 2026-10-12 19:11).

What happened: every ``POST /api/voice/enroll`` answered 503 "resemblyzer not available". The cause was the unit's
``LD_LIBRARY_PATH`` ending in ``/usr/lib/aarch64-linux-gnu`` - the distro BLAS beat the one bundled in the torch wheel and
``import torch`` died with ``undefined symbol: sbgemm_`` - but ``voice_speaker_id`` caught the ``ImportError`` and logged it at DEBUG,
so the log held nothing and a shell (without the variable) imported the package fine.

Groups (each has a break-the-fix control run by editing the module, see the PR):
  loud        an import failure is logged at WARNING with its exception TYPE and message, never silent
  explained   ``last_failure()`` carries the reason and the route's 503 body repeats it
  worked-around  an environment-shaped failure (ImportError/OSError from the linker) falls back to a clean-library-path child
  not-masked  a missing package does NOT spawn a child and stays retryable; a bad WAV does not mark the engine broken
  env         ``clean_library_env`` drops only the distro directories
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import logging
import subprocess
import sys
import types

import voice_speaker_id as vsi

LINKER_ERR = "libtorch_cpu.so: undefined symbol: sbgemm_"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(vsi, "_ENCODER", None)
    monkeypatch.setattr(vsi, "_INPROC_BROKEN", False)
    monkeypatch.setattr(vsi, "_LAST_FAILURE", "")
    yield


def _raise(exc):
    def f():
        raise exc
    return f


def test_an_import_failure_is_logged_at_warning_with_its_type(monkeypatch, caplog):
    monkeypatch.setattr(vsi, "_get_voice_encoder", _raise(ImportError(LINKER_ERR)))
    monkeypatch.setattr(vsi, "_embed_in_clean_subprocess", lambda p: None)
    with caplog.at_level(logging.DEBUG, logger=vsi.logger.name):
        assert vsi._compute_resemblyzer_embedding("x.wav") is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "an import failure must never be silent"
    text = warnings[0].getMessage()
    assert "ImportError" in text and "sbgemm_" in text


def test_the_reason_is_kept_and_reaches_the_503_body(monkeypatch):
    monkeypatch.setattr(vsi, "_get_voice_encoder", _raise(ImportError(LINKER_ERR)))
    monkeypatch.setattr(vsi, "_embed_in_clean_subprocess", lambda p: None)
    vsi._compute_resemblyzer_embedding("x.wav")
    assert "ImportError" in vsi.last_failure() and "sbgemm_" in vsi.last_failure()
    from routers.voice_tts import _speaker_engine_unavailable_detail

    body = _speaker_engine_unavailable_detail()
    assert "sbgemm_" in body and "LD_LIBRARY_PATH" in body and "not available" not in body.split(":")[0]


def test_a_linker_failure_falls_back_to_the_clean_child_and_remembers(monkeypatch):
    calls = []
    monkeypatch.setattr(vsi, "_get_voice_encoder", _raise(ImportError(LINKER_ERR)))
    monkeypatch.setattr(vsi, "_embed_in_clean_subprocess", lambda p: calls.append(p) or b"\x01" * 1024)
    assert vsi._compute_resemblyzer_embedding("a.wav") == b"\x01" * 1024
    assert vsi._INPROC_BROKEN is True
    assert vsi._compute_resemblyzer_embedding("b.wav") == b"\x01" * 1024     # straight to the child, no second doomed import
    assert calls == ["a.wav", "b.wav"]


def test_an_oserror_from_the_loader_is_an_environment_failure_too(monkeypatch):
    monkeypatch.setattr(vsi, "_get_voice_encoder", _raise(OSError("cannot open shared object file")))
    monkeypatch.setattr(vsi, "_embed_in_clean_subprocess", lambda p: b"\x02" * 1024)
    assert vsi._compute_resemblyzer_embedding("a.wav") == b"\x02" * 1024


def test_a_missing_package_spawns_nothing_and_stays_retryable(monkeypatch, caplog):
    monkeypatch.setattr(vsi, "_get_voice_encoder", _raise(ModuleNotFoundError("No module named 'resemblyzer'")))
    monkeypatch.setattr(vsi, "_embed_in_clean_subprocess", lambda p: pytest.fail("a child cannot install a package"))
    with caplog.at_level(logging.WARNING, logger=vsi.logger.name):
        assert vsi._compute_resemblyzer_embedding("a.wav") is None
    assert vsi._INPROC_BROKEN is False                    # installing the package later works without a restart
    assert "ModuleNotFoundError" in vsi.last_failure()
    assert any("ModuleNotFoundError" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


def test_a_bad_wav_is_not_an_environment_failure(monkeypatch):
    fake = types.ModuleType("resemblyzer")

    class Enc:
        def __init__(self, **kw):
            pass

    def preprocess_wav(path):
        raise OSError("not a RIFF file")

    fake.VoiceEncoder, fake.preprocess_wav = Enc, preprocess_wav
    monkeypatch.setitem(sys.modules, "resemblyzer", fake)
    monkeypatch.setattr(vsi, "_embed_in_clean_subprocess", lambda p: pytest.fail("a bad WAV must not spawn a child"))
    assert vsi._compute_resemblyzer_embedding("bad.wav") is None
    assert vsi._INPROC_BROKEN is False
    assert "OSError" in vsi.last_failure()


def test_the_child_failing_too_is_loud_and_explained(monkeypatch, caplog):
    monkeypatch.setattr(vsi, "_get_voice_encoder", _raise(ImportError(LINKER_ERR)))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="Traceback\nImportError: boom"))
    with caplog.at_level(logging.WARNING, logger=vsi.logger.name):
        assert vsi._compute_resemblyzer_embedding("a.wav") is None
    assert "clean-environment embedding failed" in vsi.last_failure() and "boom" in vsi.last_failure()
    assert any("clean-environment" in r.getMessage() for r in caplog.records)


def test_the_child_succeeding_clears_the_stale_reason(monkeypatch):
    import base64

    monkeypatch.setattr(vsi, "_get_voice_encoder", _raise(ImportError(LINKER_ERR)))
    payload = base64.b64encode(b"\x03" * 1024).decode()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=payload, stderr=""))
    assert vsi._compute_resemblyzer_embedding("a.wav") == b"\x03" * 1024
    assert vsi.last_failure() == ""


def test_the_child_gets_a_library_path_without_the_distro_directories(monkeypatch):
    seen = {}
    monkeypatch.setenv("LD_LIBRARY_PATH", "/home/x/.local/lib:/usr/local/cuda-12.6/lib64:/usr/lib/aarch64-linux-gnu")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.update(env=k["env"], cmd=cmd) or types.SimpleNamespace(
        returncode=1, stdout="", stderr="x"))
    vsi._embed_in_clean_subprocess("a.wav")
    assert seen["env"]["LD_LIBRARY_PATH"] == "/home/x/.local/lib:/usr/local/cuda-12.6/lib64"
    assert seen["cmd"][0] == sys.executable


def test_clean_library_env_edges():
    base = {"LD_LIBRARY_PATH": "/usr/lib/aarch64-linux-gnu/:/usr/lib:/opt/gpu/lib", "OTHER": "1"}
    assert vsi.clean_library_env(base)["LD_LIBRARY_PATH"] == "/opt/gpu/lib"
    assert vsi.clean_library_env(base)["OTHER"] == "1"
    only_system = {"LD_LIBRARY_PATH": "/usr/lib/aarch64-linux-gnu"}
    assert "LD_LIBRARY_PATH" not in vsi.clean_library_env(only_system)
    assert vsi.clean_library_env({}) == {}
