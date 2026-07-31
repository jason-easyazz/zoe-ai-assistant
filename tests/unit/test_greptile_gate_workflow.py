"""Behavioural tests for .github/workflows/greptile-gate.yml.

Greptile is now ADVISORY — this workflow is a COST controller, not a merge gate.
It decides when a PR is admitted through Greptile's dashboard label filter and
summoned, so both of its failure modes are about spend and signal:

  * label too early  → Greptile reviews a head that is behind or still has open
    threads, `strict` then forces an update, Greptile correctly refuses to
    re-review the unchanged diff, and the billed review bought nothing;
  * never label      → PRs get no advisory review at all, silently.

The workflow went through eight review rounds with no test at all, and the same
class of bug was introduced, fixed, and reintroduced. So these tests do not
assert on the YAML text — they EXECUTE the real embedded script against a stubbed
GitHub API and assert on what it actually does.

The suite also pins the DELETIONS from the re-tiering (Copilot summon/grace,
Codex summon/grace, the vacuous `REQUIRED_CHECKS` term, any check-publishing),
because the whole point of demoting Greptile is that this file stops being able
to block a merge. A regression that quietly re-adds a blocking path here is the
thing most worth catching.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "greptile-gate.yml"

# HARD import, deliberately not importorskip: these tests exist because this
# workflow shipped ten review rounds of logic GitHub never parsed. A skip that
# silently zeroes out the suite in CI (slim venv without PyYAML — Bugbot and
# Codex both caught exactly that) recreates the disease the tests treat. PyYAML
# is in validate.yml's slim install; if it goes missing, FAIL, don't skip.
import yaml

node = shutil.which("node")
if not node and os.environ.get("CI"):
    # In CI this suite is a merge-path gate: a runner change that drops node must
    # FAIL the lane, not skip the tests and report green (the PyYAML importorskip
    # did exactly that until two reviewers caught it). Locally, skipping is fine.
    raise RuntimeError("node is required in CI for the greptile-gate workflow tests")
pytestmark = [pytestmark, pytest.mark.skipif(not node, reason="node not available (non-CI)")]

JOB = "label-when-settled"


def _spec() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _script() -> str:
    """The inline `actions/github-script` body, exactly as CI runs it."""
    steps = _spec()["jobs"][JOB]["steps"]
    # Select by action, not position — a future checkout/setup step prepended to
    # the job must not silently point these tests at the wrong step.
    script_steps = [st for st in steps if "github-script" in str(st.get("uses", ""))]
    assert len(script_steps) == 1, f"expected exactly one github-script step, got {len(script_steps)}"
    return script_steps[0]["with"]["script"]


HARNESS = textwrap.dedent(
    """
    const SRC = require('fs').readFileSync(process.argv[2], 'utf8');
    const OPTS = JSON.parse(process.argv[3]);
    const SHA = 'a'.repeat(40);

    let conditionReads = 0;
    let getReads = 0;
    const calls = { addLabels: 0, removeLabel: 0, comments: [], checksCreated: [] };
    // OPTS.staleListSha simulates the head moving between the sweep's opening
    // `pulls.list` and this PR being processed: the list is stale, `pulls.get` is current.
    const LIST_SHA = OPTS.staleListSha ? 'b'.repeat(40) : SHA;
    const pr = { number: 1, head: { sha: LIST_SHA }, base: { ref: 'main' },
                 labels: OPTS.labels || [], draft: false };

    const github = {
      paginate: async (fn, o) => fn(o),
      graphql: async (q) => {
        if (q.includes('reviewThreads')) {
          if (OPTS.threadsFetchFails) throw new Error('threads unavailable');
          return { repository: { pullRequest: { reviewThreads: {
            pageInfo: { hasNextPage: false, endCursor: null },
            nodes: (OPTS.unresolved ? [{ isResolved: false }] : []) } } } };
        }
        return {};
      },
      rest: {
        pulls: {
          list: async () => [pr],
          // OPTS.headMovesLate: the head moves once the conditions have been read.
          // Keyed on conditionReads, NOT on the number of pulls.get calls — a
          // get-count trigger fires in BOTH orderings and so cannot tell whether
          // the head check runs before or after the condition reads.
          get: async () => {
            getReads += 1;
            const moved = OPTS.headMovesLate && getReads >= 2 && conditionReads >= 1;
            return { data: { ...pr, head: { sha: moved ? 'c'.repeat(40) : SHA },
                             // freshLabels / freshDraft: what GitHub has NOW, which the
                             // one-time pulls.list snapshot may not reflect.
                             labels: OPTS.freshLabels !== undefined ? OPTS.freshLabels : (OPTS.labels || []),
                             draft: OPTS.freshDraft || false,
                             requested_reviewers: [] } };
          },
        },
        repos: { compareCommits: async () => {
          conditionReads += 1;
          return { data: { behind_by: OPTS.behindBy || 0 } };
        } },
        checks: {
          listForRef: async () => {
            if (OPTS.checksFetchFails) throw new Error('checks unavailable');
            return OPTS.greptileRun
              ? [{ name: 'Greptile Review', status: OPTS.greptileRun.status || 'completed',
                   conclusion: OPTS.greptileRun.conclusion || null,
                   completed_at: '2026-07-27T00:00:00Z' }]
              : [];
          },
          // Nothing in an ADVISORY controller may publish a check run — a
          // published run is exactly how a workflow makes itself a merge gate.
          create: async (o) => { calls.checksCreated.push(o.name); return {}; },
        },
        issues: {
          // OPTS.markerSha: a prior handoff marker from THIS workflow's bot, pinning
          // the label to that SHA. Trust is bot-only, so the author must match exactly.
          listComments: async () => ((OPTS.greptileSummons || []).map((c) => ({
            user: { login: 'github-actions[bot]', type: 'Bot' },
            created_at: c.at,
            body: `@greptileai review\\n<!-- greptile-gate:summon:${c.sha || 'a'.repeat(40)} -->`,
          })).concat(OPTS.untrustedSummons ? [{
            // A FORGED summon marker from a random account on this PUBLIC repo.
            user: { login: 'a-stranger', type: 'User' },
            created_at: '2099-01-01T00:00:00Z',
            body: `@greptileai review\\n<!-- greptile-gate:summon:${'a'.repeat(40)} -->`,
          }] : []).concat(OPTS.markerSha ? [{
            user: { login: 'github-actions[bot]', type: 'Bot' },
            created_at: '2026-07-27T00:00:00Z',
            body: `handoff\\n<!-- greptile-gate:labelled:${OPTS.markerSha} -->`,
          }] : []).concat(OPTS.forgedMarkerSha ? [{
            user: { login: 'a-stranger', type: 'User' },
            created_at: '2099-01-01T00:00:00Z',
            body: `handoff\\n<!-- greptile-gate:labelled:${OPTS.forgedMarkerSha} -->`,
          }] : [])),
          createComment: async (o) => { calls.comments.push(o.body); return { data: { id: 777 } }; },
          addLabels: async () => { calls.addLabels += 1; return {}; },
          removeLabel: async () => { calls.removeLabel += 1; return {}; },
        },
      },
    };
    const log = [];
    const core = { info: (m) => log.push(String(m)), notice: (m) => log.push(String(m)),
                   warning: (m) => log.push(String(m)), setFailed: (m) => { throw new Error(m); } };
    const context = { repo: { owner: 'o', repo: 'r' }, payload: {}, eventName: 'schedule' };

    (async () => {
      await new Function('github', 'context', 'core',
        `return (async () => { ${SRC} })()`)(github, context, core);
      console.log(JSON.stringify({ ...calls, log }));
    })();
    """
)


def _run(tmp_path: Path, script: str, **opts) -> dict:
    sp = tmp_path / "script.js"
    sp.write_text(script)
    hp = tmp_path / "harness.js"
    hp.write_text(HARNESS)
    proc = subprocess.run(
        [node, str(hp), str(sp), json.dumps(opts)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"harness failed: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _summons(result: dict) -> list[str]:
    return [c for c in result["comments"] if c.strip().startswith("@greptileai review")]


# --- the re-tiering: this workflow can no longer gate a merge ----------------
def test_workflow_cannot_publish_a_check_run(tmp_path):
    """THE load-bearing property of the demotion.

    A workflow makes itself a merge gate by publishing a check run whose name is a
    required context. An advisory controller must never do that — and must not even
    hold the `checks: write` permission that would let it. Both are asserted: the
    permission (so the capability is absent) and the behaviour (so a future edit
    that re-grants it still gets caught here)."""
    perms = _spec()["permissions"]
    assert perms.get("checks") != "write", (
        "greptile-gate must not hold checks:write — that is the permission that lets a "
        "workflow publish a required context and turn itself back into a merge gate")
    r = _run(tmp_path, _script())
    assert r["checksCreated"] == [], (
        f"advisory controller published check runs: {r['checksCreated']}")


def test_deleted_reviewer_machinery_stays_deleted():
    """Copilot summons, Codex summons and the vacuous REQUIRED_CHECKS term were
    removed as part of the re-tiering. Each was a fail-open or a no-op:

      * `REQUIRED_CHECKS = []` made `checksOk` vacuously TRUE — a condition that
        read like enforcement and enforced nothing;
      * the `@codex review` summon could never work (a Codex mention resolves
        against the COMMENTER, and this posts as `github-actions[bot]`, which has
        no Codex account — measured across #1589: 7 mentions, 7 "not connected");
      * both reviewer graces were unbounded-wait patches for a sequencing problem
        that only mattered while Greptile was treated as a gate.

    Re-adding any of them means the demotion has been quietly undone."""
    src = _script()
    for banned in ("REQUIRED_CHECKS", "COPILOT_BOT_ID", "requestReviews",
                   "CODEX_GRACE_MIN", "@codex review", "greptile-gate:codex:",
                   "greptile-gate:copilot:"):
        assert banned not in src, f"removed machinery is back in the workflow: {banned!r}"


# --- positive control -------------------------------------------------------
def test_settled_pr_hands_off(tmp_path):
    """The positive control. Without it, every negative below could pass by never labelling."""
    r = _run(tmp_path, _script())
    assert r["addLabels"] == 1, r["log"]
    # The marker must carry the SHA, and must be posted BEFORE the label — a label
    # with no marker sits inside Greptile's filter and gets stripped again next run.
    assert any("greptile-gate:labelled:" + "a" * 40 in c for c in r["comments"]), r["comments"]
    # Measured on the pipeline's first autonomous run (#1575): the label admits the
    # PR through Greptile's filter but does NOT start the review — the summon does.
    assert _summons(r), r["comments"]


# --- the two remaining hold conditions --------------------------------------
def test_behind_branch_holds(tmp_path):
    """`strict` would force an update anyway; handing off behind wastes the review."""
    r = _run(tmp_path, _script(), behindBy=3)
    assert r["addLabels"] == 0, r["log"]


def test_unresolved_thread_holds(tmp_path):
    r = _run(tmp_path, _script(), unresolved=True)
    assert r["addLabels"] == 0, r["log"]


def test_unreadable_threads_hold(tmp_path):
    """An unreadable condition is never a pass: a GraphQL failure must hold, not
    fall through to labelling with `unresolved` stuck at its initial 0."""
    r = _run(tmp_path, _script(), threadsFetchFails=True)
    assert r["addLabels"] == 0, r["log"]
    assert any("could not read threads" in m for m in r["log"]), r["log"]


# --- stale-snapshot handling ------------------------------------------------
def test_head_moving_mid_sweep_uses_the_authoritative_sha(tmp_path):
    """`pulls.list` is fetched once for the whole sweep and goes stale.

    Everything downstream — the handed-off comparison, the summon marker, the
    handoff marker — must key off the SHA from `pulls.get`, not the stale list
    entry. Otherwise the gate reasons about a commit nobody is merging.
    """
    r = _run(tmp_path, _script(), staleListSha=True)
    assert r["addLabels"] == 1, r["log"]
    handoff = [c for c in r["comments"] if "greptile-gate:labelled:" in c]
    assert handoff, r["comments"]
    assert "a" * 40 in handoff[0], "marker must carry the CURRENT head"
    assert "b" * 40 not in handoff[0], "marker must not carry the stale list head"


def test_label_on_a_newer_head_is_stripped(tmp_path):
    """A label whose marker points at an older SHA is stale and must go.

    Left in place, the PR sits inside Greptile's dashboard filter and is
    re-reviewed — and re-billed — on every subsequent push."""
    r = _run(tmp_path, _script(), staleListSha=True,
             labels=[{"name": "greptile"}], markerSha="b" * 40)
    assert r["removeLabel"] == 1, r["log"]
    assert any("stale label removed" in m for m in r["log"]), r["log"]


def test_label_present_only_in_the_fresh_read_is_still_stripped(tmp_path):
    """A stale label snapshot disables the repair path, which is why this matters.

    The list says unlabelled; GitHub actually has the label, applied on an older
    head. Stale-label stripping only runs when `isLabelled`, so reading labels from
    the stale snapshot turns it off and the label survives on a superseded commit.
    """
    r = _run(tmp_path, _script(), labels=[],
             freshLabels=[{"name": "greptile"}], markerSha="b" * 40)
    assert r["removeLabel"] == 1, r["log"]
    assert any("stale label removed" in m for m in r["log"]), r["log"]


def test_pr_turned_draft_during_the_sweep_is_not_labelled(tmp_path):
    """Draft is read from the one-time list; a PR marked draft since then must not ship."""
    r = _run(tmp_path, _script(), freshDraft=True)
    assert r["addLabels"] == 0, r["log"]
    assert any("became a draft" in m for m in r["log"]), r["log"]


def test_head_moving_in_the_final_window_is_not_labelled(tmp_path):
    """The head check is the LAST read before the write.

    GitHub has no compare-and-swap, so the window cannot be closed — only narrowed.
    This pins that a head moving after the conditions are read still blocks the
    label, rather than stamping a marker with a superseded SHA.
    """
    r = _run(tmp_path, _script(), headMovesLate=True)
    assert r["addLabels"] == 0, r["log"]
    assert any("head moved during the sweep" in m for m in r["log"]), r["log"]


# --- forgery resistance (this repo is PUBLIC) -------------------------------
def test_forged_handoff_marker_is_ignored(tmp_path):
    """Anyone can post a comment containing the marker string. Only this
    workflow's own bot is trusted — an unfiltered match would let a stranger pin
    the label to a SHA of their choosing, or fake a clearance."""
    r = _run(tmp_path, _script(), labels=[{"name": "greptile"}],
             forgedMarkerSha="a" * 40)
    # The forged marker claims THIS head, which would make it look already-handed-off
    # and suppress repair. Untrusted, so the label reads as unexplained and is stripped.
    assert r["removeLabel"] == 1, r["log"]


def test_forged_summon_marker_does_not_debounce(tmp_path):
    """A forged summon marker must not suppress the real summon — otherwise a
    stranger can silently starve a PR of its advisory review."""
    r = _run(tmp_path, _script(), untrustedSummons=True)
    assert r["addLabels"] == 1, r["log"]
    assert _summons(r), f"forged marker suppressed the real summon: {r['comments']}"


# --- summon lifecycle -------------------------------------------------------
def test_handed_off_without_greptile_run_resummons(tmp_path):
    """Label+marker present, no Greptile Review check-run for the head: the
    post-label summon must be re-posted, or a swallowed comment failure strands
    the PR labeled-but-never-reviewed forever (Codex P1, #1577)."""
    r = _run(tmp_path, _script(), labels=[{"name": "greptile"}], markerSha="a" * 40)
    assert _summons(r), f"handed-off head with no Greptile run must re-summon: {r['comments']}"


def test_fresh_summon_debounces_resummon(tmp_path):
    """A trusted summon younger than 10min means one is in flight — events in
    Greptile's check-creation gap must not spam more (Codex, #1577)."""
    r = _run(tmp_path, _script(), labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileSummons=[{"at": "2099-01-01T00:00:00Z"}])
    assert not _summons(r), r["comments"]


def test_dead_greptile_run_does_not_block_resummon(tmp_path):
    """cancelled/timed_out runs are infra deaths on an immutable head: they must
    not satisfy the is-Greptile-coming check forever (Codex, #1577). A completed
    FAILURE is a real verdict and must still block."""
    r = _run(tmp_path, _script(), labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "cancelled"})
    assert _summons(r), r["comments"]
    r2 = _run(tmp_path, _script(), labels=[{"name": "greptile"}], markerSha="a" * 40,
              greptileRun={"status": "completed", "conclusion": "failure"})
    assert not _summons(r2), r2["comments"]


def test_in_progress_greptile_run_suppresses_resummon(tmp_path):
    """An in-flight run (status != completed) IS Greptile coming — no re-summon.

    Pins the `status !== 'completed'` arm of the LIVE filter, which nothing else
    exercises: a rewrite to "completed non-DEAD only" passed the entire suite while
    re-summoning (and re-billing) on every sweep of the review window."""
    r = _run(tmp_path, _script(), labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileRun={"status": "in_progress"})
    assert not _summons(r), r["comments"]


def test_skipped_greptile_run_does_not_block_resummon(tmp_path):
    """completed/skipped is DEAD, on BOTH the handed-off and fresh-handoff paths.

    Greptile deliberately skips PRs over ~50 files — the credit is spent and no
    review exists. Counted as LIVE, that skip suppressed re-summons forever:
    labeled-but-never-reviewed with no repair path. Re-summoning is harmless
    (attempts bounded by the per-head cap; the debounce only spaces them out)."""
    r = _run(tmp_path, _script(), labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "skipped"})
    assert _summons(r), r["comments"]
    # fresh-handoff path: the skipped run must not suppress the post-label summon
    r2 = _run(tmp_path, _script(), greptileRun={"status": "completed", "conclusion": "skipped"})
    assert r2["addLabels"] == 1, r2["log"]
    assert _summons(r2), r2["comments"]


def test_rehandoff_of_reviewed_sha_does_not_resummon(tmp_path):
    """Strip-then-relabel on the same SHA: the label is re-applied, but a live
    Greptile run already exists for the head — a fresh summon would bill a
    re-review of an already-reviewed diff (Codex, #1577)."""
    r = _run(tmp_path, _script(), greptileRun={"status": "completed", "conclusion": "success"})
    assert r["addLabels"] == 1, r["log"]
    assert not _summons(r), r["comments"]


def test_handoff_debounces_when_summon_already_in_flight(tmp_path):
    """A fresh trusted summon exists and no run yet — the handoff must not double-summon."""
    r = _run(tmp_path, _script(), greptileSummons=[{"at": "2099-01-01T00:00:00Z"}])
    assert r["addLabels"] == 1, r["log"]
    assert not _summons(r), r["comments"]


def test_stale_head_summon_does_not_debounce_the_new_head(tmp_path):
    """Per-sha debounce (Greptile P1): a fresh summon for the PREVIOUS head must
    not suppress the summon for the current one — otherwise every push inside the
    window leaves the new head unreviewed."""
    r = _run(tmp_path, _script(), labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileSummons=[{"at": "2099-01-01T00:00:00Z", "sha": "b" * 40}])
    assert _summons(r), r["comments"]


def test_summon_attempts_are_capped_per_head(tmp_path):
    """Greptile P1: treating `skipped` as DEAD makes every sweep summon-eligible,
    and a recency-only debounce has no attempt limit — an oversized PR (Greptile
    skips >50 files) would accrue a summon every 10 minutes forever. After 3 aged
    summons for the same head the gate must stop asking."""
    r = _run(tmp_path, _script(), labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileSummons=[{"at": "2020-01-01T00:00:00Z"}] * 3,
             greptileRun={"status": "completed", "conclusion": "skipped"})
    assert not _summons(r), r["comments"]
    assert any("not asking again" in m for m in r["log"]), r["log"]
    # two aged summons is still under the cap -> it DOES summon
    r2 = _run(tmp_path, _script(), labels=[{"name": "greptile"}], markerSha="a" * 40,
              greptileSummons=[{"at": "2020-01-01T00:00:00Z"}] * 2,
              greptileRun={"status": "completed", "conclusion": "skipped"})
    assert _summons(r2), r2["comments"]


def test_summon_cap_also_applies_to_fresh_handoff(tmp_path):
    """The cap must hold on BOTH paths (Codex, #1581): a strip-then-relabel on the
    same sha re-enters the fresh-handoff path, and without the cap there an
    oversized PR resumes summoning forever after the other side gave up."""
    r = _run(tmp_path, _script(),
             greptileSummons=[{"at": "2020-01-01T00:00:00Z"}] * 3,
             greptileRun={"status": "completed", "conclusion": "skipped"})
    assert r["addLabels"] == 1, r["log"]              # still hands off
    assert not _summons(r), r["comments"]
    assert any("not asking again" in m for m in r["log"]), r["log"]


def test_unreadable_checks_api_does_not_block_the_label(tmp_path):
    """A Checks API outage must not cost the handoff. The label is applied first;
    only the SUMMON needs the live-run read, and an unreadable read holds the
    summon (fail-closed on spend) while the schedule retries it."""
    r = _run(tmp_path, _script(), checksFetchFails=True)
    assert r["addLabels"] == 1, r["log"]
    assert not _summons(r), r["comments"]
    assert any("could not read Greptile runs" in m for m in r["log"]), r["log"]
