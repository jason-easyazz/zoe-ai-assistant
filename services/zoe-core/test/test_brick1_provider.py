"""Brick 1 integration smoke test.

Proves Pi (zoe-core's brain) answers a prompt on the local Gemma model through
the `local-gemma` provider extension.

This is an on-demand integration test: it needs the `pi` CLI and a reachable
OpenAI-compatible model server (`GEMMA_SERVER_URL`, default
`http://127.0.0.1:11434/v1`). It skips cleanly when either is unavailable, so it
is safe to run anywhere.

Run it explicitly:
    python -m pytest services/zoe-core/test/test_brick1_provider.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

_EXT = Path(__file__).resolve().parent.parent / "extensions" / "provider-local-gemma.ts"
_MODEL = os.environ.get("ZOE_CORE_MODEL_ID", "gemma-4-E2B-it-Q4_K_M.gguf")
_BASE_URL = (
    os.environ.get("ZOE_CORE_MODEL_URL")
    or os.environ.get("GEMMA_SERVER_URL")
    or "http://127.0.0.1:11434/v1"
)
_SENTINEL = "ZOE_CORE_BRICK1_OK"


def _model_server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{_BASE_URL}/models", timeout=4) as resp:
            return resp.status == 200
    except Exception:
        return False


@pytest.mark.integration
def test_pi_answers_on_local_gemma_via_provider():
    if shutil.which("pi") is None:
        pytest.skip("pi CLI not installed")
    if not _model_server_up():
        pytest.skip(f"model server not reachable at {_BASE_URL}")
    assert _EXT.is_file(), f"extension missing: {_EXT}"

    result = subprocess.run(
        [
            "pi", "-p",
            "--provider", "local-gemma",
            "--model", _MODEL,
            "-e", str(_EXT),
            "--no-extensions", "--no-skills", "--no-prompt-templates",
            "--no-themes", "--no-context-files", "--no-session", "--thinking", "off",
            f"Reply with exactly this token and nothing else: {_SENTINEL}",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, f"pi exited {result.returncode}: {result.stderr}"
    assert result.stdout.strip(), "pi returned an empty response"
    # The whole point of Brick 1: Pi reached the local Gemma model through our
    # provider and produced the requested answer.
    assert _SENTINEL in result.stdout, f"unexpected response: {result.stdout!r}"
