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
    # a bare "boom" is a processing failure, not an extractor outage
    assert s["verdict"] == mm.VERDICT_PROCESSING_ERRORS
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


# ── Greptile round on #1682 ──────────────────────────────────────────────────

class _FakeTurnsDb:
    """A chat_messages table small enough to hold in a list.

    Answers the three queries the nightly pass issues by SQL shape, honouring a
    bound cutoff when the SQL carries one and falling back to ``now`` (the
    legacy ``now()`` forms) when it does not — so the same fake serves as the
    negative control for the straddling-turn race.
    """

    def __init__(self, now):
        self.now = now
        self.rows: list[tuple] = []          # (created_at, user_id)
        self.calls: list[tuple[str, tuple]] = []
        self.inject_after_selection = None   # (created_at, user_id)

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        from datetime import timedelta

        class _Cur:
            def __init__(self, rows):
                self._rows = rows

            async def fetchall(self):
                return self._rows

            async def fetchone(self):
                return self._rows[0] if self._rows else None

        if "SELECT DISTINCT owner.user_id" in sql:
            if len(params) == 3:                       # (cutoff, hours, cutoff)
                end, hours = params[0], params[1]
            else:                                      # legacy (hours,) → now()
                end, hours = self.now, params[0]
            start = end - timedelta(hours=hours)
            users = sorted({u for t, u in self.rows if start <= t < end})
            out = [(u,) for u in users]
            if self.inject_after_selection is not None:
                self.rows.append(self.inject_after_selection)
                self.inject_after_selection = None
            return _Cur(out)
        if "EXTRACT(EPOCH" in sql:
            end = params[0] if params else self.now
            seen = [t for t, _ in self.rows if t < end]
            if not seen:
                return _Cur([(None,)])
            return _Cur([((end - max(seen)).total_seconds() / 3600.0,)])
        raise AssertionError(f"unexpected SQL: {sql[:80]}")


def test_fresh_install_is_idle_not_unknown_and_probe_failure_stays_unknown(mm):
    """Greptile P1: no owned turns EVER must classify as idle; None is for a probe failure only.

    Negative control: before the fix both cases returned None, so five empty
    nights on a fresh install still raised the ZERO-EFFECT ALERT.
    """
    import asyncio
    from datetime import datetime, timezone

    md = pytest.importorskip("memory_digest")
    empty = _FakeTurnsDb(now=datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc))
    age = asyncio.run(md.newest_owned_user_turn_age_hours(db=empty))
    assert age == md.NO_OWNED_TURNS and age == float("inf")
    assert md.digest_input_seen(age) is False

    for night in range(mm._ZERO_EFFECT_RUNS_DEFAULT + 1):
        summary = mm.record_digest_run([], now=float(night), input_seen=md.digest_input_seen(age))
    assert summary["verdict"] == mm.VERDICT_IDLE
    assert summary["zero_effect_alert"] is False
    assert summary["zero_effect_streak"] == 0

    class _Broken:
        async def execute(self, *a, **kw):
            raise RuntimeError("connection was closed in the middle of operation")

    assert asyncio.run(md.newest_owned_user_turn_age_hours(db=_Broken())) is None
    assert md.digest_input_seen(None) is None       # unknown → legacy alerting


def test_emotional_pass_still_runs_when_the_fact_extractor_raises(mm, monkeypatch):
    """Greptile P1: a malformed FACT reply must not cost the night's emotional moments."""
    import asyncio
    import sys
    import types

    md = pytest.importorskip("memory_digest")
    called: dict = {}

    async def _twenty_words(_user_id, _db=None):
        return " ".join(["I told Zoe my dad is called Neil and we live in Geraldton"] * 3)

    async def _no_json(_chat_text):
        raise md.ExtractorError("NoJSONArray", "I cannot help with that.")

    async def _emotional(user_id, chat_text, svc):
        called["user"] = user_id
        return 2

    monkeypatch.setattr(md, "_load_todays_messages", _twenty_words)
    monkeypatch.setattr(md, "_extract_facts_with_gemma", _no_json)
    monkeypatch.setattr(md, "_emotional_memory_pass", _emotional)
    monkeypatch.setitem(sys.modules, "zoe_agent",
                        types.SimpleNamespace(_mempalace_load_user_facts=lambda *a, **k: ""))
    monkeypatch.setitem(sys.modules, "memory_service", types.SimpleNamespace(
        MemoryServiceError=RuntimeError, get_memory_service=lambda: object()))

    row = asyncio.run(md.run_memory_digest("jason"))
    assert called["user"] == "jason", "emotional pass was skipped after the fact-parse failure"
    assert row["error"] == "extractor_failed:NoJSONArray"
    assert row["emotional_new"] == 2                       # both outcomes recorded
    assert mm.record_digest_run([row], now=1.0, input_seen=True)["verdict"] == mm.VERDICT_EXTRACTOR_ERRORS

    async def _emotional_down(user_id, chat_text, svc):
        raise TimeoutError("emotional endpoint timed out")

    monkeypatch.setattr(md, "_emotional_memory_pass", _emotional_down)
    row = asyncio.run(md.run_memory_digest("jason"))
    assert row["error"] == "extractor_failed:NoJSONArray"
    assert row["emotional_error"].startswith("TimeoutError")


def test_selection_and_probe_share_one_cutoff_so_a_straddling_turn_cannot_false_alarm(mm):
    """Greptile P2: a turn landing between selection and the probe is invisible to BOTH.

    Negative control: with the probe on ``now()`` the injected row makes it say
    "input exists" about a turn selection never saw → a false
    no_eligible_users_despite_input alert.
    """
    import asyncio
    from datetime import datetime, timedelta, timezone

    md = pytest.importorskip("memory_digest")
    cutoff = datetime(2026, 9, 26, 3, 0, tzinfo=timezone.utc)
    db = _FakeTurnsDb(now=cutoff + timedelta(seconds=5))     # "now" drifts past the cutoff
    db.inject_after_selection = (cutoff + timedelta(seconds=1), "jason")

    import memory_digest as _md
    orig = _md._utcnow
    _md._utcnow = lambda: cutoff
    try:
        passed = asyncio.run(md.run_nightly_digest_pass(db=db))
    finally:
        _md._utcnow = orig

    assert passed["results"] == []
    assert passed["newest_turn_age_hours"] == md.NO_OWNED_TURNS
    assert passed["input_seen"] is False
    selection, probe = db.calls[0], db.calls[1]
    assert "SELECT DISTINCT owner.user_id" in selection[0] and "EXTRACT(EPOCH" in probe[0]
    assert selection[1][0] == cutoff == selection[1][2] == probe[1][0] == probe[1][1]
    assert "< ?::timestamptz" in selection[0] and "< ?::timestamptz" in probe[0]

    summary = mm.record_digest_run(passed["results"], now=1.0, input_seen=passed["input_seen"])
    assert summary["verdict"] == mm.VERDICT_IDLE
    # ...and the injected row IS visible to the next pass, so nothing is lost.
    assert asyncio.run(md.newest_owned_user_turn_age_hours(db=db, cutoff=cutoff + timedelta(minutes=1))) < 1.0


def test_only_extractor_failed_rows_blame_the_extractor(mm):
    """Greptile P2: a supersede/ingest failure is a processing error, not an extractor outage."""
    write_failed = [{"user_id": "a", "extracted": 3, "new": 0, "error": "supersede failed: chroma down"}]
    s = mm.record_digest_run(write_failed, now=1.0, input_seen=True)
    assert s["verdict"] == mm.VERDICT_PROCESSING_ERRORS
    assert (s["errors"], s["extractor_errors"], s["processing_errors"]) == (1, 0, 1)
    status = mm.memory_loop_status(now=2.0)["digest"]
    assert (status["extractor_errors"], status["processing_errors"]) == (0, 1)
    assert "write/processing" in status["verdict_text"] and "not the brain" in status["verdict_text"]

    outage = [{"user_id": "b", "extracted": 0, "error": "extractor_failed:ConnectError"}]
    s = mm.record_digest_run(outage, now=3.0, input_seen=True)
    assert s["verdict"] == mm.VERDICT_EXTRACTOR_ERRORS
    assert (s["extractor_errors"], s["processing_errors"]) == (1, 0)
    s = mm.record_digest_run(write_failed + outage, now=4.0, input_seen=True)
    assert s["verdict"] == mm.VERDICT_EXTRACTOR_ERRORS      # the brain being down dominates


def test_consolidation_has_its_own_verdict_vocabulary(mm):
    """Greptile P2: the weekly loop merges/resolves/archives — never 'extractor' prose."""
    digest_vocab = {
        mm.VERDICT_PRODUCTIVE, mm.VERDICT_IDLE, mm.VERDICT_NO_ELIGIBLE_USERS,
        mm.VERDICT_NO_ELIGIBLE_USERS_DESPITE_INPUT, mm.VERDICT_ALL_SKIPPED,
        mm.VERDICT_EXTRACTOR_ERRORS, mm.VERDICT_PROCESSING_ERRORS, mm.VERDICT_ATTEMPTED_NO_FACTS,
    }
    quiet = [{"user_id": u, "merged": 0, "resolved_contradictions": 0, "archived": 0}
             for u in ("jason", "family-admin")]
    s = mm.record_consolidation_run(quiet, now=1.0)
    assert s["verdict"] == mm.CONSOLIDATION_VERDICT_NOTHING_TO_DO
    text = mm.memory_loop_status(now=2.0)["consolidation"]["verdict_text"]
    assert "merge" in text and "extractor" not in text and "activity floor" not in text

    assert mm.record_consolidation_run([], now=3.0)["verdict"] == mm.CONSOLIDATION_VERDICT_NO_USERS
    assert mm.record_consolidation_run(
        [{"user_id": "jason", "merged": 4}], now=4.0)["verdict"] == mm.CONSOLIDATION_VERDICT_CONSOLIDATED
    errored = mm.record_consolidation_run(
        [{"user_id": "jason", "merged": 0, "error": "boom"}], now=5.0)
    assert errored["verdict"] == mm.CONSOLIDATION_VERDICT_ERRORS
    for v in (mm.CONSOLIDATION_VERDICT_NOTHING_TO_DO, mm.CONSOLIDATION_VERDICT_NO_USERS,
              mm.CONSOLIDATION_VERDICT_CONSOLIDATED, mm.CONSOLIDATION_VERDICT_ERRORS):
        assert v not in digest_vocab
        assert mm.verdict_text("consolidation", v) != v     # every verdict has prose
