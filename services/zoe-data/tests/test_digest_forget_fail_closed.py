"""Forgotten means forever, FAIL CLOSED: the digest's forgotten-turn filter during a forget-ledger outage.

``memory_digest._skip_forgotten_turns`` used the lenient lookup, so on an outage it treated nothing as forgotten, and the
ingest guard behind it is also lenient on an outage - a forgotten name could be re-mined into memory. It is now ``strict``:
an outage raises ``LedgerUnavailable`` and every reader DEFERS the affected turns to a later run (nothing is lost).

Controls: the old lenient lookup mines the turn during the same outage; a healthy ledger behaves as before.
"""
from __future__ import annotations

import contextlib

import pytest

import memory_digest as md
import memory_forgotten as mf
from forgotten_support import FRIEND, OTHER, TRANSCRIPT, USER, ledger_env  # noqa: F401 - fixture + inputs

pytestmark = pytest.mark.ci_safe

OTHER_TURN = "I picked up some tea and then spent the afternoon sorting the garden shed out"


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows


class _Db:
    def __init__(self, turns):
        self.turns = turns

    async def execute(self, sql, params=()):
        return _Cur([(t,) for t in self.turns])


async def _forgotten_then_outage(ledger_env, monkeypatch):
    """Forget FRIEND, drop every in-process view (cache + overlay), then take the ledger down."""
    await mf.add(USER, FRIEND)
    mf.reset_state()

    async def down(*_a, **_k):
        raise ConnectionError("ledger down")
    monkeypatch.setattr(ledger_env, "active_hashes", down)


def _lenient(monkeypatch):
    """The OLD behaviour: the lenient lookup (negative control)."""
    real = mf.keep_unforgotten

    async def lenient(user_id, items, **kw):
        kw["strict"] = False
        return await real(user_id, items, **kw)
    monkeypatch.setattr(mf, "keep_unforgotten", lenient)


@pytest.mark.asyncio
async def test_outage_raises_ledger_unavailable_and_logs_no_name(ledger_env, monkeypatch, caplog):
    await _forgotten_then_outage(ledger_env, monkeypatch)
    with caplog.at_level("DEBUG"), pytest.raises(mf.LedgerUnavailable):
        await md._skip_forgotten_turns(USER, [TRANSCRIPT, OTHER_TURN], "digest")
    assert FRIEND not in caplog.text and "LedgerUnavailable" in caplog.text


@pytest.mark.asyncio
async def test_transcript_read_fails_as_a_retried_load_error_not_a_quiet_day(ledger_env, monkeypatch):
    """The nightly digest / night mind treat TranscriptLoadError as "read failed, try the next run" (never ``""``)."""
    await _forgotten_then_outage(ledger_env, monkeypatch)
    with pytest.raises(md.TranscriptLoadError):
        await md._load_todays_messages(USER, _Db([TRANSCRIPT, OTHER_TURN]))


@pytest.mark.asyncio
async def test_next_run_after_recovery_reads_the_same_rows_and_filters_as_usual(ledger_env, monkeypatch):
    """Nothing is lost: the failed run wrote nothing and moved no cursor (the day is re-read from chat_messages)."""
    await mf.add(USER, FRIEND)
    mf.reset_state()
    real = ledger_env.active_hashes
    state = {"down": True}

    async def flaky(*a, **k):
        if state["down"]:
            raise ConnectionError("ledger down")
        return await real(*a, **k)
    monkeypatch.setattr(ledger_env, "active_hashes", flaky)
    db = _Db([TRANSCRIPT, OTHER_TURN])
    with pytest.raises(md.TranscriptLoadError):
        await md._load_todays_messages(USER, db)
    state["down"] = False
    mf._fail_until.clear()                       # the lookup back-off from the failed run (as a later run would see it expired)
    text = await md._load_todays_messages(USER, db)
    assert text == OTHER_TURN and FRIEND not in text


@pytest.mark.asyncio
async def test_control_lenient_lookup_mines_the_forgotten_turn_during_the_outage(ledger_env, monkeypatch):
    await _forgotten_then_outage(ledger_env, monkeypatch)
    _lenient(monkeypatch)
    text = await md._load_todays_messages(USER, _Db([TRANSCRIPT, OTHER_TURN]))
    assert FRIEND in text                                              # the defect: the forgotten name is back


@pytest.mark.asyncio
async def test_healthy_path_unchanged(ledger_env):
    await mf.add(USER, FRIEND)
    assert await md._skip_forgotten_turns(USER, [TRANSCRIPT, OTHER_TURN], "digest") == [OTHER_TURN]
    assert await md._skip_forgotten_turns(OTHER, [TRANSCRIPT], "digest") == [TRANSCRIPT]   # another user untouched


@pytest.mark.asyncio
async def test_turn_digest_defers_without_calling_the_model(ledger_env, monkeypatch):
    await _forgotten_then_outage(ledger_env, monkeypatch)

    class _NoNetwork:
        def __init__(self, *a, **k):
            raise AssertionError("the model must not be called while the ledger is down")
    monkeypatch.setattr(md.httpx, "AsyncClient", _NoNetwork)
    out = await md.run_turn_digest(USER, TRANSCRIPT, "")
    assert out.get("skipped_reason") == "forget_ledger_unavailable" and out["new"] == 0


def _idle_ctx(rows, seen):
    class _Conn:
        async def fetch(self, *_a, **_k):
            return rows

        async def execute(self, *_a, **_k):
            seen["watermark"] = True

    @contextlib.asynccontextmanager
    async def ctx():
        yield _Conn()
    return ctx


@pytest.mark.asyncio
async def test_idle_consolidation_keeps_the_watermark_so_the_turns_are_retried(ledger_env, monkeypatch):
    import memory_idle_consolidation as idle

    await _forgotten_then_outage(ledger_env, monkeypatch)
    seen: dict = {}
    rows = [{"role": "user", "content": TRANSCRIPT, "metadata": {"user_id": USER}, "at": 1},
            {"role": "user", "content": "the shed roof needs a new coat of paint soon",
             "metadata": {"user_id": USER}, "at": 2}]

    async def extract(transcript):
        seen["extracted"] = True
        return []
    monkeypatch.setattr(md, "_extract_facts_with_gemma", extract)
    monkeypatch.setattr(idle, "MIN_TURNS", 1)
    assert await idle.consolidate_session("s1", USER, since=None, get_ctx=_idle_ctx(rows, seen)) == 0
    assert "extracted" not in seen                                     # nothing mined
    assert "watermark" not in seen                                     # and the cursor did not move: retried next sweep


@pytest.mark.asyncio
async def test_idle_control_lenient_lookup_mines_and_advances(ledger_env, monkeypatch):
    import memory_idle_consolidation as idle

    await _forgotten_then_outage(ledger_env, monkeypatch)
    _lenient(monkeypatch)
    seen: dict = {}
    rows = [{"role": "user", "content": TRANSCRIPT, "metadata": {"user_id": USER}, "at": 1}]

    async def extract(transcript):
        seen["transcript"] = transcript
        return []
    monkeypatch.setattr(md, "_extract_facts_with_gemma", extract)
    monkeypatch.setattr(idle, "MIN_TURNS", 1)
    await idle.consolidate_session("s1", USER, since=None, get_ctx=_idle_ctx(rows, seen))
    assert FRIEND in seen["transcript"] and seen["watermark"]          # the defect, and the lost turn
