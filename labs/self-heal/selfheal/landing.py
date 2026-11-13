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

from . import binaries, gitops
from .config import Config, check_health_argv, inside_refused, is_pinned_python
from .sandbox import Sandbox

MARKER = "zoe-self-heal:v1"


@dataclass
class LandResult:
    status: str                    # pr_open | healed | rolled_back | failed
    detail: str = ""
    pr_url: str = ""
    review: str = ""
    extra: dict = field(default_factory=dict)
    exc: BaseException | None = None   # re-raised by the runner AFTER recording, if not an Exception


# --- second opinion ---------------------------------------------------------------------

def cross_review(cfg: Config, base_wt: str, heal_wt: str, base_sha: str, head_sha: str,
                 engine: str) -> str:
    """Run the existing cross_review.sh --local from the BASE checkout (the engine may have
    edited the heal worktree's copy). Returns a STATUS only: the reviewer's text is model output
    and never goes into the public PR body or the audit log. Non-zero = 'not reviewed'."""
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
    same = " (engine codex; cross_review prefers codex, so possibly same-vendor)" if engine == "codex" else ""
    return f"reviewed: cross_review.sh exit 0{same}; run it by hand for the full report"


def pr_body(ticket, engine: str, gates: list, review: str, diff) -> str:
    """PUBLIC text. Ids, the fault class, file names, the test node id, gate names with pass/fail,
    the engine name and the marker block. Never failure text, tracebacks, model output or diff."""
    gates_min = [{"gate": g["gate"], "status": g["status"]} for g in gates]
    marker = {"engine": engine, "ticket": ticket.id, "fault_class": ticket.fault_class,
              "fault_key": ticket.fault_key, "base_sha": ticket.base_sha,
              "test": ticket.evidence.get("nodeid", ""), "files": diff.files, "gates": gates_min,
              "review": review}
    return "\n".join([
        "Opened by Zoe's self-heal loop. ONE finding per PR. This PR stays DRAFT: marking it ready",
        "must come from a reviewer that is not the engine.",
        "",
        f"- ticket: `{ticket.id}`", f"- fault class: `{ticket.fault_class}`",
        f"- failing test: `{ticket.evidence.get('nodeid', '')}`", f"- engine: `{engine}`",
        "- changed files: " + ", ".join(f"`{f}`" for f in diff.files),
        "- gates: " + ", ".join(f"`{g['gate']}` {g['status']}" for g in gates_min),
        f"- second opinion: {review or 'not reviewed'}",
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
        ctx.checkpoint("push")
        ctx.record(pushed_branch=branch)   # write-ahead: a later interrupt is cleaned up next run
        # never --force; a fresh branch name per ticket
        gitops.git(ctx.heal_wt, "push", "origin", f"HEAD:refs/heads/{branch}", timeout=300)
        body_file = Path(self.cfg.state_dir) / f"pr-body-{ctx.ticket.id}.md"
        body_file.write_text(pr_body(ctx.ticket, ctx.engine, ctx.gates, review, ctx.diff))
        title = f"fix(self-heal): {ctx.ticket.fault_class} {ctx.ticket.evidence.get('nodeid', ctx.ticket.id)}"[:120]
        ctx.checkpoint("pr")
        r = gitops.run(["gh", "pr", "create", "--draft", "--base", self.cfg.base_branch, "--head", branch,
                        "--label", self.cfg.pr_label, "--title", title, "--body-file", str(body_file)],
                       cwd=ctx.heal_wt, timeout=120)
        url = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        return LandResult("pr_open", "draft_pr", pr_url=url, review=review.split(":")[0].replace(" ", "_"))

    def cleanup_branch(self, tid: str, branch: str) -> str:
        """A late interrupt (after the push, before the PR) leaves `self-heal/<id>` on origin with no
        PR. Deleted on the next run when it is ours (exact name for that recorded ticket) and no PR
        (any state) has it as head; if a PR exists (the reply was lost), it is recorded instead."""
        if branch != f"self-heal/{tid}":
            return "refused:name"
        r = gitops.run(["gh", "pr", "list", "--head", branch, "--state", "all", "--json", "url", "--limit", "5"],
                       cwd=self.cfg.repo, timeout=60)
        prs = json.loads(r.stdout or "[]")
        if prs:
            return f"pr:{prs[0]['url']}"
        if not gitops.git(self.cfg.repo, "ls-remote", "origin", f"refs/heads/{branch}", timeout=120):
            return "absent"
        gitops.git(self.cfg.repo, "push", "origin", "--delete", f"refs/heads/{branch}", timeout=120)
        return "deleted"

    def rollback_intent(self, intent: dict) -> bool:
        return False   # github landing never advances a ref

    def merged_commit(self, pr_url: str) -> str | None:
        r = gitops.run(["gh", "pr", "view", pr_url, "--json", "state,mergeCommit"], timeout=60)
        d = json.loads(r.stdout)
        if d.get("state") == "CLOSED":
            return False
        if d.get("state") != "MERGED":
            return None
        return (d.get("mergeCommit") or {}).get("oid")


# --- local -------------------------------------------------------------------------------

class NoopApplier:
    """Lab apply: records what a deployed unit WOULD do, changes nothing live.

    A deployed unit's apply (not built; an owner step) would:
      1. `git -C <apply_target> merge --ff-only <integration sha>` (rollback: `git -C <target>
         checkout -B <branch> <snapshot sha>`),
      2. restart only the services whose paths changed (`systemctl --user restart <unit>`),
      3. wait for each unit's health endpoint (the zoe_ground_truth.sh checks), then the loop
         runs `health_checks` + the reproduction; any failure -> rollback + restart again.
    The target is refused when it is this box's live checkout (hard-coded) or `live_checkout`.
    """

    def __init__(self, cfg: Config):
        if cfg.apply_target and inside_refused(cfg.apply_target, cfg.live_checkout):
            raise ValueError("apply target is the live checkout; refusing")
        self.cfg = cfg
        self.calls: list = []

    def apply(self, sha: str) -> str:
        self.calls.append(("apply", sha))
        return "noop"

    def rollback(self, sha: str) -> str:
        self.calls.append(("rollback", sha))
        return "noop"


class LocalLanding:
    name = "local"
    REVIEW = "not_reviewed_single_engine"   # no second subscription: never "reviewed"

    def __init__(self, cfg: Config, applier=None):
        self.cfg = cfg
        self.applier = applier or NoopApplier(cfg)

    def _health(self, repo: str, ref: str, path: str) -> tuple[bool, str]:
        """Allow-listed argv (checked again here, not only at load), run from a FRESH worktree of
        the integration tip, never the live checkout, each with its own timeout."""
        if not self.cfg.health_checks:
            return True, "health_none"
        gitops.add_worktree(repo, path, base=ref)
        try:
            for argv in self.cfg.health_checks:
                check_health_argv(argv, self.cfg.python)
                if is_pinned_python(argv[0]):   # repo code: sandboxed like every gate
                    rc, _ = Sandbox(self.cfg).run(argv, path, self.cfg.health_timeout_s)
                else:                           # true / false / systemctl: resolved absolute paths
                    try:
                        rc = subprocess.run([binaries.which(argv[0]), *argv[1:]], cwd=path, capture_output=True,
                                            timeout=self.cfg.health_timeout_s, stdin=subprocess.DEVNULL).returncode
                    except subprocess.TimeoutExpired:
                        rc = None
                if rc is None:
                    return False, "health_timeout"
                if rc != 0:
                    return False, f"health_failed:rc={rc}"
            return True, "health_ok"
        finally:
            gitops.remove_worktree(repo, path)

    def land(self, ctx) -> LandResult:
        cfg, repo, t = self.cfg, self.cfg.repo, ctx.ticket
        int_ref = f"refs/heads/{cfg.integration_branch}"
        if int_ref in gitops.checked_out_branches(repo):
            return LandResult("failed", "integration_checked_out", review=self.REVIEW)
        tip = gitops.rev(repo, int_ref)
        if tip != t.base_sha:
            return LandResult("failed", "integration_moved", review=self.REVIEW)
        ctx.checkpoint("merge")
        snap = f"refs/self-heal/snapshots/{t.id}"
        gitops.git(repo, "update-ref", snap, tip)
        # write-ahead intent BEFORE the ref moves; cleared only after a confirmed heal or a completed
        # rollback, so an interrupt anywhere in between (even mid-rollback) is rolled back next run
        intent = {"ref": int_ref, "from": tip, "to": ctx.head_sha}
        ctx.record(landing_intent=intent)
        # compare-and-swap fast-forward: the heal commit's parent is the snapshot
        gitops.git(repo, "update-ref", int_ref, ctx.head_sha, tip)
        # From here the integration ref has advanced: ANY exit that is not a confirmed heal
        # (a failed check, an exception, even KeyboardInterrupt / SystemExit) restores the snapshot.
        steps = {"snapshot": snap, "sha": ctx.head_sha, "applied": False}
        ok, why, exc = False, "unknown", None
        try:
            self.applier.apply(ctx.head_sha)
            steps["applied"] = True
            ok, why = self._health(repo, int_ref, ctx.verify_path + "-health")
            if ok:
                res = gitops.run_repro(cfg, repo, int_ref, t, ctx.verify_path, ctx.lock)
                ok, why = res.ok, f"verify:{res.detail}"
        except BaseException as e:   # noqa: BLE001 - the rollback below must run for every exit
            ok, why, exc = False, f"exception:{type(e).__name__}", e   # class only, never the message
        if ok:
            ctx.record(landing_intent=None)
            return LandResult("healed", why, review=self.REVIEW, extra=steps)
        try:
            self.rollback_intent(intent)
            ctx.record(landing_intent=None)
        except Exception as e:   # the intent stays: the next run re-runs the rollback first
            why += f",rollback_apply_failed:{type(e).__name__}"
        steps["restored"] = gitops.rev(repo, int_ref) == tip
        return LandResult("rolled_back", why, review=self.REVIEW, extra=steps, exc=exc)

    def rollback_intent(self, intent: dict) -> bool:
        """Idempotent: restore the ref only if it still points at the heal (CAS), then roll the apply
        back. A ref moved by someone else is left alone (False)."""
        repo, ref, frm, to = self.cfg.repo, intent["ref"], intent["from"], intent["to"]
        cur = gitops.rev(repo, ref)
        if cur == to:
            gitops.git(repo, "update-ref", ref, frm, to)
        elif cur != frm:
            return False
        self.applier.rollback(frm)
        return gitops.rev(repo, ref) == frm

    def cleanup_branch(self, tid: str, branch: str) -> str:
        return "absent"   # local landing never pushes


def make_landing(cfg: Config, applier=None):
    return GithubLanding(cfg) if cfg.landing == "github" else LocalLanding(cfg, applier)
