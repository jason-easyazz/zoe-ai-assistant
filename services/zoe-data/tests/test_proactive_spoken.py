"""P-W2.2 — the spoken-delivery adapter in proactive/engine.fire_notification.

Contract under test (additive, never a replacement):
  * flag OFF (default)                  -> no enqueue; push still sent
  * ON + presence + allowlisted trigger -> exactly ONE panel_announce enqueue AND push sent
  * ON + no presence                    -> push only
  * ON + non-allowlisted trigger        -> push only
  * enqueue raising                     -> push STILL sent (spoken path never blocks it)

Falsifiable pins: remove the flag check and test_flag_off_no_enqueue goes red;
remove the allowlist check and test_non_allowlisted_trigger_push_only goes red;
let the adapter's exception escape and test_enqueue_raising_push_still_sent
goes red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import contextlib

import proactive.engine as engine
import proactive.presence as presence_mod
import ui_orchestrator


# --------------------------------------------------------------------------- #
# Fakes (RecordingDB pattern shared with test_scheduling_workers_review_fixes)
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
    """Wire fire_notification with recording fakes; return the recorders."""
    state = {
        "pushes": [],
        "enqueues": [],
        "presence_calls": [],
        "presence_result": "panel-kitchen",
        "enqueue_error": None,
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
        state["presence_calls"].append(user_id)
        return state["presence_result"]

    monkeypatch.setattr(presence_mod, "panel_presence", fake_panel_presence)

    async def fake_enqueue(db, **kwargs):
        if state["enqueue_error"] is not None:
            raise state["enqueue_error"]
        state["enqueues"].append(kwargs)
        return {"action_id": "a1", **kwargs}

    monkeypatch.setattr(ui_orchestrator, "enqueue_ui_action", fake_enqueue)
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
async def test_flag_off_no_enqueue(harness, monkeypatch):
    monkeypatch.delenv("ZOE_PROACTIVE_SPOKEN", raising=False)
    await _fire()
    assert harness["enqueues"] == [], "flag OFF must be byte-identical: no spoken enqueue"
    assert len(harness["pushes"]) == 1, "push must still be sent"


async def test_flag_on_presence_allowed_enqueues_and_pushes(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")
    await _fire()
    assert len(harness["enqueues"]) == 1, "exactly one panel_announce enqueue"
    enq = harness["enqueues"][0]
    assert enq["action_type"] == "panel_announce"
    assert enq["panel_id"] == "panel-kitchen"
    assert enq["payload"]["message"].startswith("Good morning")
    assert len(harness["pushes"]) == 1, "push is ALWAYS sent — spoken is additive"


async def test_no_presence_push_only(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")
    harness["presence_result"] = None
    await _fire()
    assert harness["enqueues"] == []
    assert len(harness["pushes"]) == 1


async def test_non_allowlisted_trigger_push_only(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")
    monkeypatch.delenv("ZOE_PROACTIVE_SPOKEN_TRIGGERS", raising=False)  # default allowlist
    await _fire(trigger_type="evening_windown")
    assert harness["enqueues"] == [], "non-allowlisted trigger must not speak"
    assert len(harness["pushes"]) == 1
    assert harness["presence_calls"] == [], "allowlist gate should short-circuit before the presence query"


async def test_allowlist_env_extends_triggers(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN_TRIGGERS", "morning_checkin, evening_windown")
    await _fire(trigger_type="evening_windown")
    assert len(harness["enqueues"]) == 1
    assert len(harness["pushes"]) == 1


async def test_enqueue_raising_push_still_sent(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")
    harness["enqueue_error"] = RuntimeError("queue exploded")
    await _fire()
    assert harness["enqueues"] == []
    assert len(harness["pushes"]) == 1, "a spoken-path failure must NEVER block the push"


async def test_presence_raising_push_still_sent(harness, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SPOKEN", "true")

    async def exploding_presence(user_id, within_s=None):
        raise RuntimeError("presence query died")

    monkeypatch.setattr(presence_mod, "panel_presence", exploding_presence)
    await _fire()
    assert harness["enqueues"] == []
    assert len(harness["pushes"]) == 1
