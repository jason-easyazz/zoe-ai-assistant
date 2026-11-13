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
    monkeypatch.setattr(zf, "_panel_kv_prev", {})
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


def _ops_rig(monkeypatch, tmp_path, file_text: str, env_token: str):
    """The REAL ops helper, loaded from its file and pointed at temp paths, with urlopen faked: kv_miss_alert's own send
    path end to end, with no network and nothing under ~/.zoe."""
    import importlib.util
    import urllib.request
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_zoe_clw_test", Path(kma._REPO_ROOT) / "scripts" / "maintenance" / "zoe_crash_loop_watch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    envf = tmp_path / "bot.env"
    envf.write_text(file_text)
    monkeypatch.setattr(mod, "TELEGRAM_ENV", str(envf))
    monkeypatch.setattr(mod, "OUTBOX_PATH", str(tmp_path / "alert-outbox.json"))
    monkeypatch.setattr(mod, "INLINE_BACKOFF_S", (0.0, 0.0))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", env_token)
    monkeypatch.delenv("ZOE_ALERT_TELEGRAM_CHAT_IDS", raising=False)
    monkeypatch.setattr(kma, "_ops_mod", mod)
    monkeypatch.setenv("ZOE_KV_MISS_ALERT_STATE", str(tmp_path / "state.json"))
    monkeypatch.delenv("ZOE_PANEL_KV_MISS_ALERT", raising=False)
    monkeypatch.setattr(kma, "_alerted_day", "")
    monkeypatch.setattr(kma, "_inflight", set())
    monkeypatch.setattr(kma, "_attempts", {})
    monkeypatch.setattr(kma, "_last_failure", "")
    urls: list = []
    up = [True]

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        urls.append(req.full_url)
        if not up[0]:
            import io
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b""))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return mod, urls, up


def test_kv_miss_alert_send_path_uses_the_bot_file_token_over_a_planted_env_token(monkeypatch, tmp_path):
    """The owed proof for 2026-10-28..11-03 (nine alerts lost to HTTP 401): zoe-data's environment carried a retired
    agent's token. kv_miss_alert's OWN path (note_first_miss -> _ops_send -> deliver -> the ops transport) must use the
    bot file's token. Negative control: a file with no token falls back to the env token."""
    mod, urls, _up = _ops_rig(monkeypatch, tmp_path, "TELEGRAM_BOT_TOKEN=file-token\nZOE_ALERT_TELEGRAM_CHAT_IDS=42\n",
                              "env-token")
    assert kma.note_first_miss(3400, 0, now=DAY1, sync=True)
    assert len(urls) == 1 and "/botfile-token/" in urls[0] and "env-token" not in urls[0]
    (tmp_path / "bot.env").write_text("ZOE_ALERT_TELEGRAM_CHAT_IDS=42\n")              # control: no file token
    assert kma.note_first_miss(3400, 0, now=DAY2, sync=True)
    assert "/botenv-token/" in urls[-1]


def test_kv_alert_that_fails_is_held_and_delivered_once_later_and_the_daily_cap_holds(monkeypatch, tmp_path, caplog):
    """The lesson of the nine lost alerts: a failed send is HELD in the outbox (not lost), a later drain delivers it
    exactly once, the day stays capped at one alert, and the auth failure is recorded as "alerting is broken"."""
    import json

    mod, urls, up = _ops_rig(monkeypatch, tmp_path, "TELEGRAM_BOT_TOKEN=file-token\nZOE_ALERT_TELEGRAM_CHAT_IDS=42\n",
                             "env-token")
    caplog.set_level("INFO", logger="kv_miss_alert")
    up[0] = False
    assert kma.note_first_miss(3400, 0, now=DAY1, sync=True)
    assert len(urls) == 1, "an auth refusal is not retried inline"
    assert json.loads((tmp_path / "state.json").read_text())["alerted"] is True, "the outbox owns it now"
    assert mod.alerting_broken()["reason"] == "auth" and kma.alerting_broken()["reason"] == "auth"
    for i in range(4):                                                     # more misses today: no second alert
        assert kma.note_first_miss(3400, 0, now=DAY1 + 60 + i, sync=True) is None
    assert len(urls) == 1
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "ALERT_DELIVERY event=broken code=kv_miss_first" in msgs and "held in the outbox" in msgs
    assert "file-token" not in msgs

    up[0] = True                                                           # the token is fixed
    assert mod.drain(now=10**10) == {"kv_miss_first": ("delivered", "-")}
    assert mod.drain(now=10**10 + 1) == {} and len(urls) == 2, "delivered once, never again"
    assert mod.alerting_broken() is None
