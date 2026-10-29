"""scripts/landing/land_voice_pr.sh + helpers - the early-exit rules that keep a bad PR from being probed/armed.

The lander is run for real against a fake `gh`; every rule here is checked BEFORE it touches git, Kokoro or the brain lock,
so a green test also proves nothing live was started. gh calls are logged; mutations (merge/ready) are asserted absent.
"""
import json
import os
import shutil
import subprocess
import time

import pytest

from tests.unit.landing_fakes import LANDING, Rig, epoch, tools_available

pytestmark = [pytest.mark.ci_safe,
              pytest.mark.skipif(not tools_available(), reason="needs bash + jq + flock")]

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

def artifact(rig, **d):
    p = rig.tmp / "voice_regression_last.json"
    p.write_text(json.dumps(d))
    return str(p)


def decide(rig, art, pr="7", rc="4"):
    script = f'. "{LANDING}/landing_lib.sh"; landing_requeue_decision {pr} {rc}'
    r = subprocess.run(["bash", "-c", script], env=rig.env(ZOE_VOICE_ARTIFACT=art), capture_output=True, text=True)
    return r.stdout.strip()


def test_speed_only_probe_fail_is_requeued_once_per_head(rig):
    prs(rig)
    art = artifact(rig, status="fail", said_vs_did_regressions=[], vad={"status": "pass"}, speed_comparison="compared")
    assert decide(rig, art) == "retry-once"
    assert decide(rig, art) == "park-probe-failed"        # same head, second fail -> a human
    prs(rig, headRefOid="c" * 40)                         # a new push earns one more automatic retry
    assert decide(rig, art) == "retry-once"


def test_said_vs_did_regression_is_never_retried(rig):
    prs(rig)
    art = artifact(rig, status="fail", said_vs_did_regressions=["turn on the lights"], vad={"status": "pass"})
    assert decide(rig, art) == "park-probe-failed"


def test_missing_or_garbage_artifact_is_not_retryable(rig):
    prs(rig)
    assert decide(rig, str(rig.tmp / "nope.json")) == "park-probe-failed"
    bad = rig.tmp / "bad.json"
    bad.write_text("{not json")
    assert decide(rig, str(bad)) == "park-probe-failed"


def test_vad_failure_or_refused_speed_is_not_a_speed_only_fail(rig):
    prs(rig)
    assert decide(rig, artifact(rig, status="fail", said_vs_did_regressions=[], vad={"status": "fail"})) == "park-probe-failed"
    assert decide(rig, artifact(rig, status="error", said_vs_did_regressions=[])) == "park-probe-failed"
    assert decide(rig, artifact(rig, status="fail", said_vs_did_regressions=[],
                                speed_comparison="refused: no baseline")) == "park-probe-failed"


def test_other_exit_codes_keep_the_old_requeue_behaviour(rig):
    prs(rig)
    for rc in ("5", "6", "7", "9", "10", ""):
        assert decide(rig, "/nonexistent", rc=rc) == "requeue"


def test_voice_loop_cycle_requeues_parks_and_retries(tmp_path):
    """after_voice_more.sh for real (one cycle, stub lander): requeue / park / retry-once / conflict-park."""
    rig = Rig(tmp_path)
    (rig.home / "voice-day-ok").write_text(DAY + "\n")
    shutil.copytree(LANDING, tmp_path / "landing-copy")
    (tmp_path / "landing-copy" / "land_voice_pr.sh").write_text(
        'case "$1" in 7) exit 4;; 8) exit 6;; 9) exit 5;; 10) exit 4;; esac; exit 0\n')
    st = {"prs": {"7": {"headRefOid": "7" * 40}, "8": {"headRefOid": "8" * 40},
                  "9": {"headRefOid": "9" * 40, "mergeable": "CONFLICTING"}, "10": {"headRefOid": "a" * 40}},
          "threads": {}}
    rig.set_state(st)
    (rig.home / "voice-pending.txt").write_text("7 /w/7 b7 pr7\n8 /w/8 b8 pr8\n9 /w/9 b9 pr9\n10 /w/10 b10 pr10\n")
    # one speed-only artifact for every rc-4 PR: 7 and 10 each get ONE retry on cycle 1 and are parked on cycle 2
    art = tmp_path / "art.json"
    art.write_text(json.dumps(dict(status="fail", said_vs_did_regressions=[], vad={"status": "pass"})))
    env = rig.env(ZOE_LANDING_ONCE="1", ZOE_LANDING_LOOP_SLEEP="0", ZOE_LANDING_NO_BUSY_WAIT="1",
                  ZOE_LANDING_NOW_EPOCH=NOON, ZOE_VOICE_ARTIFACT=str(art))
    for _ in range(2):      # second cycle: 7's retry mark is spent, so it parks
        r = subprocess.run(["bash", str(tmp_path / "landing-copy" / "after_voice_more.sh")], env=env, cwd=rig.repo,
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        assert r.returncode == 0, r.stdout + r.stderr
        pending = (rig.home / "voice-pending.txt").read_text()
        if _ == 0:
            assert [ln.split()[0] for ln in pending.splitlines()] == ["7", "8", "10"], pending   # 9 conflicting -> parked
            assert (rig.home / "needs-hand-merge.txt").read_text().split()[0] == "9"
    assert [ln.split()[0] for ln in (rig.home / "voice-pending.txt").read_text().splitlines()] == ["8"]
    assert [ln.split()[0] for ln in (rig.home / "probe-failed.txt").read_text().splitlines()] == ["7", "10"]


# ---- a push DURING the probe aborts it ---------------------------------------------------------------------------

def watch(rig, pr_head, cmd, poll="1"):
    script = f'. "{LANDING}/landing_lib.sh"; landing_run_watching_head 7 {HEAD} {cmd}; echo rc=$?'
    t0 = time.time()
    r = subprocess.run(["bash", "-c", script], env=rig.env(ZOE_LANDING_HEAD_POLL_S=poll), capture_output=True,
                       text=True, timeout=60)
    return r, time.time() - t0


def test_head_moved_during_the_probe_kills_it_and_returns_75(rig):
    prs(rig, headRefOid="f" * 40)          # the PR head is no longer the probed one
    marker = "31.%d" % (os.getpid() % 1000 + 100)
    r, took = watch(rig, "f" * 40, f"sleep {marker}")
    assert "rc=75" in r.stdout, r.stdout + r.stderr
    assert took < 20, "the probe was not aborted promptly (%.1fs)" % took
    assert subprocess.run(["pgrep", "-f", f"sleep {marker}"], capture_output=True).returncode == 1, "child survived"


def test_unchanged_head_lets_the_probe_finish_and_passes_its_rc_through(rig):
    prs(rig, headRefOid=HEAD)
    r, _ = watch(rig, HEAD, "bash -c 'sleep 2; exit 3'")
    assert "rc=3" in r.stdout
    assert any(c.startswith("pr view 7") for c in rig.gh_calls()), "head was never polled"
