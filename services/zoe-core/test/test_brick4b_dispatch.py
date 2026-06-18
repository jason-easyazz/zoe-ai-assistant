"""Brick 4b e2e: a real Gemma turn flows through a domain tool to the right intent.

Stubs /api/system/intent-dispatch, runs Pi with the provider + abilities
registry, sends "add milk to my shopping list" (writes enabled), and asserts the
stub received intent=list_add with item=milk — proving discovery → progressive
disclosure → permission gate → tool execute → dispatch end to end.

On-demand integration test; skips without pi/model server.

    python -m pytest services/zoe-core/test/test_brick4b_dispatch.py -v
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
_ABILITIES_EXT = _CORE / "extensions" / "abilities.ts"
_MODEL = os.environ.get("ZOE_CORE_MODEL_ID", "gemma-4-E2B-it-Q4_K_M.gguf")
_BASE_URL = (
    os.environ.get("ZOE_CORE_MODEL_URL")
    or os.environ.get("GEMMA_SERVER_URL")
    or "http://127.0.0.1:11434/v1"
)
_captured: list[dict] = []


def _model_server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{_BASE_URL}/models", timeout=4) as resp:
            return resp.status == 200
    except Exception:
        return False


class _DispatchStub(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            body = {}
        _captured.append(body)
        item = (body.get("slots") or {}).get("item", "it")
        payload = json.dumps(
            {"intent": body.get("intent"), "ok": True, "result": f"Added {item} to your shopping list."}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_a):
        pass


@pytest.mark.integration
def test_list_add_flows_through_to_intent():
    if shutil.which("pi") is None:
        pytest.skip("pi CLI not installed")
    if not _model_server_up():
        pytest.skip(f"model server not reachable at {_BASE_URL}")
    _captured.clear()

    server = HTTPServer(("127.0.0.1", 0), _DispatchStub)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        env = {
            **os.environ,
            "ZOE_DATA_URL": f"http://127.0.0.1:{port}",
            "ZOE_CORE_USER_ID": "family-admin",
            "ZOE_INTERNAL_TOKEN": "test",
            "ZOE_CORE_ALLOW_WRITES": "true",
        }
        result = subprocess.run(
            [
                "pi", "-p",
                "--provider", "local-gemma",
                "--model", _MODEL,
                "-e", str(_PROVIDER_EXT),
                "-e", str(_ABILITIES_EXT),
                "--no-extensions", "--no-skills", "--no-prompt-templates",
                "--no-themes", "--no-context-files", "--no-session", "--thinking", "off",
                "Add milk to my shopping list.",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    finally:
        server.shutdown()

    assert result.returncode == 0, f"pi exited {result.returncode}: {result.stderr}"
    assert _captured, f"no tool dispatched; pi said: {result.stdout!r}"
    intents = [c.get("intent") for c in _captured]
    assert "list_add" in intents, f"expected list_add, dispatched: {intents}"
    add = next(c for c in _captured if c.get("intent") == "list_add")
    assert "milk" in json.dumps(add.get("slots", {})).lower(), f"item not milk: {add}"
