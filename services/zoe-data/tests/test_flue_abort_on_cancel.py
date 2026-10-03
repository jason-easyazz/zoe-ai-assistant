"""A1: abort the Flue turn when its consumer goes away (ZOE_FLUE_ABORT_ON_CANCEL).

OFF = today's request bytes, no abort on any cancel path. ON = the opt-in header
is sent and every cancel path (aclose / task cancel; chat, voice, speculative,
shutdown) fires ONE guarded abort naming the echoed submission, never raising; a
finished turn is never aborted; no echo = no (unguarded) abort; the #1781 claim
still settles on emitted text.
"""
import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

import async_subprocess
import zoe_flue_client as flue

SUB = "sub_01TESTSUBMISSION"


class _Client:
    """httpx.AsyncClient double: one scripted NDJSON stream + the abort POST."""

    lines: list = []
    hang, submission, post_raises = True, SUB, None
    streams: list = []
    posts: list = []
    status_code = 200

    def __init__(self, *a, **k):
        self.headers = {"content-type": "application/x-ndjson"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def stream(self, method, url, content=b"", headers=None):
        _Client.streams.append((url, dict(headers or {})))
        if _Client.submission:
            self.headers["x-flue-submission-id"] = _Client.submission
        return self

    async def aiter_lines(self):
        for line in _Client.lines:
            yield line
        if _Client.hang:
            await asyncio.Event().wait()  # the model is still generating

    async def post(self, url, headers=None, **_):
        _Client.posts.append((url, dict(headers or {})))
        if _Client.post_raises:
            raise _Client.post_raises
        return SimpleNamespace(status_code=200, json=lambda: {"outcome": "requested"})


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3579")
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "sekret")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "1")
    monkeypatch.delenv("ZOE_FLUE_WIRE", raising=False)
    monkeypatch.delenv("ZOE_FLUE_ABORT_ON_CANCEL", raising=False)

    async def _no_block(*a, **k):
        return ""

    monkeypatch.setattr(flue, "_recall_context_block", _no_block)
    monkeypatch.setattr(flue, "_continuity_context_block", _no_block)
    monkeypatch.setattr(flue, "_pending_offer_block", _no_block)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    _Client.lines = [json.dumps("__TOOL__:{}"), json.dumps("Hello "), json.dumps("there")]
    _Client.hang, _Client.submission, _Client.post_raises = True, SUB, None
    _Client.streams, _Client.posts = [], []
    return _Client


async def _drain_abort_tasks():
    for _ in range(50):
        if not flue._ABORT_TASKS:
            return
        await asyncio.sleep(0)
    await asyncio.gather(*list(flue._ABORT_TASKS), return_exceptions=True)


async def _aclose_after_text(**kwargs):
    stream = flue.run_flue_brain_streaming("hi", "sess-1", "jason", **kwargs)
    got = [await stream.__anext__(), await stream.__anext__()]
    await stream.aclose()
    await _drain_abort_tasks()
    return got


async def _cancel_after_text(**kwargs):
    seen = asyncio.Event()

    async def consume():
        async for delta in flue.run_flue_brain_streaming("hi", "sess-1", "jason", **kwargs):
            if delta == "Hello ":
                seen.set()

    task = asyncio.ensure_future(consume())
    await seen.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await _drain_abort_tasks()


# ── flag OFF: byte-identical, never aborts ───────────────────────────────────
@pytest.mark.parametrize("path", ["aclose", "cancel"])
async def test_flag_off_sends_todays_headers_and_never_aborts(env, path):
    await (_aclose_after_text() if path == "aclose" else _cancel_after_text())
    (_, headers), = env.streams
    assert headers == {**flue._headers(), "Accept": "application/x-ndjson"}
    assert env.posts == []
    assert not flue._ABORT_TASKS


async def test_flag_is_read_at_request_time(env, monkeypatch):
    """Per-call read: the value at REQUEST time decides, a flip mid-turn does not."""
    stream = flue.run_flue_brain_streaming("hi", "sess-1", "jason")
    await stream.__anext__()
    monkeypatch.setenv("ZOE_FLUE_ABORT_ON_CANCEL", "1")
    await stream.aclose()
    await _drain_abort_tasks()
    assert env.posts == []


# ── flag ON: every cancel path aborts once, guarded ──────────────────────────
@pytest.mark.parametrize("path,voice,reason,exit_cls,emitted", [
    # aclose after "Hello "; the cancelled task had already consumed "there" too.
    ("aclose", False, "disconnect", "GeneratorExit", "emitted_chars=6 deltas=1"),
    ("aclose", True, "barge_in", "GeneratorExit", "emitted_chars=6 deltas=1"),
    ("cancel", False, "disconnect", "CancelledError", "emitted_chars=11 deltas=2"),
    ("cancel", True, "barge_in", "CancelledError", "emitted_chars=11 deltas=2"),
])
async def test_flag_on_aborts_the_named_submission(
    env, monkeypatch, caplog, path, voice, reason, exit_cls, emitted,
):
    monkeypatch.setenv("ZOE_FLUE_ABORT_ON_CANCEL", "1")
    caplog.set_level(logging.INFO, logger="zoe_flue_client")
    kw = {"voice_mode": True} if voice else {}
    await (_aclose_after_text(**kw) if path == "aclose" else _cancel_after_text(**kw))
    (_, headers), = env.streams
    assert headers["x-zoe-abort-on-cancel"] == "1"
    (url, abort_headers), = env.posts
    assert url == "http://127.0.0.1:3579/agents/zoe/sess-1/abort"
    assert abort_headers["x-zoe-abort-submission"] == SUB
    assert abort_headers["x-zoe-abort-reason"] == reason
    assert abort_headers["Authorization"] == "Bearer sekret"
    line, = [r.getMessage() for r in caplog.records if "FLUE_ABORT" in r.getMessage()]
    assert f"reason={reason} exit={exit_cls}" in line
    # Only reply text counts toward what A3 may keep — never the tool sentinel.
    assert f"{emitted} outcome=requested" in line


@pytest.mark.parametrize("cause", ["speculative", "shutdown"])
async def test_speculative_and_shutdown_cancels_are_labelled(env, monkeypatch, cause):
    import voice_speculation as vs

    monkeypatch.setenv("ZOE_FLUE_ABORT_ON_CANCEL", "1")
    token = None
    if cause == "speculative":
        gate = vs.SpeculationGate("t-1", max_hold_s=5)
        gate.resolve("cancel")
        token = vs.bind(gate)
    else:
        monkeypatch.setattr(async_subprocess, "shutting_down", lambda: True)
    try:
        await _cancel_after_text(voice_mode=True)
    finally:
        if token is not None:
            vs.unbind(token)
    assert env.posts[0][1]["x-zoe-abort-reason"] == cause


async def test_finished_turn_is_never_aborted(env, monkeypatch):
    monkeypatch.setenv("ZOE_FLUE_ABORT_ON_CANCEL", "1")
    env.lines = [json.dumps("Hello "), json.dumps({"done": True})]
    env.hang = False
    out = [d async for d in flue.run_flue_brain_streaming("hi", "sess-1", "jason")]
    await _drain_abort_tasks()
    assert out == ["Hello "]
    assert env.posts == []


@pytest.mark.parametrize("case,outcome", [
    # An older sidecar echoes no submission: an instance-wide abort could kill the
    # NEXT turn, so nothing is sent — and the log says so.
    ("no_echo", "skipped:no_submission_id"),
    ("post_fails", "error:OSError"),
])
async def test_abort_is_skipped_or_fails_quietly(env, monkeypatch, caplog, case, outcome):
    monkeypatch.setenv("ZOE_FLUE_ABORT_ON_CANCEL", "1")
    caplog.set_level(logging.INFO, logger="zoe_flue_client")
    if case == "no_echo":
        env.submission = None
    else:
        env.post_raises = OSError("sidecar gone")
    await _cancel_after_text()  # never raises anything but the cancel itself
    assert len(env.posts) == (0 if case == "no_echo" else 1)
    assert any(f"outcome={outcome}" in r.getMessage() for r in caplog.records)


# ── #1781: the brief/raise claim still settles on emitted text ───────────────
@pytest.mark.parametrize("flag", ["0", "1"])
async def test_brief_settles_on_emitted_text_with_and_without_abort(env, monkeypatch, flag):
    import brief_first_turn

    monkeypatch.setenv("ZOE_FLUE_ABORT_ON_CANCEL", flag)
    settled = []

    async def prepare(message, user_id, session_id=""):   # main's signature since #1801
        return SimpleNamespace(block="[Today …]")

    async def settle(brief, *, produced):
        settled.append(produced)

    monkeypatch.setattr(brief_first_turn, "prepare", prepare)
    monkeypatch.setattr(brief_first_turn, "settle", settle)
    await _aclose_after_text()
    assert settled == [True]
    assert len(env.posts) == (1 if flag == "1" else 0)
