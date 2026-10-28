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
    monkeypatch.setattr(kma, "_alerted_day", "")
    monkeypatch.setattr(kma, "_inflight", set())
    monkeypatch.setattr(kma, "_attempts", {})
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

    st = json.loads((tmp_path / "state.json").read_text())
    assert set(st) == {"day", "count", "alerted", "attempts", "prev_count"}
    assert isinstance(st["day"], str) and len(st["day"]) == 10 and isinstance(st["alerted"], bool)
    assert isinstance(st["count"], int) and isinstance(st["prev_count"], int)


def test_pytest_refuses_a_state_override_inside_the_live_dir(monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("ZOE_KV_MISS_ALERT_STATE", str(Path.home() / ".zoe" / "kv-miss-alert.json"))
    assert kma._enabled() is False
    monkeypatch.setenv("ZOE_KV_MISS_ALERT_STATE", str(Path.home() / ".zoe"))              # the directory itself
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
    assert zf._panel_kv_alarm("telegram-chat-9", 3400, 0, "household", now=1000.0) is None  # not a panel session
    assert zf._panel_kv_alarm("voice-panel-p3", 40, 3300, "household", now=1000.0) is None  # a hit
    assert calls == [(3400, 0)]


def test_pytest_never_reaches_the_network(monkeypatch):
    """The guard must stop BEFORE the ops sender is even loaded: any attempt to load it fails the test."""
    def boom(*a, **k):
        raise AssertionError("the ops sender was loaded under pytest")

    monkeypatch.setattr(kma.importlib.util, "spec_from_file_location", boom)
    assert kma._ops_send("x") is False


def test_one_a_day_holds_when_the_state_file_cannot_be_written(sent, monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("ZOE_KV_MISS_ALERT_STATE", str(blocker / "state.json"))      # mkdir/write under a file fails
    for _ in range(3):
        kma.note_first_miss(3400, 0, now=DAY1, sync=True)
    assert len(sent) == 1


def test_a_failed_send_is_retried_and_the_day_is_only_marked_after_delivery(sent, monkeypatch, tmp_path):
    import json

    calls: list = []

    def flaky(text):
        calls.append(text)
        return len(calls) >= 2            # Telegram fails once, then works

    monkeypatch.setattr(kma, "_ops_send", flaky)
    assert kma.note_first_miss(3400, 0, now=DAY1, sync=True)           # attempt 1 fails
    assert json.loads((tmp_path / "state.json").read_text())["alerted"] is False, "a failed send must not close the day"
    assert kma.note_first_miss(3400, 0, now=DAY1 + 60, sync=True)      # attempt 2 delivers
    assert kma.note_first_miss(3400, 0, now=DAY1 + 120, sync=True) is None   # delivered: no more today
    assert len(calls) == 2 and json.loads((tmp_path / "state.json").read_text())["alerted"] is True


def test_retries_are_bounded_per_day(sent, monkeypatch):
    calls: list = []
    monkeypatch.setattr(kma, "_ops_send", lambda text: calls.append(text) and False)
    for i in range(6):
        kma.note_first_miss(3400, 0, now=DAY1 + i, sync=True)
    assert len(calls) == kma.MAX_ATTEMPTS_PER_DAY


def test_the_voice_turn_does_no_state_io(sent, monkeypatch, tmp_path):
    """note_first_miss on the event loop only starts a thread: the state file is untouched until the worker runs."""
    started: list = []

    class _T:
        def __init__(self, target=None, args=(), **k):
            started.append((target, args))

        def start(self):
            pass

    monkeypatch.setattr(kma.threading, "Thread", _T)
    assert kma.note_first_miss(3400, 0, now=DAY1) is None
    assert not (tmp_path / "state.json").exists(), "state I/O must not run on the caller's thread"
    target, args = started[0]
    target(*args)                                                       # the worker does it
    assert (tmp_path / "state.json").exists() and len(sent) == 1


def test_a_send_that_crosses_midnight_does_not_free_the_next_days_claim(sent, monkeypatch):
    """Yesterday's send finishes after today's send has claimed today: it must clear only its own day, or a later miss
    today starts a duplicate alert."""
    kma._inflight.add(kma._today(DAY2))                              # today's first send is in progress
    monkeypatch.setattr(kma, "_ops_send", lambda text: False)        # yesterday's send ends (and fails) now
    kma.note_first_miss(3400, 0, now=DAY1, sync=True)                # yesterday's worker clears ITS day only
    assert kma._today(DAY2) in kma._inflight, "yesterday's worker must not free today's in-flight claim"
    assert kma.note_first_miss(3400, 0, now=DAY2 + 60, sync=True) is None   # no second alert for today


def test_a_partial_prefix_hit_that_still_reprefilled_a_lot_is_a_first_miss_and_alerts(sent, monkeypatch):
    """2026-10-28 18:54: first_prompt_n=1087 with first_cache_n=2154 (a wrong-envelope warm) is a miss: the classification keys
    on the RE-PREFILLED tokens, never on the cached count. Negative control: a small re-prefill with the same cache is a hit."""
    calls: list = []
    monkeypatch.setattr(kma, "note_first_miss", lambda p, c, **k: calls.append((p, c)))
    assert zf._panel_kv_alarm("voice-panel-partial-1", 1087, 2154, "household", now=1000.0) == "first"
    assert calls == [(1087, 2154)]
    assert zf._panel_kv_alarm("voice-panel-partial-2", 499, 2154, "household", now=1000.0) is None      # at/below threshold: a hit
    assert zf._panel_kv_alarm("voice-panel-partial-3", 500, 2154, "household", now=1000.0) is None
    assert zf._panel_kv_alarm("voice-panel-partial-4", 501, 2154, "household", now=1000.0) == "first"
    assert len(calls) == 2


def test_an_undelivered_alert_logs_why(monkeypatch, caplog, tmp_path):
    """The 2026-10-28 alert logged only 'not delivered'; the sender's reason (and never the bot token) must reach the log."""
    monkeypatch.setenv("ZOE_KV_MISS_ALERT_STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(kma, "_alerted_day", "")
    monkeypatch.setattr(kma, "_inflight", set())
    monkeypatch.setattr(kma, "_attempts", {})
    monkeypatch.setattr(kma, "_last_failure", "")

    def sender(text):
        kma._last_failure = kma._redact("crash-watch: telegram send failed: <urlopen error timed out> /bot123:SECRET-tok/sendMessage")
        assert kma._redact("recipient jason@example.com chat_id=123456 failed") == "other", "free text never reaches the log"
        assert kma._redact("crash-watch: telegram send failed: HTTP Error 403: Forbidden") == "http_403"
        return False

    caplog.set_level("WARNING", logger="kv_miss_alert")
    kma.note_first_miss(1087, 2154, now=DAY1, send=sender, sync=True)
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "not delivered" in msgs and "timeout" in msgs
    assert "SECRET-tok" not in msgs and "123" not in msgs.replace("2026", "")


def test_the_ops_send_failure_reason_does_not_redirect_the_process_stderr(monkeypatch):
    """The sender reports its failure through a callback: sys.stderr is never swapped, so unrelated output is not swallowed."""
    import sys
    import types

    seen = {}

    def fake_telegram(text, report=None):
        seen["stderr_untouched"] = sys.stderr is real_stderr
        report("crash-watch: telegram send failed: HTTP Error 403: Forbidden")
        return False

    real_stderr = sys.stderr
    fake = types.SimpleNamespace(_telegram=fake_telegram)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(kma.importlib.util, "spec_from_file_location",
                        lambda *a, **k: types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda m: None)))
    monkeypatch.setattr(kma.importlib.util, "module_from_spec", lambda spec: fake)
    monkeypatch.setattr(kma, "_last_failure", "")
    assert kma._ops_send("x") is False
    assert seen["stderr_untouched"] is True
    assert kma._last_failure == "http_403"
