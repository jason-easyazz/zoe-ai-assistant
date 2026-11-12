"""Landing adapters: how a gated heal commit reaches the code Zoe runs.

  github - push a branch, open a DRAFT PR labelled `self-heal`. The existing landing services
           take it from there ONLY after a reviewer who is not the engine marks it ready.
           Draft is the only thing keeping a heal PR out of an unattended merge (the lander
           takes every open non-draft PR with resolved threads and green checks), so v1
           never marks ready, never merges, never uses --admin / --force.
  local  - no GitHub, no `gh`, no network: snapshot the integration branch, fast-forward it to
           the heal commit, apply, health-check, verify the reproduction; any failure rolls
           back to the snapshot and parks the fault.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import gitops
from .config import Config

MARKER = "zoe-self-heal:v1"


@dataclass
class LandResult:
    status: str                    # pr_open | healed | rolled_back | failed
    detail: str = ""
    pr_url: str = ""
    review: str = ""
    extra: dict = field(default_factory=dict)


# --- second opinion ---------------------------------------------------------------------

def cross_review(cfg: Config, base_wt: str, heal_wt: str, base_sha: str, head_sha: str,
                 engine: str) -> str:
    """Run the existing cross_review.sh --local from the BASE checkout (the engine may have
    edited the heal worktree's copy). Exit 2 / anything non-zero is 'not reviewed', never clean."""
    script = Path(base_wt) / cfg.cross_review_script
    if not script.exists():
        return "not reviewed: cross_review.sh not found"
    contract = ("A self-heal fix for ONE fault. It must fix the cause without weakening the "
                "reproducing test, touch no CI/deploy/landing/auth/migration file, and stay small.")
    try:
        r = gitops.run(["bash", str(script), "--local", f"{base_sha}..{head_sha}", contract],
                       cwd=heal_wt, timeout=cfg.cross_review_timeout_s, check=False,
                       env={"CROSS_REVIEW_LOCAL_REPO": heal_wt})
    except subprocess.TimeoutExpired:
        return "not reviewed: cross-review timed out"
    if r.returncode == 2:
        return "not reviewed: cross-review unavailable (exit 2)"
    if r.returncode != 0:
        return f"not reviewed: cross-review failed (exit {r.returncode})"
    note = ("" if engine != "codex" else
            " NOTE: the engine was codex and cross_review prefers codex, so this may be same-vendor.")
    report = r.stdout.strip()
    return f"reviewed (cross_review.sh exit 0).{note}\n\n{report[:6000]}"


def pr_body(ticket, engine: str, gates: list, review: str, diff, evidence: dict) -> str:
    marker = {"engine": engine, "ticket": ticket.id, "fault_class": ticket.fault_class,
              "fault_key": ticket.fault_key, "base_sha": ticket.base_sha,
              "repro_cmd": ticket.repro_cmd, "gates": gates, "evidence": evidence,
              "review": review.splitlines()[0] if review else "not reviewed"}
    gate_lines = "\n".join(f"- `{g['gate']}`: **{g['status']}** {g['detail']}" for g in gates)
    return "\n".join([
        "Opened by Zoe's self-heal loop. ONE finding per PR. This PR stays DRAFT: marking it ready",
        "must come from a reviewer that is not the engine.",
        "",
        "## Fault signal", f"{ticket.fault_class}: {ticket.summary}",
        "", "## Evidence", "```", json.dumps(ticket.evidence, indent=1), "```",
        "", "## Reproduction", f"`{' '.join(ticket.repro_cmd)}` (exit 0 = healed; re-run against the ORIGINAL test file after merge)",
        "", "## What changed", f"{len(diff.files)} files, +{diff.added}/-{diff.deleted}: " + ", ".join(f"`{f}`" for f in diff.files),
        "", "## Gates (run locally before push)", gate_lines,
        "", "## Second opinion", review or "not reviewed",
        "", f"<!-- {MARKER}", json.dumps(marker, indent=1), "-->",
    ])


# --- github ------------------------------------------------------------------------------

class GithubLanding:
    name = "github"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def land(self, ctx) -> LandResult:
        review = cross_review(self.cfg, ctx.base_wt, ctx.heal_wt, ctx.ticket.base_sha, ctx.head_sha, ctx.engine)
        branch = f"self-heal/{ctx.ticket.id}"
        # never --force; a fresh branch name per ticket
        gitops.git(ctx.heal_wt, "push", "origin", f"HEAD:refs/heads/{branch}", timeout=300)
        body_file = Path(self.cfg.state_dir) / f"pr-body-{ctx.ticket.id}.md"
        body_file.write_text(pr_body(ctx.ticket, ctx.engine, ctx.gates, review, ctx.diff, ctx.evidence))
        title = f"fix(self-heal): {ctx.ticket.summary}"[:120]
        r = gitops.run(["gh", "pr", "create", "--draft", "--base", self.cfg.base_branch, "--head", branch,
                        "--label", self.cfg.pr_label, "--title", title, "--body-file", str(body_file)],
                       cwd=ctx.heal_wt, timeout=120)
        url = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        return LandResult("pr_open", "draft PR opened", pr_url=url, review=review.splitlines()[0])

    def merged_commit(self, pr_url: str) -> str | None:
        r = gitops.run(["gh", "pr", "view", pr_url, "--json", "state,mergeCommit"], timeout=60)
        d = json.loads(r.stdout)
        if d.get("state") != "MERGED":
            return None
        return (d.get("mergeCommit") or {}).get("oid")


# --- local -------------------------------------------------------------------------------

class NoopApplier:
    """Lab apply: records what a deployed unit WOULD do, changes nothing live.

    A deployed unit's apply (not built; an owner step) would:
      1. `git -C <live_checkout> merge --ff-only <integration sha>` (rollback: `git -C <live>
         checkout -B <branch> <snapshot sha>`),
      2. restart only the services whose paths changed (`systemctl --user restart <unit>`),
      3. wait for each unit's health endpoint (the zoe_ground_truth.sh checks), then the loop
         runs `health_checks` + the reproduction; any failure -> rollback + restart again.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.calls: list = []

    def apply(self, sha: str) -> str:
        self.calls.append(("apply", sha))
        return f"noop apply (lab): would fast-forward {self.cfg.live_checkout} to {sha[:12]} and restart affected units"

    def rollback(self, sha: str) -> str:
        self.calls.append(("rollback", sha))
        return f"noop rollback (lab): would reset {self.cfg.live_checkout} to {sha[:12]} and restart affected units"


class LocalLanding:
    name = "local"
    REVIEW = "not reviewed (single engine)"

    def __init__(self, cfg: Config, applier=None):
        self.cfg = cfg
        self.applier = applier or NoopApplier(cfg)

    def _health(self) -> tuple[bool, str]:
        for argv in self.cfg.health_checks:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=self.cfg.gate_timeout_s)
            if r.returncode != 0:
                return False, f"health check {argv[0]} rc={r.returncode}"
        return True, f"{len(self.cfg.health_checks)} health checks ok"

    def land(self, ctx) -> LandResult:
        cfg, repo, t = self.cfg, self.cfg.repo, ctx.ticket
        int_ref = f"refs/heads/{cfg.integration_branch}"
        if int_ref in gitops.checked_out_branches(repo):
            return LandResult("failed", f"{int_ref} is checked out in a worktree; refusing to move it", review=self.REVIEW)
        tip = gitops.rev(repo, int_ref)
        if tip != t.base_sha:
            return LandResult("failed", f"integration moved since the heal was cut ({tip[:12]} != {t.base_sha[:12]})",
                              review=self.REVIEW)
        snap = f"refs/self-heal/snapshots/{t.id}"
        gitops.git(repo, "update-ref", snap, tip)
        # compare-and-swap fast-forward: the heal commit's parent is the snapshot
        gitops.git(repo, "update-ref", int_ref, ctx.head_sha, tip)
        steps = {"snapshot": snap, "merged": ctx.head_sha, "apply": self.applier.apply(ctx.head_sha)}
        ok, why = self._health()
        if ok:
            res = gitops.run_repro(cfg, repo, int_ref, t, ctx.verify_path, ctx.lock)
            ok, why = res.ok, f"verify {res.detail}"
        steps["check"] = why
        if ok:
            return LandResult("healed", why, review=self.REVIEW, extra=steps)
        gitops.git(repo, "update-ref", int_ref, tip, ctx.head_sha)
        steps["rollback"] = self.applier.rollback(tip)
        steps["restored"] = gitops.rev(repo, int_ref) == tip
        return LandResult("rolled_back", why, review=self.REVIEW, extra=steps)


def make_landing(cfg: Config, applier=None):
    return GithubLanding(cfg) if cfg.landing == "github" else LocalLanding(cfg, applier)

