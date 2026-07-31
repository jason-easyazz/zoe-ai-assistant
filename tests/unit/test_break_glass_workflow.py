"""Behavioural tests for .github/workflows/break-glass.yml.

Break-glass force-publishes required status contexts. It is the one mechanism in
this repo that can make an unmergeable PR mergeable, so every property that keeps
it honest is load-bearing and every one of them is asserted here against the REAL
embedded script — not against the YAML text — running on a stubbed GitHub API.

What it must do:
  * refuse anyone who is not a repo ADMIN (applying a label needs only `write`);
  * override only NON-GREEN contexts, and record each one's real state, so a
    forced `failure` stays distinguishable from a forced `missing` forever;
  * ABORT without consuming the single-use label if the head moved, so an
    override can never land on a commit nobody is merging while the label — the
    owner's escape hatch — is spent;
  * leave `required_conversation_resolution` alone.

The head-race is the subtle one: publishing against a stale SHA leaves the CURRENT
head blocked while the audit trail records a successful override, so the operator
believes they are unblocked and are not.
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
WORKFLOW = REPO / ".github" / "workflows" / "break-glass.yml"

import yaml

node = shutil.which("node")
if not node and os.environ.get("CI"):
    raise RuntimeError("node is required in CI for the break-glass workflow tests")
pytestmark = [pytestmark, pytest.mark.skipif(not node, reason="node not available (non-CI)")]


def _script() -> str:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["break-glass"]["steps"]
    script_steps = [st for st in steps if "github-script" in str(st.get("uses", ""))]
    assert len(script_steps) == 1, f"expected one github-script step, got {len(script_steps)}"
    return script_steps[0]["with"]["script"]


HARNESS = textwrap.dedent(
    """
    const SRC = require('fs').readFileSync(process.argv[2], 'utf8');
    const OPTS = JSON.parse(process.argv[3]);
    const SHA = 'a'.repeat(40);
    const MOVED = 'b'.repeat(40);

    const calls = { checksCreated: [], removeLabel: 0, comments: [], failed: [] };

    let getReads = 0;
    const github = {
      paginate: async (fn, o) => fn(o),
      rest: {
        pulls: {
          get: async () => {
            getReads += 1;
            // OPTS.headMovesBeforeWrite: the revalidation read (the 2nd get) sees a
            // NEW head — a push landed between the opening read and the write.
            const sha = (OPTS.headMovesBeforeWrite && getReads >= 2) ? MOVED : SHA;
            if (OPTS.refetchFails && getReads >= 2) throw new Error('pulls.get failed');
            return { data: { number: 7, head: { sha } } };
          },
        },
        repos: {
          getCollaboratorPermissionLevel: async () => (
            { data: { permission: OPTS.permission || 'admin' } }),
          listCommitStatusesForRef: async () => (OPTS.statuses || []),
        },
        checks: {
          listForRef: async () => (OPTS.runs || []),
          create: async (o) => {
            calls.checksCreated.push({ name: o.name, sha: o.head_sha,
                                       conclusion: o.conclusion,
                                       summary: (o.output || {}).summary || '' });
            return {};
          },
        },
        issues: {
          createComment: async (o) => { calls.comments.push(o.body); return {}; },
          removeLabel: async () => { calls.removeLabel += 1; return {}; },
        },
      },
    };
    const log = [];
    const core = { info: (m) => log.push(String(m)), notice: (m) => log.push(String(m)),
                   warning: (m) => log.push(String(m)),
                   setFailed: (m) => { calls.failed.push(String(m)); } };
    const context = {
      repo: { owner: 'o', repo: 'r' },
      eventName: OPTS.eventName || 'pull_request_target',
      actor: 'jason-easyazz',
      payload: { pull_request: { number: 7 }, inputs: OPTS.inputs },
    };

    (async () => {
      await new Function('github', 'context', 'core',
        `return (async () => { ${SRC} })()`)(github, context, core);
      console.log(JSON.stringify({ ...calls, log }));
    })();
    """
)


def _run(tmp_path: Path, **opts) -> dict:
    sp = tmp_path / "script.js"
    sp.write_text(_script())
    hp = tmp_path / "harness.js"
    hp.write_text(HARNESS)
    proc = subprocess.run([node, str(hp), str(sp), json.dumps(opts)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"harness failed: {proc.stderr[-2000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _run_completed(name, conclusion):
    return {"name": name, "status": "completed", "conclusion": conclusion,
            "completed_at": "2026-07-31T00:00:00Z"}


# --- positive control -------------------------------------------------------
def test_admin_override_forces_only_non_green_contexts(tmp_path):
    """`validate` is green and must be left alone; the other two are missing and
    must be forced. Without this control every negative below could pass by doing
    nothing at all."""
    r = _run(tmp_path, runs=[_run_completed("validate", "success")])
    forced = {c["name"] for c in r["checksCreated"]}
    assert forced == {"secret-scan", "voice-gate"}, r["checksCreated"]
    assert all(c["sha"] == "a" * 40 for c in r["checksCreated"])
    assert all(c["conclusion"] == "success" for c in r["checksCreated"])
    assert r["removeLabel"] == 1, "the label must be consumed on a successful override"
    audit = [c for c in r["comments"] if "BREAK-GLASS used" in c]
    assert audit, r["comments"]
    assert "validate (`success`)" in audit[0], "an untouched green context must be named"


# --- FINDING C: the stale-head race -----------------------------------------
def test_head_moving_before_the_write_aborts_without_consuming_the_label(tmp_path):
    """THE race. Everything is read for the opening head; if a push lands before
    the write, publishing would put substitute successes on a commit nobody is
    merging, leave the CURRENT head blocked, and still burn the single-use label —
    while the audit trail claims a successful override.

    Abort instead, and critically do NOT remove the label, so the owner keeps
    their escape hatch and the next event retries with fresh reads."""
    r = _run(tmp_path, headMovesBeforeWrite=True)
    assert r["checksCreated"] == [], "must not publish against a stale head"
    assert r["removeLabel"] == 0, "the single-use label must NOT be consumed on abort"
    aborted = [c for c in r["comments"] if "ABORTED" in c]
    assert aborted, r["comments"]
    assert "a" * 8 in aborted[0] and "b" * 8 in aborted[0], "name both SHAs"
    assert "LEFT IN PLACE" in aborted[0]
    assert r["failed"], "an ineffective override must fail the run, not look successful"


def test_unreadable_refetch_also_aborts_without_consuming_the_label(tmp_path):
    """A failed revalidation read is not evidence the head is unchanged. Unknown
    must abort on the same terms — fail-closed on enforcement, recoverable for
    the owner."""
    r = _run(tmp_path, refetchFails=True)
    assert r["checksCreated"] == []
    assert r["removeLabel"] == 0
    assert any("ABORTED" in c for c in r["comments"]), r["comments"]


# --- admin gate -------------------------------------------------------------
@pytest.mark.parametrize("perm", ["write", "read", "triage", "maintain", "none"])
def test_non_admin_is_refused_and_the_label_is_stripped(tmp_path, perm):
    """Applying a label needs only `write`, so permission is verified against the
    API. A refused attempt must also leave no armed state behind."""
    r = _run(tmp_path, permission=perm)
    assert r["checksCreated"] == [], f"{perm} must not be able to override anything"
    assert r["removeLabel"] == 1, "a non-admin's label must be stripped"
    assert any("REFUSED" in c for c in r["comments"]), r["comments"]
    assert r["failed"], r["log"]


# --- honesty: a FAILED check is overridden too, and the record says so -------
def test_a_failing_context_is_overridden_and_the_audit_says_so(tmp_path):
    """The mechanism cannot distinguish "never reported" from "ran and said no",
    and `secret-scan` is in the covered set — so this CAN override a real
    secret-scan failure. That is deliberate; what keeps it honest is that the real
    state is recorded, so a forced `failure` is permanently distinguishable from a
    forced `missing`."""
    r = _run(tmp_path, runs=[_run_completed("secret-scan", "failure"),
                             _run_completed("validate", "success"),
                             _run_completed("voice-gate", "success")])
    forced = {c["name"]: c for c in r["checksCreated"]}
    assert set(forced) == {"secret-scan"}, r["checksCreated"]
    assert "`failure`" in forced["secret-scan"]["summary"], forced["secret-scan"]["summary"]
    assert "RAN AND FAILED" in forced["secret-scan"]["summary"]
    audit = [c for c in r["comments"] if "BREAK-GLASS used" in c][0]
    assert "secret-scan (was `failure`)" in audit
    assert "secret-scan" in audit and "scanner flagged" in audit


def test_an_already_fully_green_head_forces_nothing(tmp_path):
    """Blast radius: if nothing is stuck, nothing is overridden."""
    r = _run(tmp_path, runs=[_run_completed(n, "success")
                             for n in ("validate", "secret-scan", "voice-gate")])
    assert r["checksCreated"] == []
    audit = [c for c in r["comments"] if "BREAK-GLASS used" in c][0]
    assert "_none — every required context was already green_" in audit


def test_a_newer_run_beats_an_older_success(tmp_path):
    """An old green run must not stand in for a current red one — the same
    newest-wins rule the greptile gate uses."""
    r = _run(tmp_path, runs=[
        {"name": "validate", "status": "completed", "conclusion": "success",
         "completed_at": "2026-07-01T00:00:00Z"},
        {"name": "validate", "status": "completed", "conclusion": "failure",
         "completed_at": "2026-07-31T00:00:00Z"},
    ])
    assert "validate" in {c["name"] for c in r["checksCreated"]}, r["checksCreated"]


def test_thread_resolution_is_never_claimed_to_be_bypassed(tmp_path):
    """`required_conversation_resolution` is outside this mechanism entirely, and
    the audit comment must keep saying so — it is the property most likely to be
    misremembered as "break-glass unblocks everything"."""
    r = _run(tmp_path)
    audit = [c for c in r["comments"] if "BREAK-GLASS used" in c][0]
    assert "Unresolved review threads are NOT bypassed" in audit
