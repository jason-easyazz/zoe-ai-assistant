"""setup_omi_repass.sh: the supply-chain gates (pinned revisions + weights sha256, a --check that fails when anything is wrong). No network, no installs: every case stops before pip."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/setup/setup_omi_repass.sh"


def run(tmp_path, *args):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ZOE_OMI_REPASS_VENV": str(tmp_path / "venv"), "ZOE_OMI_REPASS_MODELS": str(tmp_path / "models")}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, timeout=60, env=env)


def test_seed_from_refuses_weights_that_are_not_the_pinned_ones_before_anything_is_installed(tmp_path):
    seed = tmp_path / "seed" / "Qwen3-ASR-1.7B-hf"
    seed.mkdir(parents=True)
    (seed / "model.safetensors").write_bytes(b"not the real weights")
    r = run(tmp_path, "--seed-from", str(tmp_path / "seed"))
    assert r.returncode == 1 and "REFUSED" in r.stderr and "sha256" in r.stderr
    assert not (tmp_path / "venv").exists() and not (tmp_path / "models").exists()                  # nothing created, nothing copied


def test_check_fails_when_the_venv_is_missing_and_never_reports_a_missing_model_as_ok(tmp_path):
    r = run(tmp_path, "--check")
    assert r.returncode == 1 and "MISSING" in r.stdout


def test_every_model_is_pinned_by_revision_and_weights_hash_and_the_worker_knows_the_directories():
    text = SCRIPT.read_text()
    pins = re.findall(r'^  "([^|"]+)\|([^|]+)\|([0-9a-f]{40})\|([^|]+)\|([0-9a-f]{64})"$', text, re.M)
    assert {p[0] for p in pins} == {"Qwen3-ASR-1.7B-hf", "parakeet-tdt-0.6b-v3", "granite-speech-5.0-2b"}
    assert {p[1].split("/")[0] for p in pins} <= {"Qwen", "nvidia", "ibm-granite"}                    # official orgs only
    worker = (REPO / "scripts/night/jobs/omi_repass_worker.py").read_text()
    for d, *_ in pins:
        assert f'"{d}"' in worker, d
    hf = re.findall(r'^  "([\w./-]+)\|([0-9a-f]{40})\|(model\.bin|embedding_model\.ckpt)\|([0-9a-f]{64})"$', text, re.M)
    assert {h[0] for h in hf} == {"Systran/faster-whisper-large-v3", "Systran/faster-distil-whisper-large-v3"}      # EVERY runtime model has a pinned revision AND weights hash
    assert (REPO / "scripts/setup/omi_repass_constraints.txt").read_text().count("==") > 50 and "-c \"$CON\"" in text


def test_check_exit_status_is_wired_to_drift_gpu_and_model_integrity():
    text = SCRIPT.read_text()
    assert "bad |= 0 if ok else 1" in text and "ZOE_CHECK_ALLOW_CPU" in text and "MISMATCH" in text and "rc=1" in text


def test_check_warns_when_the_cuda_ctranslate2_build_is_missing_but_does_not_fail_the_whole_runtime(tmp_path):
    r = run(tmp_path, "--check")
    assert "ct2       WARN" in r.stdout and "ln -s" in r.stdout                                    # named, with the command
    (tmp_path / ".zoe/ct2build/pylib/ctranslate2").mkdir(parents=True)
    (tmp_path / ".zoe/ct2build/install/lib").mkdir(parents=True)
    assert "ct2       ok" in run(tmp_path, "--check").stdout                                       # control
