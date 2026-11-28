"""A voice navigate the page never acknowledged is visible (2026-11-26).

The server wrote a pushed voice navigate `success` the moment it was broadcast, so a kiosk page that had stopped
executing navigations read as healthy. Now the next voice navigate for that panel finds the previous one still
carrying the delivery note, says so once in a closed-code line, and stamps the row.
"""
from __future__ import annotations

import logging
import os
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import routers.voice_tts as voice_tts  # noqa: E402

pytestmark = pytest.mark.ci_safe

_LINE = "PANEL_NAV_UNACKED"


def _ago(seconds: float) -> str:
    # ui_actions.updated_at is TEXT written by NOW()::TEXT.
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S.%f+00")


class _Cursor:
    def __init__(self, row=None, rows=None, rowcount=0):
        self._row, self._rows, self.rowcount = row, rows or [], rowcount

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return self._rows


class _Db:
    """One panel's previous voice navigate row, as the helper reads and stamps it."""

    def __init__(self, prev=None, older=(), ack_lands_first=False):
        self.prev = prev
        self.older = [dict(r) for r in older]  # earlier navigates behind `prev`, newest first
        self.ack_lands_first = ack_lands_first  # the page's ack arrives between the read and the stamp
        self.events: list[str] = []
        self.selects: list[tuple] = []

    async def execute(self, sql, params=()):
        if "FROM ui_panel_sessions" in sql:
            return _Cursor(row={"user_id": "panel-user"})
        if "SELECT id, status, error_message, updated_at" in sql:
            self.events.append("ack_check")
            self.selects.append((sql, params))
            assert "LIMIT 5" in sql
            return _Cursor(rows=[dict(r) for r in ([self.prev] if self.prev else []) + self.older])
        if "FROM ui_actions" in sql:
            return _Cursor(rows=[])
        if "UPDATE ui_actions" in sql and "delivered_unacked" in sql:
            assert "status = 'success'" in sql and "error_message = ?" in sql
            row_id, note = params
            assert note == voice_tts._NAV_DELIVERED_NOTE
            target = next(r for r in ([self.prev] if self.prev else []) + self.older if r["id"] == row_id)
            if self.ack_lands_first:
                target["error_message"] = None  # what ack_ui_action writes on success
            if target["status"] != "success" or target["error_message"] != note:
                return _Cursor(rowcount=0)
            target["error_code"] = "delivered_unacked"
            target["error_message"] = "Delivered over panel push; the page never acknowledged it"
            self.events.append(f"stamped:{row_id}")
            return _Cursor(rowcount=1)
        if "UPDATE ui_actions" in sql:
            self.delivery_sql = sql
            self.events.append(f"delivered:{params}")
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")

    async def commit(self):
        self.events.append("commit")


def _prev(**over):
    row = {"id": "nav-prev", "status": "success", "error_message": voice_tts._NAV_DELIVERED_NOTE, "updated_at": _ago(95)}
    row.update(over)
    return row


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if _LINE in r.getMessage()]


@pytest.mark.asyncio
async def test_an_unacked_previous_navigate_is_said_once_and_stamped(caplog):
    db = _Db(_prev())
    with caplog.at_level(logging.DEBUG, logger=voice_tts.logger.name):
        assert await voice_tts._note_unacked_voice_nav(db, "panel-x", "nav-now") is True
        # The same row again (a third turn): it no longer carries the delivery note, so it is not repeated.
        assert await voice_tts._note_unacked_voice_nav(db, "panel-x", "nav-now-2") is False
    lines = _lines(caplog)
    assert len(lines) == 1
    # Closed code: the panel id and an age, nothing else (no words, no url).
    head, panel, age = lines[0].split(" ")
    assert (head, panel) == (_LINE, "panel=panel-x")
    assert age.startswith("age_s=") and 94 <= int(age[len("age_s="):]) <= 100
    assert db.prev["error_code"] == "delivered_unacked"
    # It looks at THIS panel's previous voice navigate, never the one just sent.
    sql, params = db.selects[0]
    assert params == ("panel-x", "nav-now")
    assert "requested_by = 'voice'" in sql and "action_type = 'panel_navigate'" in sql and "id != ?" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "make_prev",  # built inside the test: a row's age must be measured from the test, not from collection
    [
        lambda: None,  # first navigate ever
        lambda: _prev(error_message=None),  # CONTROL: the page acked it (ack_ui_action clears the note)
        lambda: _prev(status="skipped", error_message=None),  # acked "already on that page"
        lambda: _prev(status="skipped", error_message="Superseded by newer Skybridge voice request"),  # never delivered
        lambda: _prev(updated_at=_ago(2)),  # still inside the ack grace: back-to-back turns
    ],
    ids=["no-previous", "acked", "acked-skipped", "superseded", "inside-grace"],
)
async def test_control_an_acked_or_fresh_navigate_is_silent(make_prev, caplog):
    db = _Db(make_prev())
    with caplog.at_level(logging.DEBUG, logger=voice_tts.logger.name):
        assert await voice_tts._note_unacked_voice_nav(db, "panel-x", "nav-now") is False
    assert _lines(caplog) == []
    assert not [e for e in db.events if e.startswith("stamped")]


@pytest.mark.asyncio
async def test_an_ack_that_lands_between_the_read_and_the_stamp_wins(caplog):
    """The page acknowledges after the row was read as un-acked: the stamp must not overwrite the ack, and nothing is said.

    Control run 2026-11-28: with the UPDATE made unconditional again (`WHERE id = ?`) the fake applies it and this fails.
    """
    db = _Db(_prev(), ack_lands_first=True)
    with caplog.at_level(logging.DEBUG, logger=voice_tts.logger.name):
        assert await voice_tts._note_unacked_voice_nav(db, "panel-x", "nav-now") is False
    assert _lines(caplog) == []
    assert db.prev["error_message"] is None and "error_code" not in db.prev


@pytest.mark.asyncio
async def test_a_stuck_navigate_behind_one_still_inside_the_grace_is_still_found(caplog):
    """The newest earlier navigate is 3 s old (inside the grace, skipped); the one before it never got its ack.

    Control run 2026-11-28: with `LIMIT 1` semantics (only the newest row considered) nothing is stamped and this fails.
    """
    db = _Db(_prev(id="nav-fresh", updated_at=_ago(3)), older=[_prev(id="nav-stuck", updated_at=_ago(140))])
    with caplog.at_level(logging.DEBUG, logger=voice_tts.logger.name):
        assert await voice_tts._note_unacked_voice_nav(db, "panel-x", "nav-now") is True
    assert len(_lines(caplog)) == 1
    assert "stamped:nav-stuck" in db.events and "stamped:nav-fresh" not in db.events


@pytest.mark.asyncio
async def test_a_database_fault_never_reaches_the_voice_turn(caplog):
    class _Broken:
        async def execute(self, *_a, **_k):
            raise RuntimeError("db down")

    with caplog.at_level(logging.DEBUG, logger=voice_tts.logger.name):
        assert await voice_tts._note_unacked_voice_nav(_Broken(), "panel-x", "nav-now") is False
    assert _lines(caplog) == []


def test_row_age_reads_the_text_timestamps_postgres_writes():
    assert 59 <= voice_tts._nav_row_age_s(_ago(60)) <= 62
    assert 59 <= voice_tts._nav_row_age_s(datetime.now(timezone.utc) - timedelta(seconds=60)) <= 62
    assert voice_tts._nav_row_age_s("2026-11-01 08:00:00+08") > 0
    assert voice_tts._nav_row_age_s("2026-11-01 08:00:00.5+00") > 0
    assert voice_tts._nav_row_age_s("2026-11-01T08:00:00Z") > 0
    # Offsets are honoured: the same instant written in two zones is the same age.
    a = voice_tts._nav_row_age_s("2026-11-01 08:00:00+08")
    b = voice_tts._nav_row_age_s("2026-11-01 00:00:00+00:00")
    assert abs(a - b) < 2
    assert voice_tts._nav_row_age_s("not a time") is None
    assert voice_tts._nav_row_age_s(None) is None


async def _run_broadcast(monkeypatch, db, delivered):
    sent = []

    async def get_db():
        yield db

    async def enqueue_ui_action(_db, **kwargs):
        return {"action_id": f"row-{kwargs['action_type']}", "status": "queued", "panel_id": kwargs["panel_id"],
                "action_type": kwargs["action_type"], "payload": kwargs["payload"]}

    class _Broadcaster:
        async def broadcast(self, channel, event, payload):
            db.events.append("broadcast")
            sent.append((channel, event, payload))
            return delivered

    monkeypatch.setitem(sys.modules, "database", types.SimpleNamespace(get_db=get_db))
    monkeypatch.setitem(sys.modules, "ui_orchestrator", types.SimpleNamespace(enqueue_ui_action=enqueue_ui_action))
    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=_Broadcaster()))
    result = {"handled": True, "spoken_summary": "Here it is.", "intent": {"domain": "weather", "action": "current"}, "cards": []}
    await voice_tts._broadcast_skybridge_ui("panel-x", result, utterance="show it", turn_key="turn-9")
    return sent


@pytest.mark.asyncio
async def test_the_voice_broadcast_checks_after_sending_and_sends_what_it_always_sent(monkeypatch, caplog):
    db = _Db(_prev())
    with caplog.at_level(logging.DEBUG, logger=voice_tts.logger.name):
        sent = await _run_broadcast(monkeypatch, db, delivered=1)
    # What the panel receives is unchanged: one ui_action frame, the row id under action_id, the same payload.
    assert len(sent) == 1
    channel, event, payload = sent[0]
    assert (channel, event) == ("panel_panel-x", "ui_action")
    assert payload["action_id"] == "row-panel_navigate" and "id" not in payload
    assert payload["payload"] == {"url": "/touch/home.html?heard=show+it&say=Here+it+is.&domain=weather",
                                  "label": "Showing on the panel", "panel_id": "panel-x", "source": "voice:skybridge"}
    # The check runs only after the frame has gone and the delivery was written: it cannot delay the navigate.
    assert db.events.index("broadcast") < db.events.index("ack_check")
    delivered = [e for e in db.events if e.startswith("delivered:")]
    assert delivered == [f"delivered:{(voice_tts._NAV_DELIVERED_NOTE, 'row-panel_navigate')}"]
    assert db.events.index(delivered[0]) < db.events.index("ack_check")
    assert db.selects[0][1] == ("panel-x", "row-panel_navigate")
    assert len(_lines(caplog)) == 1


@pytest.mark.asyncio
async def test_the_delivery_note_never_overwrites_an_ack_that_already_landed(monkeypatch, caplog):
    """A fast page can acknowledge while the broadcast is still going out: the delivery note is written only to a row
    that has no ack yet.

    Control run 2026-11-29: with the condition removed (`WHERE id = ?`) this fails.
    """
    db = _Db(_prev())
    with caplog.at_level(logging.DEBUG, logger=voice_tts.logger.name):
        await _run_broadcast(monkeypatch, db, delivered=1)
    where = " ".join(db.delivery_sql.split()).split("WHERE", 1)[1]
    assert where.strip() == "id = ? AND acked_at IS NULL"


@pytest.mark.asyncio
async def test_control_nothing_delivered_means_no_ack_check(monkeypatch, caplog):
    # No live socket: the navigate stays queued for the poll, and a queued row says nothing about the page.
    db = _Db(_prev())
    with caplog.at_level(logging.DEBUG, logger=voice_tts.logger.name):
        await _run_broadcast(monkeypatch, db, delivered=0)
    assert "ack_check" not in db.events and _lines(caplog) == []


def test_the_ack_clears_the_delivery_note():
    """The reader keys on the note; the page's ack must be what removes it (status/error fields come from the ack body)."""
    import inspect

    import routers.ui_actions as ui_actions

    src = inspect.getsource(ui_actions.ack_ui_action)
    assert "SET status = ?, error_code = ?, error_message = ?" in src
    assert voice_tts._NAV_DELIVERED_NOTE == "Delivered over panel push"
