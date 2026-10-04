"""Pins the Music Assistant idle-reap decision (scripts/maintenance/ma_idle_reap.py).

Pure function over player/queue states + stamps + a fake clock: no docker, no
network. Every guard has its own negative control, and the last test holds all
guards true and REQUIRES the stop — so the reaper cannot pass by refusing
everything.

FIXTURE PROVENANCE: player/queue shapes copied from the live MA 2.8.7 instance
(2026-10-04, `players/all` + `player_queues/all`); only the fields the decision
reads are kept.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "scripts" / "maintenance" / "ma_idle_reap.py"
spec = importlib.util.spec_from_file_location("ma_idle_reap", _SCRIPT)
reap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reap)

NOW = 1_791_100_000.0
HOUR = 3600.0
SONOS = "RINCON_38420B45B65001400"
PANEL = "up88a29e0a953f"


def _players(sonos="idle", panel="idle"):
    return [
        {"player_id": SONOS, "display_name": "Living Room", "state": sonos, "playback_state": sonos},
        {"player_id": PANEL, "display_name": "Zoe Panel (AirPlay)", "state": panel, "playback_state": panel},
    ]


def _queues(sonos_items=0, panel_items=0, newest=NOW - 3 * HOUR):
    return [
        {"queue_id": SONOS, "state": "idle", "items": sonos_items, "elapsed_time_last_updated": newest},
        {"queue_id": PANEL, "state": "idle", "items": panel_items, "elapsed_time_last_updated": NOW - 9 * HOUR},
    ]


def _decide(**over):
    kw = dict(flag_on=True, running=True, ma_readable=True, players=_players(), queues=_queues(),
              now=NOW, activity_epoch=NOW - 3 * HOUR, inflight_epoch=NOW - 3 * HOUR,
              idle_min=45, inflight_grace_s=120, quiet_window=None, local_hour=14)
    kw.update(over)
    return reap.decide(**kw)


def test_all_guards_pass_requires_a_stop():
    """The negative control for the whole function: idle for hours → stop."""
    action, reason = _decide()
    assert action == "stop" and reason == "idle_min=180"


def test_flag_off_is_keep():
    assert _decide(flag_on=False)[0] == "keep"


def test_already_stopped_is_keep():
    assert _decide(running=False)[0] == "keep"


def test_unreadable_ma_is_unknown_never_idle():
    assert _decide(ma_readable=False)[0] == "keep"


def test_playing_never_stops_even_when_stamps_are_ancient():
    action, reason = _decide(players=_players(sonos="playing"), activity_epoch=0, inflight_epoch=0,
                             queues=_queues(newest=0))
    assert action == "keep" and "Living Room is playing" in reason


def test_paused_with_a_queue_is_kept_but_paused_empty_is_idle():
    assert _decide(players=_players(panel="paused"), queues=_queues(panel_items=12))[0] == "keep"
    assert _decide(players=_players(panel="paused"), queues=_queues(panel_items=0))[0] == "stop"


def test_inflight_stamp_blocks_a_stop():
    assert _decide(inflight_epoch=NOW - 30)[0] == "keep"
    assert _decide(inflight_epoch=NOW - 121)[0] == "stop"


def test_recent_zoe_activity_stamp_resets_the_idle_clock():
    action, reason = _decide(activity_epoch=NOW - 10 * 60)
    assert action == "keep" and "10 min ago" in reason


def test_recent_ma_queue_change_resets_the_idle_clock():
    """MA's own elapsed_time_last_updated counts too — plays Zoe did not start."""
    assert _decide(queues=_queues(newest=NOW - 20 * 60))[0] == "keep"


def test_quiet_hours_protect_the_window_and_wrap_midnight():
    window = reap.parse_quiet_hours("22-07")
    assert window == (22, 7)
    assert _decide(quiet_window=window, local_hour=23)[0] == "keep"
    assert _decide(quiet_window=window, local_hour=3)[0] == "keep"
    assert _decide(quiet_window=window, local_hour=14)[0] == "stop"
    assert reap.parse_quiet_hours("") is None and reap.parse_quiet_hours("25-3") is None
    assert reap.in_quiet_hours(5, None) is False


def test_cli_dry_run_never_stops(monkeypatch, tmp_path, capsys):
    """End-to-end through main() with faked docker/MA: dry-run reports, --execute stops."""
    monkeypatch.setenv("ZOE_MA_IDLE_REAP", "1")
    monkeypatch.setenv("ZOE_MA_REAP_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("ZOE_MA_REAP_QUIET_HOURS", raising=False)
    calls = []

    def _docker(*args, timeout=30):
        calls.append(args)
        return 0, "true" if args[0] == "inspect" else ""
    monkeypatch.setattr(reap, "_docker", _docker)
    # main() reads the real clock, so MA's last state change is 3 h before it.
    import time as _t
    queues = _queues(newest=_t.time() - 3 * HOUR)
    monkeypatch.setattr(reap, "_ma_cmd",
                        lambda c: _players() if c == "players/all" else queues)
    assert reap.main([]) == 0
    assert "would-stop" in capsys.readouterr().out
    assert all(a[0] != "stop" for a in calls), "dry-run must never docker stop"
    assert not (tmp_path / "stopped").exists(), "dry-run must not stamp a stop"
    assert reap.main(["--execute"]) == 0
    assert ("stop", "zoe-music-assistant") in calls
    assert "MA_REAP stop idle_min=" in capsys.readouterr().out
    # The stamp ensure_running reads to distrust a pre-stop "seen up" (Codex P2).
    assert (tmp_path / "stopped").exists()


def test_execute_re_observes_under_the_lock_and_sees_a_native_play(monkeypatch, tmp_path, capsys):
    """Codex P2: a play started natively in MA (phone app, Sonos, AirPlay) between
    the snapshot and the stop critical section leaves NO Zoe stamp, so an
    inflight-only re-check passes and `docker stop` cuts a live stream. The whole
    snapshot -> decide() must run again under the lock: the second players/all
    answers 'playing' -> keep, no stop, no `stopped` stamp."""
    monkeypatch.setenv("ZOE_MA_IDLE_REAP", "1")
    monkeypatch.setenv("ZOE_MA_REAP_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("ZOE_MA_REAP_QUIET_HOURS", raising=False)
    calls = []

    def _docker(*args, timeout=30):
        calls.append(args)
        return 0, "true" if args[0] == "inspect" else ""
    monkeypatch.setattr(reap, "_docker", _docker)
    import time as _t
    queues = _queues(newest=_t.time() - 3 * HOUR)
    fetches = []

    def _ma_cmd(c):
        if c != "players/all":
            return queues
        fetches.append(c)
        # 1st fetch (before the lock): idle. 2nd (under the lock): someone pressed play.
        return _players() if len(fetches) == 1 else _players(sonos="playing")
    monkeypatch.setattr(reap, "_ma_cmd", _ma_cmd)

    assert reap.main(["--execute"]) == 0
    out = capsys.readouterr().out
    assert len(fetches) == 2, "player state must be re-fetched under the lock"
    assert "keep Living Room is playing (re-check under lock)" in out
    assert all(a[0] != "stop" for a in calls), "a play seen under the lock must veto the stop"
    assert not (tmp_path / "stopped").exists(), "no stop -> no `stopped` stamp"
