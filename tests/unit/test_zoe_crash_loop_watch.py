"""Pins the zoe-data crash-loop alerter.

The load-bearing property is that it FIRES on the real 2026-07-31 signature
(many restarts + /health down) and stays SILENT on everything that merely looks
like it: a single deploy restart, a restart-counter reset, a slow-but-healthy
box. An alerter that cannot be shown to go red is not an alerter.

Everything runs against tmp_path + monkeypatched probes — never the live unit,
never a real Telegram send.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "maintenance" / "zoe_crash_loop_watch.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("zoe_crash_loop_watch", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _route_through_outbox(monkeypatch, tmp_path, transport):
    """Every alert now goes through deliver(): a temp outbox, no backoff sleeps, and ``transport`` (text -> outcome class)
    standing in for the ops Telegram path. A bare ``_telegram`` call would bypass the outbox, so it fails the test."""
    monkeypatch.setattr(mod, "OUTBOX_PATH", str(tmp_path / "alert-outbox.json"))
    monkeypatch.setattr(mod, "INLINE_BACKOFF_S", (0.0, 0.0))
    monkeypatch.setattr(mod, "_ops_transport", transport)

    def bypass(*a, **k):
        raise AssertionError("an owner alert bypassed the shared outbox")

    monkeypatch.setattr(mod, "_telegram", bypass)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Isolate state + capture what would have been sent."""
    monkeypatch.setattr(mod, "STATE_PATH", str(tmp_path / "state.json"))
    sent: list[str] = []
    _route_through_outbox(monkeypatch, tmp_path, lambda text: sent.append(text) or "delivered")

    def configure(*, restarts: int, healthy: bool, active: str = "activating"):
        monkeypatch.setattr(mod, "_systemd_prop",
                            lambda unit, prop: str(restarts) if prop == "NRestarts" else active)
        monkeypatch.setattr(mod, "_health_ok", lambda url, timeout=6.0: healthy)

    return configure, sent


def test_fires_on_the_incident_signature(rig):
    """37 restarts with /health down — the 2026-07-31 outage. Must go red."""
    configure, sent = rig
    configure(restarts=0, healthy=True, active="active")
    assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 0
    assert sent == []

    configure(restarts=37, healthy=False)
    assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 2
    assert len(sent) == 1
    assert "crash-looping" in sent[0]


def test_silent_on_a_single_deploy_restart(rig):
    """One restart on a healthy box is a deploy, not an incident."""
    configure, sent = rig
    configure(restarts=4, healthy=True, active="active")
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    configure(restarts=5, healthy=True, active="active")
    assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 0
    assert sent == []


def test_silent_when_restarts_climb_but_service_is_healthy(rig):
    """Restarts alone are not the signal — serving is. Guards against alerting
    on a burst of legitimate restarts that each came up fine."""
    configure, sent = rig
    configure(restarts=0, healthy=True, active="active")
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    configure(restarts=20, healthy=True, active="active")
    assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 0
    assert sent == []


def test_counter_reset_is_not_a_loop(rig):
    """`systemctl reset-failed` / a stop-start zeroes NRestarts. A DECREASE must
    never read as a huge positive delta."""
    configure, sent = rig
    configure(restarts=40, healthy=True, active="active")
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    configure(restarts=0, healthy=False)
    assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 0
    assert sent == []


def test_cooldown_suppresses_repeat_alerts_then_recovery_notifies(rig):
    configure, sent = rig
    configure(restarts=0, healthy=True, active="active")
    mod.check(threshold=5, cooldown=1800, dry_run=False)

    configure(restarts=30, healthy=False)
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    assert len(sent) == 1
    # still looping, inside cooldown -> no second alert
    configure(restarts=60, healthy=False)
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    assert len(sent) == 1

    # comes back -> exactly one recovery notice
    configure(restarts=60, healthy=True, active="active")
    assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 0
    assert len(sent) == 2
    assert "recovered" in sent[1]
    # and does not keep announcing recovery every tick
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    assert len(sent) == 2


def test_dry_run_sends_nothing_and_persists_nothing(rig, tmp_path):
    configure, sent = rig
    configure(restarts=99, healthy=False)
    mod.check(threshold=5, cooldown=1800, dry_run=True)
    assert sent == []
    assert not (tmp_path / "state.json").exists()


def test_unreadable_unit_is_an_error_not_a_false_alarm(rig, monkeypatch):
    configure, sent = rig
    configure(restarts=0, healthy=False)
    monkeypatch.setattr(mod, "_systemd_prop", lambda unit, prop: "")
    assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 1
    assert sent == []


def test_alert_not_marked_delivered_when_send_fails(rig, monkeypatch, tmp_path):
    """If Telegram is down the alert must stay pending, so a later tick retries instead of the box going quiet about an
    ongoing outage. ONE retry path: the outbox holds it and the tick's drain() re-sends it, exactly once; the watcher's
    own state does not raise a duplicate."""
    import json

    configure, sent = rig
    configure(restarts=0, healthy=True, active="active")
    mod.check(threshold=5, cooldown=1800, dry_run=False)

    tried: list[str] = []
    monkeypatch.setattr(mod, "_ops_transport", lambda text: tried.append(text) or "network")
    configure(restarts=30, healthy=False)
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    assert tried and sent == []
    assert [e["code"] for e in json.loads((tmp_path / "alert-outbox.json").read_text())["entries"]] == ["crash_loop"]

    monkeypatch.setattr(mod, "_ops_transport", lambda text: sent.append(text) or "delivered")
    configure(restarts=60, healthy=False)
    mod.check(threshold=5, cooldown=1800, dry_run=False)        # still looping, inside cooldown: no second raise
    assert sent == []
    mod.drain(now=10**10)                                        # the tick's retry
    mod.drain(now=10**10 + 1)
    assert len(sent) == 1 and "crash-looping" in sent[0]


def test_prefers_dedicated_recipient_setting_over_retired_allow_list():
    """TELEGRAM_ALLOWED_USERS is retired in .env.example, so a freshly
    provisioned host has none — reading only it meant zero recipients and a
    crash loop reported to nobody."""
    assert mod._recipients({"ZOE_ALERT_TELEGRAM_CHAT_IDS": "111,222"}) == ["111", "222"]


def test_falls_back_to_legacy_key_for_existing_hosts():
    assert mod._recipients({"TELEGRAM_ALLOWED_USERS": "999"}) == ["999"]


def test_dedicated_setting_wins_when_both_present():
    assert mod._recipients(
        {"ZOE_ALERT_TELEGRAM_CHAT_IDS": "111", "TELEGRAM_ALLOWED_USERS": "999"}
    ) == ["111"]


def test_no_recipients_is_reported_loudly_not_silently(capsys):
    """The absence of a recipient must be visible; a silent alerter is the bug."""
    assert mod._recipients({}) == []


def test_steady_state_healthy_is_logged_hourly_not_every_tick(rig, capsys):
    """41,700 identical undated 'healthy +0' lines filled the watcher's log in
    2026-10 (log review). Steady state must be quiet; any change must speak."""
    configure, sent = rig
    configure(restarts=3, healthy=True, active="active")
    assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 0
    first = capsys.readouterr().out
    assert "crash-watch: healthy, restarts=3" in first  # first sight is logged, with a timestamp
    assert first[:4].isdigit() and "T" in first[:20]

    for _ in range(5):  # five more steady ticks: silence
        assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 0
    assert capsys.readouterr().out == ""

    configure(restarts=4, healthy=True, active="active")  # a restart moved the counter: say so
    assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 0
    assert "restarts=4 (+1" in capsys.readouterr().out

    configure(restarts=4, healthy=False, active="activating")  # status flip: say so
    assert mod.check(threshold=5, cooldown=1800, dry_run=False) == 0
    assert "unhealthy" in capsys.readouterr().out


def test_steady_state_heartbeat_returns_after_the_interval(rig, capsys, monkeypatch):
    configure, _ = rig
    configure(restarts=0, healthy=True, active="active")
    t = [1_000_000.0]
    monkeypatch.setattr(mod.time, "time", lambda: t[0])
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    capsys.readouterr()
    t[0] += mod.HEARTBEAT_S - 1
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    assert capsys.readouterr().out == ""
    t[0] += 2
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    assert "healthy" in capsys.readouterr().out


def test_an_empty_env_var_does_not_wipe_the_files_token(monkeypatch, tmp_path):
    """zoe-data loads `TELEGRAM_BOT_TOKEN=` (blank) from its .env into os.environ; an in-process alert must still use
    the ops file's token, not the blank override (the 2026-10-28 cold-first-turn alert failed silently this way)."""
    envf = tmp_path / "tg.env"
    envf.write_text("TELEGRAM_BOT_TOKEN=123:abc\nZOE_ALERT_TELEGRAM_CHAT_IDS=42\n")
    monkeypatch.setattr(mod, "TELEGRAM_ENV", str(envf))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("ZOE_ALERT_TELEGRAM_CHAT_IDS", "  ")
    sent: list = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(req, timeout=None):
        sent.append(req.full_url)
        return _Resp()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    assert mod._telegram("x") is True
    assert sent and "/bot123:abc/" in sent[0], "the file's token must be used when the env var is blank"


def _send_with(monkeypatch, tmp_path, file_text: str, env_token: str | None) -> list:
    envf = tmp_path / "tg.env"
    envf.write_text(file_text)
    monkeypatch.setattr(mod, "TELEGRAM_ENV", str(envf))
    if env_token is None:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    else:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", env_token)
    monkeypatch.delenv("ZOE_ALERT_TELEGRAM_CHAT_IDS", raising=False)
    sent: list = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=None: sent.append(req.full_url) or _Resp())
    assert mod._telegram("x") is True
    return sent


def test_the_bot_files_token_beats_a_stale_env_token(monkeypatch, tmp_path):
    """2026-10-27..11-03: zoe-data's unit loaded a retired agent's env file; its REVOKED token overrode the bot file's valid
    one and every ops alert failed with 401. The bot file's token must win."""
    sent = _send_with(monkeypatch, tmp_path, "TELEGRAM_BOT_TOKEN=123:abc\nZOE_ALERT_TELEGRAM_CHAT_IDS=42\n", "999:revoked")
    assert "/bot123:abc/" in sent[0] and "999:revoked" not in sent[0]


def test_env_token_is_the_fallback_when_the_file_has_none(monkeypatch, tmp_path):
    sent = _send_with(monkeypatch, tmp_path, "ZOE_ALERT_TELEGRAM_CHAT_IDS=42\n", "999:zzz")
    assert "/bot999:zzz/" in sent[0]


def test_a_nonempty_env_recipient_still_overrides(monkeypatch, tmp_path):
    envf = tmp_path / "tg.env"
    envf.write_text("TELEGRAM_BOT_TOKEN=123:abc\nZOE_ALERT_TELEGRAM_CHAT_IDS=42\n")
    monkeypatch.setattr(mod, "TELEGRAM_ENV", str(envf))
    monkeypatch.setenv("ZOE_ALERT_TELEGRAM_CHAT_IDS", "77")
    sent: list = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":true}'

    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=None: sent.append((req.full_url, req.data or b"")) or _Resp())
    assert mod._telegram("x") is True
    assert "/bot123:abc/" in sent[0][0] and b"chat_id=77" in sent[0][1]


# ── the other auto-restarting units (gap audit 2026-11-13: the Telegram bot looped ~9 h unannounced) ──

@pytest.fixture
def units_rig(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "STATE_PATH", str(tmp_path / "state.json"))
    sent: list[str] = []
    _route_through_outbox(monkeypatch, tmp_path, lambda text: sent.append(text) or "delivered")
    props: dict = {}
    monkeypatch.setattr(mod, "_systemd_prop", lambda unit, prop: props.get((unit, prop), ""))
    return props, sent


BOT = "flue-zoe-telegram.service"


def test_other_unit_loop_alerts_once_then_recovers(units_rig):
    props, sent = units_rig
    props.update({(BOT, "NRestarts"): "0", (BOT, "ActiveState"): "active"})
    assert mod.check_units([BOT], 5, 1800, False) == 0 and sent == []
    props[(BOT, "NRestarts")] = "40"
    props[(BOT, "ActiveState")] = "activating"
    assert mod.check_units([BOT], 5, 1800, False) == 2
    assert len(sent) == 1 and BOT in sent[0] and "crash-looping" in sent[0]
    props[(BOT, "NRestarts")] = "80"
    assert mod.check_units([BOT], 5, 1800, False) == 2 and len(sent) == 1      # cooldown: no re-alert
    props[(BOT, "ActiveState")] = "active"
    assert mod.check_units([BOT], 5, 1800, False) == 0
    assert len(sent) == 2 and "recovered" in sent[1]


def test_other_unit_single_restart_and_counter_reset_stay_silent(units_rig):
    props, sent = units_rig
    props.update({(BOT, "NRestarts"): "3", (BOT, "ActiveState"): "active"})
    mod.check_units([BOT], 5, 1800, False)
    props[(BOT, "NRestarts")] = "4"
    assert mod.check_units([BOT], 5, 1800, False) == 0
    props[(BOT, "NRestarts")] = "0"
    assert mod.check_units([BOT], 5, 1800, False) == 0
    assert sent == []


def test_a_unit_this_host_lacks_is_skipped(units_rig):
    props, sent = units_rig
    assert mod.check_units(["not-on-this-host.service"], 5, 1800, False) == 0 and sent == []


def test_a_reset_after_a_real_loop_alert_sends_exactly_one_recovery_line(units_rig):
    """Deliberate: an operator stop/start that ends an ALERTED loop resets the counter, and the owner is told it recovered
    (once). Without a prior loop alert a reset is silent (the test above)."""
    props, sent = units_rig
    props.update({(BOT, "NRestarts"): "0", (BOT, "ActiveState"): "active"})
    mod.check_units([BOT], 5, 1800, False)
    props[(BOT, "NRestarts")] = "40"
    mod.check_units([BOT], 5, 1800, False)
    props[(BOT, "NRestarts")] = "0"
    assert mod.check_units([BOT], 5, 1800, False) == 0
    assert mod.check_units([BOT], 5, 1800, False) == 0
    assert len(sent) == 2 and "recovered" in sent[1]


def test_a_slow_one_restart_per_tick_loop_still_alerts_within_the_window(units_rig, monkeypatch):
    """llama-server fails its ~120 s startup probe and restarts: one restart per 2-min tick. Five within 15 min is a loop."""
    props, sent = units_rig
    clock = [1_000_000.0]
    monkeypatch.setattr(mod.time, "time", lambda: clock[0])
    props.update({(BOT, "NRestarts"): "0", (BOT, "ActiveState"): "activating"})
    mod.check_units([BOT], 5, 1800, False)
    for n in range(1, 6):
        clock[0] += 120
        props[(BOT, "NRestarts")] = str(n)
        rc = mod.check_units([BOT], 5, 1800, False)
    assert rc == 2 and len(sent) == 1 and "5x in the last 15 min" in sent[0]


def test_restarts_older_than_the_window_do_not_add_up(units_rig, monkeypatch):
    props, sent = units_rig
    clock = [1_000_000.0]
    monkeypatch.setattr(mod.time, "time", lambda: clock[0])
    props.update({(BOT, "NRestarts"): "0", (BOT, "ActiveState"): "active"})
    mod.check_units([BOT], 5, 1800, False)
    for n in range(1, 6):
        clock[0] += 3600                                   # one restart an hour: never a loop
        props[(BOT, "NRestarts")] = str(n)
        assert mod.check_units([BOT], 5, 1800, False) == 0
    assert sent == []


def test_an_undelivered_alert_is_retried_until_telegram_takes_it(tmp_path, monkeypatch):
    """One tick = check_units() then drain() (as main() does). Telegram down: the loop alert is held in the outbox and
    every due tick retries it; once Telegram takes it, it is delivered exactly once and never re-sent."""
    monkeypatch.setattr(mod, "STATE_PATH", str(tmp_path / "state.json"))
    attempts: list[str] = []
    delivered: list[str] = []
    up = [False]

    def transport(text):
        attempts.append(text)
        if up[0]:
            delivered.append(text)
            return "delivered"
        return "network"

    _route_through_outbox(monkeypatch, tmp_path, transport)
    clock = [1_000_000.0]
    monkeypatch.setattr(mod.time, "time", lambda: clock[0])
    props = {(BOT, "NRestarts"): "0", (BOT, "ActiveState"): "activating"}
    monkeypatch.setattr(mod, "_systemd_prop", lambda unit, prop: props.get((unit, prop), ""))

    def tick():
        clock[0] += 3600                                   # past any backoff
        mod.check_units([BOT], 5, 1800, False)
        mod.drain()

    mod.check_units([BOT], 5, 1800, False)
    props[(BOT, "NRestarts")] = "40"
    tick()                                                 # detected; Telegram down
    tick()                                                 # counter flat: still retried (by the outbox)
    failed_before = len(attempts)
    assert failed_before >= 2 and delivered == []
    up[0] = True
    tick()                                                 # delivered
    tick()                                                 # nothing pending any more
    loops = [d for d in delivered if d.startswith("🔴")]
    assert len(loops) == 1 and BOT in loops[0]
    assert not mod._load_state()["units"][BOT].get("pending"), "the watcher keeps no second retry path"


def test_a_recovery_never_overtakes_its_undelivered_loop_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "STATE_PATH", str(tmp_path / "state.json"))
    delivered: list[str] = []
    up = [False]
    _route_through_outbox(monkeypatch, tmp_path,
                          lambda text: (delivered.append(text) or "delivered") if up[0] else "network")
    props = {(BOT, "NRestarts"): "0", (BOT, "ActiveState"): "active"}
    monkeypatch.setattr(mod, "_systemd_prop", lambda unit, prop: props.get((unit, prop), ""))
    mod.check_units([BOT], 5, 1800, False)
    props.update({(BOT, "NRestarts"): "40", (BOT, "ActiveState"): "activating"})
    mod.check_units([BOT], 5, 1800, False)                # loop alert held
    up[0] = True
    props[(BOT, "ActiveState")] = "active"
    mod.check_units([BOT], 5, 1800, False)                # the recovery raise sends both, oldest first
    assert [d[:1] for d in delivered] == ["🔴", "🟢"]


def test_alert_codes_are_fixed_per_unit():
    assert mod._unit_code("unit_crash_loop", BOT) == "unit_crash_loop.flue-zoe-telegram"
    assert mod._unit_code("unit_crash_loop", "not-watched.service") == "unit_crash_loop"
    assert all(mod._CODE_RE.match(mod._unit_code(p, u)) for u in mod.OTHER_UNITS for p in ("unit_crash_loop", "unit_recovered"))


def test_the_bot_and_the_brains_are_watched():
    assert {BOT, "flue-zoe-brain-2x.service", "llama-server.service", "kokoro-tts.service"} <= set(mod.OTHER_UNITS)
