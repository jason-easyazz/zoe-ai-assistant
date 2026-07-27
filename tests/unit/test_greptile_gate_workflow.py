"""Behavioural tests for .github/workflows/greptile-gate.yml.

The gate decides when a PR is handed to Greptile — the one REQUIRED, BILLED review.
Both of its failure modes are expensive and silent:

  * label too early  → Greptile reviews a head the cheap tier has not cleared, which
    is the exact spend the gate exists to prevent;
  * never label      → PRs stall forever with no signal.

It went through eight review rounds with no test at all, and the same class of bug
(the pre-action revalidation checking a SUBSET of the conditions the decision used)
was introduced, fixed, and then reintroduced. So these tests do not assert on the
YAML text — they EXECUTE the real embedded script against a stubbed GitHub API and
assert on what it actually does.

The load-bearing case is `test_red_check_during_summon_window_is_not_labelled`:
remove the revalidation of `checksOk` and it goes red. It was verified to do so.
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
    # FAIL the lane, not skip 15 tests and report green (the PyYAML importorskip
    # did exactly that until two reviewers caught it). Locally, skipping is fine.
    raise RuntimeError("node is required in CI for the greptile-gate workflow tests")
pytestmark = [pytestmark, pytest.mark.skipif(not node, reason="node not available (non-CI)")]


def _script() -> str:
    """The inline `actions/github-script` body, exactly as CI runs it."""
    spec = yaml.safe_load(WORKFLOW.read_text())
    steps = spec["jobs"]["label-when-others-pass"]["steps"]
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

    let checkReads = 0;
    let getReads = 0;
    const calls = { addLabels: 0, removeLabel: 0, comments: [], deleted: [] };
    // OPTS.staleListSha simulates the head moving between the sweep's opening
    // `pulls.list` and this PR being processed: the list is stale, `pulls.get` is current.
    const LIST_SHA = OPTS.staleListSha ? 'b'.repeat(40) : SHA;
    const pr = { number: 1, head: { sha: LIST_SHA }, base: { ref: 'main' },
                 labels: OPTS.labels || [], draft: false };

    let reviewReads = 0;
    const asReview = (l) => ({ commit_id: SHA, state: 'COMMENTED', user: { login: l } });
    // OPTS.dismissMidSweep: a login present on the first read and gone on the second —
    // what a DISMISSED review looks like, since DISMISSED is not a submitted state.
    const listReviews = () => {
      reviewReads += 1;
      const ls = (OPTS.reviewers || []).filter(
        (l) => !(OPTS.dismissMidSweep === l && reviewReads >= 2));
      return ls.map(asReview);
    };

    const github = {
      paginate: async (fn, o) => fn(o),
      graphql: async (q) => {
        if (OPTS.copilotSummonFails && q.includes('requestReviews')) throw new Error('mutation failed');
        if (q.includes('reviewThreads')) return { repository: { pullRequest: { reviewThreads: {
          pageInfo: { hasNextPage: false, endCursor: null },
          nodes: (OPTS.unresolved ? [{ isResolved: false }] : []) } } } };
        if (q.includes('{ id }')) return { repository: { pullRequest: { id: 'PR_1' } } };
        return {};
      },
      rest: {
        pulls: {
          list: async () => [pr],
          // OPTS.headMovesLate: the head moves once the conditions have been re-read.
          // Keyed on checkReads (readConditions calls listForRef), NOT on the number of
          // pulls.get calls — a get-count trigger fires in BOTH orderings and so cannot
          // tell whether the head check runs before or after readConditions.
          get: async () => {
            getReads += 1;
            const moved = OPTS.headMovesLate && getReads >= 2 && checkReads >= 2;
            return { data: { ...pr, head: { sha: moved ? 'c'.repeat(40) : SHA },
                             // freshLabels / freshDraft: what GitHub has NOW, which the
                             // one-time pulls.list snapshot may not reflect.
                             labels: OPTS.freshLabels !== undefined ? OPTS.freshLabels : (OPTS.labels || []),
                             draft: OPTS.freshDraft || false,
                             requested_reviewers: [] } };
          },
          listReviews: async () => listReviews(),
        },
        repos: { compareCommits: async () => ({ data: { behind_by: OPTS.behindBy || 0 } }) },
        checks: {
          listForRef: async () => {
            checkReads += 1;
            // checksFlip: green on the first read, red on the second — a required check
            // that re-runs and fails while the summon calls are in flight.
            const red = OPTS.checksRed || (OPTS.checksFlip && checkReads >= 2);
            const extra = OPTS.greptileRun
              ? [{ name: 'Greptile Review', status: OPTS.greptileRun.status || 'completed',
                   conclusion: OPTS.greptileRun.conclusion || null,
                   completed_at: '2026-07-27T00:00:00Z' }] : [];
            return extra.concat(['Cursor Bugbot'].map((name) => ({
              name, status: OPTS.checksPending ? 'in_progress' : 'completed',
              conclusion: red ? 'failure' : 'neutral',
              completed_at: '2026-07-27T00:00:00Z' })));
          },
        },
        issues: {
          // OPTS.markerSha: a prior handoff marker from THIS workflow's bot, pinning
          // the label to that SHA. Trust is bot-only, so the author must match exactly.
          listComments: async () => ((OPTS.greptileSummons || []).map((c) => ({
            user: { login: 'github-actions[bot]', type: 'Bot' },
            created_at: c.at, body: '@greptileai review',
          })).concat((OPTS.codexSummons || []).map((c) => ({
            user: { login: 'github-actions[bot]', type: 'Bot' },
            created_at: c.at,
            body: `@codex review\n<!-- greptile-gate:codex:${c.sha || 'a'.repeat(40)} -->`,
          }))).concat((OPTS.copilotSummons || []).map((c) => ({
            user: { login: 'github-actions[bot]', type: 'Bot' },
            created_at: c.at,
            body: `Requested Copilot review.\n<!-- greptile-gate:copilot:${c.sha || 'a'.repeat(40)} -->`,
          }))).concat(OPTS.markerSha ? [{
            user: { login: 'github-actions[bot]', type: 'Bot' },
            created_at: '2026-07-27T00:00:00Z',
            body: `handoff\n<!-- greptile-gate:labelled:${OPTS.markerSha} -->`,
          }] : [])),
          createComment: async (o) => { calls.comments.push(o.body); return { data: { id: 777 } }; },
          deleteComment: async (o) => { calls.deleted.push(o.comment_id); return {}; },
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


BOTH = ["copilot-pull-request-reviewer[bot]", "chatgpt-codex-connector[bot]"]


def test_all_green_hands_off(tmp_path):
    """The positive control. Without it, every negative below could pass by never labelling."""
    r = _run(tmp_path, _script(), reviewers=BOTH)
    assert r["addLabels"] == 1, r["log"]
    # The marker must carry the SHA, and must be posted BEFORE the label — a label with
    # no marker sits inside Greptile's filter and gets stripped again next run.
    assert any("greptile-gate:labelled:" + "a" * 40 in c for c in r["comments"]), r["comments"]
    # Measured on the pipeline's first autonomous run (#1575): the label admits
    # the PR through Greptile's filter but does NOT start the review — the
    # summon does. The handoff must post both.
    assert any(c.strip() == "@greptileai review" for c in r["comments"]), r["comments"]


def test_red_check_during_summon_window_is_not_labelled(tmp_path):
    """THE regression test: conditions that change mid-sweep must be re-read.

    Checks are green when the decision is made and red by the time we act. The
    pre-action revalidation originally re-read only SHA/behindness/threads, so this
    case was labelled anyway. Drop `st2.checksOk` from the revalidation and this
    test goes red — verified.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, checksFlip=True)
    assert r["addLabels"] == 0, r["log"]
    assert any("conditions changed during the sweep" in m for m in r["log"]), r["log"]


def test_pending_check_holds(tmp_path):
    """A check still in flight has no meaningful conclusion — hold, never hand off."""
    r = _run(tmp_path, _script(), reviewers=BOTH, checksPending=True)
    assert r["addLabels"] == 0, r["log"]


def test_behind_branch_holds(tmp_path):
    """`strict` would force an update anyway; handing off behind wastes the review."""
    r = _run(tmp_path, _script(), reviewers=BOTH, behindBy=3)
    assert r["addLabels"] == 0, r["log"]


def test_unresolved_thread_holds(tmp_path):
    r = _run(tmp_path, _script(), reviewers=BOTH, unresolved=True)
    assert r["addLabels"] == 0, r["log"]


def test_missing_copilot_review_holds_and_summons(tmp_path):
    """Copilot has not reviewed this head and no summon marker is aged: hold."""
    r = _run(tmp_path, _script(), reviewers=["chatgpt-codex-connector[bot]"])
    assert r["addLabels"] == 0, r["log"]
    # the summon posts a timestamp marker so the grace has an anchor
    assert any("greptile-gate:copilot:" in c for c in r["comments"]), r["comments"]


def test_copilot_summon_happens_once_per_head(tmp_path):
    """A fresh summon marker must SUPPRESS further summons — every sweep posting a
    new marker resets the newest-marker grace clock forever (Bugbot, High): the
    exact unbounded wait the grace was added to remove, rebuilt one commit later.
    With a fresh marker present, the sweep must post NO new copilot marker."""
    r = _run(tmp_path, _script(), reviewers=["chatgpt-codex-connector[bot]"],
             copilotSummons=[{"at": "2099-01-01T00:00:00Z"}])
    assert r["addLabels"] == 0, r["log"]
    new_marks = [c for c in r["comments"] if "greptile-gate:copilot:" in c]
    assert new_marks == [], f"sweep must not re-post the copilot marker: {new_marks}"


def test_copilot_grace_elapses_like_codex(tmp_path):
    """Observed live (#1573): GitHub silently drops Copilot re-requests once it
    has reviewed earlier heads — the mutation succeeds, requested_reviewers stays
    empty, the review never comes. A graceless required reviewer is an unbounded
    wait, so an aged summon marker passes Copilot exactly like Codex's grace."""
    r = _run(tmp_path, _script(), reviewers=["chatgpt-codex-connector[bot]"],
             copilotSummons=[{"at": "2020-01-01T00:00:00Z"}])
    assert r["addLabels"] == 1, r["log"]
    # and a FRESH summon does not pass it
    r2 = _run(tmp_path, _script(), reviewers=["chatgpt-codex-connector[bot]"],
              copilotSummons=[{"at": "2099-01-01T00:00:00Z"}])
    assert r2["addLabels"] == 0, r2["log"]


def test_head_moving_mid_sweep_uses_the_authoritative_sha(tmp_path):
    """`pulls.list` is fetched once for the whole sweep and goes stale.

    Everything downstream — the handed-off comparison, the condition reads, the Codex
    marker, the handoff marker — must key off the SHA from `pulls.get`, not the stale
    list entry. Otherwise the gate reasons about a commit nobody is merging: it can
    skip stale-label cleanup and skip regression revocation while the `greptile`
    label still sits on a NEWER commit.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, staleListSha=True)
    assert r["addLabels"] == 1, r["log"]
    handoff = [c for c in r["comments"] if "greptile-gate:labelled:" in c]
    assert handoff, r["comments"]
    assert "a" * 40 in handoff[0], "marker must carry the CURRENT head"
    assert "b" * 40 not in handoff[0], "marker must not carry the stale list head"


def test_label_on_a_newer_head_is_revoked_not_ignored(tmp_path):
    """A label whose marker points at an older SHA is a false claim and must go.

    With the stale list SHA this comparison used to be made against the OLD head, so
    a PR labelled on a superseded commit looked "already handed off" and was left
    alone — the label kept asserting "cheap tier green" for a commit it never saw.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, staleListSha=True,
             labels=[{"name": "greptile"}], markerSha="b" * 40)
    assert r["removeLabel"] == 1, r["log"]
    assert any("stale label removed" in m for m in r["log"]), r["log"]


def test_codex_review_dismissed_mid_sweep_is_not_labelled(tmp_path):
    """A DISMISSED review drops out of the submitted set — codex is NOT monotone.

    An earlier version froze `codexOk` on the claim that for a fixed head it only ever
    moves false->true. That claim is wrong, and this is the case that breaks it: Codex
    has reviewed when the decision is made and its review is dismissed before the gate
    acts. Freeze `codexOk` again and this goes red.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH,
             dismissMidSweep="chatgpt-codex-connector[bot]")
    assert r["addLabels"] == 0, r["log"]
    assert any("codex=false" in m for m in r["log"]), r["log"]


def test_copilot_review_dismissed_mid_sweep_is_not_labelled(tmp_path):
    r = _run(tmp_path, _script(), reviewers=BOTH,
             dismissMidSweep="copilot-pull-request-reviewer[bot]")
    assert r["addLabels"] == 0, r["log"]


def test_head_moving_in_the_final_window_is_not_labelled(tmp_path):
    """The head check is the LAST read before the write.

    GitHub has no compare-and-swap, so the window cannot be closed — only narrowed.
    This pins that a head moving after the conditions are read still blocks the label,
    rather than stamping a marker with a superseded SHA.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, headMovesLate=True)
    assert r["addLabels"] == 0, r["log"]
    assert any("head moved during the sweep" in m for m in r["log"]), r["log"]


def test_grace_clock_uses_the_newest_codex_summon(tmp_path):
    """`find` returns the OLDEST match — the grace must key off the NEWEST.

    Two trusted summon markers on the same head: one long past the 20-minute grace,
    one just posted. Taking the older one makes the grace read as elapsed and waves
    the PR through while the latest @codex review is still inside its window. Codex
    has NOT reviewed here, so the only thing that could pass it is the grace.
    """
    r = _run(
        tmp_path, _script(),
        reviewers=["copilot-pull-request-reviewer[bot]"],
        codexSummons=[{"at": "2020-01-01T00:00:00Z"}, {"at": "2099-01-01T00:00:00Z"}],
    )
    assert r["addLabels"] == 0, r["log"]


def test_regression_after_handoff_is_decided_on_fresh_conditions(tmp_path):
    """A PR already handed off, whose checks go red during the summon window.

    The revocation used to be decided from the PRE-summon read, so a regression that
    appeared in that window left the label in place — still asserting "cheap tier
    green" for a commit that had since failed.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, checksFlip=True,
             labels=[{"name": "greptile"}], markerSha="a" * 40)
    assert r["removeLabel"] == 1, r["log"]
    assert any("regressed after handoff" in m for m in r["log"]), r["log"]


def test_label_present_only_in_the_fresh_read_is_still_stripped(tmp_path):
    """A stale label snapshot disables BOTH repair paths, which is why this is severe.

    The list says unlabelled; GitHub actually has the label, applied on an older head.
    Revocation only runs when `alreadyHandedOff`, and stale-label stripping only when
    `isLabelled` — reading labels from the stale snapshot turns both off and the label
    survives, still asserting a clearance for a commit it was never granted for.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[],
             freshLabels=[{"name": "greptile"}], markerSha="b" * 40)
    assert r["removeLabel"] == 1, r["log"]
    assert any("stale label removed" in m for m in r["log"]), r["log"]


def test_pr_turned_draft_during_the_sweep_is_not_labelled(tmp_path):
    """Draft is read from the one-time list; a PR marked draft since then must not ship."""
    r = _run(tmp_path, _script(), reviewers=BOTH, freshDraft=True)
    assert r["addLabels"] == 0, r["log"]
    assert any("became a draft" in m for m in r["log"]), r["log"]


def test_failed_copilot_summon_leaves_no_grace_anchor(tmp_path):
    """Greptile P1, both rounds: the grace anchor must ATTEST a successful
    summon. A failed mutation now posts no marker at all — no anchor, no grace,
    the PR holds and the next sweep retries. Copilot can never pass the gate
    via the grace without having actually been requested."""
    r = _run(tmp_path, _script(), reviewers=["chatgpt-codex-connector[bot]"],
             copilotSummonFails=True)
    assert r["addLabels"] == 0, r["log"]
    posted = [c for c in r["comments"] if "greptile-gate:copilot:" in c]
    assert posted == [], f"failed mutation must post NO marker: {posted}"


def test_regressed_handed_off_head_is_revoked_not_summoned(tmp_path):
    """Handed off, no Greptile run, but a required check has gone RED: revoke the
    label, do NOT spend a summon on a head about to lose its handoff (Codex,
    #1577 — the re-summon must run behind the fresh-conditions read)."""
    r = _run(tmp_path, _script(), reviewers=BOTH, checksRed=True,
             labels=[{"name": "greptile"}], markerSha="a" * 40)
    assert r["removeLabel"] == 1, r["log"]
    assert not any(c.strip() == "@greptileai review" for c in r["comments"]), r["comments"]


def test_handed_off_without_greptile_run_resummons(tmp_path):
    """Label+marker present, no Greptile Review check-run for the head: the
    post-label summon must be re-posted, or a swallowed comment failure strands
    the PR labeled-but-never-reviewed forever (Codex P1, #1577)."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             labels=[{"name": "greptile"}], markerSha="a" * 40)
    summons = [c for c in r["comments"] if c.strip() == "@greptileai review"]
    assert summons, f"handed-off head with no Greptile run must re-summon: {r['comments']}"


def test_fresh_summon_debounces_resummon(tmp_path):
    """A trusted summon younger than 10min means one is in flight — events in
    Greptile's check-creation gap must not spam more (Codex, #1577)."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileSummons=[{"at": "2099-01-01T00:00:00Z"}])
    assert not any(c.strip() == "@greptileai review" for c in r["comments"]), r["comments"]


def test_dead_greptile_run_does_not_block_resummon(tmp_path):
    """cancelled/timed_out runs are infra deaths on an immutable head: they must
    not satisfy the is-Greptile-coming check forever (Codex, #1577). A completed
    FAILURE is a real verdict and must still block."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "cancelled"})
    assert any(c.strip() == "@greptileai review" for c in r["comments"]), r["comments"]
    r2 = _run(tmp_path, _script(), reviewers=BOTH,
              labels=[{"name": "greptile"}], markerSha="a" * 40,
              greptileRun={"status": "completed", "conclusion": "failure"})
    assert not any(c.strip() == "@greptileai review" for c in r2["comments"]), r2["comments"]


def test_rehandoff_of_reviewed_sha_does_not_resummon(tmp_path):
    """Regress-then-clear on the same SHA: the label is re-applied, but a live
    Greptile run already exists for the head — a fresh summon would bill a
    re-review of a reviewed diff (Codex, #1577)."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             greptileRun={"status": "completed", "conclusion": "success"})
    assert r["addLabels"] == 1, r["log"]
    assert not any(c.strip() == "@greptileai review" for c in r["comments"]), r["comments"]


def test_handoff_debounces_when_summon_already_in_flight(tmp_path):
    """Revoke-then-recover inside Greptile's check-creation gap: a fresh trusted
    summon exists, no run yet — the re-handoff must not double-summon."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             greptileSummons=[{"at": "2099-01-01T00:00:00Z"}])
    assert r["addLabels"] == 1, r["log"]
    assert not any(c.strip() == "@greptileai review" for c in r["comments"]), r["comments"]
