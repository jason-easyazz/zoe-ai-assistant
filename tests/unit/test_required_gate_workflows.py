"""Structural guards for the DETERMINISTIC required merge gate.

The re-tiering (2026-07-30) made the required set `validate`, `secret-scan`,
`voice-gate` — all deterministic, all locally runnable — and demoted Greptile to
advisory. Two failure modes of a required-status-check gate are structural rather
than behavioural, so they are asserted here rather than in a behavioural suite:

  * **A required context that does not report BLOCKS FOREVER.** GitHub waits for
    it indefinitely; there is no timeout. A `paths:` filter, a job-level `if:`
    without `always()`, or a required context no workflow produces at all each
    freeze `main` permanently — and the freeze includes the PR carrying the fix.
    (Measured 2026-07-27: a Copilot outage deadlocked every open PR this way.)
  * **A required context can be created by accident.** Any workflow holding
    `checks: write` can publish a run whose name matches a required context. The
    demotion of Greptile is only real while nothing re-publishes it.

These are cheap to assert and expensive to discover in production.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"

# The required contexts, mirroring branch protection. Kept as a literal so that a
# change to the gate has to touch this list, this test, and branch protection in
# the same PR — the coupling is the point.
REQUIRED_CONTEXTS = ("validate", "secret-scan", "voice-gate")


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _on(spec: dict) -> dict:
    # YAML 1.1 parses a bare `on:` key as the boolean True. Accept both.
    return spec.get("on", spec.get(True, {})) or {}


def test_every_required_context_is_produced_by_a_job():
    """A required context nothing produces never reports, and a context that never
    reports blocks every PR permanently. Prove each one maps to a real job."""
    produced = {}
    for wf in WORKFLOWS.glob("*.yml"):
        spec = yaml.safe_load(wf.read_text())
        for job_id, job in (spec.get("jobs") or {}).items():
            # The check-run name is the job's `name:` if set, else its id.
            produced[job.get("name", job_id)] = wf.name
    missing = [c for c in REQUIRED_CONTEXTS if c not in produced]
    assert not missing, (
        f"required contexts with no producing job: {missing}. Adding one of these to "
        f"branch protection would freeze main permanently. Produced: {sorted(produced)}")


def test_voice_gate_reports_on_every_pull_request():
    """THE property that makes `voice-gate` safe as a UNIVERSAL required context.

    It must run on all PRs and report a conclusion on all of them — passing
    trivially where the replay gate does not apply. A `paths:` filter here would
    be the classic footgun: the workflow simply does not report on unmatched PRs,
    and GitHub waits for it forever."""
    spec = _load("voice-gate.yml")
    pr = _on(spec)["pull_request"] or {}
    assert "paths" not in pr and "paths-ignore" not in pr, (
        "voice-gate must NOT use a paths filter: a path-filtered required check does "
        "not report on unmatched PRs and blocks them forever. The voice-path decision "
        "belongs in the scope JOB, which always runs and always reports.")
    # `edited` is what GitHub fires on a BASE-BRANCH retarget, which changes the
    # diff without moving the head SHA. Without it a retargeted PR keeps a scope
    # verdict computed against the old base.
    assert "edited" in pr["types"], pr["types"]
    for t in ("opened", "synchronize", "reopened"):
        assert t in pr["types"], pr["types"]


def test_voice_gate_summary_job_always_reports():
    """`if: always()` plus `needs` on both jobs is the mechanism: without it, a
    skipped `replay-evidence` skips the summary too, and the required context
    never concludes."""
    jobs = _load("voice-gate.yml")["jobs"]
    assert "voice-gate" in jobs, "the required context needs a job of exactly that name"
    gate = jobs["voice-gate"]
    assert str(gate["if"]).strip() == "always()", gate.get("if")
    assert set(gate["needs"]) == {"scope", "replay-evidence"}, gate["needs"]

    # The expensive half must be the ONLY conditional one, and must be the only
    # job pinned to the self-hosted box.
    assert jobs["replay-evidence"]["runs-on"] == "self-hosted"
    assert "needs.scope.outputs.voice" in str(jobs["replay-evidence"]["if"])
    assert jobs["scope"]["runs-on"] == "ubuntu-latest"
    assert jobs["voice-gate"]["runs-on"] == "ubuntu-latest"


def test_no_voice_gate_job_ever_checks_out_pr_controlled_code():
    """THE security property. A pull request is untrusted input, and this workflow
    runs a job on the Jetson that also runs the live voice brain.

    Two attacks, one rule. BYPASS: a PR that edits `voice_gate_check.py` to print
    `voice=false` waves itself through, because the gate was running the PR's own
    classifier. RCE: a fork PR touching any voice-path file executes its own
    `voice_gate_check.py` ON THE JETSON. Both are closed by never checking out
    anything the PR author controls — every checkout pins `base.sha`.

    `actions/checkout` with NO `ref:` is the dangerous default here: on a
    `pull_request` event it checks out the PR's merge commit, which contains the
    PR's code. An unpinned checkout in this workflow is a bug, not a shortcut."""
    jobs = _load("voice-gate.yml")["jobs"]
    seen = 0
    for job_id, job in jobs.items():
        for step in job.get("steps") or []:
            if "checkout" not in str(step.get("uses", "")):
                continue
            seen += 1
            with_ = step.get("with") or {}
            ref = str(with_.get("ref", ""))
            assert ref, (
                f"{job_id}: checkout with no explicit `ref:` — that default checks out "
                "the PR MERGE COMMIT (attacker-controlled code). Pin base.sha.")
            assert "base.sha" in ref, (
                f"{job_id}: checkout ref {ref!r} is not the base commit. Never fetch or "
                "run PR-controlled code in this workflow — the PR enters as DATA only.")
            assert "head" not in ref, f"{job_id}: checkout ref names the PR head: {ref!r}"
            # A leftover credential on the long-lived self-hosted runner is its own
            # problem, separate from what code got checked out.
            assert with_.get("persist-credentials") is False, (
                f"{job_id}: checkout must set persist-credentials: false")
    assert seen >= 2, f"expected checkouts in scope + replay-evidence, saw {seen}"


def test_self_hosted_job_is_gated_against_untrusted_forks():
    """Defence in depth: even with only trusted code executing, a fork must not be
    able to make the production box do work on demand. Same-repo PRs run normally;
    a fork needs an explicit maintainer label."""
    evidence = _load("voice-gate.yml")["jobs"]["replay-evidence"]
    cond = str(evidence["if"])
    assert "head.repo.full_name == github.repository" in cond, cond
    assert "voice-gate-approved" in cond, cond
    assert evidence["runs-on"] == "self-hosted"


def _executed_shell(job: dict) -> str:
    """The shell a job actually RUNS, with comment lines stripped.

    Matching raw `run:` text is not good enough: these blocks are heavily
    commented, and the comments name the very flags being asserted. A mutation
    that deleted `--expect-revision` from the real command left the explanatory
    comment behind and the naive check stayed green — caught by mutation-testing
    this suite, which is exactly what that exercise is for."""
    out = []
    for step in job.get("steps") or []:
        for line in str(step.get("run", "")).splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                out.append(stripped)
    return "\n".join(out)


def test_voice_gate_binds_evidence_to_the_pr_head():
    """Freshness + status do not bind an artifact to the code under review. The
    self-hosted assertion must pass the PR head sha so an artifact produced for
    `main` — or any other commit — cannot clear this PR."""
    job = _load("voice-gate.yml")["jobs"]["replay-evidence"]
    run = _executed_shell(job)
    assert "--expect-revision" in run, run
    env = {}
    for s in job["steps"]:
        env.update(s.get("env") or {})
    assert "pull_request.head.sha" in str(env.get("HEAD_SHA", "")), env


def test_voice_gate_consumes_the_pr_as_data_not_code():
    """The changed-file list comes from the API, not from running git over a
    checkout of the PR. Reading a filename can do nothing."""
    run = _executed_shell(_load("voice-gate.yml")["jobs"]["scope"])
    assert "--changed-files-from" in run, run
    assert "/files" in run, run
    # The classifier that RUNS must be the one from the base checkout, never a
    # path that could resolve into PR-controlled content.
    assert "scripts/maintenance/voice_gate_check.py" in run, run


def test_only_the_break_glass_workflow_may_publish_required_contexts():
    """`checks: write` is the capability to publish a required context. Exactly one
    workflow is allowed to hold it — the audited owner break-glass. Anything else
    holding it can make itself a merge gate, or silently satisfy one."""
    holders = []
    for wf in WORKFLOWS.glob("*.yml"):
        spec = yaml.safe_load(wf.read_text())
        perms = spec.get("permissions") or {}
        if isinstance(perms, dict) and perms.get("checks") == "write":
            holders.append(wf.name)
    assert holders == ["break-glass.yml"], (
        f"unexpected workflows holding checks:write: {holders}")


def test_break_glass_is_admin_only_and_audited():
    """The break-glass exists so a required-check OUTAGE cannot permanently freeze
    a solo-owner repo. Its safety is entirely in these properties, so pin them."""
    src = (WORKFLOWS / "break-glass.yml").read_text()
    # Admin, verified against the API — applying a label needs only `write`.
    assert "getCollaboratorPermissionLevel" in src
    assert "!== 'admin'" in src
    # Single-use: the label is removed so the override cannot become standing.
    assert "removeLabel" in src
    # Permanent audit trail.
    assert "createComment" in src
    assert "BREAK-GLASS" in src
    # It must not run on every label — only its own.
    spec = _load("break-glass.yml")
    assert "break-glass" in str(spec["jobs"]["break-glass"]["if"])
    # pull_request_target: the privileged definition comes from the BASE branch, so
    # a PR cannot rewrite the override workflow in its own head commit.
    assert "pull_request_target" in _on(spec)


def test_break_glass_covers_exactly_the_required_contexts():
    """Its context list is hardcoded (reading branch protection needs an admin
    token it deliberately lacks). Drift means either an uncoverable stuck check —
    the freeze it exists to prevent — or an override of something not required."""
    src = (WORKFLOWS / "break-glass.yml").read_text()
    line = [ln for ln in src.splitlines() if "const REQUIRED" in ln]
    assert len(line) == 1, line
    for ctx in REQUIRED_CONTEXTS:
        assert f"'{ctx}'" in line[0], f"{ctx} missing from break-glass coverage: {line[0]}"
    assert line[0].count("'") == 2 * len(REQUIRED_CONTEXTS), (
        f"break-glass covers contexts that are not required: {line[0]}")


def test_greptile_is_not_in_the_required_set():
    """The whole point of the re-tiering: a non-deterministic SaaS reviewer is not
    on the required path. If someone re-adds it, this test and the branch-protection
    change have to be argued for together."""
    assert not any("reptile" in c for c in REQUIRED_CONTEXTS), REQUIRED_CONTEXTS
