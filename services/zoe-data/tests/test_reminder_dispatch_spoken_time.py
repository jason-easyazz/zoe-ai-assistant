"""The brain's reminder, said the way people say it, is created - and when a dispatch is refused, the log says why.

QA 2026-11-05 (typed chat): "remind me on the 15th of December at 10am to book Felix in at the dentist" -> the chat lane
deferred the write to the brain (``EXPERT_DEFER_WRITE``), the brain called ``add_reminder``, and
``/api/system/intent-dispatch`` answered **422** both times (the zoe-data request log shows it). The reply opened "I can
certainly set that reminder for you." and then apologised for a "hiccup"; a retry said the reminder service was "having some
trouble"; "No reminders set." followed. Nothing logged which slot failed.

Root cause: ``reminder_service.normalize_due_time`` accepted only the scanner's strict forms ("10:00", "10:00 AM"), so the
user's own "10am" (and "10 am", "10 a.m.", "noon", "10:00:00") was a 422; and ``normalize_due_date`` did not read the lead-in
"on the 15th of December". Fixed at the write-time normaliser (spoken times become canonical HH:MM; "on" is dropped).

Also here: ``INTENT_DISPATCH`` logs status + a fixed reason slug (never slot text or names), and the write-claim guard treats
"I can certainly set that reminder for you" in a deferred-write turn as an in-progress claim, judged at the end of the turn.

Negative controls: each fix is reverted by monkeypatch inside its test (``*_negative_control``) and the old behaviour returns.
"""
from __future__ import annotations

import contextlib
import logging
from datetime import date

import pytest
from fastapi import HTTPException

import intent_router
import reminder_service
import routers.system as system
import write_claim_guard as g
import write_receipts as wr
from reminder_service import normalize_due_date, normalize_due_time

pytestmark = pytest.mark.ci_safe

TITLE = "book Felix in at the dentist"
# The slots the brain builds from the QA sentence (the add_reminder tool passes date / time through as the model wrote them).
QA_SLOTS = {"title": TITLE, "date": "15th of December", "time": "10am"}
HONEST_EN = "I didn't manage to add that; want me to try again?"


def _next_dec_15() -> str:
    from proactive.triggers.reminder_scan import zoe_now

    today = zoe_now().date()
    d = date(today.year, 12, 15)
    return (d if d >= today else d.replace(year=today.year + 1)).isoformat()


# ── 1. the normalisers ─────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("10am", "10:00"), ("10 am", "10:00"), ("10AM", "10:00"), ("10 a.m.", "10:00"), ("10.30pm", "22:30"),
    ("10:30 p.m.", "22:30"), ("12am", "00:00"), ("12 pm", "12:00"), ("noon", "12:00"), ("midnight", "00:00"),
    ("10:00:00", "10:00"),
])
def test_spoken_clock_times_are_accepted_as_canonical_hhmm(raw, want):
    assert normalize_due_time(raw) == want


@pytest.mark.parametrize("raw", ["10:00", "10:00 AM", "22:15"])
def test_strict_clock_times_are_kept_as_given(raw):
    assert normalize_due_time(raw) == raw


@pytest.mark.parametrize("raw", ["10", "7", "13pm", "0am", "25:00", "09:99", "ten", "10:61pm", "soon"])
def test_ambiguous_or_impossible_times_are_still_422(raw):
    """A bare hour could be morning or evening, and "13pm" is nonsense: a reminder never guesses."""
    with pytest.raises(HTTPException) as exc:
        normalize_due_time(raw)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("raw", ["15th of December", "the 15th of December", "on the 15th of December", "December 15th",
                                 "December the 15th", "15 December"])
def test_the_qa_date_phrasings_resolve_to_the_next_15_december(raw):
    assert normalize_due_date(raw) == _next_dec_15()


def test_time_negative_control_the_old_strict_parser_rejects_10am(monkeypatch):
    monkeypatch.setattr(reminder_service, "_canonical_spoken_time", lambda _t: None)
    with pytest.raises(HTTPException) as exc:
        normalize_due_time("10am")
    assert exc.value.status_code == 422 and "not a clock time" in exc.value.detail


# ── 2. the QA request, end to end through /api/system/intent-dispatch (fake DB, no live calls) ──────────────────────────

class _Cursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeDb:
    def __init__(self):
        self.inserted: list[tuple] = []

    async def execute(self, sql, params=()):
        if "INSERT INTO reminders" in sql:
            self.inserted.append(tuple(params))
        if sql.strip().upper().startswith("SELECT") and self.inserted:
            p = self.inserted[-1]
            return _Cursor({"id": p[0], "user_id": p[1], "title": p[2], "due_date": p[7], "due_time": p[8],
                            "is_active": 1, "acknowledged": 0, "deleted": 0})
        return _Cursor()

    async def commit(self):
        return None


@pytest.fixture
def dispatch_env(monkeypatch):
    db = _FakeDb()

    @contextlib.asynccontextmanager
    async def _ctx():
        yield db

    import database

    monkeypatch.setattr(database, "get_db_ctx", _ctx)

    async def _user(_db, user_id):
        return {"user_id": user_id, "role": "admin"}

    async def _quiet(*_a, **_k):
        return None

    async def _no_schedule(*_a, **_k):
        return None

    monkeypatch.setattr(intent_router, "_load_direct_execution_user", _user)
    monkeypatch.setattr("reminder_service.require_feature_access", _quiet)
    monkeypatch.setattr("reminder_service.broadcaster.broadcast", _quiet)
    monkeypatch.setattr("proactive.triggers.reminder_scan.schedule_due_reminder", _no_schedule)
    wr.reset()
    yield db
    wr.reset()


def _dispatch(slots):
    body = system._IntentDispatchBody(user_id="qa-user-1", intent="reminder_create", slots=dict(slots))
    return system.intent_dispatch(body, None)


@pytest.mark.asyncio
async def test_the_qa_reminder_is_created(dispatch_env, caplog):
    caplog.set_level(logging.INFO, logger="routers.system")
    started = wr.begin()
    out = await _dispatch(QA_SLOTS)

    assert out["ok"] is True and out["result"], out
    assert len(dispatch_env.inserted) == 1
    row = dispatch_env.inserted[0]
    assert row[2] == TITLE and row[7] == _next_dec_15() and row[8] == "10:00"
    assert wr.has("qa-user-1", started, "reminder"), "the write-claim guard's receipt must land"
    line = next(r.getMessage() for r in caplog.records if "INTENT_DISPATCH" in r.getMessage())
    assert "intent=reminder_create status=200 ok=1" in line


@pytest.mark.asyncio
async def test_the_qa_reminder_negative_control_is_the_live_422(dispatch_env, monkeypatch):
    """Restore the pre-fix strict time parser: the QA request is the 422 the live log showed, and nothing is stored."""
    monkeypatch.setattr(reminder_service, "_canonical_spoken_time", lambda _t: None)
    with pytest.raises(HTTPException) as exc:
        await _dispatch(QA_SLOTS)
    assert exc.value.status_code == 422
    assert dispatch_env.inserted == []


# ── 3. the dispatch log: status + reason, never the words ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_refused_dispatch_logs_status_and_reason_but_no_text(dispatch_env, caplog):
    caplog.set_level(logging.INFO, logger="routers.system")
    with pytest.raises(HTTPException):
        await _dispatch({"title": TITLE, "date": "15th of Decembr", "time": "10am"})
    with pytest.raises(HTTPException):
        await _dispatch({"title": TITLE, "date": "2001-01-01"})
    with pytest.raises(HTTPException):
        await _dispatch({"title": TITLE, "time": "half ten"})
    lines = [r.getMessage() for r in caplog.records if "INTENT_DISPATCH" in r.getMessage()]
    assert len(lines) == 3, lines
    assert "status=422 ok=0 reason=due_date_not_a_date" in lines[0]
    assert "status=422 ok=0 reason=due_date_past" in lines[1]
    assert "status=422 ok=0 reason=due_time_not_a_time" in lines[2]
    logged = caplog.text.lower()
    for private in ("felix", "dentist", "decembr", "half ten", "qa-user-1", "2001"):
        assert private not in logged, private


@pytest.mark.asyncio
async def test_an_unexpected_failure_logs_the_error_class_only(dispatch_env, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="routers.system")

    async def _boom(intent, user_id="guest"):
        raise RuntimeError("db exploded while writing book Felix in")

    monkeypatch.setattr(intent_router, "execute_intent", _boom)
    with pytest.raises(HTTPException) as exc:
        await _dispatch(QA_SLOTS)
    assert exc.value.status_code == 500
    assert "status=500 ok=0 reason=error_RuntimeError" in caplog.text
    assert "felix" not in caplog.text.lower()


@pytest.mark.parametrize("detail,slug", [
    ("due_date '15th of Decembr' is not a date; use YYYY-MM-DD", "due_date_not_a_date"),
    ("due_date 'x' is already past (2001-01-01); a reminder for a past day can never fire", "due_date_past"),
    ("due_time 'half ten' is not a clock time; use HH:MM", "due_time_not_a_time"),
    ("recurring_pattern 'every 53 days' is not a supported recurrence", "recurring_pattern_unsupported"),
    ("alarms are not switched on in this home", "alarm_off"),
    ("Felix's dentist could not be booked", "rejected"),
])
def test_the_reason_slug_is_a_fixed_vocabulary(detail, slug):
    assert system._dispatch_reason(detail) == slug


# ── 4. "I can certainly set that reminder for you" before a failed tool ───────────────────────────────────────────────────

ASSURANCE = "I can certainly set that reminder for you."


def test_the_qa_assurance_is_an_in_progress_claim_in_a_deferred_write_turn():
    claim = g.find_claim(ASSURANCE, implied_kind="reminder")
    assert claim is not None and claim.kinds == ("reminder",) and claim.in_progress
    assert g.find_claim("Sure, I can set that up for you now.", implied_kind="reminder").kinds == ("reminder",)


@pytest.mark.parametrize("sentence", [
    ASSURANCE,                                                    # no deferred write and no "now": a description of ability
    "I can set that reminder for you.",                            # no assurance word: an offer
    "I can add balloons to your shopping list.",
    "I can certainly set a reminder for that if you like.",        # conditional: an offer
    "I can't set that reminder right now.",
    "Can I certainly set that reminder for you?",
])
def test_ability_offers_and_refusals_are_not_claims_without_a_deferred_write(sentence):
    assert g.find_claim(sentence) is None, sentence


@pytest.mark.parametrize("sentence", [
    "I can set that reminder for you.",
    "I can add balloons to your shopping list.",
    "I can certainly set a reminder for that if you like.",
    "I can't set that reminder right now.",
])
def test_offers_stay_offers_even_in_a_deferred_write_turn(sentence):
    assert g.find_claim(sentence, implied_kind="reminder") is None, sentence


def _run(agen):
    import asyncio

    async def run():
        return [d async for d in agen]

    return asyncio.new_event_loop().run_until_complete(run())


def test_the_qa_reply_with_a_failed_tool_does_not_read_as_done():
    """The live reply: the assurance, a tool call that wrote nothing, then the apology. The assurance is replaced."""
    wr.reset()
    started = wr.begin()
    start = '__TOOL__:{"phase": "start", "id": "1", "name": "add_reminder"}'
    result = '__TOOL__:{"phase": "result", "id": "1", "name": "add_reminder"}'
    apology = "I'm sorry, it looks like I ran into a little hiccup."

    async def inner():
        yield ASSURANCE + "\n\n"
        yield start
        yield result
        yield apology

    out = _run(g.guard_stream(inner(), "qa-user-1", started, implied_kind="reminder"))
    text = "".join(d for d in out if not d.startswith("__TOOL__:"))
    assert ASSURANCE not in text and text.startswith(HONEST_EN) and text.endswith(apology)
    assert g.guard_text(ASSURANCE + " " + apology, "qa-user-1", started, implied_kind="reminder") == f"{HONEST_EN} {apology}"


def test_the_assurance_is_released_when_the_reminder_really_lands():
    wr.reset()
    started = wr.begin()
    start = '__TOOL__:{"phase": "start", "id": "1", "name": "add_reminder"}'

    async def inner():
        yield ASSURANCE + " "
        yield start
        wr.record("qa-user-1", "reminder", "rem-1", "tool")
        yield "It's set for the 15th of December at 10am."

    out = _run(g.guard_stream(inner(), "qa-user-1", started, implied_kind="reminder"))
    assert "".join(d for d in out if not d.startswith("__TOOL__:")) == ASSURANCE + " It's set for the 15th of December at 10am."


def test_assurance_negative_control_without_the_lexicon_rows_it_passes_through(monkeypatch, tmp_path):
    import json

    data = json.loads(g.DATA_PATH.read_text(encoding="utf-8"))
    data["en"].pop("assurance_subjects")
    stripped = tmp_path / "write_claims.json"
    stripped.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(g, "DATA_PATH", stripped)
    g._langs.cache_clear()
    try:
        assert g.find_claim(ASSURANCE, implied_kind="reminder") is None
    finally:
        monkeypatch.undo()
        g._langs.cache_clear()
    assert g.find_claim(ASSURANCE, implied_kind="reminder") is not None
