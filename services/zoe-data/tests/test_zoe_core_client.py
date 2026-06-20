"""Integration tests for the zoe-core brain client (Pi-RPC full-agent mode).

Exercises run_zoe_core_streaming / run_zoe_core against the real `pi` CLI + local
Gemma, with zoe-data stubbed (no live service, no real side effects). Skips
cleanly when pi / the model server / the extensions aren't present.

    python -m pytest services/zoe-data/tests/test_zoe_core_client.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

_CORE = Path(__file__).resolve().parents[2] / "zoe-core"
_EXTS = [_CORE / "extensions" / n for n in
         ("provider-local-gemma.ts", "soul.ts", "memory.ts", "abilities.ts")]
_SOUL = _CORE / "SOUL.md"
_BASE_URL = (os.environ.get("ZOE_CORE_MODEL_URL") or os.environ.get("GEMMA_SERVER_URL")
             or "http://127.0.0.1:11434/v1")


def _skip_reason() -> str | None:
    if shutil.which(os.environ.get("ZOE_CORE_PI_COMMAND", "pi")) is None:
        return "pi not on PATH"
    missing = [str(p) for p in (*_EXTS, _SOUL) if not p.exists()]
    if missing:
        return f"not present: {', '.join(missing)}"
    try:
        with urllib.request.urlopen(f"{_BASE_URL}/models", timeout=4) as r:
            if r.status != 200:
                return f"model server {r.status}"
    except Exception:
        return f"model server unreachable at {_BASE_URL}"
    return None


_SKIP = _skip_reason()
requires_env = pytest.mark.skipif(_SKIP is not None, reason=_SKIP or "")


class _Stub:
    def __init__(self) -> None:
        self.packet: dict[str, Any] = {"packet": "", "refs": [], "count": 0, "user_scoped": True}
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        state = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:
                return

            def _json(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                with state._lock:
                    state.requests.append({"m": "GET", "path": self.path})
                self._json(state.packet if "for-prompt" in self.path else {"ok": True})

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length", 0) or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except Exception:
                    body = {}
                with state._lock:
                    state.requests.append({"m": "POST", "path": self.path, "body": body})
                if "intent-dispatch" in self.path:
                    item = (body.get("slots") or {}).get("item", "it")
                    self._json({"intent": body.get("intent"), "ok": True, "result": f"Added {item}."})
                elif "delegate-sync" in self.path:
                    self._json({"target": "hermes", "ok": True,
                                "result": "Hermes web result: Saturday is sunny, 24 degrees."})
                else:
                    self._json({"ok": True})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def dispatches(self) -> list[dict[str, Any]]:
        with self._lock:
            return [r["body"] for r in self.requests if r["m"] == "POST" and "intent-dispatch" in r["path"]]

    def delegations(self) -> list[dict[str, Any]]:
        with self._lock:
            return [r["body"] for r in self.requests if r["m"] == "POST" and "delegate-sync" in r["path"]]

    def memory_hits(self) -> int:
        with self._lock:
            return sum(1 for r in self.requests if r["m"] == "GET" and "for-prompt" in r["path"])


import pytest_asyncio


@pytest_asyncio.fixture
async def stub(monkeypatch):
    s = _Stub()
    # Point the brain client at the stub before importing/using it.
    monkeypatch.setenv("ZOE_DATA_URL", s.url)
    monkeypatch.setenv("ZOE_CORE_DATA_URL", s.url)
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "test")
    import importlib
    import zoe_core_client
    importlib.reload(zoe_core_client)
    try:
        yield s, zoe_core_client
    finally:
        await zoe_core_client.shutdown_workers()
        s.stop()


def test_data_url_read_lazily_and_defaults_to_8011(monkeypatch):
    """Not an integration test: guards the bootstrap-timing fix. The data URL
    must be read at call time (so a .env value bootstrapped after import is
    honored) and default to loopback :8011 (live zoe-data), never the old :8000."""
    import zoe_core_client as zc
    monkeypatch.delenv("ZOE_CORE_DATA_URL", raising=False)
    monkeypatch.delenv("ZOE_DATA_URL", raising=False)
    assert zc._data_url() == "http://127.0.0.1:8011"
    monkeypatch.setenv("ZOE_CORE_DATA_URL", "http://127.0.0.1:9999")
    assert zc._data_url() == "http://127.0.0.1:9999"


@pytest.mark.integration
@requires_env
@pytest.mark.asyncio
async def test_identity_streams_as_zoe(stub):
    _s, zc = stub
    text = ""
    n_chunks = 0
    async for d in zc.run_zoe_core_streaming("Who are you? One short sentence.", "s1", "family-admin"):
        text += d
        n_chunks += 1
    assert "zoe" in text.lower(), text
    assert n_chunks >= 1


@pytest.mark.integration
@requires_env
@pytest.mark.asyncio
async def test_web_query_delegates_and_synthesizes(stub):
    """A web/research query delegates to Hermes AND folds the result into a
    spoken answer. Guards the post-tool-synthesis fix: the agent loop must run
    to agent_end (not stop at the tool-call turn_end) so the answer isn't empty."""
    s, zc = stub
    answer = await zc.run_zoe_core(
        "Search the web for this weekend's weather forecast.", "deleg", "family-admin"
    )
    assert len(s.delegations()) >= 1, f"did not delegate; requests={s.requests}"
    assert answer.strip(), "delegated but produced no synthesized answer (post-tool synthesis broken)"


@pytest.mark.integration
@requires_env
@pytest.mark.asyncio
async def test_tool_action_dispatches(stub):
    s, zc = stub
    await zc.run_zoe_core("Add bread to my shopping list.", "s2", "family-admin")
    intents = [d.get("intent") for d in s.dispatches()]
    assert "list_add" in intents, f"no list_add; got {intents}"


@pytest.mark.integration
@requires_env
@pytest.mark.asyncio
async def test_memory_recall_uses_packet(stub):
    s, zc = stub
    s.packet = {"packet": "## What I know about you\n- Jason's dog is named Pixel [mem:t1]",
                "count": 1, "user_scoped": True}
    text = await zc.run_zoe_core("What's my dog's name?", "s3", "family-admin")
    assert s.memory_hits() >= 1, "memory packet not fetched"
    assert "pixel" in text.lower(), text


@pytest.mark.integration
@requires_env
@pytest.mark.asyncio
async def test_warm_reuse_same_session(stub):
    _s, zc = stub
    await zc.run_zoe_core("Say hi.", "warm", "family-admin")
    # Second turn reuses the warm worker (no new process); just assert it answers.
    out = await zc.run_zoe_core("Say bye.", "warm", "family-admin")
    assert out


@pytest.mark.integration
@requires_env
@pytest.mark.asyncio
async def test_unknown_user_fails_closed(stub):
    s, zc = stub
    # Empty user -> memory extension must not fetch any packet (PR #692 guarantee).
    await zc.run_zoe_core("What's my dog's name?", "guest-sess", "")
    assert s.memory_hits() == 0, f"memory fetched for unknown user: {s.requests}"


# ── Unit tests for _read_turn: the text_delta streaming path ──────────────────
# These need no `pi` binary or model — they feed a synthetic Pi RPC event stream
# straight into _read_turn, so they run in CI (unlike the @requires_env tests).

async def _run_read_turn(events: list[dict], request_id: str = "req-1", timeout_s: float = 5.0):
    """Drive _ZoeCoreWorker._read_turn against a canned Pi RPC event stream and
    return the list of yielded deltas."""
    import types

    import zoe_core_client as zc

    reader = asyncio.StreamReader()
    for ev in events:
        reader.feed_data((json.dumps(ev) + "\n").encode())
    reader.feed_eof()

    # Bypass __init__ (which would set up env/locks) — _read_turn only needs .proc.
    worker = zc._ZoeCoreWorker.__new__(zc._ZoeCoreWorker)
    worker.proc = types.SimpleNamespace(stdout=reader, stdin=None)
    return [delta async for delta in worker._read_turn(request_id, timeout_s)]


def _accept(rid="req-1"):
    return {"type": "response", "command": "prompt", "id": rid, "success": True}


def _delta(text, rid="req-1"):
    return {"type": "message_update", "id": rid,
            "assistantMessageEvent": {"type": "text_delta", "delta": text}}


def _agent_end(rid="req-1", **extra):
    return {"type": "agent_end", "id": rid, **extra}


@pytest.mark.asyncio
async def test_text_delta_streams_incrementally():
    """Each text_delta is yielded as it arrives, not buffered until the end."""
    out = await _run_read_turn([
        _accept(), _delta("Hello"), _delta(" there"), _delta(" friend"), _agent_end(),
    ])
    assert out == ["Hello", " there", " friend"]


@pytest.mark.asyncio
async def test_no_double_emit_when_delta_event_also_carries_message():
    """A message_update carrying BOTH a text_delta and a full message field must
    emit only the delta — the `continue` guard stops the message-field path from
    re-emitting (the 'YouYou' double-emit)."""
    out = await _run_read_turn([
        _accept(),
        {"type": "message_update", "id": "req-1",
         "assistantMessageEvent": {"type": "text_delta", "delta": "Hi"},
         "message": {"role": "assistant", "content": "Hi there"}},
        _agent_end(),
    ])
    assert out == ["Hi"]


@pytest.mark.asyncio
async def test_agent_end_terminates_and_ignores_trailing_events():
    """agent_end ends the turn cleanly; anything after it is never read."""
    out = await _run_read_turn([
        _accept(), _delta("Done"), _agent_end(), _delta("ghost"),
    ])
    assert out == ["Done"]


@pytest.mark.asyncio
async def test_terminal_event_carrying_assistant_field_still_terminates():
    """Regression guard for the fix: an agent_end that also carries an
    assistantMessageEvent must still terminate (etype gate) instead of being
    swallowed by the delta-handling `continue` and hanging the turn."""
    out = await _run_read_turn([
        _accept(),
        _delta("Answer"),
        _agent_end(assistantMessageEvent={"type": "text_delta", "delta": "x"}),
    ])
    assert out == ["Answer"]  # terminates cleanly; the stray "x" is not emitted
