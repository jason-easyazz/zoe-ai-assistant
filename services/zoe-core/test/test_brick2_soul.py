"""Brick 2 integration smoke test: the core answers as Zoe.

Loads the provider + soul extensions and asks an identity question; asserts the
model responds as Zoe (persona applied) rather than as Pi's default coding
assistant. On-demand integration test — skips when pi or the model server are
unavailable.

    python -m pytest services/zoe-core/test/test_brick2_soul.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parent.parent
_PROVIDER_EXT = _CORE / "extensions" / "provider-local-gemma.ts"
_SOUL_EXT = _CORE / "extensions" / "soul.ts"
_SOUL_MD = _CORE / "SOUL.md"
_MODEL = os.environ.get("ZOE_CORE_MODEL_ID", "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")
_BASE_URL = (
    os.environ.get("ZOE_CORE_MODEL_URL")
    or os.environ.get("GEMMA_SERVER_URL")
    or "http://127.0.0.1:11434/v1"
)


def _model_server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{_BASE_URL}/models", timeout=4) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ask_identity_once(env: dict) -> str:
    result = subprocess.run(
        [
            "pi", "-p",
            "--provider", "local-gemma",
            "--model", _MODEL,
            "-e", str(_PROVIDER_EXT),
            "-e", str(_SOUL_EXT),
            "--no-extensions", "--no-skills", "--no-prompt-templates",
            "--no-themes", "--no-context-files", "--no-session", "--thinking", "off",
            "Who are you? Answer in one short sentence.",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"pi exited {result.returncode}: {result.stderr}"
    text = result.stdout.strip()
    assert text, "pi returned an empty response"
    return text.lower()


@pytest.mark.integration
def test_core_answers_as_zoe():
    if shutil.which("pi") is None:
        pytest.skip("pi CLI not installed")
    if not _model_server_up():
        pytest.skip(f"model server not reachable at {_BASE_URL}")
    for path in (_PROVIDER_EXT, _SOUL_EXT, _SOUL_MD):
        assert path.is_file(), f"missing: {path}"

    env = {**os.environ, "ZOE_CORE_SOUL_PATH": str(_SOUL_MD)}
    # A 2B model is not deterministic about persona: with the SOUL prompt wired
    # in it usually answers as Zoe, but occasionally blurts its base identity
    # ("I'm Gemma 4..."). The brick is correct if the persona is adopted within a
    # few attempts, so sample up to 3 times rather than gamble on one generation.
    lower = ""
    for _ in range(3):
        lower = _ask_identity_once(env)
        if "zoe" in lower and "coding assistant" not in lower:
            break
    # Persona applied: identifies as Zoe, not Pi's default coding assistant.
    assert "zoe" in lower, f"expected Zoe identity in 3 tries, last: {lower!r}"
    assert "coding assistant" not in lower, f"persona not applied (default prompt leaked): {lower!r}"
