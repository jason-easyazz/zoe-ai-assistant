"""Brick 4a integration smoke test: the capability registry works end-to-end.

Loads the provider + abilities extensions and asks a question the reference
`info` tool answers locally (time/date) — proving auto-discovery, progressive
disclosure (the tool gets surfaced), registration, and execution all work.

On-demand integration test: needs `pi` + a reachable model server; skips
otherwise.

    python -m pytest services/zoe-core/test/test_brick4a_abilities.py -v
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parent.parent
_PROVIDER_EXT = _CORE / "extensions" / "provider-local-gemma.ts"
_ABILITIES_EXT = _CORE / "extensions" / "abilities.ts"
_INFO_ABILITY = _CORE / "abilities" / "info.ts"
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


def _run_pi(prompt: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "pi", "-p",
            "--provider", "local-gemma",
            "--model", _MODEL,
            "-e", str(_PROVIDER_EXT),
            "-e", str(_ABILITIES_EXT),
            "--no-extensions", "--no-skills", "--no-prompt-templates",
            "--no-themes", "--no-context-files", "--no-session", "--thinking", "off",
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.integration
def test_registry_loads_and_info_tool_answers():
    if shutil.which("pi") is None:
        pytest.skip("pi CLI not installed")
    if not _model_server_up():
        pytest.skip(f"model server not reachable at {_BASE_URL}")
    for p in (_ABILITIES_EXT, _INFO_ABILITY):
        assert p.is_file(), f"missing: {p}"

    result = _run_pi("What is today's date? Use your tools, then answer in one short sentence.")
    assert result.returncode == 0, f"pi exited {result.returncode}: {result.stderr}"
    text = result.stdout.strip()
    assert text, "pi returned an empty response"
    # The `info` tool was discovered, surfaced (progressive disclosure), and run:
    # the answer should name the current year (deterministic, from local date()).
    year = str(__import__("datetime").date.today().year)
    assert year in text, f"info tool result not reflected; got: {text!r}"
