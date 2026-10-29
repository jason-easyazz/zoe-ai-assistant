"""scripts/landing/land_voice_pr.sh + helpers - the early-exit rules that keep a bad PR from being probed/armed.

The lander is run for real against a fake `gh`; every rule here is checked BEFORE it touches git, Kokoro or the brain lock,
so a green test also proves nothing live was started. gh calls are logged; mutations (merge/ready) are asserted absent.
"""
import json
import subprocess
import time

import pytest

from tests.unit.landing_fakes import LANDING, NEEDS_TOOLS, Rig, epoch

pytestmark = [pytest.mark.ci_safe, NEEDS_TOOLS]

DAY = "2026-10-29"
NOON = str(epoch(DAY, "12:00"))
HEAD = "b" * 40


@pytest.fixture()
def rig(tmp_path):
    r = Rig(tmp_path)
    (r.home / "voice-day-ok").write_text(DAY + "\n")   # daytime pause lifted so the rules below are the only gate
    return r


def land(rig, pr="7", **extra):
    return rig.run("land_voice_pr.sh", pr, "/unused", "feature/x", "title", ZOE_LANDING_NOW_EPOCH=NOON, **extra)


def mutations(rig):
    return [c for c in rig.gh_calls() if c.startswith(("pr merge", "pr ready")) and "--disable-auto" not in c]


def prs(rig, **pr):
    st = json.loads(rig.state_file.read_text())
    st["prs"]["7"] = {"headRefOid": HEAD, **pr}
    rig.set_state(st)
    return st


# ---- refuse drafts ----------------------------------------------------------------------------------------------

def test_draft_pr_is_never_landed(rig):
    prs(rig, isDraft=True)
    r = land(rig)
    assert r.returncode == 6, r.stdout + r.stderr
    assert "draft" in r.stdout
    assert mutations(rig) == [], "a draft must not be merged, armed or marked ready"


def test_unreadable_draft_state_is_treated_as_draft(rig):
    st = prs(rig)
    st["pr_view_fails"] = True      # `gh pr view` errors for every field -> fail closed
    rig.set_state(st)
    r = land(rig)
    assert r.returncode != 0 and mutations(rig) == []


def test_lander_never_runs_gh_pr_ready():
    # un-drafting is the owner's act: the script text must not contain the call at all
    text = (LANDING / "land_voice_pr.sh").read_text()
    code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "pr ready" not in code


def test_merged_and_closed_prs_exit_0_without_acting(rig):
    for state in ("MERGED", "CLOSED"):
        prs(rig, state=state)
        assert land(rig).returncode == 0
    assert mutations(rig) == []


# ---- review threads: paginate past 100, fail closed --------------------------------------------------------------

def threads(rig, total, unresolved):
    st = json.loads(rig.state_file.read_text())
    st["threads"]["7"] = {"total": total, "unresolved": unresolved}
    rig.set_state(st)


def test_unresolved_thread_beyond_the_first_100_blocks_landing(rig):
    prs(rig)
    threads(rig, total=230, unresolved=[150])        # only on page 2 of 3
    r = land(rig)
    assert r.returncode == 6, r.stdout
    assert "1 unresolved review thread" in r.stdout
    assert mutations(rig) == []
    pages = [c for c in rig.gh_calls() if c.startswith("api graphql")]
    assert len(pages) == 3, "must walk every page, got %d" % len(pages)


def test_unresolved_thread_on_the_very_last_page_is_counted(rig):
    prs(rig)
    threads(rig, total=201, unresolved=[200])        # page boundary: the 201st thread
    r = land(rig)
    assert r.returncode == 6 and "1 unresolved" in r.stdout


def test_all_threads_resolved_gets_past_the_thread_gate(rig):
    prs(rig)
    threads(rig, total=230, unresolved=[])
    (rig.home / "validate-failed-heads.txt").write_text(HEAD + "\n")   # the NEXT gate - proves we got through
    r = land(rig)
    assert r.returncode == 7, r.stdout
    assert "already failed validate" in r.stdout


def test_unreadable_threads_fail_closed_not_as_zero(rig):
    st = prs(rig)
    st["graphql_fails"] = True
    rig.set_state(st)
    r = land(rig)
    assert r.returncode == 6 and "unreadable" in r.stdout
    assert mutations(rig) == []


def test_threads_sh_counts_across_pages_and_lists_them(rig):
    threads(rig, total=250, unresolved=[3, 105, 249])
    assert rig.run("threads.sh", "--count", "7").stdout.strip() == "3"
    listing = rig.run("threads.sh", "7").stdout
    assert listing.count("--- ") == 3 and "T249" in listing and "===== #7" in listing


def test_threads_sh_exits_nonzero_when_the_api_fails(rig):
    st = json.loads(rig.state_file.read_text())
    st["graphql_fails"] = True
    rig.set_state(st)
    r = rig.run("threads.sh", "--count", "7")
    assert r.returncode != 0 and r.stdout.strip() == ""


# ---- the hold lists ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("holdfile", ["coord/hold.txt", "chain-hold.txt"])
def test_held_pr_is_not_touched_at_all(rig, holdfile):
    prs(rig)
    (rig.home / holdfile).write_text("# parked by the owner\n\n7\n")
    r = land(rig)
    assert r.returncode == 10 and "hold" in r.stdout
    assert rig.gh_calls() == [], "a held PR must not even be disarmed or queried"


def test_hold_matches_whole_pr_numbers_only(rig):
    prs(rig, isDraft=True)
    (rig.home / "coord").joinpath("hold.txt").write_text("70\n17\n77 some note\n")
    assert land(rig).returncode == 6          # reached the draft gate, i.e. 7 is NOT held


def test_hold_line_may_carry_a_trailing_note(rig):
    prs(rig)
    (rig.home / "coord" / "hold.txt").write_text("7 waiting on the owner\n")
    assert land(rig).returncode == 10


def test_queue_skips_a_held_pr_without_spawning_a_lander(rig):
    prs(rig)
    (rig.home / "coord" / "hold.txt").write_text("7\n")
    q = rig.tmp / "q.txt"
    q.write_text("7 /wt feature/x pr7\n")
    r = rig.run("land_queue.sh", str(q), ZOE_LANDING_NOW_EPOCH=NOON)
    assert "hold list" in r.stdout and rig.gh_calls() == []
    assert (rig.home / "last-results.txt").read_text().split() == ["7", "10"]


def test_queue_records_the_real_lander_exit_code(rig):
    prs(rig, isDraft=True)
    q = rig.tmp / "q.txt"
    q.write_text("7 /wt feature/x pr7\n")
    rig.run("land_queue.sh", str(q), ZOE_LANDING_NOW_EPOCH=NOON)
    assert (rig.home / "last-results.txt").read_text().split() == ["7", "6"]


# ---- the night/day gate is the lander's FIRST act ----------------------------------------------------------------

def test_daytime_without_voice_day_ok_the_lander_exits_9_before_any_gh_call(rig):
    (rig.home / "voice-day-ok").write_text("2026-10-28\n")
    prs(rig)
    r = land(rig)
    assert r.returncode == 9 and "night pause" in r.stdout
    assert rig.gh_calls() == []


# ---- bounded retry: one re-queue for a speed-only probe fail ------------------------------------------------------
# The lander snapshots the shared artifact slot PER PR right after its probe (probe-artifacts/<pr>.json); the voice loop's
# decision reads that snapshot - never the shared slot, which belongs to whichever probe ran last.

def snapshot(rig, pr="7", head=HEAD, **d):
    d.setdefault("revision", {"commit": head[:8]})
    (rig.home / "probe-artifacts").mkdir(exist_ok=True)
    (rig.home / "probe-artifacts" / f"{pr}.json").write_text(json.dumps(d))


SPEED_ONLY = dict(status="fail", said_vs_did_regressions=[], vad={"status": "pass"}, speed_comparison="compared")


def decide(rig, pr="7", rc="4"):
    script = f'. "{LANDING}/landing_lib.sh"; landing_requeue_decision {pr} {rc}'
    r = subprocess.run(["bash", "-c", script], env=rig.env(), capture_output=True, text=True)
    return r.stdout.strip()


def test_speed_only_probe_fail_is_requeued_once_per_head(rig):
    prs(rig)
    snapshot(rig, **SPEED_ONLY)
    assert decide(rig) == "retry-once"
    assert decide(rig) == "park-probe-failed"             # same head, second fail -> a human
    prs(rig, headRefOid="c" * 40)                         # a new push earns one more automatic retry...
    assert decide(rig) == "park-probe-failed"             # ...but only on an artifact FOR that head (the old one is stale)
    snapshot(rig, head="c" * 40, **SPEED_ONLY)
    assert decide(rig) == "retry-once"


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


def test_threads_sh_rejects_a_graphql_error_envelope(rig):
    st = json.loads(rig.state_file.read_text())
    st["threads"]["7"] = {"total": 3, "unresolved": [1]}
    st["graphql_errors"] = True                     # nodes present AND an errors array: partial failure
    rig.set_state(st)
    r = rig.run("threads.sh", "--count", "7")
    assert r.returncode != 0 and r.stdout.strip() == ""


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
