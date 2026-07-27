"""Pin the voice replay-gate skip-streak alarm + the unit's bounded Postgres wait.

Issue #6093: the probe exits 0 on the low-memory skip, so systemd records
SUCCESS on every run and the gate can skip forever under green timers (last
real pass 2026-07-21 while the 07-21/07-22 timer fires both skipped, silently).
Separately, the user unit fired before the dockerized Postgres was up at boot
(`After=docker.service` cannot order a USER unit) and recorded status=error runs.

Contracts pinned here:
  * every result artifact carries `non_pass_streak` / `non_pass_alert_after` /
    `non_pass_alert`; a real pass resets the streak to 0;
  * N consecutive skips flip `non_pass_alert` true AND make the skip path exit
    4 (not 0) so the oneshot unit/timer goes visibly red — never silent SUCCESS;
  * a missing / corrupt / pre-streak previous artifact starts the count at this
    run (no crash, no invented alarm);
  * `wait_for_port` returns True on connect, False once its bounded deadline
    passes — and the unit template actually gates on it before the probe.

Pure logic only — NO live DB, NO replay run, NO Kokoro load, NO real sockets.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


vrp = _load("voice_regression_probe_streak", "scripts/maintenance/voice_regression_probe.py")
wfp = _load("wait_for_port_mod", "scripts/maintenance/wait_for_port.py")


def _args(tmp_path: Path, alert_after: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        baseline=tmp_path / "baseline.json",
        results=tmp_path / "last.json",
        trend=tmp_path / "trend.jsonl",
        alert_after_non_pass=alert_after,
    )


def _emit(args, status: str) -> dict:
    return vrp.emit_result(args, status=status, summary=dict(vrp.EMPTY_SUMMARY),
                           said_vs_did=[], speed_deltas={}, baseline={}, reason="t")


class TestNonPassStreak:
    def test_streak_increments_and_alerts_at_threshold(self, tmp_path):
        args = _args(tmp_path, alert_after=3)
        assert (_emit(args, "skip"))["non_pass_streak"] == 1
        assert (_emit(args, "skip"))["non_pass_streak"] == 2
        third = _emit(args, "skip")
        assert third["non_pass_streak"] == 3
        assert third["non_pass_alert"] is True
        assert third["non_pass_alert_after"] == 3

    def test_below_threshold_no_alert(self, tmp_path):
        args = _args(tmp_path, alert_after=3)
        for expected in (1, 2):
            payload = _emit(args, "skip")
            assert payload["non_pass_streak"] == expected
            assert payload["non_pass_alert"] is False

    def test_pass_resets_streak(self, tmp_path):
        args = _args(tmp_path)
        _emit(args, "skip"), _emit(args, "error"), _emit(args, "fail")
        passed = _emit(args, "pass")
        assert passed["non_pass_streak"] == 0
        assert passed["non_pass_alert"] is False
        # and the counter restarts from 1 after the reset
        assert (_emit(args, "skip"))["non_pass_streak"] == 1

    def test_fail_and_error_count_toward_streak(self, tmp_path):
        args = _args(tmp_path)
        _emit(args, "error")
        _emit(args, "fail")
        assert (_emit(args, "skip"))["non_pass_streak"] == 3

    def test_pre_streak_artifact_starts_count_at_this_run(self, tmp_path):
        args = _args(tmp_path)
        args.results.write_text(json.dumps({"status": "skip"}) + "\n")  # old-format artifact
        assert (_emit(args, "skip"))["non_pass_streak"] == 1

    def test_corrupt_artifact_never_crashes_or_invents_alarm(self, tmp_path):
        args = _args(tmp_path)
        args.results.write_text("{not json")
        payload = _emit(args, "skip")
        assert payload["non_pass_streak"] == 1
        assert payload["non_pass_alert"] is False

    def test_trend_lines_carry_streak_fields(self, tmp_path):
        args = _args(tmp_path)
        _emit(args, "skip")
        _emit(args, "skip")
        lines = [json.loads(l) for l in args.trend.read_text().splitlines()]
        assert [l["non_pass_streak"] for l in lines] == [1, 2]
        assert all("non_pass_alert" in l for l in lines)


class TestSkipPathExitCode:
    """The acceptance criterion itself: N consecutive skips => the unit goes red."""

    def _run_main(self, monkeypatch, tmp_path):
        monkeypatch.setattr(vrp, "_acquire_harness_lock", lambda: None)
        monkeypatch.setattr(vrp, "mem_available_mb", lambda: 358)  # < 1500 => skip
        monkeypatch.setattr(vrp, "_resolve_service_dir", lambda _x: tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "voice_regression_probe.py",
            "--results", str(tmp_path / "last.json"),
            "--trend", str(tmp_path / "trend.jsonl"),
            "--baseline", str(tmp_path / "baseline.json"),
            "--alert-after-non-pass", "3",
        ])
        return vrp.main()

    def test_skips_green_until_streak_then_exit_4(self, monkeypatch, tmp_path):
        assert self._run_main(monkeypatch, tmp_path) == 0   # streak 1
        assert self._run_main(monkeypatch, tmp_path) == 0   # streak 2
        assert self._run_main(monkeypatch, tmp_path) == 4   # streak 3 => visibly red
        artifact = json.loads((tmp_path / "last.json").read_text())
        assert artifact["status"] == "skip"                 # still a skip, never a fake fail
        assert artifact["non_pass_alert"] is True


class TestWaitForPort:
    def test_true_on_immediate_connect(self):
        closed = []
        conn = SimpleNamespace(close=lambda: closed.append(True))
        assert wfp.wait_for_port("h", 5432, 1.0, _connect=lambda *a, **k: conn,
                                 _sleep=lambda s: None, _monotonic=lambda: 0.0) is True
        assert closed == [True]

    def test_false_after_bounded_deadline(self):
        clock = {"t": 0.0}

        def _monotonic():
            return clock["t"]

        def _sleep(s):
            clock["t"] += s

        def _connect(*a, **k):
            raise OSError("refused")

        assert wfp.wait_for_port("h", 5432, 30.0, 5.0, _connect=_connect,
                                 _sleep=_sleep, _monotonic=_monotonic) is False
        assert clock["t"] <= 35.0  # bounded — never spins forever

    def test_recovers_when_port_comes_up_mid_wait(self):
        clock = {"t": 0.0}
        attempts = {"n": 0}

        def _connect(*a, **k):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise OSError("refused")
            return SimpleNamespace(close=lambda: None)

        assert wfp.wait_for_port("h", 5432, 60.0, 5.0, _connect=_connect,
                                 _sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
                                 _monotonic=lambda: clock["t"]) is True
        assert attempts["n"] == 3


class TestUnitTemplateGatesOnPostgres:
    def test_unit_has_bounded_wait_before_probe(self):
        unit = (REPO / "scripts" / "setup" / "systemd" / "zoe-voice-regression.service").read_text()
        wait_at = unit.index("wait_for_port.py")
        assert "--port 5432" in unit
        assert "--timeout" in unit
        # the wait is an ExecStartPre and precedes the probe ExecStart
        assert wait_at < unit.index("voice_regression_probe.py --samples")
        pre_line = next(l for l in unit.splitlines() if "wait_for_port.py" in l)
        assert pre_line.startswith("ExecStartPre=")


class TestSkipDiagnosisReportsWhatItObserved:
    """A probe must never NAME a cause it did not check.

    The skip branch used to hardcode "no .env in resolved --service-dir" as the
    reason measure_voice produced no JSON. On 2026-07-27 the real cause was
    Postgres not yet listening after a reboot, while the .env was present and
    correct the whole time — so every run in the log pointed at a healthy file
    and the actual dependency went unmentioned. A wrong cause is worse than no
    cause: it sends you to the wrong place.
    """

    def test_reports_env_present_when_it_is_present(self, tmp_path):
        (tmp_path / ".env").write_text("X=1")
        obs = "; ".join(vrp._diagnose_skip(str(tmp_path)))
        assert ".env present" in obs
        assert "NO .env" not in obs, "must not claim a missing .env that exists"

    def test_reports_env_missing_when_it_is_missing(self, tmp_path):
        obs = "; ".join(vrp._diagnose_skip(str(tmp_path)))
        assert "NO .env" in obs

    def test_replay_path_mirrors_measure_voice_resolution(self, tmp_path):
        """service_dir/tests/replay_samples.py — the path measure_voice.py itself uses."""
        obs = "; ".join(vrp._diagnose_skip(str(tmp_path)))
        assert "MISSING at" in obs and "tests/replay_samples.py" in obs
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "replay_samples.py").write_text("")
        assert "replay harness present" in "; ".join(vrp._diagnose_skip(str(tmp_path)))

    def test_postgres_state_is_probed_not_assumed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vrp, "_port_open", lambda *a, **k: False)
        assert "postgres 127.0.0.1:5432 REFUSED" in "; ".join(vrp._diagnose_skip(str(tmp_path)))
        monkeypatch.setattr(vrp, "_port_open", lambda *a, **k: True)
        assert "postgres 127.0.0.1:5432 reachable" in "; ".join(vrp._diagnose_skip(str(tmp_path)))

    def test_remote_mode_observes_token_and_endpoint(self, tmp_path, monkeypatch):
        """Remote mode's own failure modes must be in the observation list.

        A missing ZOE_DEVICE_TOKEN makes the replay exit 1 before any sample runs;
        the diagnosis previously listed only .env/harness/postgres, so the one
        thing actually wrong was the one thing not named (Bugbot, #1572).
        """
        monkeypatch.delenv("ZOE_DEVICE_TOKEN", raising=False)
        monkeypatch.delenv("DEVICE_TOKEN", raising=False)
        monkeypatch.setattr(vrp, "_port_open", lambda h, p_, timeout=2.0: True)
        obs = "; ".join(vrp._diagnose_skip(str(tmp_path), "remote"))
        assert "ZOE_DEVICE_TOKEN MISSING" in obs
        assert "zoe-data 127.0.0.1:8000 reachable" in obs
        monkeypatch.setenv("ZOE_DEVICE_TOKEN", "x")
        assert "ZOE_DEVICE_TOKEN present" in "; ".join(vrp._diagnose_skip(str(tmp_path), "remote"))
        # inprocess mode must NOT name remote-only observations
        assert "ZOE_DEVICE_TOKEN" not in "; ".join(vrp._diagnose_skip(str(tmp_path), "inprocess"))

    def test_the_field_failure_names_postgres_not_the_env(self, tmp_path, monkeypatch):
        """The exact 2026-07-27 state: .env fine, harness fine, database down."""
        (tmp_path / ".env").write_text("X=1")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "replay_samples.py").write_text("")
        monkeypatch.setattr(vrp, "_port_open", lambda *a, **k: False)
        obs = "; ".join(vrp._diagnose_skip(str(tmp_path)))
        assert "REFUSED" in obs
        assert ".env present" in obs and "NO .env" not in obs


class TestPerModeMemoryThreshold:
    """The min-mem default follows the STT mode, both values measured not guessed.

    inprocess carries its own Moonshine: 1500MB (historical empirical bar).
    remote rides the live service's warm model: 700MB, against a measured 445MB
    peak for a real 2-sample remote run on the live box (2026-07-27).
    An explicit flag or ZOE_VOICE_PROBE_MIN_MEM_MB always wins.
    """

    def _default_for(self, monkeypatch, stt, env=None):
        monkeypatch.delenv("ZOE_VOICE_PROBE_MIN_MEM_MB", raising=False)
        if env is not None:
            monkeypatch.setenv("ZOE_VOICE_PROBE_MIN_MEM_MB", env)
        # the REAL function main() calls — not a mirror of its expression
        return vrp.resolve_min_mem(stt)

    def test_inprocess_default_is_1500(self, monkeypatch):
        assert self._default_for(monkeypatch, "inprocess") == 1500

    def test_remote_default_is_700(self, monkeypatch):
        assert self._default_for(monkeypatch, "remote") == 700

    def test_env_override_wins_either_mode(self, monkeypatch):
        assert self._default_for(monkeypatch, "remote", env="1200") == 1200
        assert self._default_for(monkeypatch, "inprocess", env="800") == 800
