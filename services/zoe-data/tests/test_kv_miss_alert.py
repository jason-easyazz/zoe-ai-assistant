"""PANEL_KV_MISS kind=first on a household turn alerts the owner: at most once a day, with the daily count."""
from __future__ import annotations

import pytest

import kv_miss_alert as kma
import zoe_flue_client as zf

pytestmark = pytest.mark.ci_safe

DAY1 = 1_790_000_000.0          # an arbitrary instant
DAY2 = DAY1 + 86400             # the next calendar day (in any timezone: a whole 24 h later)
DAY4 = DAY1 + 3 * 86400


@pytest.fixture
def sent(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_KV_MISS_ALERT_STATE", str(tmp_path / "state.json"))
    monkeypatch.delenv("ZOE_PANEL_KV_MISS_ALERT", raising=False)
    out: list = []
    monkeypatch.setattr(kma, "_ops_send", lambda text: out.append(text) or True)
    return out


def test_one_alert_a_day_with_the_daily_count(sent):
    assert kma.note_first_miss(3400, 0, now=DAY1, sync=True)
    for _ in range(3):
        assert kma.note_first_miss(3400, 0, now=DAY1 + 60, sync=True) is None
    assert len(sent) == 1 and "today: 1" in sent[0]
    again = kma.note_first_miss(3000, 10, now=DAY2, sync=True)       # next day: one more, naming yesterday's 4
    assert again and len(sent) == 2 and "today: 1" in sent[1] and "yesterday: 4" in sent[1]
    kma.note_first_miss(3000, 10, now=DAY4, sync=True)               # after a gap yesterday had none: 0, not the old 1
    assert "yesterday: 0" in sent[2]


def test_off_switch_and_a_failing_sender_never_raise(monkeypatch, sent):
    monkeypatch.setenv("ZOE_PANEL_KV_MISS_ALERT", "0")
    assert kma.note_first_miss(3400, 0, now=DAY1, sync=True) is None and not sent
    monkeypatch.delenv("ZOE_PANEL_KV_MISS_ALERT")

    def boom(_):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(kma, "_ops_send", boom)
    assert kma.note_first_miss(3400, 0, now=DAY2, sync=True)          # swallowed


def test_state_holds_counts_only(sent, tmp_path):
    kma.note_first_miss(3400, 0, now=DAY1, sync=True)
    import json

    assert set(json.loads((tmp_path / "state.json").read_text())) == {"day", "count", "alerted", "prev_count"}


def test_pytest_refuses_a_state_override_inside_the_live_dir(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("ZOE_KV_MISS_ALERT_STATE", str(Path.home() / ".zoe" / "kv-miss-alert.json"))
    assert kma._enabled() is False
    monkeypatch.delenv("ZOE_KV_MISS_ALERT_STATE")
    assert kma._enabled() is False


def test_alarm_wiring_first_household_only(sent, monkeypatch):
    calls: list = []
    monkeypatch.setattr(kma, "note_first_miss", lambda p, c, **k: calls.append((p, c)))
    sid = "voice-panel-alert-1"
    assert zf._panel_kv_alarm(sid, 3400, 0, "household", now=1000.0) == "first"
    assert calls == [(3400, 0)]
    assert zf._panel_kv_alarm(sid, 3400, 0, "household", now=1030.0) == "warm"      # warm miss: logged, no alert
    assert zf._panel_kv_alarm("voice-panel-p2", 3400, 0, "probe", now=1000.0) is None  # probe/harness traffic
    assert zf._panel_kv_alarm("voice-panel-p3", 40, 3300, "household", now=1000.0) is None  # a hit
    assert calls == [(3400, 0)]


def test_pytest_never_reaches_the_network(monkeypatch):
    monkeypatch.delenv("ZOE_KV_MISS_ALERT_LIVE_TEST", raising=False)
    assert kma._ops_send("x") is False
