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
ADVISED = {"tombstone_ratio": 6.17, "compaction_advised": True, "threshold": 3.0, "fresh": False}


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
    assert m.index_compaction_decision(SUN, {**ADVISED, "fresh": True})[0] is False   # just rebuilt
    assert m.index_compaction_decision(SUN, None)[0] is False               # health unavailable
    assert m.index_compaction_decision(SUN, {"compaction_advised": "yes"})[0] is False  # strict bool


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
