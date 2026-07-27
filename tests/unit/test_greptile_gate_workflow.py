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
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "greptile-gate.yml"

pytest.importorskip("yaml")
import yaml  # noqa: E402

node = shutil.which("node")
pytestmark = [pytestmark, pytest.mark.skipif(not node, reason="node not available")]


def _script() -> str:
    """The inline `actions/github-script` body, exactly as CI runs it."""
    spec = yaml.safe_load(WORKFLOW.read_text())
    step = spec["jobs"]["label-when-others-pass"]["steps"][0]
    return step["with"]["script"]


HARNESS = textwrap.dedent(
    """
    const SRC = require('fs').readFileSync(process.argv[2], 'utf8');
    const OPTS = JSON.parse(process.argv[3]);
    const SHA = 'a'.repeat(40);

    let checkReads = 0;
    const calls = { addLabels: 0, removeLabel: 0, comments: [] };
    const pr = { number: 1, head: { sha: SHA }, base: { ref: 'main' }, labels: [], draft: false };

    const reviewers = (OPTS.reviewers || []).map(
      (l) => ({ commit_id: SHA, state: 'COMMENTED', user: { login: l } }));

    const github = {
      paginate: async (fn, o) => fn(o),
      graphql: async (q) => {
        if (q.includes('reviewThreads')) return { repository: { pullRequest: { reviewThreads: {
          pageInfo: { hasNextPage: false, endCursor: null },
          nodes: (OPTS.unresolved ? [{ isResolved: false }] : []) } } } };
        if (q.includes('{ id }')) return { repository: { pullRequest: { id: 'PR_1' } } };
        return {};
      },
      rest: {
        pulls: {
          list: async () => [pr],
          get: async () => ({ data: { ...pr, requested_reviewers: [] } }),
          listReviews: async () => reviewers,
        },
        repos: { compareCommits: async () => ({ data: { behind_by: OPTS.behindBy || 0 } }) },
        checks: {
          listForRef: async () => {
            checkReads += 1;
            // checksFlip: green on the first read, red on the second — a required check
            // that re-runs and fails while the summon calls are in flight.
            const red = OPTS.checksRed || (OPTS.checksFlip && checkReads >= 2);
            return ['Cursor Bugbot'].map((name) => ({
              name, status: OPTS.checksPending ? 'in_progress' : 'completed',
              conclusion: red ? 'failure' : 'neutral',
              completed_at: '2026-07-27T00:00:00Z' }));
          },
        },
        issues: {
          listComments: async () => [],
          createComment: async (o) => { calls.comments.push(o.body); return {}; },
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
    """Copilot has not reviewed this head: hold, and actually request it."""
    r = _run(tmp_path, _script(), reviewers=["chatgpt-codex-connector[bot]"])
    assert r["addLabels"] == 0, r["log"]
