"""P-W2.3 — the daemon-queue lane in proactive/engine._maybe_speak_notification.

W2.2 proved the panel_announce lane fires; W2.3 adds the lane that actually
reaches the SPEAKER (the Pi voice daemon's announcement queue). Contract:

  * flag ON + presence + allowlisted -> daemon enqueue AND panel enqueue AND push
  * flag OFF                          -> neither lane, push only
  * daemon-queue failure              -> panel lane + push UNAFFECTED
  * panel-lane failure                -> daemon queue + push UNAFFECTED

Falsifiable pins: drop the daemon enqueue and
test_daemon_queue_enqueued_alongside_panel goes red; couple the two lanes in
one try-block and the independence tests go red; let either failure escape and
the push assertions go red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import contextlib

import proactive.engine as engine
import proactive.presence as presence_mod
import ui_orchestrator
import voice_announce


# --------------------------------------------------------------------------- #
# Fakes (RecordingDB pattern shared with test_proactive_spoken)
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Exec:
    def __init__(self, factory):
        self._factory = factory

    def __await__(self):
        return self._factory().__await__()

    async def __aenter__(self):
        return await self._factory()

    async def __aexit__(self, *_):
        return False


class RecordingDB:
    def __init__(self, ops):
        self.ops = ops

    def execute(self, sql, params=()):
        return _Exec(lambda: self._do(sql, params))

    async def _do(self, sql, params):
        self.ops.append((" ".join(sql.split()).upper(), params))
        return _Cursor([])

    async def commit(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


@pytest.fixture
def harness(monkeypatch):
    state = {
        "pushes": [],
        "panel_enqueues": [],
        "daemon_enqueues": [],
        "presence_result": "panel-kitchen",
        "panel_error": None,
        "daemon_error": None,
    }

    ops = []

    @contextlib.asynccontextmanager
    async def fake_compat_db():
        yield RecordingDB(ops)

    monkeypatch.setattr(engine, "_get_compat_db", fake_compat_db)

    async def fake_create_pending(**_kw):
        return "pending-1"

    monkeypatch.setattr(engine, "create_pending", fake_create_pending)

    async def fake_send_push(user_id, message, extra=None):
        state["pushes"].append({"user_id": user_id, "message": message})
        return 1

    monkeypatch.setattr(engine, "_send_push", fake_send_push)

    async def fake_compose(trigger_type, ctx, fallback=""):
        return fallback

    monkeypatch.setattr(engine, "compose_message", fake_compose)

    async def fake_panel_presence(user_id, within_s=None):
        return state["presence_result"]

    monkeypatch.setattr(presence_mod, "panel_presence", fake_panel_presence)

    async def fake_panel_enqueue(db, **kwargs):
        if state["panel_error"] is not None:
            raise state["panel_error"]
        state["panel_enqueues"].append(kwargs)
        return {"action_id": "a1", **kwargs}

    monkeypatch.setattr(ui_orchestrator, "enqueue_ui_action", fake_panel_enqueue)

    async def fake_daemon_enqueue(db, **kwargs):
        if state["daemon_error"] is not None:
            raise state["daemon_error"]
        state["daemon_enqueues"].append(kwargs)
        return "ann-1"

    monkeypatch.setattr(voice_announce, "enqueue_announcement", fake_daemon_enqueue)
    return state


async def _fire(trigger_type="morning_checkin"):
    await engine.fire_notification(
        user_id="jason",
        message="Good morning Jason — two things on today.",
        trigger_type=trigger_type,
        context={"force_send": True},  # bypass quiet hours regardless of local clock
    )


# --------------------------------------------------------------------------- #
# The packet cases
# --------------------------------------------------------------------------- #
async def test_daemon_queue_enqueued_alongside_panel(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")
    await _fire()
    assert len(harness["daemon_enqueues"]) == 1, "the SPEAKER lane must be fed"
    d = harness["daemon_enqueues"][0]
    assert d["user_id"] == "jason"
    assert d["panel_id"] == "panel-kitchen"
    assert d["message"].startswith("Good morning")
    assert d["trigger_type"] == "morning_checkin"
    assert len(harness["panel_enqueues"]) == 1, "the toast lane stays (additive)"
    assert len(harness["pushes"]) == 1, "push is ALWAYS sent"


async def test_flag_off_no_daemon_enqueue(harness, monkeypatch):
    monkeypatch.delenv("ZOE_PROACTIVE_SPOKEN", raising=False)
    await _fire()
    assert harness["daemon_enqueues"] == []
    assert harness["panel_enqueues"] == []
    assert len(harness["pushes"]) == 1


async def test_no_presence_no_daemon_enqueue(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")
    harness["presence_result"] = None
    await _fire()
    assert harness["daemon_enqueues"] == []
    assert len(harness["pushes"]) == 1


async def test_daemon_queue_failure_never_blocks_push_or_panel(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")
    harness["daemon_error"] = RuntimeError("queue table missing")
    await _fire()
    assert harness["daemon_enqueues"] == []
    assert len(harness["panel_enqueues"]) == 1, "toast lane must survive a daemon-queue failure"
    assert len(harness["pushes"]) == 1, "push must survive a daemon-queue failure"


async def test_panel_failure_never_blocks_daemon_queue_or_push(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")
    harness["panel_error"] = RuntimeError("ui_actions exploded")
    await _fire()
    assert harness["panel_enqueues"] == []
    assert len(harness["daemon_enqueues"]) == 1, "speaker lane must survive a toast-lane failure"
    assert len(harness["pushes"]) == 1


async def test_both_lanes_failing_still_pushes(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")
    harness["panel_error"] = RuntimeError("boom")
    harness["daemon_error"] = RuntimeError("boom")
    await _fire()
    assert len(harness["pushes"]) == 1, "spoken delivery must NEVER block the push"
