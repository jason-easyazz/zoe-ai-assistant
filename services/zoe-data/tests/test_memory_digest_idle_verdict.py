"""The nightly digest's ZERO-EFFECT ALERT must say WHICH kind of nothing happened.

Measured 2026-08-13..09-25: 44 consecutive nightly runs raised
``memory_digest: ZERO-EFFECT ALERT — N consecutive runs produced no effects
(0 users processed)``. Root cause was NOT a broken loop: the newest owned user
turn in ``chat_messages`` was 2026-09-03 (an idle household), and the nights
before that were voice commands ("turn on the light") with no personal facts
in them. The alert — built for the #1480 dead-window bug — could not tell "no
input to digest" from "processed N users and got nothing", so a human had to
re-derive the answer from SQL.

These tests pin the fix: an IDLE run (no owned user turn inside the lookback
window, zero users) holds the streak and never alerts; every non-idle
zero-effect run still counts exactly as before and now carries a verdict.
Pure logic — no brain, no DB.
"""

import logging

import pytest

pytestmark = pytest.mark.ci_safe


@pytest.fixture()
def mm():
    import memory_metrics as _mm

    _mm._LAST_RUN.clear()
    _mm._ZERO_EFFECT_STREAK.clear()
    _mm._IDLE_STREAK.clear()
    _mm.memory_digest_last_run_effects.clear()
    _mm.memory_loop_last_run_effect_count.clear()
    _mm.memory_loop_zero_effect_streak.clear()
    _mm.memory_loop_zero_effect_alert.clear()
    _mm.memory_loop_idle_streak.clear()
    return _mm


def _run_to_threshold(mm, **kw):
    summary = None
    for night in range(mm._ZERO_EFFECT_RUNS_DEFAULT):
        summary = mm.record_digest_run([], now=float(night + 1), **kw)
    return summary


def test_legacy_callers_keep_every_zero_run_counting(mm):
    """Backward compatibility: no ``input_seen`` → the pre-fix behaviour, verbatim."""
    summary = _run_to_threshold(mm)
    assert summary["zero_effect_alert"] is True
    assert summary["zero_effect_streak"] == mm._ZERO_EFFECT_RUNS_DEFAULT
    assert summary["idle"] is False
    assert summary["verdict"] == mm.VERDICT_NO_ELIGIBLE_USERS


def test_idle_run_holds_the_streak_and_never_alerts(mm):
    """No owned user turn inside the lookback window + zero users = idle, not broken.

    Negative control: before the fix ``record_digest_run`` had no ``input_seen``
    (TypeError) and an empty run always extended the streak and kept alerting.
    """
    before = _run_to_threshold(mm)
    assert before["zero_effect_alert"] is True

    idle = mm.record_digest_run([], now=100.0, input_seen=False)
    assert idle["idle"] is True
    assert idle["verdict"] == mm.VERDICT_IDLE
    assert idle["zero_effect_alert"] is False
    # Held, not extended and not reset: nothing was tested.
    assert idle["zero_effect_streak"] == before["zero_effect_streak"]
    assert idle["idle_streak"] == 1
    assert mm.record_digest_run([], now=101.0, input_seen=False)["idle_streak"] == 2

    status = mm.memory_loop_status(now=101.0)["digest"]
    assert status["idle"] is True
    assert status["idle_streak"] == 2
    assert status["zero_effect_alert"] is False
    assert status["healthy"] is True
    # (consolidation never ran in this process, so it is legitimately stale)
    assert [a for a in mm.memory_loop_health(now=101.0)["alerts"] if a.startswith("digest")] == []


def test_input_reappearing_with_no_effects_re_raises_the_alert(mm):
    """An idle night forgives nothing: the held streak fires again on the next real zero."""
    _run_to_threshold(mm)
    mm.record_digest_run([], now=100.0, input_seen=False)
    again = mm.record_digest_run(
        [{"user_id": "jason", "extracted": 0, "skipped_reason": "insufficient_activity"}],
        now=200.0, input_seen=True,
    )
    assert again["idle"] is False
    assert again["zero_effect_streak"] == mm._ZERO_EFFECT_RUNS_DEFAULT + 1
    assert again["zero_effect_alert"] is True
    assert again["verdict"] == mm.VERDICT_ALL_SKIPPED
    prose = mm.memory_loop_health(now=200.0)["alerts"]
    prose = next(a for a in prose if a.startswith("digest"))
    assert "activity floor" in prose, prose


def test_zero_users_despite_input_is_named_as_the_dead_window_class(mm):
    """Turns exist inside the window but nobody was selected: the #1480 bug shape."""
    summary = _run_to_threshold(mm, input_seen=True)
    assert summary["zero_effect_alert"] is True
    assert summary["idle"] is False
    assert summary["verdict"] == mm.VERDICT_NO_ELIGIBLE_USERS_DESPITE_INPUT
    prose = mm.memory_loop_health(now=10.0)["alerts"]
    prose = next(a for a in prose if a.startswith("digest"))
    assert "#1480" in prose and "selection query" in prose, prose


def test_verdict_distinguishes_attempted_errors_and_skipped(mm):
    rows = [
        {"user_id": "a", "extracted": 0},                                   # reached the extractor
        {"user_id": "b", "extracted": 0, "skipped_reason": "insufficient_activity"},
        {"user_id": "c", "extracted": 0, "error": "boom"},
    ]
    s = mm.record_digest_run(rows, now=1.0, input_seen=True)
    assert (s["attempted"], s["skipped"], s["errors"]) == (1, 1, 1)
    assert s["verdict"] == mm.VERDICT_EXTRACTOR_ERRORS
    s = mm.record_digest_run(rows[:1], now=2.0, input_seen=True)
    assert s["verdict"] == mm.VERDICT_ATTEMPTED_NO_FACTS
    s = mm.record_digest_run([{"user_id": "a", "extracted": 2, "new": 1}], now=3.0, input_seen=True)
    assert s["verdict"] == mm.VERDICT_PRODUCTIVE and s["zero_effect_streak"] == 0


def test_a_productive_run_is_never_idle_even_without_input_flag(mm):
    s = mm.record_digest_run([{"extracted": 1}], now=1.0, input_seen=False)
    assert s["idle"] is False and s["verdict"] == mm.VERDICT_PRODUCTIVE


def test_idle_streak_gauge_is_exported(mm):
    _run_to_threshold(mm)
    mm.record_digest_run([], now=50.0, input_seen=False)
    samples = {
        (sample.labels.get("loop"), sample.name): sample.value
        for metric in mm.REGISTRY.collect()
        if metric.name in ("zoe_memory_loop_idle_streak", "zoe_memory_loop_zero_effect_alert")
        for sample in metric.samples
    }
    assert samples[("digest", "zoe_memory_loop_idle_streak")] == 1
    assert samples[("digest", "zoe_memory_loop_zero_effect_alert")] == 0


def test_digest_input_seen_uses_the_lookback_window():
    md = pytest.importorskip("memory_digest")
    lookback = md._DIGEST_LOOKBACK_HOURS
    assert md.digest_input_seen(None) is None              # unknown → legacy alerting
    assert md.digest_input_seen(lookback - 0.5) is True
    assert md.digest_input_seen(lookback + 1.0) is False    # the 2026-09 case: 530h


def test_extractor_outage_raises_a_typed_error_not_an_empty_list(monkeypatch):
    """Transport / HTTP / non-JSON answers raise ExtractorError; only a real ``[]`` is ``[]``.

    Negative control: before this fix the extractor swallowed every failure and
    returned ``[]``, indistinguishable from "no facts stated".
    """
    import asyncio

    md = pytest.importorskip("memory_digest")

    class _Resp:
        def __init__(self, content):
            self._c = content

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": self._c}}]}

    def _client_factory(behaviour):
        class _Client:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return behaviour()
        return _Client

    def _boom():
        raise md.httpx.ConnectError("All connection attempts failed")

    monkeypatch.setattr(md.httpx, "AsyncClient", _client_factory(_boom))
    with pytest.raises(md.ExtractorError) as ei:
        asyncio.run(md._extract_facts_with_gemma("some transcript " * 10))
    assert ei.value.kind == "ConnectError"

    monkeypatch.setattr(md.httpx, "AsyncClient", _client_factory(lambda: _Resp("I cannot help with that.")))
    with pytest.raises(md.ExtractorError) as ei:
        asyncio.run(md._extract_facts_with_gemma("x " * 30))
    assert ei.value.kind == "NoJSONArray"

    monkeypatch.setattr(md.httpx, "AsyncClient", _client_factory(lambda: _Resp("[]")))
    assert asyncio.run(md._extract_facts_with_gemma("x " * 30)) == []


def test_extractor_failure_is_an_error_row_and_classifies_as_extractor_errors(mm, monkeypatch):
    """End to end through run_memory_digest: an outage must NOT read as attempted_no_facts."""
    import asyncio
    import sys
    import types

    md = pytest.importorskip("memory_digest")

    async def _twenty_words(_user_id, _db=None):
        return " ".join(["I told Zoe my dad is called Neil and we live in Geraldton"] * 3)  # >= 20 words

    async def _down(_chat_text):
        raise md.ExtractorError("ReadTimeout", "timed out")

    monkeypatch.setattr(md, "_load_todays_messages", _twenty_words)
    monkeypatch.setattr(md, "_extract_facts_with_gemma", _down)
    # The engine imports its store collaborators before extracting; stub them so
    # the slim CI venv never touches the brain or MemPalace.
    monkeypatch.setitem(sys.modules, "zoe_agent",
                        types.SimpleNamespace(_mempalace_load_user_facts=lambda *a, **k: []))
    monkeypatch.setitem(sys.modules, "memory_service", types.SimpleNamespace(
        MemoryServiceError=RuntimeError, get_memory_service=lambda: object()))

    row = asyncio.run(md.run_memory_digest("jason"))
    assert row["error"] == "extractor_failed:ReadTimeout"
    assert row["extracted"] == 0

    summary = mm.record_digest_run([row], now=1.0, input_seen=True)
    assert summary["verdict"] == mm.VERDICT_EXTRACTOR_ERRORS
    assert summary["verdict"] != mm.VERDICT_ATTEMPTED_NO_FACTS
    assert (summary["attempted"], summary["errors"]) == (0, 1)


def test_status_surfaces_the_row_counts_of_the_last_run(mm):
    """memory_loop_status (and the admin endpoint built on it) carry attempted/skipped/errors."""
    import asyncio

    never = mm.memory_loop_status(now=0.0)["digest"]
    assert (never["attempted"], never["skipped"], never["errors"]) == (None, None, None)

    mm.record_digest_run([
        {"user_id": "a", "extracted": 0},
        {"user_id": "b", "extracted": 0, "skipped_reason": "insufficient_activity"},
        {"user_id": "b2", "extracted": 0, "skipped_reason": "insufficient_activity"},
        {"user_id": "c", "extracted": 0, "error": "extractor_failed:ConnectError"},
    ], now=1.0, input_seen=True)
    digest = mm.memory_loop_status(now=2.0)["digest"]
    assert (digest["attempted"], digest["skipped"], digest["errors"]) == (1, 2, 1)
    assert digest["verdict"] == mm.VERDICT_EXTRACTOR_ERRORS

    import routers.system as system
    body = asyncio.run(system.get_memory_loops_status(user={"role": "admin"}))
    via_endpoint = body["loops"]["digest"]
    assert (via_endpoint["attempted"], via_endpoint["skipped"], via_endpoint["errors"]) == (1, 2, 1)


def test_loop_recorder_logs_idle_as_info_and_names_the_verdict_on_alert(mm, caplog):
    """The durable memory-loop log must carry the same distinction the endpoint shows."""
    import routers.system as system

    with caplog.at_level(logging.INFO, logger=system.logger.name):
        for _ in range(mm._ZERO_EFFECT_RUNS_DEFAULT):
            summary = system._record_memory_loop("digest", [])   # legacy call still alerts
        assert summary["zero_effect_alert"] is True
        alert_lines = [r for r in caplog.records if "ZERO-EFFECT ALERT" in r.message]
        assert alert_lines and "verdict=no_eligible_users" in alert_lines[-1].message

        caplog.clear()
        idle = system._record_memory_loop(
            "digest", [], input_seen=False, newest_turn_age_hours=530.2,
        )
    assert idle["idle"] is True
    assert not any("ZERO-EFFECT ALERT" in r.message for r in caplog.records), caplog.text
    idle_lines = [r for r in caplog.records if "run idle" in r.message]
    assert idle_lines and idle_lines[0].levelno == logging.INFO
    assert "530.2h ago" in idle_lines[0].message
