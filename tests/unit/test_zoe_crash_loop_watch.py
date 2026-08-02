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


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Isolate state + capture what would have been sent."""
    monkeypatch.setattr(mod, "STATE_PATH", str(tmp_path / "state.json"))
    sent: list[str] = []
    monkeypatch.setattr(mod, "_telegram", lambda text: sent.append(text) or True)

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


def test_alert_not_marked_delivered_when_send_fails(rig, monkeypatch):
    """If Telegram is down the alert must stay pending, so the next tick retries
    instead of the box going quiet about an ongoing outage."""
    configure, sent = rig
    configure(restarts=0, healthy=True, active="active")
    mod.check(threshold=5, cooldown=1800, dry_run=False)

    monkeypatch.setattr(mod, "_telegram", lambda text: False)
    configure(restarts=30, healthy=False)
    mod.check(threshold=5, cooldown=1800, dry_run=False)

    monkeypatch.setattr(mod, "_telegram", lambda text: sent.append(text) or True)
    configure(restarts=60, healthy=False)
    mod.check(threshold=5, cooldown=1800, dry_run=False)
    assert len(sent) == 1 and "crash-looping" in sent[0]
