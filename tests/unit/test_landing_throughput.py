"""scripts/landing - voice-landing throughput (2026-11-10): voice hold, front re-queue, reality-derived CI wait, chat drivers yield.
Why each rule exists (the measured disarms / false probe fails): docs/knowledge/landing-tooling.md."""
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from tests.unit.landing_fakes import LANDING, NEEDS_TOOLS, REPO, Rig, epoch

pytestmark = [pytest.mark.ci_safe, NEEDS_TOOLS]

DAY = "2026-10-29"
NOON = str(epoch(DAY, "12:00"))
HEAD = "b" * 40
GREEN = {"validate": "pass", "secret-scan": "pass"}
QUIET = LANDING / "quiet_for_probe.py"


@pytest.fixture()
def rig(tmp_path):
    return Rig(tmp_path)


def set_prs(rig, **prs):
    rig.set_state({"prs": {k: v for k, v in prs.items()}, "threads": {}})


def lib(rig, body, timeout=30, **extra):
    env = rig.env(**{"ZOE_LANDING_WAIT_STEP_S": "0", "ZOE_LANDING_NOW_EPOCH": NOON, **extra})
    return subprocess.run(["bash", "-c", f'. "{LANDING}/landing_lib.sh"; {body}'], env=env, capture_output=True, text=True, timeout=timeout)


def calls(rig, prefix):
    return [c for c in rig.gh_calls() if c.startswith(prefix)]


def qlog(rig):
    p = rig.home / "logs" / "land_queue.log"
    return p.read_text() if p.exists() else ""


# ---- the CI wait reads reality -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("required,want", [
    (GREEN, "ok"),
    ({"validate": "pending", "secret-scan": "pass"}, "pending"),
    ({"secret-scan": "pass"}, "pending"),                      # validate not REPORTED yet (queued for the runner) = pending, not ok
    ({"validate": "fail", "secret-scan": "pending"}, "fail"),
    ({}, "unknown"),                                           # nothing readable = unknown, never ok
])
def test_required_state_counts_an_unreported_required_check_as_pending(rig, required, want):
    set_prs(rig, **{"7": {"headRefOid": HEAD, "required": required}})
    assert lib(rig, "landing_required_state 7").stdout.strip() == want


def test_unreported_required_checks_on_the_bound_head_extend_the_wait_to_the_hard_cap(rig):
    # the 26 BLOCKED-at-30-min disarms: validate had not reported, so the old awk saw no "pending" line and gave up at the base wait
    set_prs(rig, **{"7": {"headRefOid": HEAD, "armed": True, "mergeStateStatus": "BLOCKED", "required": {"secret-scan": "pass"}}})
    r = lib(rig, f"landing_wait_merged 7 {HEAD} 3 9; echo rc=$?")
    assert "rc=1" in r.stdout and len(calls(rig, "pr merge 7 --disable-auto")) == 1
    assert len(calls(rig, "pr view 7")) == 9 + 1, "extended to the hard cap (then the one re-check), not the 3-step base wait"
    assert "wait ended: hard cap (9 polls), required checks pending" in r.stdout


def test_an_unreadable_required_state_on_the_bound_head_also_extends_but_is_capped(rig):
    set_prs(rig, **{"7": {"headRefOid": HEAD, "armed": True, "mergeStateStatus": "BLOCKED"}})   # gh prints no required checks at all
    r = lib(rig, f"landing_wait_merged 7 {HEAD} 2 6; echo rc=$?")
    assert "rc=1" in r.stdout and len(calls(rig, "pr view 7")) == 6 + 1


def test_a_pr_that_merged_as_the_wait_gave_up_is_not_reported_or_disarmed_as_unmerged(rig):
    # 7 of 55 "not merged within the wait" lines were PRs that HAD merged: the state is read once more before any disarm
    set_prs(rig, **{"7": {"headRefOid": HEAD, "armed": True, "mergeStateStatus": "BLOCKED", "required": GREEN}})
    flip = rig.tmp / "flip.py"
    flip.write_text(f'import json\nf="{rig.state_file}"\nd=json.load(open(f))\nd["prs"]["7"]["state"]="MERGED"\njson.dump(d,open(f,"w"))\n')
    # base wait of 1 poll: poll 1 reads OPEN, the checks read flips the PR to MERGED, the wait gives up - and the re-check sees MERGED
    body = (f'landing_required_state(){{ python3 "{flip}"; echo ok; }}; '
            f'landing_wait_merged 7 {HEAD} 1 5; echo rc=$?')
    r = lib(rig, body)
    assert "rc=0" in r.stdout, r.stdout + r.stderr
    assert calls(rig, "pr merge 7 --disable-auto") == []


# ---- the voice hold: the chain does not overtake a probed voice PR ----------------------------------------------------------------

def hold_owner(rig, sweep="0"):
    """A live process that takes the hold like the lander does (its own $$), then sleeps until killed. No sweep by default, so a
    chain test sees only what the CHAIN does."""
    script = f'. "{LANDING}/landing_lib.sh"; landing_voice_hold_take 50; echo taken; exec sleep 60'
    env = rig.env(ZOE_LANDING_NOW_EPOCH=NOON, ZOE_LANDING_VOICE_HOLD_SWEEP=sweep)
    p = subprocess.Popen(["bash", "-c", script], env=env, stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "taken"
    return p


def chain(rig, timeout=20, **extra):
    env = dict(ZOE_LANDING_CHAIN_HOLD_S="0", ZOE_LANDING_CHAIN_STEP_S="0", ZOE_LANDING_CHAIN_NIGHT_S="0", ZOE_LANDING_REPO=str(REPO),
               ZOE_LANDING_NOW_EPOCH=NOON, ZOE_LANDING_CHAIN_PASSES="4")
    env.update(extra)
    try:
        return rig.run("docs_merge_chain.sh", "21", timeout=timeout, **env)
    except subprocess.TimeoutExpired:
        return None


CLEAN_DOCS = {"mergeStateStatus": "CLEAN", "files": ["docs/x.md"],
              "checks": [{"name": "validate", "state": "SUCCESS"}, {"name": "secret-scan", "state": "SUCCESS"}]}


def test_the_chain_neither_updates_nor_arms_while_a_live_voice_hold_exists_and_drops_its_own_arm(rig):
    set_prs(rig, **{"21": dict(CLEAN_DOCS, mergeStateStatus="BEHIND", armed=True), "50": {"headRefOid": HEAD}})
    owner = hold_owner(rig)
    try:
        r = chain(rig)
    finally:
        owner.kill(); owner.wait()
    assert r is not None and r.returncode == 0, "the 90-pass chain must END (bounded), not freeze"
    assert calls(rig, "pr update-branch") == [] and calls(rig, "pr merge 21 --squash") == []
    assert calls(rig, "pr merge 21 --disable-auto"), "an armed chain PR would merge first and put the voice PR BEHIND"
    log = qlog(rig)
    assert log.count("voice PR #50 is landing") == 1, "logged once per hold, not every pass"


def test_the_chain_arms_as_usual_once_the_holder_is_gone(rig):
    # negative control for the test above: same PR, the hold's owner is dead -> STALE, cleared, and the chain arms
    set_prs(rig, **{"21": dict(CLEAN_DOCS), "50": {"headRefOid": HEAD}})
    owner = hold_owner(rig)
    owner.kill(); owner.wait()
    chain(rig, timeout=4)
    assert calls(rig, "pr merge 21 --squash --auto"), rig.gh_calls()
    assert "VOICE HOLD STALE: #50 hold cleared - its owner" in qlog(rig)
    assert not (rig.home / "coord" / "voice-hold").exists()


def test_a_hold_older_than_the_cap_is_stale_even_with_a_live_owner(rig):
    owner = hold_owner(rig)
    try:
        young = lib(rig, "landing_voice_hold_active; echo rc=$?")
        old = lib(rig, "landing_voice_hold_active; echo rc=$?", ZOE_LANDING_NOW_EPOCH=str(int(NOON) + 10800))
    finally:
        owner.kill(); owner.wait()
    assert young.stdout.split() == ["50", "rc=0"]
    assert old.stdout.split() == ["rc=1"] and "older than 10800s" in qlog(rig)


def test_a_reused_pid_does_not_keep_a_hold_alive(rig):
    # the hold names pid + kernel start time: a different process that happens to carry the pid is not its owner
    (rig.home / "coord").mkdir(exist_ok=True)
    (rig.home / "coord" / "voice-hold").write_text(f"50 {os.getpid()} 1 {NOON}\n")   # our pid, wrong start time
    assert lib(rig, "landing_voice_hold_active; echo rc=$?").stdout.split() == ["rc=1"]


def test_taking_the_hold_drops_other_armed_prs_and_releasing_it_rearms_them_bound_to_their_head(rig):
    set_prs(rig, **{"50": {"headRefOid": HEAD, "armed": True}, "21": {"headRefOid": "c" * 40, "armed": True},
                    "22": {"headRefOid": "d" * 40}, "23": {"headRefOid": "e" * 40, "armed": True}})
    r = lib(rig, "landing_voice_hold_take 50; cat \"$LANDING_VOICE_HOLD_DISARMED\"; "
                 # 23 got a push while the hold was held: its arm must not be restored onto an unseen head
                 f"python3 -c 'import json;f=\"{rig.state_file}\";d=json.load(open(f));d[\"prs\"][\"23\"][\"headRefOid\"]=\"f\"*40;json.dump(d,open(f,\"w\"))'; "
                 "landing_voice_hold_release; [ -e \"$LANDING_VOICE_HOLD\" ] && echo STILL-HELD; echo done")
    assert "STILL-HELD" not in r.stdout and "done" in r.stdout, r.stdout + r.stderr
    assert sorted(c for c in calls(rig, "pr merge") if "--disable-auto" in c) == \
        ["pr merge 21 --disable-auto", "pr merge 23 --disable-auto"], "the voice PR itself and an unarmed PR are left alone"
    assert calls(rig, "pr merge 21 --squash --auto") == ["pr merge 21 --squash --auto --match-head-commit " + "c" * 40]
    assert calls(rig, "pr merge 23 --squash") == [] and "#23 NOT re-armed" in qlog(rig)
    assert not (rig.home / "coord" / "voice-hold-disarmed.txt").exists()


def test_a_failed_rearm_is_kept_and_retried_by_the_next_release_not_lost(rig):
    st = {"prs": {"21": {"headRefOid": "c" * 40, "armed": True}}, "threads": {}, "arm_fails": ["21"]}
    rig.set_state(st)
    lib(rig, "landing_voice_hold_take 50; landing_voice_hold_release")
    rec = rig.home / "coord" / "voice-hold-disarmed.txt"
    assert rec.read_text().split() == ["21", "c" * 40], "a re-arm that failed must stay recorded"
    rig.set_state({**st, "arm_fails": []})
    lib(rig, "landing_voice_hold_active; true")          # no hold now: the next chain pass alone retries the kept list
    assert calls(rig, "pr merge 21 --squash --auto")[-1].endswith("c" * 40) and not rec.exists()


def test_a_pr_the_sweep_could_not_disarm_fails_the_sweep(rig):
    rig.set_state({"prs": {"21": {"headRefOid": "c" * 40, "armed": True}}, "threads": {}, "disarm_fails": ["21"]})
    assert "rc=1" in lib(rig, "landing_voice_hold_sweep 50; echo rc=$?").stdout
    assert (rig.home / "coord" / "voice-hold-disarmed.txt").read_text().split()[0] == "21"


def test_taking_the_hold_fails_loudly_when_its_lock_is_busy_or_the_pr_list_unreadable(rig):
    lock = rig.home / "coord" / "voice-hold.lock"
    lock.parent.mkdir(exist_ok=True)
    holder = hold(lock, secs=90)
    try:
        busy = lib(rig, "landing_voice_hold_take 50; echo rc=$?", timeout=90)
    finally:
        holder.kill(); holder.wait()
    assert "rc=2" in busy.stdout and not (rig.home / "coord" / "voice-hold").exists()
    rig.set_state({"prs": {}, "threads": {}, "pr_list_fails": True})
    assert "rc=1" in lib(rig, "landing_voice_hold_sweep 50; echo rc=$?").stdout
    code = (LANDING / "land_voice_pr.sh").read_text()
    assert 'landing_voice_hold_take "$PR" || {' in code and 'landing_voice_hold_sweep "$PR" || {' in code


def test_a_second_lander_cannot_take_over_a_live_hold(rig):
    owner = hold_owner(rig)
    try:
        r = lib(rig, "landing_voice_hold_take 60; echo rc=$?")
        now = (rig.home / "coord" / "voice-hold").read_text().split()[0]
    finally:
        owner.kill(); owner.wait()
    assert "rc=1" in r.stdout and now == "50"


def test_release_only_by_the_owner_and_an_exit_trap_releases_it(rig):
    other = lib(rig, f'echo "50 1 1 {NOON}" > "$LANDING_VOICE_HOLD"; landing_voice_hold_release; [ -e "$LANDING_VOICE_HOLD" ] && echo kept')
    assert "kept" in other.stdout, "a process that does not own the hold must not drop it"
    trapped = lib(rig, 'trap landing_voice_hold_release EXIT; landing_voice_hold_take 50; exit 9', ZOE_LANDING_VOICE_HOLD_SWEEP="0")
    assert trapped.returncode == 9 and not (rig.home / "coord" / "voice-hold").exists()


def test_the_lander_takes_the_hold_before_update_branch_under_a_trap_and_re_sweeps_before_arming():
    code = "\n".join(ln for ln in (LANDING / "land_voice_pr.sh").read_text().splitlines() if not ln.lstrip().startswith("#"))
    trap, take, update = code.index("trap lander_exit EXIT"), code.index('landing_voice_hold_take "$PR"'), code.index("gh pr update-branch")
    assert trap < take < update
    assert "landing_voice_hold_release" in code[code.index("lander_exit(){"):trap]
    sweep, arm = code.index('landing_voice_hold_sweep "$PR"'), code.index('gh pr merge "$PR" --squash --auto')
    assert sweep < arm
    assert "trap '" not in code, "a later bare `trap '...' EXIT` would replace lander_exit and leak the hold"


def test_autoland_starts_no_chain_pass_while_a_live_voice_hold_exists(rig):
    # a copy of the tooling whose `landing_running` sees nothing: on the live box the REAL loops are running and autoland would wait for
    # them instead, which made this test pass WITHOUT the hold check (caught by the negative control)
    copy = rig.tmp / "landing-copy"
    shutil.copytree(LANDING, copy)
    with (copy / "landing_lib.sh").open("a") as fh:
        fh.write("\nlanding_running(){ return 1; }\n")
    set_prs(rig, **{"21": dict(CLEAN_DOCS)})
    env = rig.env(ZOE_LANDING_ONCE="1", ZOE_LANDING_REPO=str(REPO), ZOE_LANDING_NOW_EPOCH=NOON, ZOE_LANDING_CHAIN_PASSES="1",
                  ZOE_LANDING_CHAIN_STEP_S="0", ZOE_LANDING_CHAIN_HOLD_S="0")

    def run(t):
        return subprocess.run(["bash", str(copy / "autoland.sh")], env=env, cwd=rig.repo, capture_output=True, text=True,
                              timeout=t, stdin=subprocess.DEVNULL)

    owner = hold_owner(rig)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run(6)
    finally:
        owner.kill(); owner.wait()
    assert "=== #21" not in qlog(rig) and calls(rig, "pr merge 21") == []
    # positive control: the owner is gone -> the hold is stale -> autoland runs its chain pass
    try:
        run(30)
    except subprocess.TimeoutExpired:
        pass
    assert "=== #21" in qlog(rig), qlog(rig)


# ---- a probed-and-passed PR that lost its turn goes to the FRONT -----------------------------------------------------------------

def test_rc_12_is_a_front_requeue_and_other_codes_keep_their_meaning(rig):
    set_prs(rig, **{"7": {"headRefOid": HEAD}})
    out = lib(rig, "for rc in 12 5 9 11; do landing_requeue_decision 7 $rc; done").stdout.split()
    assert out == ["requeue-front", "requeue", "requeue", "drop"]


def test_the_voice_loop_puts_a_passed_but_overtaken_pr_ahead_of_everything_queued_meanwhile(tmp_path):
    rig = Rig(tmp_path)
    (rig.home / "voice-day-ok").write_text(DAY + "\n")
    shutil.copytree(LANDING, tmp_path / "landing-copy")
    pending = rig.home / "voice-pending.txt"
    # stub lander: #7 passed its probe then went BEHIND (12); #8 needs another go (5); while #7 lands, autoland queues #9 and re-adds #7
    (tmp_path / "landing-copy" / "land_voice_pr.sh").write_text(
        f'case "$1" in 7) echo "9 /w/9 b9 pr9" >> "{pending}"; echo "7 /w/7 b7 pr7" >> "{pending}"; exit 12;; 8) exit 5;; esac; exit 0\n')
    set_prs(rig, **{"7": {"headRefOid": "7" * 40}, "8": {"headRefOid": "8" * 40}, "9": {"headRefOid": "9" * 40}})
    pending.write_text("8 /w/8 b8 pr8\n7 /w/7 b7 pr7\n")
    env = rig.env(ZOE_LANDING_ONCE="1", ZOE_LANDING_LOOP_SLEEP="0", ZOE_LANDING_NO_BUSY_WAIT="1", ZOE_LANDING_NOW_EPOCH=NOON)
    r = subprocess.run(["bash", str(tmp_path / "landing-copy" / "after_voice_more.sh")], env=env, cwd=rig.repo,
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    order = [ln.split()[0] for ln in pending.read_text().splitlines()]
    assert order[0] == "7", order
    assert [n for n in order if n != "7"] == ["9", "8"], order   # autoland's line, then the ordinary back-of-queue requeue
    batch = subprocess.run(["awk", "!seen[$1]++"], input=pending.read_text(), capture_output=True, text=True).stdout
    assert [ln.split()[0] for ln in batch.splitlines()] == ["7", "9", "8"], "the next batch lands #7 first, once"
    assert "#7 probe passed but not merged - re-queued at the FRONT" in qlog(rig)


def test_the_lander_exits_12_for_behind_after_a_pass_and_for_an_unmerged_wait():
    code = (LANDING / "land_voice_pr.sh").read_text()
    behind = next(ln for ln in code.splitlines() if 'case "$MS" in BEHIND|DIRTY)' in ln)
    assert "exit 12" in behind
    tail = code[code.index("landing_wait_merged \"$PR\" \"$HEAD_SHA\""):]
    assert tail.rstrip().endswith("exit 12") and "MERGED*) exit 0" in tail


# ---- probe vs chat drivers ------------------------------------------------------------------------------------------------------

def hold(path, mode="ex", secs=10):
    """ONE process holding a flock (killing it frees the lock - `flock <f> sleep` would leave the sleep child holding it)."""
    code = (f"import fcntl,os,time; fd=os.open({str(path)!r}, os.O_CREAT|os.O_RDWR, 0o666); "
            f"fcntl.flock(fd, fcntl.LOCK_{'EX' if mode == 'ex' else 'SH'}); print('held', flush=True); time.sleep({secs})")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def qenv(rig):
    return {**os.environ, "ZOE_PROBE_GATE_LOCK": str(rig.tmp / "probe-gate.lock"), "ZOE_CHAT_TURN_LOCK": str(rig.tmp / "chat-turn.lock")}


def test_a_chat_turn_waits_while_the_probe_holds_the_gate_and_runs_after(rig):
    gate = rig.tmp / "probe-gate.lock"
    gate.touch()
    stamp = rig.tmp / "turn-ran"
    holder = hold(gate, secs=2)
    try:
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, str(QUIET), "turn", "--wait", "30", "--", "touch", str(stamp)], env=qenv(rig),
                           capture_output=True, text=True, timeout=40)
        waited = time.monotonic() - t0
    finally:
        holder.kill(); holder.wait()
    assert r.returncode == 0 and stamp.exists(), r.stderr
    assert waited >= 1.0, f"the turn ran while the probe held the gate (waited only {waited:.2f}s)"
    assert "voice probe running" in r.stderr


def test_a_chat_turn_gives_up_with_75_and_does_not_run_when_the_probe_outlasts_its_wait(rig):
    gate = rig.tmp / "probe-gate.lock"
    gate.touch()
    stamp = rig.tmp / "turn-ran"
    holder = hold(gate)
    try:
        r = subprocess.run([sys.executable, str(QUIET), "turn", "--wait", "1", "--", "touch", str(stamp)], env=qenv(rig),
                           capture_output=True, text=True, timeout=20)
        status = subprocess.run([sys.executable, str(QUIET), "status"], env=qenv(rig), capture_output=True, text=True)
    finally:
        holder.kill(); holder.wait()
    assert r.returncode == 75 and not stamp.exists()
    assert status.returncode == 1 and status.stdout.strip() == "probe active"
    assert subprocess.run([sys.executable, str(QUIET), "status"], env=qenv(rig), capture_output=True, text=True).returncode == 0


def test_the_probe_side_waits_for_an_in_flight_turn_and_blocks_new_ones(rig):
    env = qenv(rig)
    # an in-flight turn (2.5 s) holds the turn lock shared via the python API
    turn = subprocess.Popen([sys.executable, "-c", (
        f"import sys,time; sys.path.insert(0, {str(LANDING)!r}); from quiet_for_probe import chat_turn\n"
        "with chat_turn(5):\n    print('in', flush=True); time.sleep(2.5)\n")], env=env, stdout=subprocess.PIPE, text=True)
    assert turn.stdout.readline().strip() == "in"
    t0 = time.monotonic()
    probe = subprocess.Popen(["bash", "-c", f'. "{LANDING}/landing_lib.sh"; landing_quiet_chat_take 10 10 && echo HELD && sleep 2; echo rc=$?'],
                             env=rig.env(), stdout=subprocess.PIPE, text=True)
    assert probe.stdout.readline().strip() == "HELD"
    took = time.monotonic() - t0
    turn.wait(timeout=10)
    assert took >= 1.5, f"the probe started measuring while a turn was in flight (after {took:.2f}s)"
    # while the probe holds both locks, a new turn does not start
    late = subprocess.run([sys.executable, str(QUIET), "wait", "--wait", "0.5"], env=env, capture_output=True, text=True, timeout=10)
    probe.wait(timeout=10)
    assert late.returncode == 75


def test_a_timed_out_turn_kills_its_whole_process_group_before_the_turn_lock_is_released(rig):
    pidf = rig.tmp / "child.pid"
    r = subprocess.run([sys.executable, str(QUIET), "turn", "--turn-timeout", "1", "--", "bash", "-c", f"sleep 30 & echo $! > {pidf}; wait"],
                       env=qenv(rig), capture_output=True, text=True, timeout=20)
    assert r.returncode == 124
    child = int(pidf.read_text())
    time.sleep(0.2)
    assert not os.path.exists(f"/proc/{child}") or "Z" in open(f"/proc/{child}/stat").read().split()[2], "a grandchild outlived the turn"


def test_the_probe_side_gives_up_cleanly_when_a_turn_never_ends(rig):
    turn = hold(rig.tmp / "chat-turn.lock", "sh")
    try:
        r = lib(rig, "landing_quiet_chat_take 5 1; echo rc=$?; flock -n \"$ZOE_PROBE_GATE_LOCK\" true && echo gate-free")
    finally:
        turn.kill(); turn.wait()
    assert r.stdout.split() == ["rc=1", "gate-free"], r.stdout + r.stderr


def test_kokoro_is_stopped_only_after_in_flight_turns_drain_and_a_hung_turn_leaves_it_untouched(rig):
    set_prs(rig, **{"7": {"headRefOid": HEAD}})
    sysl, lane = rig.tmp / "systemctl.log", rig.tmp / "lane.lock"
    lane.touch()
    body = f'probe(){{ echo PROBE; }}; landing_probe_window 5 7 {HEAD} probe; echo rc=$?'
    extra = dict(FAKE_SYSTEMCTL_LOG=str(sysl), ZOE_TEST_LANE_LOCK=str(lane), ZOE_LANDING_SETTLE_S="0", ZOE_PROBE_DRAIN_WAIT_S="1")
    hung = hold(rig.tmp / "chat-turn.lock", "sh")
    try:
        r = lib(rig, body, **extra)
    finally:
        hung.kill(); hung.wait()
    assert "rc=9" in r.stdout and "PROBE" not in r.stdout
    assert not sysl.exists() or sysl.read_text() == "", "Kokoro was stopped with a chat turn still in flight"
    assert subprocess.run(["flock", "-n", str(lane), "true"]).returncode == 0, "the lane stayed locked"
    r = lib(rig, body, **extra)                                            # the turn is gone: the window runs
    assert "rc=0" in r.stdout and "PROBE" in r.stdout
    assert sysl.read_text().split("\n")[:2] == ["stop kokoro-tts.service lane_held=yes", "start kokoro-tts.service lane_held=yes"]
    assert subprocess.run(["flock", "-n", str(rig.tmp / "probe-gate.lock"), "true"]).returncode == 0, "the gate was not released"
