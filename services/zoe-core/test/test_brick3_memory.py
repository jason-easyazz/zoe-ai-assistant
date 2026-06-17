"""Brick 3 integration smoke test: memory packet reaches the Pi brain.

Stands up a tiny stub of zoe-data's /api/memories/for-prompt that returns a
distinctive fact, runs Pi with the provider + memory extensions pointed at the
stub, and asserts the model's answer reflects the injected memory — proving the
fetch → inject path works end to end.

On-demand integration test: needs `pi` + a reachable model server; skips
otherwise.

    python -m pytest services/zoe-core/test/test_brick3_memory.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parent.parent
_PROVIDER_EXT = _CORE / "extensions" / "provider-local-gemma.ts"
_MEMORY_EXT = _CORE / "extensions" / "memory.ts"
_MODEL = os.environ.get("ZOE_CORE_MODEL_ID", "gemma-4-E2B-it-Q4_K_M.gguf")
_BASE_URL = (
    os.environ.get("ZOE_CORE_MODEL_URL")
    or os.environ.get("GEMMA_SERVER_URL")
    or "http://127.0.0.1:11434/v1"
)
_PACKET = "## What I know about you\n- Jason's dog is named Pixel [mem:test0001]"


def _model_server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{_BASE_URL}/models", timeout=4) as resp:
            return resp.status == 200
    except Exception:
        return False


class _MemoryStub(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/memories/for-prompt"):
            body = json.dumps(
                {"packet": _PACKET, "refs": [], "count": 1, "user_scoped": True}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args):  # silence
        pass


@pytest.mark.integration
def test_memory_packet_reaches_the_brain():
    if shutil.which("pi") is None:
        pytest.skip("pi CLI not installed")
    if not _model_server_up():
        pytest.skip(f"model server not reachable at {_BASE_URL}")
    assert _MEMORY_EXT.is_file()

    server = HTTPServer(("127.0.0.1", 0), _MemoryStub)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = {
            **os.environ,
            "ZOE_DATA_URL": f"http://127.0.0.1:{port}",
            "ZOE_CORE_USER_ID": "family-admin",
            "ZOE_INTERNAL_TOKEN": "test",
        }
        result = subprocess.run(
            [
                "pi", "-p",
                "--provider", "local-gemma",
                "--model", _MODEL,
                "-e", str(_PROVIDER_EXT),
                "-e", str(_MEMORY_EXT),
                "--no-extensions", "--no-skills", "--no-prompt-templates",
                "--no-themes", "--no-context-files", "--no-session", "--thinking", "off",
                "What is my dog's name? Answer with just the name.",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        server.shutdown()

    assert result.returncode == 0, f"pi exited {result.returncode}: {result.stderr}"
    text = result.stdout.strip()
    assert text, "pi returned an empty response"
    # The injected memory packet said the dog is "Pixel" — the brain should use it.
    assert "pixel" in text.lower(), f"memory not used; got: {text!r}"
