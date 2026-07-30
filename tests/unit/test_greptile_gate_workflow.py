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

The suite also pins reviewer grace; handoff revalidation via dismissal, behind-ness,
and unresolved-thread conditions; label repair; and bounded Greptile re-summons while
proving that disabled Bugbot checks cannot block the gate.
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
    let conditionReads = 0;
    let getReads = 0;
    // `events` records ORDER, which is the whole property under test for the
    // auto-merge race: the blocking check must be raised BEFORE the label.
    const calls = { addLabels: 0, removeLabel: 0, comments: [], deleted: [],
                    gateChecks: [], events: [] };
    // OPTS.staleListSha simulates the head moving between the sweep's opening
    // `pulls.list` and this PR being processed: the list is stale, `pulls.get` is current.
    const LIST_SHA = OPTS.staleListSha ? 'b'.repeat(40) : SHA;
    // Far-future default for the head-seen anchor: the Codex grace can never read as
    // elapsed unless a test opts in, so "Codex has not reviewed" HOLDS by default —
    // matching the pre-anchor behaviour where an absent summon left graceElapsed false.
    const FUTURE = '2099-01-01T00:00:00Z';
    const pr = { number: 1, head: { sha: LIST_SHA }, base: { ref: 'main' },
                 labels: OPTS.labels || [], draft: false,
                 // The PR's own birth time clamps the Codex grace anchor: a branch
                 // pushed long before the PR opened must not read as already-graced.
                 created_at: OPTS.prCreatedAt || '2020-01-01T00:00:00Z' };

    // OPTS.sharedShaPrs: OTHER open PRs pointing at the same head commit. Branch
    // protection resolves a required context by check NAME on the PR's head SHA and
    // never looks at external_id, so a success published for one of these satisfies
    // the others too — which is the whole point of the shared-SHA coordination.
    //
    // Per-PR spec knobs:
    //   markerSha   — the SHA this PR's own trusted handoff marker pins it to (absent
    //                 = no marker at all, i.e. a label with no proof of handoff)
    //   arrivesLate — NOT in the sweep's OPENING pulls.list; visible to every later
    //                 read. Models a PR moving onto the head mid-sweep.
    //   assocOnly   — never in ANY pulls.list; visible only via the commit->PRs
    //                 association endpoint.
    //   hiddenFromAssoc — the inverse: absent from the association index (which is
    //                 eventually consistent and can lag a very recent push).
    const otherSpecs = (OPTS.sharedShaPrs || []).map((o, i) => ({
      spec: o,
      pr: { number: o.number === undefined ? 100 + i : o.number,
            head: { sha: o.sha || SHA }, base: { ref: 'main' },
            labels: o.labels || [], draft: o.draft || false, state: o.state || 'open',
            created_at: '2020-01-01T00:00:00Z', requested_reviewers: [] } }));
    const otherById = new Map(otherSpecs.map((o) => [o.pr.number, o]));
    let prListReads = 0;

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
          // The OPENING snapshot (first call) cannot contain a PR that moves onto the
          // head afterwards. Coordination must therefore RE-READ rather than trust it.
          list: async () => {
            prListReads += 1;
            return [pr].concat(otherSpecs
              .filter((o) => !o.spec.assocOnly && (prListReads > 1 || !o.spec.arrivesLate))
              .map((o) => o.pr));
          },
          // OPTS.headMovesLate: the head moves once the conditions have been re-read.
          // Keyed on conditionReads, NOT on the number of
          // pulls.get calls — a get-count trigger fires in BOTH orderings and so cannot
          // tell whether the head check runs before or after readConditions.
          get: async (o) => {
            // Co-located PRs answer for themselves; only PR 1 drives the counters, so
            // adding a sharer cannot perturb the head-moves-late timing above.
            const oth = o && otherById.get(o.pull_number);
            if (oth) return { data: oth.pr };
            // OPTS.prGetFails: the AUTHORITATIVE refetch is unavailable. The sweep then
            // skips this PR entirely, so whatever blocking check exists at that moment is
            // all that stands between a green cheap tier and an armed auto-merge.
            if (OPTS.prGetFails) throw new Error('pulls.get unavailable');
            getReads += 1;
            const moved = OPTS.headMovesLate && getReads >= 2 && conditionReads >= 2;
            return { data: { ...pr, head: { sha: moved ? 'c'.repeat(40) : SHA },
                             // freshLabels / freshDraft: what GitHub has NOW, which the
                             // one-time pulls.list snapshot may not reflect.
                             labels: OPTS.freshLabels !== undefined ? OPTS.freshLabels : (OPTS.labels || []),
                             draft: OPTS.freshDraft || false,
                             requested_reviewers: [] } };
          },
          listReviews: async () => listReviews(),
        },
        repos: {
          compareCommits: async () => {
            conditionReads += 1;
            return { data: { behind_by: OPTS.behindBy || 0 } };
          },
          // GET /repos/{o}/{r}/commits/{sha}/pulls — GitHub answers by SHA, so it sees a
          // PR that moved onto the commit after the sweep's opening snapshot. It also
          // returns PRs where the commit is merely an ANCESTOR, so the gate must decide
          // membership by head-equality itself rather than trusting this list.
          listPullRequestsAssociatedWithCommit: async () => {
            if (OPTS.assocFetchFails) throw new Error('association index unavailable');
            return [pr].concat(otherSpecs
              .filter((o) => !o.spec.hiddenFromAssoc).map((o) => o.pr));
          },
          // Fallback anchor for the Codex grace when the head has no check suites.
          getCommit: async () => {
            if (OPTS.commitFetchFails) throw new Error('commit unavailable');
            return { data: { commit: { committer: {
              date: OPTS.commitDate || FUTURE } } } };
          },
        },
        checks: {
          // When GitHub first saw this head — the Codex grace anchor. Defaults to a
          // FUTURE timestamp so the grace is NEVER elapsed unless a test says so,
          // preserving the old default (no anchor -> Codex must actually review).
          listSuitesForRef: async () => {
            if (OPTS.suitesFetchFail) throw new Error('suites unavailable');
            if (OPTS.noSuites) return [];
            return (OPTS.suiteCreatedAt || [OPTS.headSeenAt || FUTURE])
              .map((created_at) => ({ created_at }));
          },
          // The gate's OWN blocking check (greptile-complete).
          create: async (o) => {
            if (OPTS.gateCheckFails) throw new Error('cannot create check');
            // external_id is recorded so a test with co-located PRs can attribute each
            // run to the PR that created it.
            calls.gateChecks.push({ name: o.name, status: o.status,
                                    conclusion: o.conclusion || null,
                                    external_id: String(o.external_id) });
            calls.events.push(`check:${o.status}${o.conclusion ? '/' + o.conclusion : ''}`);
            return { data: { id: 1 } };
          },
          listForRef: async () => {
            if (OPTS.checksFetchFails) throw new Error('checks unavailable');
            checkReads += 1;
            // checksFlip: green on the first read, red on the second — a required check
            // that re-runs and fails while the summon calls are in flight.
            const red = OPTS.checksFlip && checkReads >= 2;
            // greptileRuns (plural) puts SEVERAL runs on the same head, which is how a
            // stale completed success can coexist with a newer in-flight re-review.
            // Pre-existing greptile-complete runs on this SHA. `extId` defaults to THIS
            // PR (1); a test can set a different one to model two PRs sharing a commit.
            const gateExisting = (OPTS.gateRuns || []).map((g) => ({
              name: 'greptile-complete',
              external_id: g.extId === undefined ? '1' : String(g.extId),
              status: g.status || 'in_progress',
              conclusion: g.conclusion || null,
              started_at: g.started_at || null,
              completed_at: g.completed_at || null }));
            const gr = OPTS.greptileRuns
              || (OPTS.greptileRun ? [OPTS.greptileRun] : []);
            const extra = gr.map((g, i) => ({
              name: 'Greptile Review', status: g.status || 'completed',
              conclusion: g.conclusion || null,
              // Check-run ids break a started_at tie unambiguously.
              id: g.id === undefined ? 1000 + i : g.id,
              started_at: g.started_at || null,
              completed_at: g.completed_at || '2026-07-27T00:00:00Z' }));
            return extra.concat(gateExisting).concat(['Cursor Bugbot'].map((name) => ({
              name, status: OPTS.checksPending ? 'in_progress' : 'completed',
              conclusion: red ? 'failure' : 'neutral',
              completed_at: '2026-07-27T00:00:00Z' })));
          },
        },
        issues: {
          // OPTS.markerSha: a prior handoff marker from THIS workflow's bot, pinning
          // the label to that SHA. Trust is bot-only, so the author must match exactly.
          listComments: async (o) => {
            // A co-located PR has its OWN comments — it does not inherit PR 1's
            // handoff marker. Without this, a sharer would borrow PR 1's proof of
            // handoff and the marker contract would be untestable.
            const oth = o && otherById.get(o.issue_number);
            if (oth) return oth.spec.markerSha ? [{
              user: { login: 'github-actions[bot]', type: 'Bot' },
              created_at: '2026-07-27T00:00:00Z',
              body: `handoff\n<!-- greptile-gate:labelled:${oth.spec.markerSha} -->`,
            }] : [];
            return mainComments();
          },
          createComment: async (o) => { calls.comments.push(o.body); return { data: { id: 777 } }; },
          deleteComment: async (o) => { calls.deleted.push(o.comment_id); return {}; },
          addLabels: async () => { calls.addLabels += 1; calls.events.push('label'); return {}; },
          removeLabel: async () => { calls.removeLabel += 1; return {}; },
        },
      },
    };
    function mainComments() { return ((OPTS.greptileSummons || []).map((c) => ({
            user: { login: 'github-actions[bot]', type: 'Bot' },
            created_at: c.at,
            body: `@greptileai review\n<!-- greptile-gate:summon:${c.sha || 'a'.repeat(40)} -->`,
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
          }] : [])); }
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


def test_the_gate_wakes_on_regression_events_not_only_on_progress(tmp_path):
    """A trigger assertion, deliberately — this one cannot be caught by executing the
    script, because it decides whether the script RUNS at all.

    `pull_request_review: [submitted]` alone meant the gate only woke when a condition
    IMPROVED. The events that break a published `greptile-complete: success` are the
    opposite ones: a review `dismissed` (the case `codexOk` is explicitly not monotone
    for), a review `edited`, or a fresh `review_requested` that makes Copilot pending
    again. Without them the success stayed green until the next cron tick — up to 30
    minutes in which an armed auto-merge merges a regressed head.

    The job body needs no per-event handling: its only event-specific logic is the
    `check_suite` guard, and every other event falls through to the full open-PR sweep.
    That is asserted here too, so adding a trigger can never silently do nothing.
    """
    spec = yaml.safe_load(WORKFLOW.read_text())
    # `on:` parses as the boolean True in YAML 1.1 unless quoted.
    triggers = spec[True] if True in spec else spec["on"]

    review = set(triggers["pull_request_review"]["types"])
    assert {"submitted", "dismissed", "edited"} <= review, review

    pull = set(triggers["pull_request"]["types"])
    assert {"ready_for_review", "synchronize", "opened", "review_requested"} <= pull, pull

    # The schedule stays: it is the wake-up for every wait that is not event-driven.
    assert "schedule" in triggers, triggers

    job = spec["jobs"]["label-when-others-pass"]
    assert "check_suite" in job["if"], (
        "the job's only event-specific gate should be the check_suite one; a new "
        f"condition here could silently drop the regression events: {job['if']}")


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
    assert any(c.strip().startswith("@greptileai review") for c in r["comments"]), r["comments"]


def test_failed_bugbot_check_does_not_block_handoff(tmp_path):
    """Bugbot is disabled and must not remain an implicit required check."""
    r = _run(tmp_path, _script(), reviewers=BOTH, checksFlip=True)
    assert r["addLabels"] == 1, r["log"]


def test_pending_bugbot_check_does_not_block_handoff(tmp_path):
    """A stale in-flight Bugbot run cannot deadlock the live gate."""
    r = _run(tmp_path, _script(), reviewers=BOTH, checksPending=True)
    assert r["addLabels"] == 1, r["log"]


def test_checks_api_outage_now_holds_the_gate_fail_closed(tmp_path):
    """A Checks API outage must HOLD the gate — a deliberate change of behaviour.

    This test previously asserted the opposite ("an outage cannot hold the gate when
    no checks are required"), which was right while the gate only READ checks. It now
    OWNS one: `greptile-complete` is the required context that blocks merge until
    Greptile has reviewed. If the Checks API is unreachable the gate can neither
    confirm nor raise that blocker, and labelling anyway would hand off to Greptile
    with no blocking context present — which is precisely the #1587/#1589 race. So the
    gate fails CLOSED and retries on the next sweep.

    The original second assertion still holds and is kept: with REQUIRED_CHECKS empty
    the conditions read must still not fetch check RUNS.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, checksFetchFails=True)
    assert r["addLabels"] == 0, r["log"]
    assert any("could not raise greptile-complete" in m for m in r["log"]), r["log"]
    assert not any("PR #1 check runs" in m for m in r["log"]), r["log"]


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


def test_codex_is_never_summoned(tmp_path):
    """The gate must NOT post `@codex review`.

    Codex reviews on PUSH; the mention resolves against the COMMENTER, which is
    github-actions[bot] and has no Codex account. Measured on #1589: 7 mentions,
    7 "create a Codex account and connect to github" replies ~7s later, while all
    4 real reviews followed pushes. Re-add the summon and this goes red.
    """
    r = _run(tmp_path, _script(), reviewers=["copilot-pull-request-reviewer[bot]"])
    assert not any("@codex" in c for c in r["comments"]), r["comments"]
    assert not any("greptile-gate:codex:" in c for c in r["comments"]), r["comments"]


def test_codex_grace_is_anchored_on_the_head_not_a_summon(tmp_path):
    """The bound survives removing the summon — it now runs from when the head appeared.

    Codex has NOT reviewed in either case, so the grace is the only thing that can
    pass the PR. A head that just landed must HOLD; one older than CODEX_GRACE_MIN
    must proceed. Pin graceElapsed to false (the naive "just delete the summon" fix)
    and the second case deadlocks — which is what this test exists to catch.
    """
    fresh = _run(tmp_path, _script(), reviewers=["copilot-pull-request-reviewer[bot]"],
                 headSeenAt="2099-01-01T00:00:00Z")
    assert fresh["addLabels"] == 0, fresh["log"]

    aged = _run(tmp_path, _script(), reviewers=["copilot-pull-request-reviewer[bot]"],
                headSeenAt="2020-01-01T00:00:00Z")
    assert aged["addLabels"] == 1, aged["log"]


def test_codex_grace_falls_back_to_the_commit_date_without_suites(tmp_path):
    """No check suites for the head must not strand the gate forever.

    The suite read is the primary anchor; the commit date is the fallback. With
    neither available the gate HOLDS rather than passes — an unreadable condition
    is never a pass — and the next sweep retries.
    """
    aged = _run(tmp_path, _script(), reviewers=["copilot-pull-request-reviewer[bot]"],
                noSuites=True, commitDate="2020-01-01T00:00:00Z")
    assert aged["addLabels"] == 1, aged["log"]

    unreadable = _run(tmp_path, _script(), reviewers=["copilot-pull-request-reviewer[bot]"],
                      noSuites=True, commitFetchFails=True)
    assert unreadable["addLabels"] == 0, unreadable["log"]


def test_regressed_handed_off_head_is_revoked_not_summoned(tmp_path):
    """An unresolved thread revokes handoff before any Greptile re-summon."""
    r = _run(tmp_path, _script(), reviewers=BOTH, unresolved=True,
             labels=[{"name": "greptile"}], markerSha="a" * 40)
    assert r["removeLabel"] == 1 and not any(
        c.strip().startswith("@greptileai review") for c in r["comments"])


def test_regression_after_handoff_is_decided_on_fresh_conditions(tmp_path):
    """A review dismissed mid-sweep is caught by fresh handoff revalidation."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             dismissMidSweep="chatgpt-codex-connector[bot]",
             labels=[{"name": "greptile"}], markerSha="a" * 40)
    assert r["removeLabel"] == 1


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


def test_handed_off_without_greptile_run_resummons(tmp_path):
    """Label+marker present, no Greptile Review check-run for the head: the
    post-label summon must be re-posted, or a swallowed comment failure strands
    the PR labeled-but-never-reviewed forever (Codex P1, #1577)."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             labels=[{"name": "greptile"}], markerSha="a" * 40)
    summons = [c for c in r["comments"] if c.strip().startswith("@greptileai review")]
    assert summons, f"handed-off head with no Greptile run must re-summon: {r['comments']}"


def test_fresh_summon_debounces_resummon(tmp_path):
    """A trusted summon younger than 10min means one is in flight — events in
    Greptile's check-creation gap must not spam more (Codex, #1577)."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileSummons=[{"at": "2099-01-01T00:00:00Z"}])
    assert not any(c.strip().startswith("@greptileai review") for c in r["comments"]), r["comments"]


def test_dead_greptile_run_does_not_block_resummon(tmp_path):
    """cancelled/timed_out runs are infra deaths on an immutable head: they must
    not satisfy the is-Greptile-coming check forever (Codex, #1577). A completed
    FAILURE is a real verdict and must still block."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "cancelled"})
    assert any(c.strip().startswith("@greptileai review") for c in r["comments"]), r["comments"]
    r2 = _run(tmp_path, _script(), reviewers=BOTH,
              labels=[{"name": "greptile"}], markerSha="a" * 40,
              greptileRun={"status": "completed", "conclusion": "failure"})
    assert not any(c.strip().startswith("@greptileai review") for c in r2["comments"]), r2["comments"]


def test_in_progress_greptile_run_suppresses_resummon(tmp_path):
    """An in-flight run (status != completed) IS Greptile coming — no re-summon.

    Pins the `status !== 'completed'` arm of the LIVE filter, which nothing else
    exercised: a rewrite to "completed non-DEAD only" passed the entire suite while
    re-summoning (and re-billing) on every sweep of the review window. Verified: with
    the filter rewritten that way, this test goes red."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileRun={"status": "in_progress"})
    assert not any(c.strip().startswith("@greptileai review") for c in r["comments"]), r["comments"]


def test_skipped_greptile_run_does_not_block_resummon(tmp_path):
    """completed/skipped is DEAD, in BOTH live-run checks.

    Greptile deliberately skips PRs over ~50 files — the credit is spent and no
    review exists. Counted as LIVE, that skip suppressed re-summons forever:
    labeled-but-never-reviewed with no repair path. Re-summoning is harmless
    (attempts bounded by the per-head three-summon cap; the debounce only
    spaces them out) even when the PR is still oversized."""
    # handed-off branch (DEAD)
    r = _run(tmp_path, _script(), reviewers=BOTH,
             labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "skipped"})
    assert any(c.strip().startswith("@greptileai review") for c in r["comments"]), r["comments"]
    # fresh-handoff branch (DEAD2): the skipped run must not suppress the post-label summon
    r2 = _run(tmp_path, _script(), reviewers=BOTH,
              greptileRun={"status": "completed", "conclusion": "skipped"})
    assert r2["addLabels"] == 1, r2["log"]
    assert any(c.strip().startswith("@greptileai review") for c in r2["comments"]), r2["comments"]


def test_rehandoff_of_reviewed_sha_does_not_resummon(tmp_path):
    """Regress-then-clear on the same SHA: the label is re-applied, but a live
    Greptile run already exists for the head — a fresh summon would bill a
    re-review of a reviewed diff (Codex, #1577)."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             greptileRun={"status": "completed", "conclusion": "success"})
    assert r["addLabels"] == 1, r["log"]
    assert not any(c.strip().startswith("@greptileai review") for c in r["comments"]), r["comments"]


def test_handoff_debounces_when_summon_already_in_flight(tmp_path):
    """Revoke-then-recover inside Greptile's check-creation gap: a fresh trusted
    summon exists, no run yet — the re-handoff must not double-summon."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             greptileSummons=[{"at": "2099-01-01T00:00:00Z"}])
    assert r["addLabels"] == 1, r["log"]
    assert not any(c.strip().startswith("@greptileai review") for c in r["comments"]), r["comments"]


def test_stale_head_summon_does_not_debounce_the_new_head(tmp_path):
    """Per-sha debounce (Greptile P1): a fresh summon for the PREVIOUS head must
    not suppress the summon for the current one — otherwise every push inside
    the window leaves the new head unreviewed."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileSummons=[{"at": "2099-01-01T00:00:00Z", "sha": "b" * 40}])
    assert any(c.strip().startswith("@greptileai review") for c in r["comments"]), r["comments"]


def test_summon_attempts_are_capped_per_head(tmp_path):
    """Greptile P1: treating `skipped` as DEAD makes every sweep summon-eligible,
    and a recency-only debounce has no attempt limit — an oversized PR (Greptile
    skips >50 files) would accrue a summon every 10 minutes forever. After 3
    aged summons for the same head the gate must stop asking."""
    aged = [{"at": "2020-01-01T00:00:00Z"}] * 3
    r = _run(tmp_path, _script(), reviewers=BOTH,
             labels=[{"name": "greptile"}], markerSha="a" * 40,
             greptileSummons=aged,
             greptileRun={"status": "completed", "conclusion": "skipped"})
    assert not any(c.strip().startswith("@greptileai review") for c in r["comments"]), r["comments"]
    assert any("not asking again" in m for m in r["log"]), r["log"]
    # two aged summons is still under the cap -> it DOES summon
    r2 = _run(tmp_path, _script(), reviewers=BOTH,
              labels=[{"name": "greptile"}], markerSha="a" * 40,
              greptileSummons=[{"at": "2020-01-01T00:00:00Z"}] * 2,
              greptileRun={"status": "completed", "conclusion": "skipped"})
    assert any(c.strip().startswith("@greptileai review") for c in r2["comments"]), r2["comments"]


def test_summon_cap_also_applies_to_fresh_handoff(tmp_path):
    """The cap must hold on BOTH branches (Codex, #1581): a revoke-then-re-apply
    on the same sha re-enters the fresh-handoff path, and without the cap there
    an oversized PR resumes summoning forever after the handed-off side gave up."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             greptileSummons=[{"at": "2020-01-01T00:00:00Z"}] * 3,
             greptileRun={"status": "completed", "conclusion": "skipped"})
    assert r["addLabels"] == 1, r["log"]              # still hands off
    assert not any(c.strip().startswith("@greptileai review") for c in r["comments"]), r["comments"]
    assert any("not asking again" in m for m in r["log"]), r["log"]


# ── The auto-merge race (measured on #1587 and #1589) ────────────────────────
# A required status check only BLOCKS once it has reported for the head. Greptile
# creates `Greptile Review` only after the gate labels the PR, so between labelling
# and that check appearing the requirement is ABSENT and an armed auto-merge merges
# straight through. #1589 merged 12s after the label with Greptile never having
# reviewed; #1587 merged 3s BEFORE Greptile's check even started, and Greptile then
# concluded `failure` on code already on main. The gate therefore raises its own
# `greptile-complete` check, in_progress, BEFORE labelling.


def test_blocking_check_is_raised_before_the_label(tmp_path):
    """ORDER is the property. Label-then-create leaves the same race window open."""
    r = _run(tmp_path, _script(), reviewers=BOTH)
    assert r["addLabels"] == 1, r["log"]
    names = [c["name"] for c in r["gateChecks"]]
    assert "greptile-complete" in names, r["gateChecks"]
    ev = r["events"]
    assert "label" in ev and "check:in_progress" in ev, ev
    assert ev.index("check:in_progress") < ev.index("label"), (
        f"blocking check must be raised BEFORE the label, got {ev}")


def test_a_failed_head_refetch_still_leaves_a_blocking_check(tmp_path):
    """A transient `pulls.get` failure must never leave the head UNBLOCKED.

    The authoritative refetch runs before the head-observation blocker, and a failure
    `continue`s this PR. That left a ready head with NO greptile-complete run at all
    while `validate`/`secret-scan` were already green — so the required context was
    simply absent and an armed auto-merge passed through it until the next sweep, up to
    30 minutes later. A provisional blocker on the list/event SHA closes that window:
    whatever else fails, something blocking is present.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, prGetFails=True)
    assert r["addLabels"] == 0, r["log"]
    blocking = [c for c in r["gateChecks"]
                if c["external_id"] == "1" and c["status"] == "in_progress"]
    assert blocking, (
        f"refetch failed and the head was left with no blocking check: {r['gateChecks']}")


def test_the_provisional_blocker_is_not_duplicated_when_the_head_is_unchanged(tmp_path):
    """The provisional and authoritative raises are the SAME run when the head has not
    moved — otherwise every sweep would post two blockers instead of one and bury the
    checks tab, which is the cost the read-first design exists to avoid."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             gateRuns=[{"status": "in_progress", "started_at": "2026-07-27T00:00:00Z"}])
    made = [c for c in r["gateChecks"] if c["status"] == "in_progress"]
    assert made == [], f"raised a blocker that already existed: {made}"


def test_no_label_when_the_blocking_check_cannot_be_raised(tmp_path):
    """Labelling without the blocker IS the race — so failure to raise it must hold."""
    r = _run(tmp_path, _script(), reviewers=BOTH, gateCheckFails=True)
    assert r["addLabels"] == 0, r["log"]
    assert any("could not raise" in m for m in r["log"]), r["log"]


def test_blocking_check_resolves_success_only_when_greptile_completed(tmp_path):
    """in_progress Greptile must NOT resolve the blocker — that is the #1589 merge."""
    running = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
                   markerSha="a" * 40,
                   greptileRun={"status": "in_progress", "conclusion": None})
    done = [c for c in running["gateChecks"] if c["status"] == "completed"]
    assert done == [], f"resolved while Greptile was still running: {done}"

    passed = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
                  markerSha="a" * 40,
                  greptileRun={"status": "completed", "conclusion": "success"})
    assert any(c["conclusion"] == "success" for c in passed["gateChecks"]), passed["gateChecks"]


def test_blocking_check_fails_when_greptile_failed(tmp_path):
    """#1587's exact shape: Greptile concluded failure. The blocker must not pass."""
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "failure"})
    assert any(c["conclusion"] == "failure" for c in r["gateChecks"]), r["gateChecks"]


def test_greptile_decline_fails_the_blocker_rather_than_waving_it_through(tmp_path):
    """A DECLINED review must BLOCK. Reversal of an earlier decision in this PR.

    Greptile skips PRs over ~50 files. This first resolved `neutral` to avoid stranding
    such a PR — but branch protection treats neutral as non-blocking, and Greptile's own
    skipped check is non-blocking too, so the combination merged a head with NO review
    at all: exactly what the gate exists to prevent (Codex P1 on #1592). Failing is the
    safe direction; the escape is the documented one (split the PR, or `oversized-ok`
    plus an explicit operator decision).
    """
    for declined in ("skipped", "cancelled", "stale", "neutral"):
        r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
                 markerSha="a" * 40,
                 greptileRun={"status": "completed", "conclusion": declined})
        concl = [c["conclusion"] for c in r["gateChecks"] if c["status"] == "completed"]
        assert concl == ["failure"], f"{declined} -> {concl}"


def test_stale_success_does_not_resolve_while_a_newer_greptile_run_is_in_flight(tmp_path):
    """A completed success + a NEWER in-flight run on the same head must NOT resolve.

    Filtering to `completed` FIRST and then taking the newest completed makes an
    in-flight re-review invisible, so the gate publishes success while Greptile is
    still working — re-creating the #1587/#1589 class this PR exists to close. The
    file's own checksOk path already guards this ("still in flight → return false");
    the resolution block must apply the same rule.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRuns=[
                 {"status": "completed", "conclusion": "success",
                  "completed_at": "2026-07-27T00:00:00Z"},
                 {"status": "in_progress", "conclusion": None,
                  "started_at": "2026-07-28T00:00:00Z"},
             ])
    done = [c for c in r["gateChecks"] if c["status"] == "completed"]
    assert done == [], f"resolved from a stale success while a newer run was in flight: {done}"


def test_stuck_older_run_does_not_deadlock_when_a_later_run_completed(tmp_path):
    """A duplicate summon can leave an OLDER run stuck in_progress forever.

    Blocking on ANY non-completed run would then make `greptile-complete` permanently
    unresolvable — a required check nobody can clear is worse than the race it guards.
    Here the stuck run STARTED before the good run finished, so it is superseded and
    must be ignored. Codex P2 on #1592.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRuns=[
                 {"status": "in_progress", "conclusion": None,
                  "started_at": "2026-07-26T00:00:00Z"},                      # stuck, older
                 {"status": "completed", "conclusion": "success",
                  "started_at": "2026-07-26T12:00:00Z",
                  "completed_at": "2026-07-27T00:00:00Z"},                    # later verdict
             ])
    assert any(c["conclusion"] == "success" for c in r["gateChecks"]), r["gateChecks"]


def test_overlapping_rerun_is_not_mistaken_for_a_superseded_run(tmp_path):
    """Greptile's attempts OVERLAP — start-vs-completion is the wrong comparison.

    A rerun routinely BEGINS while the previous attempt is still running. Comparing the
    in-flight run's start against the completed run's COMPLETION marks such a rerun as
    "older", dismisses it as superseded, and publishes success from the stale verdict
    while Greptile is still reviewing — the exact #1587/#1589 class this check closes.

    Here the rerun starts at 11:00, after the earlier attempt started (10:00) but before
    it finished (12:00). It is the NEWER attempt and must hold the blocker. Restore
    `started(c) > finished(newestDone)` and this goes red.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRuns=[
                 {"status": "completed", "conclusion": "success",
                  "started_at": "2026-07-27T10:00:00Z",
                  "completed_at": "2026-07-27T12:00:00Z"},
                 {"status": "in_progress", "conclusion": None,
                  "started_at": "2026-07-27T11:00:00Z"},   # overlaps the run above
             ])
    done = [c for c in r["gateChecks"] if c["status"] == "completed"]
    assert done == [], f"published from a stale verdict while a rerun was in flight: {done}"


def test_current_verdict_is_the_newest_attempt_not_the_last_to_finish(tmp_path):
    """Completion order is not attempt order when a rerun outruns a slow predecessor.

    Attempt A starts 10:00 and grinds until 14:00 (failure); rerun B starts 11:00 and
    finishes at 12:00 (success). B is the current attempt, so the head's verdict is
    success. Ranking completed runs by `completed_at` picks A and publishes failure for
    a head Greptile actually passed.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRuns=[
                 {"status": "completed", "conclusion": "failure",
                  "started_at": "2026-07-27T10:00:00Z",
                  "completed_at": "2026-07-27T14:00:00Z"},   # earlier attempt, slower
                 {"status": "completed", "conclusion": "success",
                  "started_at": "2026-07-27T11:00:00Z",
                  "completed_at": "2026-07-27T12:00:00Z"},   # later attempt, faster
             ])
    concl = [c["conclusion"] for c in r["gateChecks"] if c["status"] == "completed"]
    assert concl == ["success"], f"took the last run to FINISH, not the newest attempt: {concl}"


def test_a_rerun_tying_on_started_at_holds_rather_than_publishing(tmp_path):
    """Equal `started_at` must HOLD — "cannot tell which is later" is never a pass.

    `started_at` has one-second resolution, so a completed attempt and a live rerun
    sharing a timestamp is ordinary rather than exotic. With a strict `>` the running
    rerun read as superseded and the stale completed verdict was published while
    Greptile was still working.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRuns=[
                 {"status": "completed", "conclusion": "success",
                  "started_at": "2026-07-27T10:00:00Z",
                  "completed_at": "2026-07-27T12:00:00Z"},
                 {"status": "in_progress", "conclusion": None,
                  "started_at": "2026-07-27T10:00:00Z"},   # exact tie
             ])
    done = [c for c in r["gateChecks"] if c["status"] == "completed"]
    assert done == [], f"a tie on started_at published the stale verdict: {done}"


def test_tied_completed_attempts_are_ordered_by_check_run_id(tmp_path):
    """With started_at tied, the verdict must still be deterministic — not whichever
    order the API happened to return. Check-run ids increase monotonically, so the
    higher id is the later attempt."""
    runs = [
        {"status": "completed", "conclusion": "failure", "id": 500,
         "started_at": "2026-07-27T10:00:00Z", "completed_at": "2026-07-27T12:00:00Z"},
        {"status": "completed", "conclusion": "success", "id": 900,
         "started_at": "2026-07-27T10:00:00Z", "completed_at": "2026-07-27T11:00:00Z"},
    ]
    for order in (runs, list(reversed(runs))):
        r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
                 markerSha="a" * 40, greptileRuns=order)
        concl = [c["conclusion"] for c in r["gateChecks"] if c["status"] == "completed"]
        assert concl == ["success"], f"id tiebreak not applied (order={order[0]['id']}): {concl}"


def test_in_flight_run_without_a_start_time_holds(tmp_path):
    """Unknown timestamp must fail CLOSED — treating it as old would let a head merge."""
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRuns=[
                 {"status": "completed", "conclusion": "success",
                  "completed_at": "2026-07-27T00:00:00Z"},
                 {"status": "in_progress", "conclusion": None},   # no started_at
             ])
    done = [c for c in r["gateChecks"] if c["status"] == "completed"]
    assert done == [], f"resolved despite an in-flight run of unknown age: {done}"


def test_blocker_is_not_completed_when_conditions_regress_mid_sweep(tmp_path):
    """A review dismissed DURING the sweep must stop the blocker resolving.

    Publishing `greptile-complete: success` from the first conditions read lets an
    armed auto-merge run in the window before the label revocation lands — the other
    required contexts are already green by then. Resolution is therefore gated on the
    same fresh `stillOk` verdict the revocation uses. Codex P2 on #1592.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             dismissMidSweep="chatgpt-codex-connector[bot]",
             greptileRuns=[{"status": "completed", "conclusion": "success",
                            "completed_at": "2026-07-27T00:00:00Z"}])
    done = [c for c in r["gateChecks"] if c["status"] == "completed"]
    assert done == [], f"resolved despite a mid-sweep regression: {done}"
    assert r["removeLabel"] == 1, r["log"]


def test_grace_anchor_is_clamped_to_the_pr_creation_time(tmp_path):
    """A pre-existing branch must not arrive with its Codex grace already spent.

    `listSuitesForRef` returns repo-wide timestamps for the SHA, so a branch pushed
    and checked weeks before the PR was opened yields an anchor that PREDATES the PR.
    Codex cannot have reviewed a PR that did not exist yet, so an unclamped anchor
    collapses the cheap-review window to nothing. Codex P2 on #1592.
    """
    # Head seen long ago, PR opened just now -> grace must NOT be elapsed.
    fresh_pr = _run(tmp_path, _script(), reviewers=["copilot-pull-request-reviewer[bot]"],
                    headSeenAt="2020-01-01T00:00:00Z", prCreatedAt="2099-01-01T00:00:00Z")
    assert fresh_pr["addLabels"] == 0, fresh_pr["log"]

    # Both old -> the grace genuinely has elapsed and the gate proceeds.
    old_pr = _run(tmp_path, _script(), reviewers=["copilot-pull-request-reviewer[bot]"],
                  headSeenAt="2020-01-01T00:00:00Z", prCreatedAt="2020-01-01T00:00:00Z")
    assert old_pr["addLabels"] == 1, old_pr["log"]


def test_blocker_from_another_pr_on_the_same_sha_is_not_reused(tmp_path):
    """Check runs attach to a SHA, not a PR — two branches can share a commit.

    A COMPLETED blocker created for another PR must not satisfy this one: reusing it
    lets this PR inherit green required contexts and become mergeable before its own
    Greptile handoff. Scoped by external_id. Codex P2 on #1592.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH,
             gateRuns=[{"extId": 999, "status": "completed", "conclusion": "success",
                        "completed_at": "2026-07-27T00:00:00Z"}])
    made = [c for c in r["gateChecks"] if c["status"] == "in_progress"]
    assert made, f"did not raise its own blocker; reused another PR's: {r['gateChecks']}"


def test_own_existing_blocker_is_not_duplicated(tmp_path):
    """Idempotence: checks.create always makes a NEW run, so an unconditional call
    would post one per sweep (every 30 min plus every event) and bury the checks tab."""
    r = _run(tmp_path, _script(), reviewers=BOTH,
             gateRuns=[{"status": "in_progress", "started_at": "2026-07-27T00:00:00Z"}])
    made = [c for c in r["gateChecks"] if c["status"] == "in_progress"]
    assert made == [], f"re-created a blocker that already existed: {made}"


def test_already_successful_blocker_is_superseded_when_conditions_regress(tmp_path):
    """A RELEASED blocker must be re-raised when the contract regresses on the same head.

    The sibling test above (`..._not_completed_when_conditions_regress_mid_sweep`) only
    proves the gate emits no NEW completion mid-sweep. That is not enough: if
    `greptile-complete` ALREADY concluded `success` for this head on an earlier sweep,
    emitting nothing leaves that success standing. Revoking the `greptile` label does
    not touch a check run, so the required context stays green and an armed auto-merge
    merges a head whose cheap tier has regressed — the gate is fail-OPEN.

    Worse, `ensureBlocker` treated ANY existing run as sufficient, so it would not
    re-raise either. Here the pre-existing run is a completed success for THIS PR
    (external_id 1) and Codex's review is dismissed mid-sweep: the gate must supersede
    that success with an unresolved run (in_progress, or a failure) for the same SHA.
    Cross-review finding on #1592.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             dismissMidSweep="chatgpt-codex-connector[bot]",
             gateRuns=[{"status": "completed", "conclusion": "success",
                        "started_at": "2026-07-27T00:00:00Z",
                        "completed_at": "2026-07-27T00:00:00Z"}])
    # The regression itself is still caught (guards against the test passing because
    # the sweep bailed out early for some unrelated reason).
    assert r["removeLabel"] == 1, r["log"]
    # No fresh SUCCESS may be published…
    assert not any(c["conclusion"] == "success" for c in r["gateChecks"]), r["gateChecks"]
    # …and the stale one must be superseded by a run that actually blocks.
    blocking = [c for c in r["gateChecks"]
                if c["status"] != "completed" or c["conclusion"] != "success"]
    assert blocking, (
        "a released greptile-complete success was left standing after the cheap tier "
        f"regressed — auto-merge can consume it: {r['gateChecks']} / {r['log']}")


def test_supersede_is_not_vouched_for_by_the_stale_head_observation_run(tmp_path):
    """The REAL run list, and the case an "any blocking run exists" test cannot see.

    A released head always carries BOTH runs: the `in_progress` raised when the head was
    first seen, and the later `success` that resolved it. Superseded runs never leave
    `listForRef`, so asking "is some run still blocking?" finds that stale pending run
    and concludes the head is covered — while the context GitHub actually evaluates (the
    newest run) is green. The re-raise must therefore judge the NEWEST run only.

    Without this case the fix passes its own tests while the fail-open survives in
    production, because the tests above supply the success as the ONLY existing run.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             dismissMidSweep="chatgpt-codex-connector[bot]",
             gateRuns=[
                 # raised at head-observation, then superseded by the success below
                 {"status": "in_progress", "started_at": "2026-07-26T00:00:00Z"},
                 {"status": "completed", "conclusion": "success",
                  "started_at": "2026-07-27T00:00:00Z",
                  "completed_at": "2026-07-27T00:00:00Z"},
             ])
    assert r["removeLabel"] == 1, r["log"]
    blocking = [c for c in r["gateChecks"]
                if c["status"] != "completed" or c["conclusion"] != "success"]
    assert blocking, (
        "the stale head-observation run vouched for a head whose current context is "
        f"green — auto-merge can still consume it: {r['gateChecks']} / {r['log']}")


def test_published_verdict_is_not_republished_on_every_sweep(tmp_path):
    """Idempotent publish. `checks.create` always makes a NEW run, and this block is
    re-entered on every 30-min sweep plus every event for as long as the PR sits
    handed-off and green — so an unconditional call posted a duplicate completed run
    forever and buried the checks tab. The verdict here is already published and
    unchanged, so nothing may be created."""
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "success"},
             gateRuns=[{"status": "completed", "conclusion": "success",
                        "started_at": "2026-07-27T00:00:00Z",
                        "completed_at": "2026-07-27T00:00:00Z"}])
    assert r["gateChecks"] == [], f"re-published an unchanged verdict: {r['gateChecks']}"


def test_a_changed_verdict_is_still_published(tmp_path):
    """The positive control for the idempotence guard: it must skip only when the
    current context already says the intended thing. Greptile now concludes failure
    while the published context is success — that MUST be written."""
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "failure"},
             gateRuns=[{"status": "completed", "conclusion": "success",
                        "started_at": "2026-07-27T00:00:00Z",
                        "completed_at": "2026-07-27T00:00:00Z"}])
    assert any(c["conclusion"] == "failure" for c in r["gateChecks"]), r["gateChecks"]


def test_neutral_greptile_verdict_stays_re_summonable(tmp_path):
    """`neutral` was a DEAD END at the publish site but absent from both live-run lists.

    So a neutral verdict published `failure` AND counted as a live run on every sweep:
    no re-summon could ever fire, and the PR was permanently blocked with no repair
    path. It is a decline, so it must fail the blocker AND stay retryable — the single
    DEAD_ENDS list is what keeps those two answers in agreement.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "neutral"})
    assert any(c.strip().startswith("@greptileai review") for c in r["comments"]), (
        f"a neutral verdict is a dead end and must be re-summonable: {r['comments']}")
    concl = [c["conclusion"] for c in r["gateChecks"] if c["status"] == "completed"]
    assert concl == ["failure"], f"neutral must still FAIL the blocker: {concl}"
    # fresh-handoff branch (the second live-run list) must agree
    r2 = _run(tmp_path, _script(), reviewers=BOTH,
              greptileRun={"status": "completed", "conclusion": "neutral"})
    assert r2["addLabels"] == 1, r2["log"]
    assert any(c.strip().startswith("@greptileai review") for c in r2["comments"]), r2["comments"]


# ── Shared-SHA coordination ──────────────────────────────────────────────────
# `external_id` scopes the gate's own LOOKUP of its blocker, but branch protection
# resolves a required context by check NAME on the PR's head SHA and never looks at
# external_id. Two open PRs can point at the same commit, so a success published for
# one satisfies the other's required context too — while that other PR's own blocker
# is still in_progress and its cheap tier has never been checked.


def test_success_is_withheld_while_a_co_located_pr_has_not_handed_off(tmp_path):
    """PR 100 shares this head and carries no `greptile` label — so publishing success
    for PR 1 would hand PR 100 a green required context it never earned. Hold instead;
    the blocker stays in_progress and clears once PR 100 qualifies or moves."""
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "success"},
             sharedShaPrs=[{"number": 100, "labels": []}])
    mine = [c for c in r["gateChecks"] if c["external_id"] == "1"]
    assert not any(c["conclusion"] == "success" for c in mine), (
        f"published a success that also satisfies unqualified PR 100: {mine}")
    assert any("share this head" in m for m in r["log"]), r["log"]


def test_success_is_withheld_even_when_the_co_located_pr_has_handed_off(tmp_path):
    """A co-located PR withholds success REGARDLESS of its own state.

    This reverses an earlier, weaker rule on this PR. Coordination used to ask whether
    the OTHER PR had cleared its cheap tier (label + handoff marker for this SHA) and
    published success once it had. But clearing PR 100's cheap tier does not make
    Greptile's review of the COMMIT into a review of PR 1 — a `Greptile Review` run
    carries nothing naming the PR it reviewed, and the two PRs can differ in base branch
    and review context entirely. So while anyone else is on this head, the verdict is
    unattributable and success is withheld.

    Strictly stronger than the label-plus-marker contract it replaces: that contract
    would have published here.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "success"},
             sharedShaPrs=[{"number": 100, "labels": [{"name": "greptile"}],
                            "markerSha": "a" * 40}])
    mine = [c for c in r["gateChecks"] if c["external_id"] == "1"]
    assert not any(c["conclusion"] == "success" for c in mine), (
        f"published an unattributable verdict because the sharer looked qualified: {mine}")
    assert any("cannot be attributed" in m or "share this head" in m for m in r["log"]), r["log"]


def test_the_sharer_is_alone_positive_control(tmp_path):
    """The guard must withhold only when someone ELSE is on the head.

    Without this, every test above could pass by blocking unconditionally — which would
    strand every PR in the repo. PR 100 sits on a different commit, so PR 1 is alone on
    its head and the verdict IS attributable.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "success"},
             sharedShaPrs=[{"number": 100, "labels": [], "sha": "d" * 40}])
    mine = [c for c in r["gateChecks"] if c["external_id"] == "1"]
    assert any(c["conclusion"] == "success" for c in mine), (
        f"withheld success though this PR is alone on its head: {mine}")


def test_a_co_located_greptile_run_does_not_count_as_this_prs_review(tmp_path):
    """The summon side of the same attribution problem.

    A completed `Greptile Review` run on the head made PR 1 look already-reviewed, so it
    was never summoned — and a later sweep published its blocker from that run. When the
    run may belong to PR 100 it is not evidence for PR 1: treat it as no live run and
    summon. Bounded by MAX_SUMMONS, so this cannot loop.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "success"},
             sharedShaPrs=[{"number": 100, "labels": []}])
    assert any(c.strip().startswith("@greptileai review") for c in r["comments"]), (
        f"took a co-located PR's Greptile run as proof this PR was reviewed: {r['comments']}")


def test_a_pr_arriving_on_the_head_after_the_snapshot_still_withholds(tmp_path):
    """The opening `pulls.list` snapshot cannot see a PR that moves onto the head later.

    PR 100 is absent from the sweep's first list read and present in every later one.
    Deriving candidates from the snapshot excluded it entirely — it was never refetched
    — so this PR published `success`, which became the NEWEST run of that name on the
    SHA, and PR 100 consumed it. Its own `in_progress` blocker does NOT protect it:
    required contexts resolve by name+SHA, so a newer success supersedes a pending run.
    Coordination must therefore enumerate the head's PRs FRESH, immediately before
    publishing.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "success"},
             sharedShaPrs=[{"number": 100, "labels": [], "arrivesLate": True}])
    mine = [c for c in r["gateChecks"] if c["external_id"] == "1"]
    assert not any(c["conclusion"] == "success" for c in mine), (
        f"published success for a head a late-arriving PR shares: {mine}")
    assert any("share this head" in m for m in r["log"]), r["log"]


def test_the_commit_to_prs_association_endpoint_is_load_bearing(tmp_path):
    """`GET /commits/{sha}/pulls` is keyed on the SHA, not on a list we have to scan.

    Here PR 100 is invisible to EVERY `pulls.list` read and shows up only in the
    association index. Drop that call and the sharer is missed. (The reverse case is
    covered by `..._after_the_snapshot_still_withholds`, where the association index
    lags — which is why both sources are unioned rather than either being trusted.)
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "success"},
             sharedShaPrs=[{"number": 100, "labels": [], "assocOnly": True}])
    mine = [c for c in r["gateChecks"] if c["external_id"] == "1"]
    assert not any(c["conclusion"] == "success" for c in mine), (
        f"missed a sharer only the association endpoint reports: {mine}")


def test_a_lagging_association_index_is_covered_by_the_fresh_pr_list(tmp_path):
    """The other half of the union, and the reason it IS a union.

    `GET /commits/{sha}/pulls` is an eventually-consistent index and can lag a very
    recent push. Here PR 100 has already moved onto the head — a fresh `pulls.list`
    sees it — but the association index has not caught up yet. Relying on the
    association endpoint alone would miss it and publish a shared success.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "success"},
             sharedShaPrs=[{"number": 100, "labels": [], "hiddenFromAssoc": True}])
    mine = [c for c in r["gateChecks"] if c["external_id"] == "1"]
    assert not any(c["conclusion"] == "success" for c in mine), (
        f"missed a sharer the association index had not indexed yet: {mine}")


def test_an_unreadable_pr_enumeration_withholds_success(tmp_path):
    """Fail CLOSED. If we cannot enumerate who shares the head we cannot assert
    anything about them, and publishing a green shared context on an unverified
    commit is the direction that lets an unreviewed head merge."""
    r = _run(tmp_path, _script(), reviewers=BOTH, labels=[{"name": "greptile"}],
             markerSha="a" * 40,
             greptileRun={"status": "completed", "conclusion": "success"},
             assocFetchFails=True)
    mine = [c for c in r["gateChecks"] if c["external_id"] == "1"]
    assert not any(c["conclusion"] == "success" for c in mine), (
        f"published success without knowing who shares the head: {mine}")
    assert any("could not enumerate" in m for m in r["log"]), r["log"]


def test_superseding_a_released_blocker_happens_once_not_every_sweep(tmp_path):
    """The re-raise must key on the NEWEST run, not "a success exists somewhere".

    Superseded runs stay in `listForRef` forever, so a head that was released once and
    then re-blocked would re-raise on every sweep (every 30 min plus every event) and
    bury the checks tab — the duplicate-create the read-first design exists to avoid.
    Here the older success is already superseded by an in_progress run, which is what
    GitHub actually evaluates: nothing more may be created.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, unresolved=True,
             gateRuns=[
                 {"status": "completed", "conclusion": "success",
                  "started_at": "2026-07-27T00:00:00Z",
                  "completed_at": "2026-07-27T00:00:00Z"},
                 {"status": "in_progress", "started_at": "2026-07-28T00:00:00Z"},
             ])
    assert r["gateChecks"] == [], f"re-raised over an already-blocking run: {r['gateChecks']}"


def test_released_blocker_is_superseded_when_the_head_no_longer_qualifies(tmp_path):
    """Same fail-open, reached from the un-labelled side (the general safety net).

    Once the label is gone (revoked, or removed by hand) a regressed head falls through
    to the `!qualifies` hold instead of the handed-off branch. A `greptile-complete`
    success from an earlier cycle on this same SHA must be superseded there too,
    otherwise the next sweep after any regression still leaves a stale green.
    """
    r = _run(tmp_path, _script(), reviewers=BOTH, unresolved=True,
             gateRuns=[{"status": "completed", "conclusion": "success",
                        "started_at": "2026-07-27T00:00:00Z",
                        "completed_at": "2026-07-27T00:00:00Z"}])
    assert r["addLabels"] == 0, r["log"]
    blocking = [c for c in r["gateChecks"]
                if c["status"] != "completed" or c["conclusion"] != "success"]
    assert blocking, f"stale success left standing on a non-qualifying head: {r['gateChecks']}"
