"""The DAYTIME REHEARSAL of the pendant re-pass (``night_window.sh --rehearse-repass``): the real stops, compaction and restore order with the real job on ONE recording, writing nothing.
Owner rule 2026-11-09: no night job's first run is unattended at night. Host-seam doubles from test_night_window.py.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "scripts" / "perf"))
sys.path.insert(0, str(REPO / "scripts" / "night"))

import night_window as nw  # noqa: E402
from test_night_window import _private_locks, at, make, wake_order  # noqa: E402,F401

ARGV = ["--rehearse-repass"]


def stand_in(tmp_path):
    p = tmp_path / "zoe-omi-repass.py"
    p.write_text("# stand-in")
    return {"omi_repass_script": p, "repass_preflight": False, "unit_env_file": tmp_path / "absent.env"}        # the preflight and the env file have their own tests below


def idle_box(host, busy=False):
    """The double answers night_busy.sh: rc 0 = voice work must pause."""
    orig = host.run

    def run(argv, timeout=60.0, mutating=True, env=None):
        if argv and str(argv[0]).endswith("night_busy.sh"):
            host.cmds.append((list(argv), mutating))
            return nw.bk.Result(0 if busy else 1, "")
        return orig(argv, timeout, mutating, env)
    host.run = run


def test_the_rehearsal_stops_the_stack_runs_the_job_with_rehearse_on_one_recording_and_restores_in_order(tmp_path):
    w, host, cfg = make(tmp_path, argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(11, 0))
    idle_box(host)
    assert cfg.rehearse and cfg.pre_jobs == ("omi_repass",) and cfg.cap_min <= 25.0 and not cfg.jobs and not cfg.fallback_4b
    assert w.run() == nw.EXIT_OK, w.outcome
    assert [j["name"] for j in host.jobs_run] == ["zoe-omi-repass.py"]                              # the one job; no 12B, no digest, no dreaming, no 4B fallback
    j = host.jobs_run[0]
    assert "--rehearse" in j["argv"] and "--budget-s" in j["argv"]
    assert host.idx("--user stop") < host.idx("zoe-omi-repass.py")                                  # asleep first (zoe-data, router, Kokoro, 4B)
    assert not any("--ctx-size" in c for c in host.joined())                                        # the 12B was never started
    assert wake_order(host) == list(nw.RESTORE_ORDER)                                              # the night's restore order
    assert any("pre-jobs done" in e["msg"] for e in w.rec["events"])                                # compacted and re-measured, like the night
    rec = next(r for r in w.jobs if r["name"] == "omi_repass")
    assert rec["backend"] == "pre" and rec["best_effort"] is False


def test_a_rehearsal_that_does_not_pass_fails_the_run_loudly_and_still_restores(tmp_path):
    w, host, cfg = make(tmp_path, argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(11, 0), job_rc={"zoe-omi-repass.py": 4})
    idle_box(host)
    assert w.run() == nw.EXIT_JOBS_FAILED
    assert wake_order(host) == list(nw.RESTORE_ORDER)


def test_the_rehearsal_refuses_the_night_hours_and_a_busy_voice_gate_before_anything_is_stopped(tmp_path):
    for hh, mm in ((1, 50), (3, 0), (4, 54)):
        w, host, cfg = make(tmp_path / f"h{hh}{mm}", argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(hh, mm))
        idle_box(host)
        assert w.run() == nw.EXIT_REFUSED and not host.jobs_run and host.count("--user stop") == 0, (hh, mm)
    w, host, cfg = make(tmp_path / "busy", argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(11, 0))
    idle_box(host, busy=True)
    assert w.run() == nw.EXIT_REFUSED and not host.jobs_run and host.count("--user stop") == 0
    assert any("voice-day-ok" in e["msg"] for e in w.rec["events"])                                  # the refusal names the owner's switch
    (tmp_path / "ok").mkdir()
    w2, host2, _ = make(tmp_path / "ok", argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(14, 0))        # control: the same box in the afternoon is allowed
    idle_box(host2)
    assert w2.run() == nw.EXIT_OK, w2.outcome


def test_without_the_switch_nothing_is_a_rehearsal(tmp_path):
    w, host, cfg = make(tmp_path, cfg_kw=stand_in(tmp_path))
    assert not cfg.rehearse and nw.build_pre_jobs(cfg) == [] and "--rehearse" not in " ".join(sum((j.argv for j in nw.build_pre_jobs(cfg)), []))


# ── 2026-11-11 rehearsal findings: the headline, the up-front preflight, the env file, the redacted setup error ──────────────

import importlib.util  # noqa: E402
import json  # noqa: E402


def preflight_cfg(tmp_path):
    setup = tmp_path / "setup_omi_repass.sh"
    setup.write_text("# stand-in")
    return {**stand_in(tmp_path), "repass_preflight": True, "repass_setup_script": setup}


def test_the_headline_says_failed_when_the_job_failed_in_the_rehearsal_and_in_a_real_night(tmp_path):
    w, host, _ = make(tmp_path, argv=ARGV, cfg_kw=stand_in(tmp_path), start=at(11, 0), job_rc={"zoe-omi-repass.py": 4})
    idle_box(host)
    rc = w.run()
    assert rc == nw.EXIT_JOBS_FAILED and w.outcome.startswith("failed") and "omi_repass" in w.outcome
    md = (tmp_path / "reports" / f"{w.started_iso[:10]}-rehearse.md").read_text()
    js = json.loads((tmp_path / "reports" / f"{w.started_iso[:10]}-rehearse.json").read_text())
    assert "**Outcome: failed" in md and "Outcome: ok" not in md and js["outcome"].startswith("failed") and js["exit"] == nw.EXIT_JOBS_FAILED
    (tmp_path / "ok").mkdir()                                                                                  # control: a passing rehearsal still reads ok
    w2, host2, _ = make(tmp_path / "ok", argv=ARGV, cfg_kw=stand_in(tmp_path / "ok"), start=at(11, 0))
    idle_box(host2)
    assert w2.run() == nw.EXIT_OK and w2.outcome == "ok"
    (tmp_path / "realnight").mkdir()                                                                               # the real night: a failed job is not "ok" either
    w3, host3, _ = make(tmp_path / "realnight", argv=["--no-fallback"], job_rc={"night_digest.py": 1})
    rc3 = w3.run()
    assert rc3 == nw.EXIT_JOBS_FAILED and w3.outcome.startswith("failed") and "digest" in w3.outcome


def test_classify_outcome_keeps_what_the_run_concluded_and_never_calls_a_bad_exit_ok():
    ok_job = {"name": "digest", "backend": "12B", "status": "ok"}
    assert nw.classify_outcome("ok", nw.EXIT_OK, [ok_job], []) == "ok"
    assert nw.classify_outcome("ok", nw.EXIT_OK, [{"name": "digest", "backend": "12B", "status": "degraded"}], []).startswith("degraded")
    assert nw.classify_outcome("ok", nw.EXIT_RESTORE_FAILED, [ok_job], []).startswith("failed")
    assert nw.classify_outcome("ok", nw.EXIT_JOBS_FAILED, [ok_job], ["x"]).startswith("failed")
    assert nw.classify_outcome("refused: hour", nw.EXIT_REFUSED, [], []) == "refused: hour"


def test_a_rehearsal_without_the_runtime_is_refused_before_anything_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:pw@127.0.0.1:5432/db")
    monkeypatch.setattr(nw, "tcp_reachable", lambda url, timeout_s=3.0: True)
    w, host, _ = make(tmp_path, argv=ARGV, cfg_kw=preflight_cfg(tmp_path), start=at(11, 0), fail_run=("--check",))
    idle_box(host)
    assert w.run() == nw.EXIT_REFUSED
    assert host.count("--user stop") == 0 and not host.jobs_run                                              # not one second of silence
    assert any(nw.REPASS_NOT_INSTALLED in e["msg"] for e in w.rec["events"])
    (tmp_path / "ok").mkdir()                                                                                  # control: the runtime is there, the same rehearsal runs
    w2, host2, _ = make(tmp_path / "ok", argv=ARGV, cfg_kw=preflight_cfg(tmp_path / "ok"), start=at(11, 0))
    idle_box(host2)
    assert w2.run() == nw.EXIT_OK, w2.outcome
    assert host2.idx("--check") < host2.idx("--user stop") < host2.idx("zoe-omi-repass.py")                    # checked first, stopped second


def test_a_missing_database_credential_or_unreachable_database_is_refused_up_front(tmp_path, monkeypatch):
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    w, host, _ = make(tmp_path, argv=ARGV, cfg_kw=preflight_cfg(tmp_path), start=at(11, 0))
    idle_box(host)
    assert w.run() == nw.EXIT_REFUSED and host.count("--user stop") == 0
    assert any("POSTGRES_URL is not set" in e["msg"] for e in w.rec["events"])
    (tmp_path / "down").mkdir()
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:topsecret@127.0.0.1:5432/db")
    monkeypatch.setattr(nw, "tcp_reachable", lambda url, timeout_s=3.0: False)
    w2, host2, _ = make(tmp_path / "down", argv=ARGV, cfg_kw=preflight_cfg(tmp_path / "down"), start=at(11, 0))
    idle_box(host2)
    assert w2.run() == nw.EXIT_REFUSED and host2.count("--user stop") == 0
    assert "topsecret" not in json.dumps(w2.rec)                                                              # the message never carries the URL


def test_a_real_night_without_the_runtime_skips_only_the_repass_slot_and_the_other_jobs_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "on")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:pw@127.0.0.1:5432/db")
    monkeypatch.setattr(nw, "tcp_reachable", lambda url, timeout_s=3.0: True)
    w, host, _ = make(tmp_path, argv=["--pre-jobs", "omi_repass"], cfg_kw=preflight_cfg(tmp_path), fail_run=("--check",))
    assert w.run() == nw.EXIT_OK, w.outcome
    names = [j["name"] for j in host.jobs_run]
    assert "zoe-omi-repass.py" not in names and "zoe-nightly-dreaming.py" in names                            # the other jobs proceeded
    rec = next(j for j in w.jobs if j["name"] == "omi_repass")
    assert rec["status"] == "skipped" and nw.REPASS_NOT_INSTALLED in rec["notes"][0]
    assert any("omi_repass SKIPPED" in n for n in w.rec["notes"])
    assert host.idx("--check") < host.idx("--user stop")                                                      # decided before the first stop
    (tmp_path / "off").mkdir()                                                                                 # control: flag off = the job is a no-op, no runtime needed, no --check
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "off")
    w2, host2, _ = make(tmp_path / "off", argv=["--pre-jobs", "omi_repass"], cfg_kw=preflight_cfg(tmp_path / "off"), fail_run=("--check",))
    assert w2.run() == nw.EXIT_OK and host2.count("--check") == 0


def test_a_hand_run_rehearsal_loads_the_units_env_file_names_only_and_a_night_run_does_not(tmp_path, monkeypatch, capsys):
    envf = tmp_path / "unit.env"
    envf.write_text("# c\nPOSTGRES_URL=postgresql://u:s3cr3tvalue@h/db\nZOE_X=\"quoted\"\nexport IGNORED=1\nbad line\n")
    monkeypatch.setenv("POSTGRES_URL", "stale")
    monkeypatch.delenv("ZOE_X", raising=False)
    monkeypatch.delenv("IGNORED", raising=False)
    kw = {**stand_in(tmp_path), "unit_env_file": envf}
    import os
    (tmp_path / "realnight").mkdir()
    make(tmp_path / "realnight", argv=[], cfg_kw=kw)
    assert os.environ["POSTGRES_URL"] == "stale" and "ZOE_X" not in os.environ                                # a night run: the unit supplies the env, the tool does not read the file
    make(tmp_path, argv=ARGV, cfg_kw=kw, start=at(11, 0))
    assert os.environ["POSTGRES_URL"].endswith("@h/db") and os.environ["ZOE_X"] == "quoted" and "IGNORED" not in os.environ
    env = {}
    assert nw.load_unit_env(envf, env) == ["POSTGRES_URL", "ZOE_X"]                                           # names, never values
    assert nw.load_unit_env(tmp_path / "missing.env", env) == []
    monkeypatch.delenv("ZOE_X", raising=False)


def _repass_script():
    spec = importlib.util.spec_from_file_location("zoe_omi_repass_cli", REPO / "scripts" / "maintenance" / "zoe-omi-repass.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_setup_error_names_what_failed_with_every_dsn_password_and_token_scrubbed(monkeypatch):
    mod = _repass_script()
    monkeypatch.setenv("POSTGRES_URL", "postgresql://zoe:Sup3rS3cretPw@localhost:5432/zoe")
    raw = ("could not connect to postgresql://zoe:Sup3rS3cretPw@localhost:5432/zoe: password authentication failed; password=hunter2hunter2 "
           "token: abcdef0123456789abcdef0123456789abcd Bearer xyz123 secret Sup3rS3cretPw")
    out = mod.redact_message(raw)
    for leak in ("Sup3rS3cretPw", "hunter2", "abcdef0123456789", "xyz123", "postgresql://"):
        assert leak not in out, (leak, out)
    assert "password authentication failed" in out and "could not connect" in out                              # still says WHAT went wrong
    assert mod.redact_message("POSTGRES_URL environment variable is not set. Cannot initialize PostgreSQL connection pool.").startswith("POSTGRES_URL environment variable is not set")


def test_init_pool_failure_reports_a_redacted_message_not_just_the_class(monkeypatch):
    import asyncio
    import types
    mod = _repass_script()
    fake_rp = types.ModuleType("omi_repass")
    fake_pool = types.ModuleType("db_pool")

    async def boom():
        raise RuntimeError("connect failed postgresql://zoe:Sup3rS3cretPw@127.0.0.1/zoe")

    async def noop():
        return None
    fake_pool.init_pool, fake_pool.close_pool = boom, noop
    monkeypatch.setitem(sys.modules, "omi_repass", fake_rp)
    monkeypatch.setitem(sys.modules, "db_pool", fake_pool)
    rc, summary = asyncio.run(mod.amain([]))
    assert rc == 1 and summary["setup_error"] == "RuntimeError" and "connect failed" in summary["setup_message"]
    assert "Sup3rS3cretPw" not in json.dumps(summary)


def test_a_missing_repass_entry_point_is_a_preflight_failure_before_any_stop(tmp_path):
    kw = preflight_cfg(tmp_path)
    kw["omi_repass_script"] = tmp_path / "absent" / "zoe-omi-repass.py"
    w, host, _ = make(tmp_path, argv=ARGV, cfg_kw=kw, start=at(11, 0))
    idle_box(host)
    assert w.run() == nw.EXIT_REFUSED and host.count("--user stop") == 0 and not host.jobs_run
    assert any("zoe-omi-repass.py not found" in e["msg"] for e in w.rec["events"])


def test_a_best_effort_timeout_or_a_preflight_skip_is_degraded_never_ok():
    timed_out = {"name": "omi_repass", "backend": "pre", "status": "timeout", "best_effort": True}
    assert nw.classify_outcome("ok", nw.EXIT_OK, [timed_out], []).startswith("degraded")
    assert nw.classify_outcome("ok", nw.EXIT_OK, [{"name": "omi_repass", "backend": "pre", "status": "skipped", "preflight_skip": True}], []).startswith("degraded")
    assert nw.classify_outcome("ok", nw.EXIT_OK, [{"name": "digest", "backend": "12B", "status": "skipped"}, {"name": "x", "backend": "12B", "status": "idle"}], []) == "ok"


def test_redaction_catches_quoted_and_space_delimited_secrets_that_are_not_in_the_environment(monkeypatch):
    mod = _repass_script()
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    out = mod.redact_message("FATAL: authentication failed for password 'hunter2hunter2'; token abc123 rejected; pwd is \"zz9top\"")
    for leak in ("hunter2", "abc123", "zz9top"):
        assert leak not in out, (leak, out)
    assert "authentication failed" in out and "rejected" in out


def test_a_slow_preflight_check_that_makes_the_box_busy_stops_nothing(tmp_path, monkeypatch):
    """Codex P1 on #2407: the check takes minutes; the quiet / busy clearance must come AFTER it, immediately before the first stop."""
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "on")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://u:pw@127.0.0.1:5432/db")
    monkeypatch.setattr(nw, "tcp_reachable", lambda url, timeout_s=3.0: True)
    w, host, _ = make(tmp_path, argv=["--pre-jobs", "omi_repass"], cfg_kw=preflight_cfg(tmp_path))
    orig = host.run

    def run(argv, timeout=60.0, mutating=True, env=None):
        if "--check" in argv:
            host.t += 200.0                         # the slow check ...
            host.panel_busy = True                  # ... during which a voice turn starts and a timer job begins
            host.busy.add("zoe-backup.service")
        return orig(argv, timeout, mutating, env)
    host.run = run
    assert w.run() == nw.EXIT_REFUSED
    assert host.count("--user stop") == 0 and not host.jobs_run
