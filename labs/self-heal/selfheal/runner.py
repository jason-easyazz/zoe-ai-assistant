"""One heal run: preflight -> detect -> ticket -> (dry: plan and stop) -> worktree -> engine ->
inspect (nothing staged) -> diff policy -> gates -> stage the checked paths -> commit ->
(local: verify the commit) | (full: land) -> audit.

The kill switch is checked before every step; kill switch + quiet hours + voice lander +
night_busy are re-checked before the engine, the gates, the landing and each push / PR call.
A mid-run stop is `interrupted`: worktree discarded, no cap spent.

audit.jsonl carries ids, classes, counts, durations, file names and exit codes ONLY: keys are
an allow-list and every string value must be a single whitespace-free token.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import binaries, gitops
from .board import make_board
from .config import Config
from .detectors import COLLECTION_ERROR, Ticket, collection_errors, failing_test_tickets, run_detector
from .engines import select_engine
from .gates import gates_green, run_gates
from .landing import make_landing
from .safety import (HealLock, Refused, State, check_busy, check_diff, check_kill_switch,
                     check_quiet_hours, live_fingerprint)
from .sandbox import Sandbox

AUDIT_KEYS = {"ts", "run", "step", "outcome", "ticket", "dispatch", "landing", "engine",
              "rule", "reason", "found", "skipped", "fault_class", "fault_key", "test", "plan",
              "seconds", "rc", "files", "lines", "gates", "sha", "branch", "pr", "review", "snapshot",
              "applied", "restored", "verify", "issue", "chosen", "checkpoint", "error", "bins", "sandbox"}
_TOKEN = re.compile(r"^[A-Za-z0-9_./:@+=,#\[\]-]{0,240}$")


def _safe(v):
    if isinstance(v, str):
        return v if _TOKEN.match(v) else "<redacted>"
    if isinstance(v, (list, tuple)):
        return [_safe(x) for x in v]
    if isinstance(v, dict):
        return {k: _safe(x) for k, x in v.items() if k in AUDIT_KEYS or k in ("gate", "status", "detail")}
    return v if isinstance(v, (int, float, bool)) or v is None else "<redacted>"


class Interrupted(Exception):
    def __init__(self, checkpoint: str, rule: str):
        super().__init__(f"{checkpoint}:{rule}")
        self.checkpoint, self.rule = checkpoint, rule


@dataclass
class LandCtx:
    ticket: Ticket
    base_wt: str
    heal_wt: str
    head_sha: str
    engine: str
    gates: list
    diff: object
    verify_path: str
    lock: list = field(default_factory=list)
    checkpoint: Callable = lambda name: None
    record: Callable = lambda **kw: None   # write-ahead facts onto the ticket's state record (saved at once)


class Runner:
    def __init__(self, cfg: Config, *, engine=None, applier=None, now=None, board="auto"):
        cfg.validate()   # the hard floor holds even for a Config built in code and then mutated
        self.cfg = cfg
        try:   # absolute, checked paths from a fixed trusted PATH, before anything runs
            self.bins, self.bin_error = binaries.resolve_all(cfg), None
        except binaries.UnsafeBinary as e:
            self.bins, self.bin_error = {}, e
        self.sandbox = Sandbox(cfg)
        self.engine_skips: list = []
        if engine is None:
            engine, self.engine_skips = select_engine(cfg)
        self.engine = engine          # None = no engine linked: detect and report, never touch code
        self.landing = make_landing(cfg, applier)
        self.now = now or dt.datetime.now
        self.run_id = uuid.uuid4().hex[:8]
        Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
        self.audit_path = Path(cfg.state_dir) / "audit.jsonl"
        self.board = make_board(cfg, self.audit) if board == "auto" else board

    # --- plumbing ---
    def audit(self, step: str, outcome: str, ticket: str = "", **detail) -> None:
        unknown = set(detail) - AUDIT_KEYS
        if unknown:
            raise ValueError(f"audit key(s) not on the allow-list: {sorted(unknown)}")
        row = {"ts": self.now().isoformat(timespec="seconds"), "run": self.run_id, "step": step,
               "outcome": outcome, "ticket": ticket, "dispatch": self.cfg.dispatch,
               "landing": self.cfg.landing, "engine": getattr(self.engine, "name", "none"), **detail}
        with self.audit_path.open("a") as fh:
            fh.write(json.dumps(_safe(row), sort_keys=True) + "\n")

    def step(self, name: str, ticket: str = "") -> None:
        try:
            check_kill_switch(self.cfg)
        except Refused as e:
            self.audit(name, "refused", ticket, rule=e.rule)
            raise
        self.audit(name, "start", ticket)

    def checkpoint(self, name: str) -> None:
        """Re-check everything that may have changed since entry."""
        try:
            check_kill_switch(self.cfg)
            check_quiet_hours(self.cfg, self.now())
            check_busy(self.cfg, self.cfg.repo)
        except Refused as e:
            raise Interrupted(name, e.rule) from None

    @property
    def lock_argv(self) -> list:
        c = self.cfg
        return ["flock", "-x", "-w", str(c.test_lock_wait_s), c.test_lock] if c.test_lock else []

    def _wt(self, name: str) -> str:
        return str(Path(self.cfg.worktree_root) / name)

    # --- the run ---
    def run_once(self, *, junit: str | None = None) -> dict:
        try:
            self.step("preflight")
            if self.bin_error:
                raise Refused("unsafe_binary", f"{self.bin_error.name}:{self.bin_error.why}")
            self.audit("preflight", "bins", bins=[f"{k}={v}" for k, v in sorted(self.bins.items())],
                       sandbox=f"{self.sandbox.backend}:{self.sandbox.why}")
            if self.engine_skips:
                self.audit("engine_select", "fallback" if self.engine else "none",
                           chosen=getattr(self.engine, "name", "none"),
                           skipped=[f"{x['engine']}:{x['why']}" for x in self.engine_skips])
            with HealLock(self.cfg):
                now = self.now()
                check_quiet_hours(self.cfg, now)
                check_busy(self.cfg, self.cfg.repo)
                return self._run_locked(now, junit)
        except Refused as e:
            self.audit("refused", e.rule, rule=e.rule)
            return {"outcome": "refused", "rule": e.rule}
        except Exception as e:  # a broken step is reported (by class), never retried in-process
            self.audit("error", type(e).__name__, error=type(e).__name__)
            return {"outcome": "error", "error": type(e).__name__}

    def _base_sha(self) -> str:
        c = self.cfg
        if c.landing == "github":
            gitops.git(c.repo, "fetch", "-q", "origin", c.base_branch, timeout=300)
            return gitops.rev(c.repo, f"origin/{c.base_branch}")
        return gitops.rev(c.repo, f"refs/heads/{c.integration_branch}")

    def _run_locked(self, now: dt.datetime, junit: str | None) -> dict:
        c = self.cfg
        state = State(c, now.date())
        self._recover(state)
        self.step("base")
        base_sha = self._base_sha()
        base_wt = self._wt(f"heal-scan-{self.run_id}")
        gitops.add_worktree(c.repo, base_wt, base=base_sha)
        try:
            self.step("detect")
            xml = Path(junit).read_text() if junit else run_detector(c, base_wt, self.lock_argv)
            if xml is None:
                self.audit("detect", "failed", reason="no_junit")
                return {"outcome": "detect_failed"}
            # A module that fails to COLLECT hides its tests: the lane's picture is incomplete, and the
            # cheapest "fix" (skip / delete the module) is test weakening. Record it, heal nothing.
            broken = collection_errors(xml)
            if broken:
                self.audit("detect", "cannot_reproduce", fault_class=COLLECTION_ERROR, files=broken)
                return {"outcome": COLLECTION_ERROR, "files": broken}
            tickets = failing_test_tickets(xml, base_sha=base_sha, python=c.python, ignore=c.ignore_nodeids, now=now)
            self.audit("detect", "done", found=len(tickets))
            ticket, skipped = None, []
            for t in tickets:
                try:
                    state.check_caps(c, t.fault_key)
                    self._check_not_open(state, t.fault_key)
                    ticket = t
                    break
                except Refused as e:
                    skipped.append(e.rule)
                    if e.rule == "daily_cap":
                        raise
            if ticket is None:
                self.audit("ticket", "none", skipped=skipped)
                return {"outcome": "no_fault" if not tickets else "all_capped", "skipped": skipped}
            state.data["tickets"][ticket.id] = {"ticket": ticket.to_dict(), "status": "planned"}
            state.save()
            self.audit("ticket", "chosen", ticket.id, fault_class=ticket.fault_class,
                       fault_key=ticket.fault_key, test=ticket.evidence.get("nodeid", ""))
            if self.board:
                self.board.on_ticket(state, ticket)
            if self.engine is None:
                reasons = [f"{x['engine']}:{x['why']}" for x in self.engine_skips] or ["engine_none"]
                rec = state.data["tickets"][ticket.id]
                rec.update(status="no_engine", why="no_engine_linked", reasons=reasons)
                state.save()
                self.audit("engine", "none", ticket.id, reason="no_engine_linked", skipped=reasons)
                return {"outcome": "no_engine", "ticket": ticket.id, "why": "no_engine_linked", "reasons": reasons}
            if c.dispatch == "dry":
                plan = ["worktree", f"engine:{self.engine.name}", "inspect", "diff_policy", "gates",
                        "commit", f"then:local=verify,full=land:{c.landing}"]
                self.audit("plan", "dry", ticket.id, plan=plan)
                return {"outcome": "planned", "ticket": ticket.id, "plan": plan}
            if self.sandbox.backend == "none":   # engine code never runs unsandboxed: fail closed
                self.audit("sandbox", "refused", ticket.id, reason=self.sandbox.why)
                return {"outcome": "refused", "rule": "sandbox_unavailable", "why": self.sandbox.why}
            return self._heal(state, ticket, base_wt)
        finally:
            gitops.remove_worktree(c.repo, base_wt)

    def _check_not_open(self, state: State, key: str) -> None:
        """One heal in flight per fault: an open heal PR (github) or an unlanded local heal branch."""
        heads = gitops.heal_branches(self.cfg.repo)
        for tid, rec in state.data["tickets"].items():
            if rec["ticket"]["fault_key"] == key and (rec.get("status") == "pr_open" or (
                    rec.get("status") == "healed_local" and f"self-heal/{tid}" in heads)):
                raise Refused("heal_open", tid)

    def _recover(self, state: State) -> None:
        """Start of every run: finish what an interrupted run left half-done, through the re-checks.
        (f) a local landing whose write-ahead intent is still set is rolled back (idempotent);
        (e) a pushed self-heal branch with no PR is deleted from origin (a PR found = record it)."""
        for tid, rec in list(state.data["tickets"].items()):
            intent, branch = rec.get("landing_intent"), rec.get("pushed_branch")
            if not intent and not (branch and rec.get("status") not in ("pr_open", "healed", "not_healed", "pr_closed")):
                continue
            try:
                self.checkpoint("recover")
            except Interrupted as e:
                raise Refused(e.rule, "recover") from None
            if intent:
                f = state.fault(rec["ticket"]["fault_key"])
                try:
                    restored, err = self.landing.rollback_intent(intent), ""
                except Exception as e:   # noqa: BLE001
                    restored, err = False, type(e).__name__
                if not restored:   # keep the intent; this run (dry included) does nothing else
                    rec["status"], f["rollback_stuck"], f["parked"] = "rollback_stuck", True, True
                    state.save()
                    self.audit("recover", "rollback_stuck", tid, error=err, fault_key=rec["ticket"]["fault_key"])
                    raise Refused("rollback_stuck", tid)
                rec.update(landing_intent=None, status="rolled_back", restored=True)
                f["rollback_stuck"], f["parked"] = False, True
                self.audit("recover", "rolled_back", tid, restored=True)
            elif branch:
                done = self.landing.cleanup_branch(tid, branch)
                if done.startswith("pr:"):
                    rec.update(status="pr_open", pr_url=done[3:])
                rec["pushed_branch"] = None
                self.audit("recover", done.split(":")[0], tid, branch=branch)
            state.save()

    def _gate_names(self) -> list:
        c = self.cfg
        return c.local_gates if (c.dispatch == "full" and c.landing == "local") else c.gates

    def _finish(self, state: State, ticket: Ticket, status: str, **detail) -> dict:
        rec = state.data["tickets"][ticket.id]
        rec["status"] = status
        rec.update(detail)
        f = state.fault(ticket.fault_key)
        f["rollback_stuck"] = status == "rollback_stuck"
        if status in ("not_healed", "rolled_back", "pr_closed", "rollback_stuck"):
            f["not_healed"] += 1
            if status in ("rolled_back", "rollback_stuck") or f["not_healed"] >= self.cfg.not_healed_park_after:
                f["parked"] = True
                self.audit("park", "parked", ticket.id, fault_key=ticket.fault_key)
        state.save()
        names = {"why": "reason", "pr_url": "pr"}
        self.audit("result", status, ticket.id, **{names.get(k, k): v for k, v in detail.items() if k != "gates"})
        if self.board:
            self.board.note(state, ticket, f"{status} {detail.get('why', '')}".strip(),
                            "parked" if f["parked"] else status)
        return {"outcome": status, "ticket": ticket.id, **detail}

    def _heal(self, state: State, ticket: Ticket, base_wt: str) -> dict:
        c = self.cfg
        branch = f"self-heal/{ticket.id}"
        wt = self._wt(f"heal-{ticket.id}")
        state.count_attempt(ticket.fault_key)
        keep = False
        try:
            self.step("worktree", ticket.id)
            gitops.add_worktree(c.repo, wt, base=ticket.base_sha, branch=branch)
            self.checkpoint("engine")
            self.step("engine", ticket.id)
            before = live_fingerprint(c)
            res = self.engine.run(ticket, wt)
            rc = res.detail if res.detail.startswith(("rc=", "timeout")) else "rc=?"
            self.audit("engine", "ok" if res.ok else "failed", ticket.id, rc=rc.split(" ")[0],
                       seconds=round(res.seconds, 1))
            if live_fingerprint(c) != before:
                self.audit("live_guard", "ALARM", ticket.id, reason="live_head_changed")
                return self._finish(state, ticket, "discarded", why="live_checkout_changed")
            if not res.ok:
                return self._finish(state, ticket, "discarded", why="engine_failed")

            # inspect BEFORE anything is staged; a violation discards the worktree, nothing staged
            self.step("diff_policy", ticket.id)
            diff = gitops.inspect_worktree(wt, ticket.base_sha)
            try:
                check_diff(c, diff, ticket_has_repro_test=bool(ticket.repro_files))
            except Refused as e:
                return self._finish(state, ticket, "discarded", why=e.rule, files=diff.files)
            self.audit("diff_policy", "pass", ticket.id, files=diff.files, lines=diff.lines)

            self.checkpoint("gates")
            self.step("gates", ticket.id)
            gates = run_gates(c, self._gate_names(), wt, diff.files, ticket, base_wt=base_wt)
            self.audit("gates", "green" if gates_green(gates) else "red", ticket.id, gates=gates)
            if not gates_green(gates):
                weak = any(str(g.get("detail", "")).startswith("test_weakened") for g in gates)
                return self._finish(state, ticket, "discarded", why="test_weakened" if weak else "gate_failed",
                                    gates=[{"gate": g["gate"], "status": g["status"]} for g in gates])

            self.step("commit", ticket.id)
            gitops.stage_checked(wt, diff.files, ticket.base_sha)
            head = gitops.commit(wt, f"fix(self-heal): {ticket.fault_class} {ticket.evidence.get('nodeid', '')}"[:72] +
                                 f"\n\nSelf-Heal-Ticket: {ticket.id}\nSelf-Heal-Engine: {self.engine.name}\n"
                                 f"Self-Heal-Fault: {ticket.fault_class} {ticket.fault_key}\n")
            self.audit("commit", "done", ticket.id, sha=head)
            keep = True   # the commit is evidence: the owner can inspect the branch
            verify_path = self._wt(f"heal-{ticket.id}-verify")
            if c.dispatch == "local":
                self.step("verify", ticket.id)
                r = gitops.run_repro(c, c.repo, head, ticket, verify_path, self.lock_argv)
                return self._finish(state, ticket, "healed_local" if r.ok else "not_healed",
                                    sha=head, verify=r.detail, branch=branch)

            self.checkpoint("land")
            self.step("land", ticket.id)
            rec = state.data["tickets"][ticket.id]
            ctx = LandCtx(ticket, base_wt, wt, head, self.engine.name, gates, diff, verify_path,
                          self.lock_argv, self.checkpoint, lambda **kw: (rec.update(kw), state.save()))
            lr = self.landing.land(ctx)
            self.audit("land", lr.status, ticket.id, reason=lr.detail.split(" ")[0], review=lr.review,
                       pr=lr.pr_url, **lr.extra)
            if c.landing == "local":
                keep = False   # landed or rolled back: the integration branch / snapshot ref hold it
            done = self._finish(state, ticket, lr.status, sha=head, branch=branch, pr_url=lr.pr_url,
                                review=lr.review, why=lr.detail.split(" ")[0], verify=lr.detail)
            if lr.exc is not None and not isinstance(lr.exc, Exception):
                raise lr.exc   # Ctrl-C / SystemExit still stop the process, after rollback + record
            return done
        except (Interrupted, Refused) as e:   # a step's own kill-switch refusal counts too
            keep = False
            cp = getattr(e, "checkpoint", "step")
            state.refund_attempt(ticket.fault_key)
            self.audit("interrupted", e.rule, ticket.id, checkpoint=cp, reason=e.rule)
            return self._finish(state, ticket, "interrupted", why=e.rule, checkpoint=cp)
        finally:
            if not keep:
                gitops.remove_worktree(c.repo, wt, branch=branch)

    # --- close the loop for github heals, after merge + deploy ---
    def verify_pending(self) -> list:
        c, out = self.cfg, []
        with HealLock(c):
            check_quiet_hours(c, self.now())
            check_busy(c, c.repo)
            state = State(c, self.now().date())
            for tid, rec in list(state.data["tickets"].items()):
                if rec.get("status") != "pr_open":
                    continue
                try:   # re-check before EACH item; a failure stops the pass cleanly, nothing spent
                    self.checkpoint("verify")
                except Interrupted as e:
                    self.audit("interrupted", e.rule, tid, checkpoint=e.checkpoint, reason=e.rule)
                    out.append({"ticket": tid, "outcome": "interrupted", "why": e.rule})
                    break
                self.step("verify", tid)
                ticket = Ticket.from_dict(rec["ticket"])
                sha = self.landing.merged_commit(rec["pr_url"])
                if sha is False:   # closed unmerged: a reviewer said no; the fault may be retried (caps apply)
                    out.append(self._finish(state, ticket, "pr_closed"))
                    continue
                deployed_ref = f"refs/heads/{c.base_branch}"   # what the deploy fast-forwards
                if not sha or gitops.run(["git", "-C", c.repo, "merge-base", "--is-ancestor", sha, deployed_ref],
                                         check=False).returncode != 0:
                    self.audit("verify", "pending", tid, reason="not_merged_or_deployed")
                    out.append({"ticket": tid, "outcome": "pending"})
                    continue
                r = gitops.run_repro(c, c.repo, deployed_ref, ticket, self._wt(f"heal-{tid}-verify"), self.lock_argv)
                out.append(self._finish(state, ticket, "healed" if r.ok else "not_healed", verify=r.detail))
        return out
