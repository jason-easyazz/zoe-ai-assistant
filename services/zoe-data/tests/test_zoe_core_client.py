"""Integration tests for the zoe-core brain client (Pi-RPC full-agent mode).

Exercises run_zoe_core_streaming / run_zoe_core against the real `pi` CLI + local
Gemma, with zoe-data stubbed (no live service, no real side effects). Skips
cleanly when pi / the model server / the extensions aren't present.

    python -m pytest services/zoe-data/tests/test_zoe_core_client.py -v

⚠️ THESE ASSERT LIVE MODEL BEHAVIOUR AND ARE THEREFORE NON-DETERMINISTIC.

`_skip_reason()` skips the whole file unless `pi` is on PATH, the zoe-core
extensions exist, AND the model server answers on :11434. In CI none of that
holds, so these SKIP. On a dev box where the brain is up, they RUN — against
the real Gemma 4 E4B.

That asymmetry has a cost worth knowing before you go bisecting: a test like
`test_tool_action_dispatches` asserts the model *chose* a particular tool for a
particular prompt. A 4B model does not do that deterministically.

Measured 2026-07-20 on the Orin: **2 failures in 14 runs (~14%)**, spread across
full-suite and single-file runs, and 5/5 green when run as a single test. It also
passed alone on both the branch and `main` while the full suite was red — a
signature indistinguishable from a test-isolation leak, which is what makes it
expensive. (It refused to fail during the runs where failure output was being
captured, so the losing intent list was never recorded.)

So if one of these fails intermittently on the Orin while CI stays green, the
first hypothesis should be model nondeterminism, not your diff. Re-run before
investigating. Genuinely deterministic behaviour belongs in a stubbed unit test,
not here.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

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


@pytest.mark.asyncio
async def test_shutdown_workers_force_terminates_after_reset_timeout():
    import zoe_core_client as zc

    class StuckWorker:
        def __init__(self) -> None:
            self.reset_called = False
            self.terminate_called = False

        async def reset(self) -> None:
            self.reset_called = True
            await asyncio.Event().wait()

        async def terminate_now(self) -> None:
            self.terminate_called = True

    worker = StuckWorker()
    async with zc._WORKERS_LOCK:
        zc._WORKERS.clear()
        zc._WORKERS[("jason", "stuck", False)] = worker
    try:
        await zc.shutdown_workers(reset_timeout_s=0.01)
    finally:
        async with zc._WORKERS_LOCK:
            zc._WORKERS.clear()

    assert worker.reset_called is True
    assert worker.terminate_called is True


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
    to agent_end (not stop at the tool-call turn_end) so the answer isn't empty.

    The prompt must be answerable ONLY via web delegation: the old weather-
    phrased prompt became ambiguous once the brain grew a weather tool — the
    live model validly picked either tool, flaking ~1-in-3 on the full-stack
    host (the test skips on GitHub)."""
    s, zc = stub
    answer = await zc.run_zoe_core(
        "Search the web for the latest headlines about the Mars sample return mission.",
        "deleg", "family-admin"
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


# ── Unit tests for _read_turn: tool-activity / thinking sentinels ─────────────
# These feed the verified Pi RPC tool-turn frames (captured live 2026-06-23) into
# _read_turn and assert the __TOOL__/__THINKING__ sentinels surface in order while
# the text stream is untouched. No `pi` binary or model needed.


def _thinking(text, rid="req-1"):
    return {"type": "message_update", "id": rid,
            "assistantMessageEvent": {"type": "thinking", "thinking": text}}


def _toolcall_start(tc_id, name, rid="req-1", content_index=0):
    return {"type": "message_update", "id": rid,
            "assistantMessageEvent": {
                "type": "toolcall_start",
                "contentIndex": content_index,
                "partial": {"content": [
                    {"type": "toolCall", "id": tc_id, "name": name,
                     "arguments": {}, "partialArgs": "", "streamIndex": 0}
                ]},
            }}


def _toolcall_end(rid="req-1"):
    return {"type": "message_update", "id": rid,
            "assistantMessageEvent": {"type": "toolcall_end"}}


def _message_end_toolcall(tc_id, name, arguments, rid="req-1"):
    return {"type": "message_end", "id": rid,
            "message": {"role": "assistant",
                        "content": [{"type": "toolCall", "id": tc_id,
                                     "name": name, "arguments": arguments}]}}


def _tool_exec_start(tc_id, rid="req-1"):
    # tool id rides under toolCallId, NOT top-level id (top-level id is the RPC
    # request id and would be rejected by the request matcher otherwise).
    return {"type": "tool_execution_start", "requestId": rid, "toolCallId": tc_id}


def _tool_exec_end(rid="req-1", tc_id=None, result=None, **extra):
    ev = {"type": "tool_execution_end", "requestId": rid}
    if tc_id is not None:
        ev["toolCallId"] = tc_id
    if result is not None:
        ev["result"] = result
    ev.update(extra)
    return ev


def _parse_tool(sentinel):
    assert sentinel.startswith("__TOOL__:"), sentinel
    return json.loads(sentinel[len("__TOOL__:"):])


@pytest.mark.asyncio
async def test_thinking_emits_sentinel_without_disturbing_text():
    """A thinking frame yields a __THINKING__ sentinel; text deltas stream as-is."""
    out = await _run_read_turn([
        _accept(), _thinking("let me check the list"),
        _delta("Sure"), _delta(", one sec"), _agent_end(),
    ])
    assert out == ["__THINKING__:let me check the list", "Sure", ", one sec"]


@pytest.mark.asyncio
async def test_toolcall_start_emits_start_sentinel():
    """toolcall_start yields a phase=start sentinel reading id/name from the
    partial.content[contentIndex] toolCall block."""
    out = await _run_read_turn([
        _accept(),
        _toolcall_start("tc-1", "list_add"),
        _toolcall_end(),
        _agent_end(),
    ])
    tools = [_parse_tool(s) for s in out if s.startswith("__TOOL__:")]
    assert tools == [{"phase": "start", "id": "tc-1", "name": "list_add"}]


@pytest.mark.asyncio
async def test_message_end_toolcall_emits_args_sentinel_with_full_args():
    """message_end carrying a toolCall block yields a phase=args sentinel with the
    FULL arguments (the cleanest source of complete args)."""
    out = await _run_read_turn([
        _accept(),
        _message_end_toolcall("tc-1", "list_add", {"item": "bread", "list": "shopping"}),
        _agent_end(),
    ])
    tools = [_parse_tool(s) for s in out if s.startswith("__TOOL__:")]
    assert tools == [{
        "phase": "args", "id": "tc-1", "name": "list_add",
        "args": {"item": "bread", "list": "shopping"},
    }]


@pytest.mark.asyncio
async def test_tool_execution_end_emits_result_sentinel():
    """tool_execution_end yields a phase=result sentinel with the result text."""
    out = await _run_read_turn([
        _accept(),
        _toolcall_start("tc-1", "list_add"),
        _tool_exec_end(tc_id="tc-1", result="Added bread."),
        _agent_end(),
    ])
    tools = [_parse_tool(s) for s in out if s.startswith("__TOOL__:")]
    assert tools == [
        {"phase": "start", "id": "tc-1", "name": "list_add"},
        {"phase": "result", "id": "tc-1", "result": "Added bread."},
    ]


@pytest.mark.asyncio
async def test_tool_execution_end_nested_result_content():
    """A nested {result:{content:...}} envelope is unwrapped to a string result."""
    out = await _run_read_turn([
        _accept(),
        _toolcall_start("tc-2", "web_search"),
        _tool_exec_end(tc_id="tc-2", result={"content": "Hermes web result: sunny, 24."}),
        _agent_end(),
    ])
    tools = [_parse_tool(s) for s in out if s.startswith("__TOOL__:")]
    assert tools == [
        {"phase": "start", "id": "tc-2", "name": "web_search"},
        {"phase": "result", "id": "tc-2", "result": "Hermes web result: sunny, 24."},
    ]


@pytest.mark.asyncio
async def test_full_tool_turn_then_text_reply_in_order():
    """End-to-end tool turn: thinking → start → end → (message_end args) →
    tool_execution_end result → then a SECOND turn streams the text reply. The
    bare turn_end after the tool turn must NOT terminate; the text still streams."""
    out = await _run_read_turn([
        _accept(),
        _thinking("checking the weather"),
        _toolcall_start("tc-9", "web_search"),
        _toolcall_end(),
        _message_end_toolcall("tc-9", "web_search", {"query": "weekend weather"}),
        _tool_exec_start("tc-9"),
        _tool_exec_end(tc_id="tc-9", result="Saturday is sunny, 24 degrees."),
        {"type": "turn_end", "id": "req-1"},  # bare turn_end — must NOT terminate
        _delta("It'll be"), _delta(" sunny, 24°."),
        _agent_end(),
    ])
    # Text deltas survive unchanged.
    text = [c for c in out if not c.startswith("__TOOL__:") and not c.startswith("__THINKING__:")]
    assert text == ["It'll be", " sunny, 24°."]
    # Tool/thinking sentinels surface in capture order.
    thinking = [c for c in out if c.startswith("__THINKING__:")]
    assert thinking == ["__THINKING__:checking the weather"]
    phases = [_parse_tool(c)["phase"] for c in out if c.startswith("__TOOL__:")]
    assert phases == ["start", "args", "result"]


@pytest.mark.asyncio
async def test_malformed_tool_frames_never_crash_and_skip_sentinel():
    """Defensive: a toolcall_start missing its content / a message_end with no
    toolCall block / a tool_execution_end with nothing useful emit NO sentinel
    but never crash the turn — text still streams."""
    out = await _run_read_turn([
        _accept(),
        {"type": "message_update", "id": "req-1",
         "assistantMessageEvent": {"type": "toolcall_start"}},  # no partial/content
        {"type": "message_end", "id": "req-1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}},
        _tool_exec_end(),  # no id, no result
        _delta("ok"),
        _agent_end(),
    ])
    assert [c for c in out if c.startswith("__TOOL__:")] == []
    assert "ok" in out


# ── Unit tests for _read_turn: idle-timeout vs. a slow outstanding tool (P1-B) ─
# A slow tool (web search / deep research ~60s / CloakBrowser) produces a long
# stdout gap with NO events AFTER a turn_end. The old reader applied the idle
# timeout during that gap, returned the pre-tool fragment as a "complete" answer,
# and left the worker mid-generation. These tests drive _read_turn against a
# stream that delivers events with REAL async delays so the idle window can elapse
# under a shrunk _IDLE_TIMEOUT_S — no `pi` binary or model needed.


async def _run_read_turn_streamed(batches, *, request_id="req-1", timeout_s=5.0):
    """Drive _read_turn while feeding the RPC stream in timed batches.

    `batches` is a list of (delay_s, [events]); each batch is fed after sleeping
    `delay_s`, concurrently with _read_turn, so genuine idle gaps elapse between
    events. EOF is fed after the last batch. Returns the list of yielded deltas.
    """
    import types

    import zoe_core_client as zc

    reader = asyncio.StreamReader()

    async def _feeder():
        for delay, events in batches:
            if delay:
                await asyncio.sleep(delay)
            for ev in events:
                reader.feed_data((json.dumps(ev) + "\n").encode())
        reader.feed_eof()

    worker = zc._ZoeCoreWorker.__new__(zc._ZoeCoreWorker)
    worker.proc = types.SimpleNamespace(stdout=reader, stdin=None)
    feed_task = asyncio.create_task(_feeder())
    try:
        return [delta async for delta in worker._read_turn(request_id, timeout_s)]
    finally:
        feed_task.cancel()
        try:
            await feed_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_slow_tool_gap_does_not_truncate(monkeypatch):
    """A >idle-window gap WHILE a tool is outstanding must NOT trigger the idle
    timeout: the turn waits out the slow tool and streams the full answer.

    Without the fix the idle timeout fires during the gap (streamed_any +
    saw_turn_end) and _read_turn returns the pre-tool fragment as complete."""
    import zoe_core_client as zc
    monkeypatch.setattr(zc, "_IDLE_TIMEOUT_S", 0.05)

    out = await _run_read_turn_streamed(
        [
            (0.0, [
                _accept(),
                _delta("Let me search"),
                _toolcall_start("tc-1", "web_search"),
                _message_end_toolcall("tc-1", "web_search", {"query": "weekend weather"}),
                {"type": "turn_end", "id": "req-1"},
            ]),
            # Tool runs far longer than the idle window before any event resumes.
            (0.30, [
                _tool_exec_end(tc_id="tc-1", result="Saturday is sunny, 24 degrees."),
                {"type": "message_start", "id": "req-1"},
                _delta("It'll be"),
                _delta(" sunny, 24°."),
                _agent_end(),
            ]),
        ],
        timeout_s=5.0,
    )
    text = [c for c in out if not c.startswith("__TOOL__:") and not c.startswith("__THINKING__:")]
    # The full answer streamed — the pre-tool fragment was NOT returned as complete.
    assert text == ["Let me search", "It'll be", " sunny, 24°."]


@pytest.mark.asyncio
async def test_duplicate_tool_end_does_not_clear_other_pending_tool(monkeypatch):
    """A duplicated end for one tool must not clear a different slow tool.

    The scalar counter bug decremented on every tool_execution_end: start slow +
    start fast + fast end + duplicate fast end dropped the count to zero, so the
    idle window returned the pre-tool fragment while the slow tool was still
    running."""
    import zoe_core_client as zc
    monkeypatch.setattr(zc, "_IDLE_TIMEOUT_S", 0.05)

    out = await _run_read_turn_streamed(
        [
            (0.0, [
                _accept(),
                _delta("Let me check"),
                _toolcall_start("tc-slow", "web_search"),
                _toolcall_start("tc-fast", "calendar_lookup"),
                _tool_exec_end(tc_id="tc-fast", result="Calendar ready."),
                {"type": "turn_end", "id": "req-1"},
                _tool_exec_end(tc_id="tc-fast", result="Duplicate calendar end."),
            ]),
            (0.30, [
                _tool_exec_end(tc_id="tc-slow", result="Saturday is sunny."),
                {"type": "message_start", "id": "req-1"},
                _delta("Saturday looks sunny."),
                _agent_end(),
            ]),
        ],
        timeout_s=5.0,
    )
    text = [c for c in out if not c.startswith("__TOOL__:") and not c.startswith("__THINKING__:")]
    assert text == ["Let me check", "Saturday looks sunny."]


@pytest.mark.asyncio
async def test_idless_stray_tool_end_does_not_clear_pending_tool(monkeypatch):
    """An id-less/name-less stray end must not zero a real matched tool."""
    import zoe_core_client as zc
    monkeypatch.setattr(zc, "_IDLE_TIMEOUT_S", 0.05)

    out = await _run_read_turn_streamed(
        [
            (0.0, [
                _accept(),
                _delta("Let me search"),
                _toolcall_start("tc-1", "web_search"),
                {"type": "turn_end", "id": "req-1"},
                _tool_exec_end(result="stray result without id or name"),
            ]),
            (0.30, [
                _tool_exec_end(tc_id="tc-1", result="Saturday is sunny."),
                {"type": "message_start", "id": "req-1"},
                _delta("It'll be sunny."),
                _agent_end(),
            ]),
        ],
        timeout_s=5.0,
    )
    text = [c for c in out if not c.startswith("__TOOL__:") and not c.startswith("__THINKING__:")]
    assert text == ["Let me search", "It'll be sunny."]


@pytest.mark.asyncio
async def test_wrong_named_tool_end_does_not_clear_pending_tool(monkeypatch):
    """An id-less end naming a different tool must not match the outstanding start."""
    import zoe_core_client as zc
    monkeypatch.setattr(zc, "_IDLE_TIMEOUT_S", 0.05)

    out = await _run_read_turn_streamed(
        [
            (0.0, [
                _accept(),
                _delta("Let me search"),
                _toolcall_start("tc-1", "web_search"),
                {"type": "turn_end", "id": "req-1"},
                _tool_exec_end(result="wrong tool done", name="calendar_lookup"),
            ]),
            (0.30, [
                _tool_exec_end(tc_id="tc-1", result="Saturday is sunny."),
                {"type": "message_start", "id": "req-1"},
                _delta("The weather is sunny."),
                _agent_end(),
            ]),
        ],
        timeout_s=5.0,
    )
    text = [c for c in out if not c.startswith("__TOOL__:") and not c.startswith("__THINKING__:")]
    assert text == ["Let me search", "The weather is sunny."]


@pytest.mark.asyncio
async def test_idless_tool_start_matches_end_by_name_and_completes(monkeypatch):
    """When a start has no id, fall back to matching the outstanding tool by name."""
    import zoe_core_client as zc
    monkeypatch.setattr(zc, "_IDLE_TIMEOUT_S", 0.05)

    out = await _run_read_turn_streamed(
        [
            (0.0, [
                _accept(),
                _delta("Let me search"),
                _toolcall_start(None, "web_search"),
                {"type": "turn_end", "id": "req-1"},
            ]),
            (0.30, [
                _tool_exec_end(result="Saturday is sunny.", name="web_search"),
                {"type": "message_start", "id": "req-1"},
                _delta("It'll be sunny."),
                _agent_end(),
            ]),
        ],
        timeout_s=5.0,
    )
    text = [c for c in out if not c.startswith("__TOOL__:") and not c.startswith("__THINKING__:")]
    assert text == ["Let me search", "It'll be sunny."]


@pytest.mark.asyncio
async def test_idle_timeout_with_pending_tool_raises_not_truncates(monkeypatch):
    """If the deadline is reached while a tool is STILL outstanding, _read_turn
    raises (so stream() resets the worker) rather than silently returning the
    pre-tool fragment as a complete reply."""
    import zoe_core_client as zc
    monkeypatch.setattr(zc, "_IDLE_TIMEOUT_S", 0.05)

    with pytest.raises(asyncio.TimeoutError):
        # Tool starts + turn_end, then the stream goes silent forever. Deadline is
        # short; the idle window (0.05) is shorter still — but must NOT be applied
        # because a tool is pending, so we hit the real deadline and raise.
        await _run_read_turn_streamed(
            [
                (0.0, [
                    _accept(),
                    _delta("Let me search"),
                    _toolcall_start("tc-1", "web_search"),
                    {"type": "turn_end", "id": "req-1"},
                ]),
                (5.0, []),  # never delivers the tool result within the deadline
            ],
            timeout_s=0.25,
        )


@pytest.mark.asyncio
async def test_idle_timeout_still_applies_with_no_pending_tool(monkeypatch):
    """Safety valve intact: with NO tool outstanding, an idle gap after an answer
    + turn_end still returns (agent_end presumed lost) instead of hanging."""
    import zoe_core_client as zc
    monkeypatch.setattr(zc, "_IDLE_TIMEOUT_S", 0.05)

    out = await _run_read_turn_streamed(
        [
            (0.0, [
                _accept(),
                _delta("All done"),
                {"type": "turn_end", "id": "req-1"},
            ]),
            # No tool pending and no agent_end — idle window elapses → return.
            (5.0, [_agent_end()]),  # arrives long after the idle window
        ],
        timeout_s=2.0,
    )
    assert out == ["All done"]


@pytest.mark.asyncio
async def test_malformed_toolcall_start_does_not_block_idle(monkeypatch):
    """A malformed toolcall_start (no valid toolCall block) must NOT count as an
    outstanding tool: it has no matching tool_execution_end, so counting it would
    leave the turn permanently non-idle-eligible — after turn_end the user would
    wait out the FULL deadline and get a reset instead of the short idle
    completion. The frame is validated BEFORE tools_outstanding is bumped, so the
    turn still goes idle."""
    import zoe_core_client as zc
    monkeypatch.setattr(zc, "_IDLE_TIMEOUT_S", 0.05)

    out = await _run_read_turn_streamed(
        [
            (0.0, [
                _accept(),
                _delta("All done"),
                # Malformed: no partial/content, so _toolcall_block_from_amev
                # rejects it. Must not increment tools_outstanding.
                {"type": "message_update", "id": "req-1",
                 "assistantMessageEvent": {"type": "toolcall_start"}},
                {"type": "turn_end", "id": "req-1"},
            ]),
            # Stream then goes silent: with no REAL tool pending the idle window
            # (0.05s) must elapse and return — not run out the 2s deadline and
            # raise (which is what a phantom pending tool would cause).
            (5.0, [_agent_end()]),
        ],
        timeout_s=2.0,
    )
    assert out == ["All done"]


async def test_unnamed_toolcall_start_matches_end_by_id(monkeypatch):
    """A toolCall block WITH an id but WITHOUT a usable name is still trackable by
    id. It must stay outstanding across the idle window and complete only when the
    matching tool_execution_end arrives."""
    import zoe_core_client as zc
    monkeypatch.setattr(zc, "_IDLE_TIMEOUT_S", 0.05)

    out = await _run_read_turn_streamed(
        [
            (0.0, [
                _accept(),
                _delta("Let me check"),
                # Valid-shaped block but name missing: accepted by the parser and
                # tracked by id. The start sentinel remains suppressed because chat
                # tool UI needs both id and name.
                {"type": "message_update", "id": "req-1",
                 "assistantMessageEvent": {
                     "type": "toolcall_start",
                     "contentIndex": 0,
                     "partial": {"content": [
                         {"type": "toolCall", "id": "tc-noname",
                          "arguments": {}, "partialArgs": "", "streamIndex": 0}
                     ]},
                 }},
                {"type": "turn_end", "id": "req-1"},
            ]),
            (0.30, [
                _tool_exec_end(tc_id="tc-noname", result="Done."),
                {"type": "message_start", "id": "req-1"},
                _delta("Done."),
                _agent_end(),
            ]),
        ],
        timeout_s=5.0,
    )
    assert [c for c in out if not c.startswith("__TOOL__:")] == ["Let me check", "Done."]
    assert [_parse_tool(c)["phase"] for c in out if c.startswith("__TOOL__:")] == ["result"]
