"""Kokoro start-up: explicit repo id + opt-in offline-from-cache load (log review 2026-10-04).

Every Kokoro start printed ``WARNING: Defaulting repo_id to hexgrad/Kokoro-82M`` and
made four HEAD requests to huggingface.co although the snapshot was already cached.
Pure logic only — no torch, no kokoro, no network (the module lazy-imports them).
"""
import importlib.util
import os
import pathlib
import sys

import pytest

pytestmark = pytest.mark.ci_safe

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "setup" / "kokoro_sidecar.py"


@pytest.fixture(scope="module")
def kok():
    spec = importlib.util.spec_from_file_location("kokoro_sidecar_hf", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kokoro_sidecar_hf"] = module
    spec.loader.exec_module(module)
    return module


def _snapshot(root, voice="af_sky", *, config=True, weights=True, with_voice=True):
    snap = root / "models--hexgrad--Kokoro-82M" / "snapshots" / "abc123"
    (snap / "voices").mkdir(parents=True)
    if config:
        (snap / "config.json").write_text("{}")
    if weights:
        (snap / "kokoro-v1_0.pth").write_bytes(b"x")
    if with_voice:
        (snap / "voices" / f"{voice}.pt").write_bytes(b"x")
    return snap


def test_repo_id_is_the_one_kpipeline_defaults_to(kok):
    # Passing it explicitly must not change which model loads.
    assert kok._KOKORO_REPO_ID == "hexgrad/Kokoro-82M"


def test_snapshot_cached_requires_config_weights_and_voice(kok, tmp_path):
    assert kok._kokoro_snapshot_cached("af_sky", tmp_path) is False  # nothing cached
    _snapshot(tmp_path)
    assert kok._kokoro_snapshot_cached("af_sky", tmp_path) is True
    assert kok._kokoro_snapshot_cached("af_heart", tmp_path) is False  # other voice not cached


@pytest.mark.parametrize("missing", ["config", "weights", "with_voice"])
def test_incomplete_snapshot_is_not_offline_ready(kok, tmp_path, missing):
    _snapshot(tmp_path, **{missing: False})
    assert kok._kokoro_snapshot_cached("af_sky", tmp_path) is False


def test_offline_scope_sets_and_restores_env(kok, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    _snapshot(tmp_path)
    with kok._hf_offline_for_load(True, "af_sky") as active:
        assert active is True
        assert os.environ.get("HF_HUB_OFFLINE") == "1"
    # Scoped to the load: a later voice switch must still be able to download.
    assert "HF_HUB_OFFLINE" not in os.environ


def test_offline_scope_restores_a_preexisting_value(kok, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    _snapshot(tmp_path)
    with kok._hf_offline_for_load(True, "af_sky"):
        assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HF_HUB_OFFLINE"] == "0"


def test_offline_scope_is_inert_when_flag_off_or_cache_incomplete(kok, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    _snapshot(tmp_path)
    with kok._hf_offline_for_load(False, "af_sky") as active:  # flag dark
        assert active is False and "HF_HUB_OFFLINE" not in os.environ
    with kok._hf_offline_for_load(True, "af_heart") as active:  # voice not cached -> online path
        assert active is False and "HF_HUB_OFFLINE" not in os.environ


def test_offline_scope_restores_env_when_the_load_raises(kok, tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    _snapshot(tmp_path)
    with pytest.raises(RuntimeError):
        with kok._hf_offline_for_load(True, "af_sky"):
            raise RuntimeError("load failed")
    assert "HF_HUB_OFFLINE" not in os.environ


def test_flag_defaults_dark(kok):
    assert kok._HF_OFFLINE_ON_LOAD is False


def _timeout_s(path):
    import re

    m = re.search(r"^TimeoutStartSec=(\d+)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
    assert m, f"{path.name} has no numeric TimeoutStartSec"
    return int(m.group(1))


def test_start_timeout_outlasts_the_brain_wait(kok):
    """Kokoro blocks its CUDA load for up to _BRAIN_WAIT_S waiting on the brain, then
    loads + warms (~14 s). A TimeoutStartSec at or below that wait makes systemd kill it
    mid-wait and loop (the installed unit had 120 s against 180 s)."""
    unit_dir = _SCRIPT.parents[1] / "setup" / "systemd"
    for f in (unit_dir / "kokoro-tts.service", unit_dir / "kokoro-tts.service.d" / "70-start-timeout.conf"):
        assert _timeout_s(f) > kok._BRAIN_WAIT_S + 30, f.name
