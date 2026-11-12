"""One heal run: preflight -> detect -> ticket -> (dry: plan and stop) -> worktree -> engine ->
diff policy -> gates -> commit -> (local: verify the commit) | (full: land) -> audit.

The kill switch is checked before EVERY step. Every step is appended to audit.jsonl (no
household content, no secrets: ids, paths, counts, rc lines).
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import gitops
from .config import Config
from .detectors import Ticket, run_failing_test_detector, failing_test_tickets
from .engines import select_engine
from .gates import gates_green, run_gates
from .board import make_board
from .landing import make_landing
from .safety import (HealLock, Refused, State, check_busy, check_diff, check_kill_switch,
                     check_quiet_hours, live_fingerprint)


@dataclass
class LandCtx:
    ticket: Ticket
    base_wt: str
    heal_wt: str
    head_sha: str
    engine: str
    gates: list
    diff: object
    evidence: dict
    verify_path: str
    lock: list = field(default_factory=list)


class Runner:
    def __init__(self, cfg: Config, *, engine=None, applier=None, now=None, board="auto"):
        self.cfg = cfg
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
        row = {"ts": self.now().isoformat(timespec="seconds"), "run": self.run_id, "step": step,
               "outcome": outcome, "ticket": ticket, "dispatch": self.cfg.dispatch,
               "landing": self.cfg.landing, "engine": getattr(self.engine, "name", "none")}
        row.update({k: (v[:400] if isinstance(v, str) else v) for k, v in detail.items()})
        with self.audit_path.open("a") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def step(self, name: str, ticket: str = "") -> None:
        try:
            check_kill_switch(self.cfg)
        except Refused as e:
            self.audit(name, "refused", ticket, rule=e.rule, why=e.detail)
            raise
        self.audit(name, "start", ticket)

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
            if self.engine_skips:
                self.audit("engine_select", "fallback" if self.engine else "none",
                           chosen=getattr(self.engine, "name", "none"), skipped=self.engine_skips)
            with HealLock(self.cfg):
                now = self.now()
                check_quiet_hours(self.cfg, now)
                check_busy(self.cfg, self.cfg.repo)
                return self._run_locked(now, junit)
        except Refused as e:
            self.audit("refused", e.rule, why=e.detail)
            return {"outcome": "refused", "rule": e.rule, "detail": e.detail}
        except Exception as e:  # a broken step is reported, never retried in-process
            self.audit("error", type(e).__name__, why=str(e))
            return {"outcome": "error", "detail": f"{type(e).__name__}: {e}"[:400]}

    def _base_sha(self) -> str:
        c = self.cfg
        if c.landing == "github":
            gitops.git(c.repo, "fetch", "-q", "origin", c.base_branch, timeout=300)
            return gitops.rev(c.repo, f"origin/{c.base_branch}")
        return gitops.rev(c.repo, f"refs/heads/{c.integration_branch}")

    def _run_locked(self, now: dt.datetime, junit: str | None) -> dict:
        c = self.cfg
        state = State(c, now.date())
        self.step("base")
        base_sha = self._base_sha()
        base_wt = self._wt(f"heal-scan-{self.run_id}")
        gitops.add_worktree(c.repo, base_wt, base=base_sha)
        try:
            self.step("detect")
            if junit:
                tickets = failing_test_tickets(Path(junit).read_text(), base_sha=base_sha, python=c.python,
                                               ignore=c.ignore_nodeids, now=now)
            else:
                tickets = run_failing_test_detector(base_wt, c.detect_cmd, timeout=c.detect_timeout_s,
                                                    base_sha=base_sha, python=c.python, ignore=c.ignore_nodeids,
                                                    now=now, lock=self.lock_argv)
            self.audit("detect", "done", found=len(tickets))
            ticket, skipped = None, []
            for t in tickets:
                try:
                    state.check_caps(c, t.fault_key)
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
                       fault_key=ticket.fault_key, repro=" ".join(ticket.repro_cmd))
            if self.board:
                self.board.on_ticket(state, ticket)
            if self.engine is None:
                why = "no engine linked: " + ("; ".join(f"{x['engine']}: {x['why']}" for x in self.engine_skips)
                                              or "engine is none")
                state.data["tickets"][ticket.id]["status"] = "no_engine"
                state.data["tickets"][ticket.id]["why"] = why
                state.save()
                self.audit("engine", "none", ticket.id, why=why)
                return {"outcome": "no_engine", "ticket": ticket.id, "why": why}
            if c.dispatch == "dry":
                plan = ["worktree", f"engine:{self.engine.name}", "diff-policy", "gates", "commit",
                        f"then local: verify the commit | full: land via {c.landing}"]
                self.audit("plan", "dry", ticket.id, plan=plan)
                return {"outcome": "planned", "ticket": ticket.id, "plan": plan}
            return self._heal(state, ticket, base_wt)
        finally:
            gitops.remove_worktree(c.repo, base_wt)

    def _gate_names(self) -> list:
        c = self.cfg
        return c.local_gates if (c.dispatch == "full" and c.landing == "local") else c.gates

    def _finish(self, state: State, ticket: Ticket, status: str, **detail) -> dict:
        rec = state.data["tickets"][ticket.id]
        rec["status"] = status
        rec.update(detail)
        f = state.fault(ticket.fault_key)
        if status in ("not_healed", "rolled_back"):
            f["not_healed"] += 1
            if status == "rolled_back" or f["not_healed"] >= self.cfg.not_healed_park_after:
                f["parked"] = True
                self.audit("park", "parked", ticket.id, fault_key=ticket.fault_key)
        state.save()
        self.audit("result", status, ticket.id, **{k: v for k, v in detail.items() if k != "gates"})
        if self.board:
            why = detail.get("why") or detail.get("verify") or detail.get("pr_url") or ""
            self.board.note(state, ticket, f"{status} {why}".strip(),
                            "parked" if f["parked"] else status)
        return {"outcome": status, "ticket": ticket.id, **detail}

    def _heal(self, state: State, ticket: Ticket, base_wt: str) -> dict:
        c = self.cfg
        branch = f"self-heal/{ticket.id}"
        wt = self._wt(f"heal-{ticket.id}")
        state.count_attempt(ticket.fault_key)
        self.step("worktree", ticket.id)
        gitops.add_worktree(c.repo, wt, base=ticket.base_sha, branch=branch)
        keep = False
        try:
            self.step("engine", ticket.id)
            before = live_fingerprint(c)
            res = self.engine.run(ticket, wt)
            self.audit("engine", "ok" if res.ok else "failed", ticket.id, detail=res.detail,
                       seconds=round(res.seconds, 1))
            if live_fingerprint(c) != before:
                self.audit("live_guard", "ALARM", ticket.id, why="live checkout HEAD changed during the engine run")
                return self._finish(state, ticket, "discarded", why="live checkout changed (alarm)")
            if not res.ok:
                return self._finish(state, ticket, "discarded", why=f"engine: {res.detail}")

            self.step("diff_policy", ticket.id)
            diff = gitops.diff_report(wt, ticket.base_sha)
            try:
                check_diff(c, diff, ticket_has_repro_test=bool(ticket.repro_files))
            except Refused as e:
                return self._finish(state, ticket, "discarded", why=f"{e.rule}: {e.detail}", files=diff.files)
            self.audit("diff_policy", "pass", ticket.id, files=diff.files, lines=diff.lines)

            self.step("gates", ticket.id)
            gates = run_gates(c, self._gate_names(), wt, diff.files, ticket)
            self.audit("gates", "green" if gates_green(gates) else "red", ticket.id, gates=gates)
            if not gates_green(gates):
                return self._finish(state, ticket, "discarded", why="gate failed", gates=gates)

            self.step("commit", ticket.id)
            head = gitops.commit(wt, f"fix(self-heal): {ticket.summary}"[:72] +
                                 f"\n\nSelf-Heal-Ticket: {ticket.id}\nSelf-Heal-Engine: {self.engine.name}\n"
                                 f"Self-Heal-Fault: {ticket.fault_class} {ticket.fault_key}\n")
            self.audit("commit", "done", ticket.id, sha=head)
            keep = True   # the commit is evidence: the owner can inspect the branch
            verify_path = self._wt(f"heal-{ticket.id}-verify")
            if c.dispatch == "local":
                self.step("verify", ticket.id)
                r = gitops.run_repro(c, c.repo, head, ticket, verify_path, self.lock_argv)
                return self._finish(state, ticket, "healed_local" if r.ok else "not_healed",
                                    sha=head, verify=r.detail, branch=branch, gates=gates)

            self.step("land", ticket.id)
            ctx = LandCtx(ticket, base_wt, wt, head, self.engine.name, gates, diff,
                          {"dispatch": c.dispatch, "gates_green": True}, verify_path, self.lock_argv)
            lr = self.landing.land(ctx)
            self.audit("land", lr.status, ticket.id, detail=lr.detail, review=lr.review, pr=lr.pr_url, **lr.extra)
            if c.landing == "local":
                keep = False   # landed or rolled back: the integration branch / snapshot ref hold it
            return self._finish(state, ticket, lr.status, sha=head, branch=branch, pr_url=lr.pr_url,
                                review=lr.review, why=lr.detail)
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
                self.step("verify", tid)
                ticket = Ticket.from_dict(rec["ticket"])
                sha = self.landing.merged_commit(rec["pr_url"])
                deployed_ref = f"refs/heads/{c.base_branch}"   # what the deploy fast-forwards
                if not sha or gitops.run(["git", "-C", c.repo, "merge-base", "--is-ancestor", sha, deployed_ref],
                                         check=False).returncode != 0:
                    self.audit("verify", "pending", tid, why="not merged or not deployed yet")
                    out.append({"ticket": tid, "outcome": "pending"})
                    continue
                r = gitops.run_repro(c, c.repo, deployed_ref, ticket, self._wt(f"heal-{tid}-verify"), self.lock_argv)
                out.append(self._finish(state, ticket, "healed" if r.ok else "not_healed", verify=r.detail))
        return out
