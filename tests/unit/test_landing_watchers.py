"""scripts/landing/watchd.sh + docs_merge_chain.sh - the watcher's events and the chain's hold/draft refusals."""
import pytest

from tests.unit.landing_fakes import NEEDS_TOOLS, REPO, Rig, epoch

pytestmark = [pytest.mark.ci_safe, NEEDS_TOOLS]


NOON = str(epoch("2026-10-29", "12:00"))


@pytest.fixture()
def rig(tmp_path):
    return Rig(tmp_path)


def state(rig, prs, threads=None):
    rig.set_state({"prs": prs, "threads": threads or {}})


def events(rig):
    return (rig.home / "logs" / "watchd-events.log").read_text().splitlines()


def watchd(rig, **extra):
    r = rig.run("watchd.sh", ZOE_WATCHD_ONCE="1", **extra)
    assert r.returncode == 0, r.stdout + r.stderr
    return r


def test_watchd_flags_armed_green_but_blocked_with_the_paginated_thread_count(rig):
    # the 2026-10-28 case: >100 threads, one unresolved past page 1, everything green, armed, still BLOCKED
    state(rig, {"21": {"mergeStateStatus": "BLOCKED", "armed": True, "required": {"validate": "pass", "secret-scan": "pass"}}},
          {"21": {"total": 140, "unresolved": [120]}})
    watchd(rig)
    ev = events(rig)
    assert len(ev) == 1 and "#21 armed + required checks green but BLOCKED: 1 unresolved thread(s)" in ev[0], ev


def test_watchd_is_quiet_for_a_pr_that_is_merely_waiting_on_checks(rig):
    state(rig, {"21": {"mergeStateStatus": "BLOCKED", "armed": True, "required": {"validate": "pending"}}})
    watchd(rig)
    assert events(rig) == []


def test_watchd_ignores_draft_prs(rig):
    state(rig, {"21": {"isDraft": True, "mergeStateStatus": "BLOCKED", "armed": True, "required": {"validate": "pass"}}})
    watchd(rig)
    assert events(rig) == []


def test_watchd_emits_each_event_once_across_passes(rig):
    state(rig, {"21": {"mergeStateStatus": "BLOCKED", "armed": True, "required": {"validate": "pass"}}},
          {"21": {"total": 3, "unresolved": [1]}})
    watchd(rig)
    watchd(rig)
    assert len(events(rig)) == 1


def test_watchd_surfaces_attention_lines_from_the_queue_log_but_not_skipped_prs(rig):
    state(rig, {})
    log = rig.home / "logs" / "land_queue.log"
    log.write_text("[t] #31 PROBE NOT PASS - not arming\n[t] #32 PROBE NOT PASS - not arming\n[t] #33 all fine\n")
    watchd(rig, ZOE_WATCHD_SKIP="32")
    ev = events(rig)
    assert len(ev) == 1 and "#31 PROBE NOT PASS" in ev[0]


def test_watchd_flags_a_required_job_running_too_long(rig):
    state(rig, {"21": {"checks": [{"name": "validate", "state": "IN_PROGRESS", "startedAt": "2020-01-01T00:00:00Z"},
                                  {"name": "lint", "state": "IN_PROGRESS", "startedAt": "2020-01-01T00:00:00Z"}]}})
    watchd(rig)
    ev = events(rig)
    assert len(ev) == 1 and "#21 validate running" in ev[0] and "possible hang" in ev[0]


# ---- docs_merge_chain.sh ----------------------------------------------------------------------------------------

def chain(rig, pr="21"):
    r = rig.run("docs_merge_chain.sh", pr, timeout=60, ZOE_LANDING_NOW_EPOCH=NOON, ZOE_LANDING_REPO=str(REPO))
    return r, (rig.home / "logs" / "land_queue.log").read_text()


def merges(rig):
    return [c for c in rig.gh_calls() if c.startswith(("pr merge", "pr ready"))]


@pytest.mark.parametrize("holdfile", ["coord/hold.txt", "chain-hold.txt"])
def test_chain_skips_held_prs(rig, holdfile):
    state(rig, {"21": {"mergeStateStatus": "CLEAN", "required": {"validate": "pass"}}})
    (rig.home / holdfile).write_text("21\n")
    r, log = chain(rig)
    assert r.returncode == 0 and "HOLD" in log and rig.gh_calls() == []


def test_chain_never_arms_a_draft(rig):
    state(rig, {"21": {"isDraft": True, "mergeStateStatus": "CLEAN", "checks": [{"name": "validate", "state": "SUCCESS"}], "files": ["docs/x.md"]}})
    r, log = chain(rig)
    assert r.returncode == 0 and "draft" in log and merges(rig) == []


def test_chain_does_not_arm_with_an_unresolved_thread_past_page_one(rig):
    state(rig, {"21": {"mergeStateStatus": "CLEAN", "files": ["docs/x.md"], "checks": [{"name": "validate", "state": "SUCCESS"},
                                                               {"name": "secret-scan", "state": "SUCCESS"}]}},
          {"21": {"total": 130, "unresolved": [110]}})
    r = rig.run("docs_merge_chain.sh", "21", ZOE_LANDING_CHAIN_HOLD_S="0", ZOE_LANDING_CHAIN_STEP_S="0",
                ZOE_LANDING_NOW_EPOCH=NOON, ZOE_LANDING_REPO=str(REPO))
    log = (rig.home / "logs" / "land_queue.log").read_text()
    assert r.returncode == 0 and "1 unresolved review thread(s)" in log and "still has unresolved" in log
    assert merges(rig) == []


def test_chain_does_not_arm_when_threads_are_unreadable(rig):
    st = {"prs": {"21": {"mergeStateStatus": "CLEAN", "files": ["docs/x.md"], "checks": [{"name": "validate", "state": "SUCCESS"}]}},
          "threads": {}, "graphql_fails": True}
    rig.set_state(st)
    rig.run("docs_merge_chain.sh", "21", ZOE_LANDING_CHAIN_HOLD_S="0", ZOE_LANDING_CHAIN_STEP_S="0", ZOE_LANDING_NOW_EPOCH=NOON, ZOE_LANDING_REPO=str(REPO))
    assert merges(rig) == []


def test_chain_arms_a_clean_nondraft_pr_with_all_threads_resolved(rig):
    # the positive control: without it the refusals above could be passing because the chain never arms anything
    st = {"prs": {"21": {"mergeStateStatus": "CLEAN", "files": ["docs/x.md"], "checks": [{"name": "validate", "state": "SUCCESS"},
                                                                  {"name": "secret-scan", "state": "SUCCESS"}]}},
          "threads": {"21": {"total": 130, "unresolved": []}}}
    rig.set_state(st)
    # the fake never flips the PR to MERGED, so bound the 90-iteration loop by a state that merges after arming
    # (watch for the arm call, then stop): run with a short timeout and inspect the log.
    import subprocess
    try:
        rig.run("docs_merge_chain.sh", "21", ZOE_LANDING_CHAIN_HOLD_S="0", ZOE_LANDING_CHAIN_STEP_S="0",
                ZOE_LANDING_NOW_EPOCH=NOON, ZOE_LANDING_REPO=str(REPO), timeout=3)
    except subprocess.TimeoutExpired:
        pass
    assert any(c.startswith("pr merge 21 --squash --auto") for c in rig.gh_calls())


def chain_run(rig, **extra):
    return rig.run("docs_merge_chain.sh", "21", ZOE_LANDING_CHAIN_HOLD_S="0", ZOE_LANDING_CHAIN_STEP_S="0",
                   ZOE_LANDING_CHAIN_NIGHT_S="0", ZOE_LANDING_REPO=str(REPO), **{"ZOE_LANDING_NOW_EPOCH": NOON, **extra})


GREEN = {"mergeStateStatus": "CLEAN", "checks": [{"name": "validate", "state": "SUCCESS"}, {"name": "secret-scan", "state": "SUCCESS"}]}


def test_chain_defers_a_pr_that_gained_a_voice_file(rig):
    state(rig, {"21": dict(GREEN, files=["docs/x.md", "services/zoe-data/routers/voice_tts.py"])}, {"21": {"total": 0, "unresolved": []}})
    chain_run(rig)
    assert merges(rig) == [] and "not CLEAR" in (rig.home / "logs" / "land_queue.log").read_text()


def test_chain_pauses_during_the_measured_deploy_pause(rig):
    state(rig, {"21": dict(GREEN, files=["docs/x.md"])}, {"21": {"total": 0, "unresolved": []}})
    chain_run(rig, ZOE_LANDING_NOW_EPOCH=str(epoch("2026-10-29", "02:46")))
    assert merges(rig) == [] and "deploy pause" in (rig.home / "logs" / "land_queue.log").read_text()


def test_chain_skips_the_pass_when_the_changed_file_list_is_unreadable_or_empty(rig):
    for variant in ({"files_fail": True}, {"empty": True}):
        pr = dict(GREEN, files=[] if variant.get("empty") else ["docs/x.md"])
        rig.set_state({"prs": {"21": pr}, "threads": {"21": {"total": 0, "unresolved": []}}, **{k: v for k, v in variant.items() if k == "files_fail"}})
        chain_run(rig)
        assert merges(rig) == [], variant


def test_chain_arms_bound_to_the_head_it_classified(rig):
    rig.set_state({"prs": {"21": dict(GREEN, files=["docs/x.md"], headRefOid="d" * 40)}, "threads": {"21": {"total": 0, "unresolved": []}}})
    import subprocess
    try:
        chain_run(rig, timeout=3)
    except subprocess.TimeoutExpired:
        pass
    calls = [c for c in rig.gh_calls() if c.startswith("pr merge 21 --squash --auto")]
    assert calls and all("--match-head-commit " + "d" * 40 in c for c in calls), calls
