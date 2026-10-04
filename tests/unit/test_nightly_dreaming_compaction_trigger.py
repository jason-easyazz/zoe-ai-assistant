"""The weekly drawers-index compaction trigger in zoe-nightly-dreaming.py (pure parts).

The rebuild itself is never run from the dreaming process — it only asks zoe-data. Here:
the day parsing, the clock-injected decision, and the request flow against a fake API.
"""
from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe
REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "zoe_nightly_dreaming", REPO / "scripts" / "maintenance" / "zoe-nightly-dreaming.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SUN = datetime.datetime(2026, 10, 4, 2, 31)   # a Sunday
MON = SUN + datetime.timedelta(days=1)
ADVISED = {"tombstone_ratio": 6.17, "compaction_advised": True, "threshold": 3.0, "fresh": False,
           "ratio_known": True, "live_rows": 258, "maintenance_blocked": False}
UNKNOWN = {"tombstone_ratio": None, "compaction_advised": None, "ratio_known": False, "live_rows": 258,
           "fresh": True, "note": "index not persisted yet and no write-ahead log — unknown"}
BLOCKED = {**ADVISED, "maintenance_blocked": True, "maintenance_reason": "restore failed: boom"}


def test_day_parsing_defaults_to_sunday():
    m = _load()
    assert m.compaction_day_index(None) == 6 and m.compaction_day_index("") == 6
    assert m.compaction_day_index("monday") == 0 and m.compaction_day_index("Wed") == 2
    assert m.compaction_day_index("3") == 3 and m.compaction_day_index("fri") == 4
    assert m.compaction_day_index("nonsense") == 6 and m.compaction_day_index("9") == 6


def test_decision_runs_only_on_the_day_and_only_when_advised():
    m = _load()
    assert m.index_compaction_decision(SUN, ADVISED) == (True, "advised: ratio=6.17 >= 3.0")
    assert m.index_compaction_decision(MON, ADVISED)[0] is False            # wrong day
    assert m.index_compaction_decision(MON, ADVISED, day=0)[0] is True      # configured day
    assert m.index_compaction_decision(SUN, {**ADVISED, "compaction_advised": False, "tombstone_ratio": 1.4}) == (False, "not advised: ratio=1.4")
    # a fresh (not yet persisted) index is COUNTED from the WAL — when advised, it runs
    assert m.index_compaction_decision(SUN, {**ADVISED, "fresh": True})[0] is True
    assert m.index_compaction_decision(SUN, {**ADVISED, "fresh": True, "compaction_advised": False})[0] is False
    assert m.index_compaction_decision(SUN, None)[0] is False               # health unavailable
    assert m.index_compaction_decision(SUN, {"compaction_advised": "yes", "live_rows": 0})[0] is False  # strict bool, nothing live


def test_unknown_ratio_compacts_once_per_period_and_a_closed_gate_never_runs():
    """Codex P2: an unknown ratio must not skip compaction indefinitely — on the compaction
    day it runs (that is once per period); with no live rows there is nothing to rebuild;
    a gate that failed closed is operator work, never a retry."""
    m = _load()
    run, reason = m.index_compaction_decision(SUN, UNKNOWN)
    assert run is True and reason.startswith("ratio unknown") and "once this period" in reason
    assert m.index_compaction_decision(MON, UNKNOWN)[0] is False            # still only on the day
    assert m.index_compaction_decision(SUN, {**UNKNOWN, "live_rows": 0})[0] is False
    run, reason = m.index_compaction_decision(SUN, BLOCKED)
    assert run is False and "CLOSED" in reason and "boom" in reason


def test_flow_posts_only_when_due_and_reports_the_service_verdict(monkeypatch, capsys):
    m = _load()
    calls = []

    def fake_api(method, path, timeout):
        calls.append((method, path))
        if method == "GET":
            return 200, ADVISED
        return 200, {"ok": True, "elements_added": 258, "seconds": 3.2}

    monkeypatch.setattr(m, "_api", fake_api)
    monkeypatch.delenv("ZOE_MEMORY_INDEX_COMPACT_DAY", raising=False)
    assert m.weekly_index_compaction(MON) == 0 and calls == []            # off-day: no request at all
    assert m.weekly_index_compaction(SUN) == 0
    assert calls == [("GET", "/api/memories/maintenance/index-health"),
                     ("POST", "/api/memories/maintenance/compact-index")]
    out = capsys.readouterr().out
    assert '"elements_added": 258' in out and "tok" not in out


def test_flow_handles_dark_flag_and_failures(monkeypatch, capsys):
    m = _load()

    def dark(method, path, timeout):
        return (200, ADVISED) if method == "GET" else (404, {"detail": "disabled"})

    monkeypatch.setattr(m, "_api", dark)
    assert m.weekly_index_compaction(SUN) == 0
    assert "manual fallback" in capsys.readouterr().out

    def failed(method, path, timeout):
        return (200, ADVISED) if method == "GET" else (500, {"ok": False, "restored": True})

    monkeypatch.setattr(m, "_api", failed)
    assert m.weekly_index_compaction(SUN) == 1

    monkeypatch.setattr(m, "_api", lambda *a, **k: (503, {"detail": "no palace"}))
    assert m.weekly_index_compaction(SUN) == 0                             # health down: nothing to act on


def test_flow_busy_is_skipped_not_failed_and_blocked_is_loud(monkeypatch, capsys):
    m = _load()

    def busy(method, path, timeout):
        return (200, ADVISED) if method == "GET" else (409, {"ok": False, "status": "busy", "changed": False})

    monkeypatch.setattr(m, "_api", busy)
    assert m.weekly_index_compaction(SUN) == 0
    assert "busy" in capsys.readouterr().out

    def blocked_after(method, path, timeout):
        return (200, ADVISED) if method == "GET" else (500, {"ok": False, "status": "blocked", "backup_tar": "/b.tar"})

    monkeypatch.setattr(m, "_api", blocked_after)
    assert m.weekly_index_compaction(SUN) == 1
    assert "FAILED CLOSED" in capsys.readouterr().err

    calls = []
    monkeypatch.setattr(m, "_api", lambda method, path, timeout: (calls.append(method), (200, BLOCKED))[1])
    assert m.weekly_index_compaction(SUN) == 1 and calls == ["GET"]        # never POSTs onto a closed gate
    assert "CLOSED" in capsys.readouterr().err


def test_flow_posts_on_unknown_ratio(monkeypatch, capsys):
    m = _load()
    calls = []

    def api(method, path, timeout):
        calls.append(method)
        return (200, UNKNOWN) if method == "GET" else (200, {"ok": True, "status": "ok", "elements_added": 258})

    monkeypatch.setattr(m, "_api", api)
    assert m.weekly_index_compaction(SUN) == 0 and calls == ["GET", "POST"]
    assert "once this period" in capsys.readouterr().out
