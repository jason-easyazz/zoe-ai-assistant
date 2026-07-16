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
    _mm.memory_digest_last_run_effects.clear()
    _mm.memory_consolidation_last_run_effects.clear()
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
