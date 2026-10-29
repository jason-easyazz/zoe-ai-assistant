"""scripts/landing - the voice loop's per-cycle decisions and the mid-probe head watcher (stacked on test_landing_lander_rules.py)."""
import json
import os
import shutil
import subprocess
import threading
import time

import pytest

from tests.unit.landing_fakes import LANDING, NEEDS_TOOLS, REPO, Rig, epoch

pytestmark = [pytest.mark.ci_safe, NEEDS_TOOLS]

DAY = "2026-10-29"
NOON = str(epoch(DAY, "12:00"))
HEAD = "b" * 40
SPEED_ONLY = dict(status="fail", said_vs_did_regressions=[], vad={"status": "pass"}, speed_comparison="compared")


@pytest.fixture()
def rig(tmp_path):
    return Rig(tmp_path)


def prs(rig, **pr):
    st = json.loads(rig.state_file.read_text())
    st["prs"]["7"] = {"headRefOid": HEAD, **pr}
    rig.set_state(st)
    return st


def snapshot(rig, pr="7", head=HEAD, **d):
    d.setdefault("revision", {"commit": head[:8]})
    (rig.home / "probe-artifacts").mkdir(exist_ok=True)
    (rig.home / "probe-artifacts" / f"{pr}.json").write_text(json.dumps(d))


def decide(rig, pr="7", rc="4"):
    script = f'. "{LANDING}/landing_lib.sh"; landing_requeue_decision {pr} {rc}'
    return subprocess.run(["bash", "-c", script], env=rig.env(), capture_output=True, text=True).stdout.strip()


def test_each_pr_is_judged_on_its_own_artifact_not_the_shared_slot(rig):
    # PR 7: speed-only fail; PR 8: a said-vs-did regression. The shared slot holds whichever probe ran LAST.
    st = prs(rig)
    st["prs"]["8"] = {"headRefOid": "d" * 40}
    rig.set_state(st)
    snapshot(rig, "7", **SPEED_ONLY)
    snapshot(rig, "8", head="d" * 40, status="fail", said_vs_did_regressions=["turn on the lights"],
             vad={"status": "pass"}, speed_comparison="compared")
    shared = rig.tmp / "shared.json"
    shared.write_text(json.dumps(dict(SPEED_ONLY, revision={"commit": "d" * 8})))   # as if the speed-only probe ran last
    script = f'. "{LANDING}/landing_lib.sh"; landing_requeue_decision 8 4'
    r = subprocess.run(["bash", "-c", script], env=rig.env(ZOE_VOICE_ARTIFACT=str(shared)), capture_output=True, text=True)
    assert r.stdout.strip() == "park-probe-failed"
    assert decide(rig, "7") == "retry-once"


def test_vad_failure_error_or_unproven_speed_is_not_a_speed_only_fail(rig):
    prs(rig)
    for variant in (dict(SPEED_ONLY, vad={"status": "fail"}), dict(SPEED_ONLY, status="error"),
                    dict(SPEED_ONLY, speed_comparison="refused: no baseline")):
        snapshot(rig, **variant)
        assert decide(rig) == "park-probe-failed", variant
    bare = {k: v for k, v in SPEED_ONLY.items() if k != "speed_comparison"}     # field absent: fail closed
    snapshot(rig, **bare)
    assert decide(rig) == "park-probe-failed"


def test_other_exit_codes_keep_the_old_requeue_behaviour(rig):
    prs(rig)
    for rc in ("5", "6", "7", "9", "10", ""):
        assert decide(rig, rc=rc) == "requeue"


def test_voice_loop_cycle_requeues_parks_and_retries(tmp_path):
    """after_voice_more.sh for real (one cycle, stub lander): requeue / park / retry-once / conflict-park."""
    rig = Rig(tmp_path)
    (rig.home / "voice-day-ok").write_text(DAY + "\n")
    shutil.copytree(LANDING, tmp_path / "landing-copy")
    (tmp_path / "landing-copy" / "land_voice_pr.sh").write_text(
        'case "$1" in 7) exit 4;; 8) exit 6;; 9) exit 5;; 10) exit 4;; esac; exit 0\n')
    heads = {"7": "7" * 40, "8": "8" * 40, "9": "9" * 40, "10": "a" * 40}
    rig.set_state({"prs": {"7": {"headRefOid": heads["7"]}, "8": {"headRefOid": heads["8"]},
                           "9": {"headRefOid": heads["9"], "mergeable": "CONFLICTING"},
                           "10": {"headRefOid": heads["10"]}}, "threads": {}})
    (rig.home / "voice-pending.txt").write_text("7 /w/7 b7 pr7\n8 /w/8 b8 pr8\n9 /w/9 b9 pr9\n10 /w/10 b10 pr10\n")
    snapshot(rig, "7", head=heads["7"], **SPEED_ONLY)                                              # speed-only
    snapshot(rig, "10", head=heads["10"], **dict(SPEED_ONLY, said_vs_did_regressions=["x"]))      # real regression
    env = rig.env(ZOE_LANDING_ONCE="1", ZOE_LANDING_LOOP_SLEEP="0", ZOE_LANDING_NO_BUSY_WAIT="1", ZOE_LANDING_NOW_EPOCH=NOON)
    cycle = lambda: subprocess.run(["bash", str(tmp_path / "landing-copy" / "after_voice_more.sh")], env=env, cwd=rig.repo,
                                   capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    queued = lambda: [ln.split()[0] for ln in (rig.home / "voice-pending.txt").read_text().splitlines()]
    r = cycle()
    assert r.returncode == 0, r.stdout + r.stderr
    assert queued() == ["7", "8"], "7 retried once, 8 requeued (rc 6); 9 parked (conflict); 10 parked (said-vs-did)"
    assert (rig.home / "needs-hand-merge.txt").read_text().split()[0] == "9"
    assert [ln.split()[0] for ln in (rig.home / "probe-failed.txt").read_text().splitlines()] == ["10"]
    r = cycle()                                       # second cycle: 7's retry is spent -> parked too
    assert r.returncode == 0, r.stdout + r.stderr
    assert queued() == ["8"]
    assert [ln.split()[0] for ln in (rig.home / "probe-failed.txt").read_text().splitlines()] == ["10", "7"]


# ---- a push DURING the probe aborts it ---------------------------------------------------------------------------

def watch(rig, cmd, poll="1"):
    script = f'. "{LANDING}/landing_lib.sh"; landing_run_watching_head 7 {HEAD} {cmd}; echo rc=$?'
    t0 = time.time()
    r = subprocess.run(["bash", "-c", script], env=rig.env(ZOE_LANDING_HEAD_POLL_S=poll), capture_output=True,
                       text=True, timeout=60)
    return r, time.time() - t0


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_head_moved_mid_probe_kills_it_and_returns_75(rig):
    prs(rig, headRefOid=HEAD)                       # the probe STARTS on the right head...
    pidfile = rig.tmp / "probe.pid"
    pushed = threading.Timer(2.5, lambda: prs(rig, headRefOid="f" * 40))   # ...and a push lands while it runs
    pushed.start()
    try:
        r, took = watch(rig, f"bash -c 'echo $$ > {pidfile}; exec sleep 40'")
    finally:
        pushed.cancel()
    assert "rc=75" in r.stdout, r.stdout + r.stderr
    assert 2.4 <= took < 25, "aborted at %.1fs: not before the push (2.5s), not long after it" % took   # an unchanged head must NOT kill it
    pid = int(pidfile.read_text())
    time.sleep(0.3)
    assert not alive(pid), "the probe process survived the abort"


def test_unchanged_head_lets_the_probe_finish_and_passes_its_rc_through(rig):
    prs(rig, headRefOid=HEAD)
    r, _ = watch(rig, "bash -c 'sleep 2; exit 3'")
    assert "rc=3" in r.stdout
    assert any(c.startswith("pr view 7") for c in rig.gh_calls()), "head was never polled"


def test_a_failing_gh_does_not_kill_a_running_probe(rig):
    st = prs(rig)
    st["pr_view_fails"] = True                      # API outage mid-probe: the final pre-arm head check fails closed instead
    rig.set_state(st)
    r, _ = watch(rig, "bash -c 'sleep 2; exit 0'")
    assert "rc=0" in r.stdout




def test_said_vs_did_regression_is_never_retried(rig):
    prs(rig)
    snapshot(rig, **dict(SPEED_ONLY, said_vs_did_regressions=["turn on the lights"]))
    assert decide(rig) == "park-probe-failed"



def test_missing_or_garbage_artifact_is_not_retryable(rig):
    prs(rig)
    assert decide(rig) == "park-probe-failed"             # no snapshot at all
    (rig.home / "probe-artifacts").mkdir()
    (rig.home / "probe-artifacts" / "7.json").write_text("{not json")
    assert decide(rig) == "park-probe-failed"




# ---- Kokoro is never stopped before the test lane is held --------------------------------------------------------
# 2026-10-29: the lander stopped Kokoro and THEN waited an hour for the (non-FIFO) lane = Zoe mute in the daytime.

def window(rig, wait_s="1"):
    log, lane, probe_log = rig.tmp / "systemctl.log", rig.tmp / "lane.lock", rig.tmp / "probe.log"
    script = (f'. "{LANDING}/landing_lib.sh"; probe(){{ echo PROBE >> "{probe_log}"; }}; '
              f'landing_probe_window {wait_s} 7 {HEAD} probe; echo rc=$?')
    env = rig.env(FAKE_SYSTEMCTL_LOG=str(log), ZOE_TEST_LANE_LOCK=str(lane), ZOE_LANDING_SETTLE_S="0")
    lane.touch()
    return script, env, log, lane, probe_log


def test_kokoro_is_stopped_only_while_the_lane_is_held_and_always_restarted(rig):
    prs(rig)
    script, env, log, _, probe_log = window(rig)
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert log.read_text().split("\n")[:2] == ["stop kokoro-tts.service lane_held=yes", "start kokoro-tts.service lane_held=yes"]
    assert probe_log.read_text().strip() == "PROBE"


def test_a_lane_we_cannot_get_leaves_kokoro_untouched(rig):
    prs(rig)
    script, env, log, lane, probe_log = window(rig, wait_s="1")
    holder = subprocess.Popen(["flock", "-x", str(lane), "sleep", "20"])
    try:
        time.sleep(0.5)
        r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    finally:
        holder.kill()
    assert "rc=9" in r.stdout, r.stdout + r.stderr
    assert not log.exists() or log.read_text() == "", "Kokoro was touched without the lane"
    assert not probe_log.exists()


def test_the_lander_has_no_direct_kokoro_stop_only_the_lane_first_helper():
    code = "\n".join(ln for ln in (LANDING / "land_voice_pr.sh").read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert "stop kokoro" not in code and "landing_probe_window" in code and "/tmp/zoe-test-lane.lock" not in code


# ---- review-thread fixes (2026-10-29) ----------------------------------------------------------------------------

def test_pre_stop_failure_after_the_lane_wait_leaves_kokoro_untouched_and_frees_the_lane(rig):
    prs(rig)
    script, env, log, lane, probe_log = window(rig)
    script = script.replace("landing_probe_window", "landing_pre_stop(){ return 1; }; landing_probe_window", 1)
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=30)
    assert "rc=9" in r.stdout and not probe_log.exists() and (not log.exists() or log.read_text() == "")
    assert subprocess.run(["flock", "-n", str(lane), "true"]).returncode == 0, "the lane stayed locked"


def test_an_artifact_older_than_this_attempt_is_not_fresh(rig):
    art = rig.tmp / "a.json"
    art.write_text("{}")
    t = int(art.stat().st_mtime)
    ok = lambda t0: subprocess.run(["bash", "-c", f'. "{LANDING}/landing_lib.sh"; landing_artifact_fresh "{art}" {t0}']).returncode == 0
    assert ok(t - 5) and not ok(t + 5)


def test_a_parked_head_is_not_refilled_by_autoland_until_a_push(rig):
    prs(rig)
    snapshot(rig, **dict(SPEED_ONLY, said_vs_did_regressions=["x"]))
    assert decide(rig) == "park-probe-failed"
    parked = lambda: subprocess.run(["bash", "-c", f'. "{LANDING}/landing_lib.sh"; landing_head_parked 7'], env=rig.env()).returncode == 0
    assert parked()
    prs(rig, headRefOid="c" * 40)
    assert not parked()


def run_once(rig, script, cwd, **extra):
    return subprocess.run(["bash", str(LANDING / script)], env=rig.env(ZOE_LANDING_ONCE="1", ZOE_WATCHD_ONCE="1",
                          ZOE_LANDING_NOW_EPOCH=NOON, FAKE_GH_CWD_LOG=str(rig.tmp / "cwd.log"), **extra),
                          cwd=cwd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("script", ["autoland.sh", "watchd.sh"])
def test_gh_loops_select_the_configured_checkout_whatever_the_launch_dir(rig, script, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    run_once(rig, script, elsewhere)
    assert {ln for ln in (rig.tmp / "cwd.log").read_text().split()} == {str(rig.repo)}


def test_autoland_queues_a_voice_pr_but_not_a_head_the_loop_parked(rig):
    prs(rig, files=["services/zoe-data/routers/voice_tts.py"])
    pending = rig.home / "voice-pending.txt"
    run_once(rig, "autoland.sh", rig.repo, ZOE_LANDING_REPO=str(REPO))
    assert pending.read_text().startswith("7 "), "positive control: an unparked voice PR is queued"
    pending.write_text("")
    (rig.home / "probe-failed-heads.txt").write_text(f"7:{HEAD[:12]}\n")
    run_once(rig, "autoland.sh", rig.repo, ZOE_LANDING_REPO=str(REPO))
    assert pending.read_text() == "", "a parked head must not be refilled"
