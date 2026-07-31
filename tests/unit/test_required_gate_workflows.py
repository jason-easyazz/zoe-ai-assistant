"""Structural guards for the DETERMINISTIC required merge gate.

The re-tiering (2026-07-31) made the required set `validate`, `secret-scan`,
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
    reports blocks every PR permanently.

    A context is produced either by a JOB of that name, or — for `voice-gate` —
    by an explicit `checks.create` naming it. The published form exists because a
    `pull_request_target` run is associated with the BASE commit, so a job named
    `voice-gate` would report against the wrong SHA."""
    produced = {}
    for wf in WORKFLOWS.glob("*.yml"):
        spec = yaml.safe_load(wf.read_text())
        for job_id, job in (spec.get("jobs") or {}).items():
            # The check-run name is the job's `name:` if set, else its id.
            produced[job.get("name", job_id)] = wf.name
        # Contexts published by name through the Checks API.
        text = wf.read_text()
        if "checks.create" in text:
            for ctx in REQUIRED_CONTEXTS:
                if f"'{ctx}'" in text:
                    produced.setdefault(ctx, wf.name)
    missing = [c for c in REQUIRED_CONTEXTS if c not in produced]
    assert not missing, (
        f"required contexts with no producer: {missing}. Adding one of these to "
        f"branch protection would freeze main permanently. Produced: {sorted(produced)}")


def test_voice_gate_reports_on_every_pull_request():
    """THE property that makes `voice-gate` safe as a UNIVERSAL required context.

    It must run on all PRs and report a conclusion on all of them — passing
    trivially where the replay gate does not apply. A `paths:` filter here would
    be the classic footgun: the workflow simply does not report on unmatched PRs,
    and GitHub waits for it forever."""
    spec = _load("voice-gate.yml")
    pr = _on(spec)["pull_request_target"] or {}
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


def test_voice_gate_verdict_job_always_reports():
    """`if: always()` plus `needs` on both jobs is the mechanism: without it, a
    skipped `replay-evidence` skips the verdict too, and the required context
    never concludes."""
    jobs = _load("voice-gate.yml")["jobs"]
    verdict = jobs["verdict"]
    assert str(verdict["if"]).strip() == "always()", verdict.get("if")
    assert set(verdict["needs"]) == {"scope", "replay-evidence"}, verdict["needs"]

    # The expensive half must be the ONLY conditional one, and must be the only
    # job pinned to the self-hosted box.
    assert jobs["replay-evidence"]["runs-on"] == "self-hosted"
    assert "needs.scope.outputs.voice" in str(jobs["replay-evidence"]["if"])
    assert jobs["scope"]["runs-on"] == "ubuntu-latest"
    assert verdict["runs-on"] == "ubuntu-latest"


def test_voice_gate_definition_is_immutable_from_the_pr():
    """THE fix for the self-referential gate.

    On a plain `pull_request` trigger GitHub runs the workflow file FROM THE PR
    HEAD — so a PR could edit this file to delete the fork gate, repoint the
    trusted checkout at its own head, or make the verdict `exit 0`, and thereby
    author the required check that is supposed to gate it. `pull_request_target`
    reads the definition from the BASE ref, which the PR cannot rewrite."""
    spec = _load("voice-gate.yml")
    on = _on(spec)
    assert "pull_request_target" in on, (
        "voice-gate must trigger on pull_request_target: with `pull_request` the PR "
        "supplies the workflow definition and can author its own required check")
    assert "pull_request" not in on, (
        "a `pull_request` trigger alongside pull_request_target reintroduces a "
        "PR-authored run of this workflow")


def test_voice_gate_publishes_its_context_against_the_pr_head():
    """A `pull_request_target` run is associated with the BASE commit, so a job
    NAMED `voice-gate` would report against the wrong SHA and the PR would wait
    forever. The verdict must publish explicitly against `pull_request.head.sha`."""
    jobs = _load("voice-gate.yml")["jobs"]
    assert "voice-gate" not in jobs, (
        "a job named `voice-gate` under pull_request_target reports the context on the "
        "BASE sha, not the PR head — publish it explicitly instead")
    script = ""
    for step in jobs["verdict"]["steps"]:
        script += str((step.get("with") or {}).get("script", ""))
    assert "checks.create" in script, script[:400]
    assert "head_sha: headSha" in script, script[:400]
    assert "pr.head.sha" in script, script[:400]
    assert "name: CONTEXT" in script and "const CONTEXT = 'voice-gate'" in script


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


def test_checks_write_is_never_granted_at_workflow_level():
    """`checks: write` is the capability to SATISFY the merge gate, so it is scoped
    to the single job that publishes, never to a whole workflow.

    A workflow-level grant hands the write-capable token to every job in the file —
    including `replay-evidence`, which runs on the Jetson. A long-lived production
    box holding a token that can satisfy branch protection is a standing risk that
    job has no use for: it reads one JSON file."""
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        spec = yaml.safe_load(wf.read_text())
        top = spec.get("permissions") or {}
        assert not (isinstance(top, dict) and top.get("checks") == "write"), (
            f"{wf.name} grants checks:write at WORKFLOW level — every job in the file "
            "inherits it. Move it to the one job that publishes.")


def test_voice_gate_checks_write_is_scoped_to_the_verdict_job_only():
    """Pinned by job. `verdict` publishes the context; nothing else may be able to."""
    jobs = _load("voice-gate.yml")["jobs"]
    holders = [j for j, cfg in jobs.items()
               if (cfg.get("permissions") or {}).get("checks") == "write"]
    assert holders == ["verdict"], f"checks:write must be verdict-only, got {holders}"
    evidence = jobs["replay-evidence"]["permissions"]
    assert evidence.get("checks") is None, (
        "the SELF-HOSTED job must never hold checks:write — it runs on the Jetson")
    assert set(evidence) == {"contents"} and evidence["contents"] == "read", (
        f"replay-evidence must be read-only, got {evidence}")
    # Every job declares its own block: a job-level `permissions:` REPLACES the
    # workflow default wholesale, so an undeclared job silently inherits.
    for job_id, cfg in jobs.items():
        assert "permissions" in cfg, f"{job_id} does not declare its own permissions"


def test_the_competing_producer_guard_is_present_and_base_owned():
    """Branch protection matches a required context BY NAME and cannot authenticate
    its producer — and GitHub publishes a check run per job automatically, needing
    no `checks: write`. So a PR adding any workflow with a job named `voice-gate`
    publishes its own passing gate on its own head, BEFORE merge.

    The guard must live in the `pull_request_target` workflow (definition from the
    base ref), or the PR could simply delete it."""
    spec = _load("voice-gate.yml")
    assert "pull_request_target" in _on(spec), "the guard is only meaningful if base-owned"
    script = ""
    for step in spec["jobs"]["verdict"]["steps"]:
        script += str((step.get("with") or {}).get("script", ""))
    assert "findCompetingProducers" in script, "the competing-producer guard is missing"
    assert "SANCTIONED" in script and ".github/workflows/voice-gate.yml" in script
    # It must consider the guard failing to be a failure, not a pass.
    assert "guard could not run" in script


def test_checks_write_is_held_by_exactly_the_two_workflows_that_need_it():
    """`checks: write` is the capability to publish — or silently SATISFY — a
    required context. It is the single most dangerous permission in this repo, so
    the holder list is pinned by name.

    Exactly two may hold it, for different reasons:
      * `voice-gate.yml` publishes the `voice-gate` context itself (a
        pull_request_target run is associated with the base commit, so the context
        has to be published against the PR head explicitly);
      * `break-glass.yml` publishes substitute contexts during an outage.

    Both are `pull_request_target`, i.e. their definitions come from the base ref
    and a PR cannot rewrite them. That pairing is the point: a workflow that can
    publish a required context must not be one a PR can author. Anything else
    acquiring this permission can make itself a merge gate, or satisfy one."""
    holders = {}
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        spec = yaml.safe_load(wf.read_text())
        # JOB level — the workflow level is asserted empty of it separately, so
        # scanning only the top would now match nothing and pass vacuously.
        for cfg in (spec.get("jobs") or {}).values():
            if ((cfg.get("permissions") or {}).get("checks") == "write"):
                holders[wf.name] = spec
    assert sorted(holders) == ["break-glass.yml", "voice-gate.yml"], (
        f"unexpected workflows holding checks:write: {sorted(holders)}")
    for name, spec in holders.items():
        assert "pull_request_target" in _on(spec), (
            f"{name} can publish a required context but is not pull_request_target — a "
            "PR could supply its definition and publish its own green context")
        assert "pull_request" not in _on(spec), (
            f"{name} also triggers on `pull_request`, which runs a PR-authored copy of a "
            "workflow that can publish required contexts")


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
