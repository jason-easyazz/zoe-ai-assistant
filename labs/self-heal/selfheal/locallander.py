"""Land locally, with no GitHub: gates -> merge into the local integration branch -> prove the MERGED
tree -> apply -> health window -> ledger, and automatic rollback on any exit that is not a kept landing.

Built on `landing.LocalLanding` (snapshot ref, write-ahead intent, compare-and-swap, verified rollback).
What this adds:

- a real MERGE: when the integration branch moved since the heal's base, the heal is merged onto its
  tip (a textual conflict refuses); the merged tree is one nobody gated, so
- the PROOF is re-run on the merged tree: the supervised gate (base = the pre-merge tip) and the whole
  offline lane, sandboxed, BEFORE anything is applied; red = rollback;
- a HEALTH WINDOW after apply: a probe polled for `health_window_s`; red inside it = rollback;
- the LEDGER (`ledger.py`): landed / rolled_back / refused, the reason and the proof hash;
- intent PHASES (merged -> proven -> watching) so the next start can tell a half-landing apart:
  `watching` (proven and applied, only the window was cut short) is FINISHED by re-running the window;
  anything earlier is ROLLED BACK. The integration branch is never left advanced and unproven;
- one landing per repo at a time (a lock in the git dir) on top of the compare-and-swap;
- two deployment SHAPES behind one interface: `SoldUnit` (the repo must have no remote at all) and
  `ThisUnit` (the same local landing, plus the push + draft-PR command lines, PRINTED, never run).

Lab only: the applier is `NoopApplier`, the probe is a fake, every repo is a scratch repo.
"""
from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import os
import time
from pathlib import Path

from . import gitops
from .config import SUPERVISED_GATE, Config
from .gates import gates_green, run_gates
from .landing import LandResult, LocalLanding
from .ledger import Ledger, proof_hash
from .safety import Refused
from .supervisor import RUNNER_SHA

REASONS = ("landing_busy", "remote_present", "integration_checked_out", "integration_rewritten", "merge_conflict",
           "health_timeout",
           "lost_race", "proof_failed", "health_failed", "health_red", "exception", "interrupted")


class HardKill(BaseException):
    """Test seam standing for SIGKILL / power loss mid-landing: NOTHING after it runs, no rollback, no
    record. Only the next start's recovery sees what it left."""


class FakeProbe:
    """The lab's health probe: a scripted sequence of green (True) / red (False); the last repeats."""

    def __init__(self, *results):
        self.results, self.calls = list(results) or [True], 0

    def check(self) -> bool:
        self.calls += 1
        r = self.results[min(self.calls, len(self.results)) - 1]
        return bool(r() if callable(r) else r)


class SoldUnit:
    """No remote at all: a repo that has one is refused before anything moves."""
    name = "sold_unit"

    def preflight(self, repo: str) -> None:
        if gitops.git(repo, "remote"):
            raise Refused("remote_present", "")

    def after_land(self, cfg: Config, ticket, sha: str) -> list:
        return []


class ThisUnit:
    """This box: the same local landing, and ALSO a draft PR. Only the command lines are produced."""
    name = "this_unit"

    def preflight(self, repo: str) -> None:
        return None

    def after_land(self, cfg: Config, ticket, sha: str) -> list:
        branch = f"self-heal/{ticket.id}"
        return [["git", "push", "origin", f"{sha}:refs/heads/{branch}"],
                ["gh", "pr", "create", "--draft", "--base", cfg.base_branch, "--head", branch, "--label", cfg.pr_label,
                 "--title", f"fix(self-heal): {ticket.fault_class} {ticket.evidence.get('nodeid', ticket.id)}"[:120],
                 "--body-file", "<pr-body>"]]


SHAPES = {"sold_unit": SoldUnit, "this_unit": ThisUnit}


class LedgerLanding(LocalLanding):
    name = "local"

    def __init__(self, cfg: Config, applier=None, *, shape=None, probe=None, now=None, clock=time.monotonic,
                 sleep=time.sleep, hooks=None):
        super().__init__(cfg, applier)
        self.shape = shape or SHAPES[cfg.unit_shape]()
        self.probe, self.clock, self.sleep, self.hooks = probe, clock, sleep, hooks or {}
        self.now = now or (lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
        self.ledger = Ledger(str(Path(cfg.state_dir) / "ledger.jsonl"))
        self.would_run: list = []     # ThisUnit: the PR command lines of the last landing (never executed)

    # --- plumbing ---
    def _hook(self, name: str) -> None:
        if name in self.hooks:
            self.hooks[name]()

    @contextlib.contextmanager
    def _repo_lock(self):
        gitdir = gitops.git(self.cfg.repo, "rev-parse", "--absolute-git-dir")
        with open(os.path.join(gitdir, "self-heal-landing.lock"), "w") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused("landing_busy", "") from None
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _note(self, event: str, intent: dict, reason: str = "", **kw) -> None:
        keep = ("ticket", "fault_key", "test", "engine", "ref", "from", "to", "tree", "heal", "merge", "files", "proof")
        self.ledger.append(event, self.now(), shape=self.shape.name, reason=reason.split(":")[0],
                           **{k: intent[k] for k in keep if k in intent}, **kw)

    def _merge(self, tip: str, ctx) -> str | None:
        """The heal merged onto a MOVED integration tip, in a throwaway worktree; None on a conflict."""
        path = ctx.verify_path + "-merge"
        gitops.add_worktree(self.cfg.repo, path, base=tip)
        try:
            r = gitops.run(["git", "-C", path, "merge", "-q", "--no-ff", "--no-edit", ctx.head_sha], check=False,
                           env={"GIT_AUTHOR_NAME": "zoe-self-heal", "GIT_COMMITTER_NAME": "zoe-self-heal",
                                "GIT_AUTHOR_EMAIL": "self-heal@zoe.local", "GIT_COMMITTER_EMAIL": "self-heal@zoe.local"})
            return gitops.rev(path, "HEAD") if r.returncode == 0 else None
        finally:
            gitops.remove_worktree(self.cfg.repo, path)

    def _prove(self, ctx, intent: dict) -> tuple[bool, str]:
        """The proof, re-run on the merged tree, with the pre-merge tip as the trusted base."""
        cfg, tip, to = self.cfg, intent["from"], intent["to"]
        t = dataclasses.replace(ctx.ticket, base_sha=tip)
        base_wt, wt = ctx.verify_path + "-proof-base", ctx.verify_path + "-proof"
        try:
            gitops.add_worktree(cfg.repo, base_wt, base=tip)
            gitops.add_worktree(cfg.repo, wt, base=to)
            changed = sorted(filter(None, gitops.git(cfg.repo, "diff", "--no-renames", "--name-only", tip, to).splitlines()))
            post = run_gates(cfg, [SUPERVISED_GATE, "offline_lane"], wt, changed, t, base_wt=base_wt)
        finally:
            gitops.remove_worktree(cfg.repo, wt)
            gitops.remove_worktree(cfg.repo, base_wt)
        trio = lambda gs: [[g["gate"], g["status"], str(g.get("detail", ""))] for g in gs]   # noqa: E731
        intent["proof"] = proof_hash(base=tip, to=to, tree=intent["tree"], heal=intent["heal"], merge=intent["merge"],
                                     before=trio(ctx.gates), after=trio(post), runner=RUNNER_SHA)
        if gates_green(post) and len(post) == 2:
            return True, "proof:" + ",".join(f"{g['gate']}={g['status']}" for g in post)
        return False, f"proof_failed:{post[-1]['gate']}"

    def _watch(self) -> tuple[bool, str]:
        """Poll the probe for the window; one red reading inside it fails the landing."""
        if self.probe is None or self.cfg.health_window_s <= 0:
            return True, "health_window_none"
        end = self.clock() + self.cfg.health_window_s
        while True:
            if not self.probe.check():
                return False, "health_red"
            if self.clock() >= end:
                return True, "health_window_ok"
            self.sleep(max(1, self.cfg.health_interval_s))

    def _refuse(self, ctx, reason: str) -> LandResult:
        t = ctx.ticket
        self.ledger.append("refused", self.now(), shape=self.shape.name, reason=reason, ticket=t.id,
                           fault_key=t.fault_key, heal=ctx.head_sha, engine=ctx.engine)
        return LandResult("failed", reason, review=self.REVIEW)

    # --- the landing ---
    def land(self, ctx) -> LandResult:
        try:
            with self._repo_lock():
                return self._land(ctx)
        except Refused as e:
            return self._refuse(ctx, e.rule)

    def _land(self, ctx) -> LandResult:
        cfg, repo, t = self.cfg, self.cfg.repo, ctx.ticket
        ref = f"refs/heads/{cfg.integration_branch}"
        self.would_run = []
        self.shape.preflight(repo)
        if ref in gitops.checked_out_branches(repo):
            raise Refused("integration_checked_out", "")
        tip = gitops.rev(repo, ref)
        to, how = ctx.head_sha, "ff"
        if tip != t.base_sha:
            if gitops.run(["git", "-C", repo, "merge-base", "--is-ancestor", t.base_sha, tip], check=False).returncode:
                raise Refused("integration_rewritten", "")
            to, how = self._merge(tip, ctx), "merge"
            if to is None:
                raise Refused("merge_conflict", "")
        ctx.checkpoint("merge")
        snap = f"refs/self-heal/snapshots/{t.id}"
        gitops.git(repo, "update-ref", snap, tip)
        intent = {"ref": ref, "from": tip, "to": to, "snap": snap, "phase": "merged", "merge": how,
                  "heal": ctx.head_sha, "tree": gitops.git(repo, "rev-parse", f"{to}^{{tree}}"), "ticket": t.id,
                  "fault_key": t.fault_key, "test": t.evidence.get("nodeid", ""), "engine": ctx.engine,
                  "files": list(ctx.diff.files)}
        ctx.record(landing_intent=intent)   # write-ahead, BEFORE the ref moves
        self._hook("intent")
        try:
            gitops.git(repo, "update-ref", ref, to, tip)   # compare-and-swap
        except gitops.GitError:
            ctx.record(landing_intent=None)
            raise Refused("lost_race", "") from None
        self._hook("advanced")
        steps = {"snapshot": snap, "sha": to, "applied": False, "merge": how}
        ok, why, exc = False, "unknown", None
        try:
            ok, why = self._prove(ctx, intent)            # nothing is applied before the merged tree is proven
            if ok:
                intent["phase"] = "proven"
                ctx.record(landing_intent=intent)
                self._hook("proven")
                self.applier.apply(to)
                steps["applied"] = True
                ok, health = self._health(repo, ref, ctx.verify_path + "-health")
                why = why if ok else health
            if ok:
                intent["phase"] = "watching"
                ctx.record(landing_intent=intent)
                self._hook("watching")
                ok, watch = self._watch()
                why = why if ok else watch
        except HardKill:
            raise
        except BaseException as e:   # noqa: BLE001 - the rollback below must run for every other exit
            ok, why, exc = False, f"exception:{type(e).__name__}", e
        steps["proof"] = intent.get("proof", "")
        if ok:
            self._note("landed", intent)
            self._hook("ledgered")
            ctx.record(landing_intent=None)
            self.would_run = self.shape.after_land(cfg, t, to)
            return LandResult("healed", why, review=self.REVIEW, extra=steps)
        restored = False
        try:
            restored = super().rollback_intent(intent)
        except Exception as e:   # noqa: BLE001 - not verified: the intent stays
            why += f",rollback_apply_failed:{type(e).__name__}"
        steps["restored"] = restored
        self._note("rolled_back", intent, why, restored=restored)
        if not restored:
            return LandResult("rollback_stuck", why, review=self.REVIEW, extra=steps, exc=exc)
        ctx.record(landing_intent=None)
        return LandResult("rolled_back", why, review=self.REVIEW, extra=steps, exc=exc)

    # --- recovery (the runner calls these at the start of every run, before anything else) ---
    def finish_intent(self, intent: dict) -> bool:
        """True when a half-landing can be KEPT: the ref still holds the landing, the merged tree was
        proven and applied (`watching`), and the health window, re-run in full now, stays green."""
        done = self.ledger.last_for(intent.get("ticket", ""), intent["to"])
        if done:   # the ledger line was written, only the intent was not cleared
            return done["event"] == "landed" and gitops.rev(self.cfg.repo, intent["ref"]) == intent["to"]
        if intent.get("phase") != "watching" or gitops.rev(self.cfg.repo, intent["ref"]) != intent["to"]:
            return False
        if not self._watch()[0]:
            return False
        self._note("landed", intent, recovered=True)
        return True

    def rollback_intent(self, intent: dict) -> bool:
        restored = super().rollback_intent(intent)
        done = self.ledger.last_for(intent.get("ticket", ""), intent["to"])
        if not (done and done["event"] == "rolled_back" and done.get("restored")):
            self._note("rolled_back", intent, "interrupted", restored=restored, recovered=True)
        return restored
