"""Observability for the two in-process memory-maintenance loops.

Pure-logic coverage for the last-run recording + staleness surface added after
the nightly digest broke silently in production for weeks. No brain, no DB —
just the metric/state helpers in ``memory_metrics`` and the loop wiring in
``routers.system``.
"""

import importlib

import pytest

pytestmark = pytest.mark.ci_safe


@pytest.fixture()
def mm():
    """A fresh memory_metrics module with cleared last-run state.

    The module holds process-global gauges + ``_LAST_RUN``; reset the state so
    tests don't leak into each other or into other suites in the same run.
    """
    import memory_metrics as _mm

    _mm._LAST_RUN.clear()
    _mm._ZERO_EFFECT_STREAK.clear()
    _mm.memory_digest_last_run_effects.clear()
    _mm.memory_consolidation_last_run_effects.clear()
    _mm.memory_loop_last_run_effect_count.clear()
    _mm.memory_loop_zero_effect_streak.clear()
    _mm.memory_loop_zero_effect_alert.clear()
    return _mm


def _effect_samples(mm, metric_name):
    return {
        sample.labels.get("effect"): sample.value
        for metric in mm.REGISTRY.collect()
        if metric.name == metric_name
        for sample in metric.samples
        if sample.name == metric_name
    }


def test_record_digest_run_sums_effects_and_sets_gauges(mm):
    results = [
        {"user_id": "demo-1", "extracted": 3, "new": 2, "superseded": 1, "skipped_duplicates": 4},
        {"user_id": "demo-2", "extracted": 1, "new": 1, "superseded": 0, "skipped_duplicates": 2},
    ]

    summary = mm.record_digest_run(results, now=1000.0)

    assert summary["users"] == 2
    assert summary["effects"] == {
        "extracted": 4,
        "new": 3,
        "superseded": 1,
        "skipped_duplicates": 6,
    }
    # Prometheus gauges reflect the same aggregates.
    ts = next(
        s.value
        for metric in mm.REGISTRY.collect()
        if metric.name == "zoe_memory_digest_last_run_timestamp_seconds"
        for s in metric.samples
    )
    assert ts == 1000.0
    assert _effect_samples(mm, "zoe_memory_digest_last_run_effects")["skipped_duplicates"] == 6.0


def test_record_consolidation_run_sums_only_its_keys(mm):
    results = [
        {"user_id": "demo-1", "merged": 2, "resolved_contradictions": 1, "archived": 5},
        # Foreign digest-shaped keys must NOT leak into consolidation effects.
        {"user_id": "demo-2", "merged": 1, "extracted": 99},
    ]

    summary = mm.record_consolidation_run(results, now=2000.0)

    assert summary["users"] == 2
    assert summary["effects"] == {"merged": 3, "resolved_contradictions": 1, "archived": 5}
    assert "extracted" not in summary["effects"]


def test_sum_effects_ignores_bools_and_non_dicts(mm):
    # A stray True must not read as 1; non-dict rows are skipped defensively.
    results = [
        {"extracted": True, "new": 2},
        "not-a-dict",
        None,
        {"extracted": 3},
    ]
    summary = mm.record_digest_run(results, now=10.0)
    assert summary["users"] == 4  # user count is len(results), unfiltered
    assert summary["effects"]["extracted"] == 3
    assert summary["effects"]["new"] == 2


def test_memory_loop_status_stale_when_never_run(mm):
    status = mm.memory_loop_status(now=5000.0)
    for loop in ("digest", "consolidation"):
        assert status[loop]["ever_ran"] is False
        assert status[loop]["stale"] is True
        assert status[loop]["last_run_ts"] is None
        assert status[loop]["age_seconds"] is None


def test_memory_loop_status_fresh_and_stale_by_age(mm):
    mm.record_digest_run([{"extracted": 1}], now=1000.0)

    # 1 hour later: well within the ~26h digest grace → fresh.
    fresh = mm.memory_loop_status(now=1000.0 + 3600)["digest"]
    assert fresh["ever_ran"] is True
    assert fresh["stale"] is False
    assert fresh["age_seconds"] == 3600

    # 2 days later: past the digest cadence grace → stale (a missed nightly).
    stale = mm.memory_loop_status(now=1000.0 + 2 * 86400)["digest"]
    assert stale["stale"] is True
    assert stale["age_seconds"] == 2 * 86400


def test_consolidation_weekly_grace_is_wider_than_digest(mm):
    mm.record_consolidation_run([{"merged": 1}], now=0.0)
    # 2 days old: fine for a weekly loop (would be stale for the nightly one).
    assert mm.memory_loop_status(now=2 * 86400)["consolidation"]["stale"] is False
    # 9 days old: past the weekly grace → a missed Sunday pass surfaces.
    assert mm.memory_loop_status(now=9 * 86400)["consolidation"]["stale"] is True


def test_record_helper_in_system_router_never_raises(mm, monkeypatch):
    """The loop-side recorder degrades gracefully if metrics blow up."""
    import routers.system as system

    def _boom(_results):
        raise RuntimeError("metrics exploded")

    monkeypatch.setattr("memory_metrics.record_digest_run", _boom)

    summary = system._record_memory_loop("digest", [{"extracted": 1}, {"extracted": 2}])

    # No exception; falls back to a plain user count.
    assert summary["users"] == 2
    assert summary["effects"] == {}


def test_attach_memory_loop_log_handler_is_idempotent(mm, tmp_path, monkeypatch):
    import logging

    import routers.system as system

    monkeypatch.setenv("ZOE_MEMORY_LOOP_LOG_PATH", str(tmp_path / "mem-loops.log"))

    before = len(system.logger.handlers)
    system._attach_memory_loop_log_handler()
    system._attach_memory_loop_log_handler()  # second call must not add a duplicate
    added = [h for h in system.logger.handlers if getattr(h, "_zoe_memory_loop_log", False)]
    try:
        assert len(added) == 1
        assert len(system.logger.handlers) == before + 1
    finally:
        for h in added:
            system.logger.removeHandler(h)
            if isinstance(h, logging.Handler):
                h.close()


def test_attach_memory_loop_log_handler_accepts_bare_filename(mm, tmp_path, monkeypatch):
    """A bare filename → dirname "" must not make os.makedirs("") raise and skip
    the handler (the durable log this change guarantees would silently go missing).
    """
    import logging

    import routers.system as system

    monkeypatch.chdir(tmp_path)  # bare filename lands in an isolated cwd
    monkeypatch.setenv("ZOE_MEMORY_LOOP_LOG_PATH", "mem-loops.log")

    system._attach_memory_loop_log_handler()
    added = [h for h in system.logger.handlers if getattr(h, "_zoe_memory_loop_log", False)]
    try:
        assert len(added) == 1  # handler attached despite the bare path
    finally:
        for h in added:
            system.logger.removeHandler(h)
            if isinstance(h, logging.Handler):
                h.close()


# ── Effect-count heartbeat + zero-effect alerting ────────────────────────────
# Staleness only catches a MISSED run. These pin the DID-NOTHING signal: the
# nightly digest processed zero users for ten consecutive nights while logging
# "nightly run complete", and the observability above recorded every one of
# those empty successes without ever raising an alert.


def test_run_records_a_scalar_effect_count(mm):
    """Every recorded run must carry HOW MUCH work it did, not just that it ran."""
    summary = mm.record_digest_run(
        [{"extracted": 3, "new": 2, "superseded": 1, "skipped_duplicates": 4}], now=1.0
    )
    assert summary["effect_count"] == 10  # 3 + 2 + 1 + 4
    assert summary["zero_effect_streak"] == 0

    empty = mm.record_digest_run([], now=2.0)
    assert empty["users"] == 0
    assert empty["effect_count"] == 0
    assert empty["zero_effect_streak"] == 1


def test_ten_consecutive_zero_effect_digest_runs_raise_the_alert(mm):
    """THE falsification test: the exact #1480 shape — ten on-time runs, zero users.

    If the alert condition is broken this goes RED. Staleness cannot catch this
    case: every run is punctual, so ``stale`` stays False throughout.
    """
    for night in range(10):
        summary = mm.record_digest_run([], now=float(night) * 86400)

    assert summary["zero_effect_streak"] == 10
    assert summary["effect_count"] == 0
    assert summary["zero_effect_alert"] is True, "10 empty nights must raise the alert"

    status = mm.memory_loop_status(now=9 * 86400)["digest"]
    assert status["stale"] is False, "the runs were all on time — staleness can't catch this"
    assert status["zero_effect_alert"] is True
    assert status["healthy"] is False, "ran 10 times and did nothing 10 times is NOT healthy"

    health = mm.memory_loop_health(now=9 * 86400)
    assert health["healthy"] is False
    assert any("did nothing" in a and "digest" in a for a in health["alerts"]), health["alerts"]


def test_zero_effect_alert_fires_when_users_run_but_produce_nothing(mm):
    """Zero EFFECTS, not zero users, is the trigger — the #1217 shape."""
    for night in range(5):
        summary = mm.record_digest_run(
            [{"user_id": "demo-1", "extracted": 0, "new": 0}], now=float(night)
        )
    assert summary["users"] == 1  # the loop "processed" a user every night
    assert summary["effect_count"] == 0
    assert summary["zero_effect_alert"] is True


def test_idle_tolerant_loop_never_cries_wolf(mm):
    """A loop that DECLARED it is legitimately idle must not alert on zero effect.

    Weekly consolidation genuinely has nothing to merge for weeks at a time on a
    healthy store; its missed-run staleness check still covers it dying.
    """
    for week in range(10):
        summary = mm.record_consolidation_run([{"merged": 0}], now=float(week) * 86400)
    # A healthy digest alongside it, so the rollup below isolates consolidation.
    mm.record_digest_run([{"extracted": 1}], now=9 * 86400)

    assert summary["effect_count"] == 0
    assert summary["zero_effect_streak"] == 10  # the streak is still REPORTED …
    assert summary["zero_effect_alert_after"] is None
    assert summary["zero_effect_alert"] is False  # … but it never alerts

    status = mm.memory_loop_status(now=9 * 86400)["consolidation"]
    assert status["idle_tolerant"] is True
    assert status["zero_effect_alert"] is False
    assert status["healthy"] is True
    assert mm.memory_loop_health(now=9 * 86400)["healthy"] is True


def test_streak_resets_on_a_productive_run(mm):
    for night in range(4):
        mm.record_digest_run([], now=float(night))
    assert mm.memory_loop_status(now=4.0)["digest"]["zero_effect_streak"] == 4

    after = mm.record_digest_run([{"extracted": 1}], now=5.0)
    assert after["zero_effect_streak"] == 0
    assert after["zero_effect_alert"] is False
    assert mm.memory_loop_status(now=5.0)["digest"]["healthy"] is True


def test_alert_threshold_is_configurable_and_disablable(mm, monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS", "2")
    assert mm.record_digest_run([], now=1.0)["zero_effect_alert"] is False
    assert mm.record_digest_run([], now=2.0)["zero_effect_alert"] is True

    # Threshold is re-read on status, so retuning takes effect without a run.
    monkeypatch.setenv("ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS", "50")
    assert mm.memory_loop_status(now=2.0)["digest"]["zero_effect_alert"] is False

    # < 1 disables zero-effect alerting entirely; garbage falls back to default.
    monkeypatch.setenv("ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS", "0")
    assert mm.zero_effect_threshold("digest") is None
    monkeypatch.setenv("ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS", "not-a-number")
    assert mm.zero_effect_threshold("digest") == mm._ZERO_EFFECT_RUNS_DEFAULT


def test_default_threshold_tolerates_a_couple_of_quiet_nights(mm):
    """A quiet night or two must not alert — only a sustained run of them."""
    assert mm._ZERO_EFFECT_RUNS_DEFAULT >= 3
    for night in range(2):
        summary = mm.record_digest_run([], now=float(night))
    assert summary["zero_effect_alert"] is False


def test_zero_effect_gauges_are_exported(mm):
    for night in range(5):
        mm.record_digest_run([], now=float(night))
    samples = {
        (metric.name, sample.labels.get("loop")): sample.value
        for metric in mm.REGISTRY.collect()
        for sample in metric.samples
        if metric.name.startswith("zoe_memory_loop_")
    }
    assert samples[("zoe_memory_loop_last_run_effect_count", "digest")] == 0.0
    assert samples[("zoe_memory_loop_zero_effect_streak", "digest")] == 5.0
    assert samples[("zoe_memory_loop_zero_effect_alert", "digest")] == 1.0


def test_retuned_threshold_moves_the_gauge_not_just_the_endpoint(mm, monkeypatch):
    """The status endpoint and the PromQL alert must never disagree about health.

    Lowering the threshold between runs makes ``memory_loop_status`` report
    ``zero_effect_alert: true`` immediately; the exported gauge must follow, or
    the endpoint reads unhealthy while PromQL sits at 0 until the next nightly.
    """
    def _alert_gauge():
        return next(
            s.value
            for metric in mm.REGISTRY.collect()
            if metric.name == "zoe_memory_loop_zero_effect_alert"
            for s in metric.samples
            if s.labels.get("loop") == "digest"
        )

    monkeypatch.setenv("ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS", "10")
    for night in range(3):
        mm.record_digest_run([], now=float(night))
    assert _alert_gauge() == 0.0

    monkeypatch.setenv("ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS", "3")
    assert mm.memory_loop_status(now=3.0)["digest"]["zero_effect_alert"] is True
    assert _alert_gauge() == 1.0, "gauge must track the recomputed verdict"

    # The /metrics scrape path re-syncs too, without anyone hitting the endpoint.
    monkeypatch.setenv("ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS", "10")
    mm.refresh_memory_loop_gauges()
    assert _alert_gauge() == 0.0


def test_never_run_loop_is_unhealthy_without_a_zero_effect_alert(mm):
    health = mm.memory_loop_health(now=5000.0)
    assert health["healthy"] is False
    assert health["loops"]["digest"]["zero_effect_streak"] == 0
    assert health["loops"]["digest"]["zero_effect_alert"] is False
    assert any("never run" in a for a in health["alerts"])


def test_digest_loop_recorder_escalates_to_warning_on_alert(mm, caplog):
    """The durable memory-loop log must carry the same verdict the endpoint shows."""
    import logging

    import routers.system as system

    with caplog.at_level(logging.WARNING, logger=system.logger.name):
        for night in range(mm._ZERO_EFFECT_RUNS_DEFAULT):
            summary = system._record_memory_loop("digest", [])

    assert summary["zero_effect_alert"] is True
    assert any("ZERO-EFFECT ALERT" in r.message for r in caplog.records), caplog.text
