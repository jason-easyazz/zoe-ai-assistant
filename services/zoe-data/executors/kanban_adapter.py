"""Hermes Kanban executor adapter.

Turns a Multica issue into a durable implement -> review -> closeout chain on
the Hermes Kanban board, where cheap DeepSeek worker profiles do the work. All
Kanban-specific behaviour lives here so it never leaks into Zoe core.

Boundary: this shells the ``hermes kanban`` CLI (same SQLite DB the in-gateway
dispatcher reads) rather than importing Hermes internals — keeping Zoe
surface-agnostic.

Worker profiles + pinned skills encode Zoe's agentic-engineering loop:
  - implement (zoe-coder):  zoe-engineering, zoe-graphify, source-code-context,
                            code-structure-cleanup  (graph-first, opensrc, lean)
  - review    (zoe-reviewer): zoe-engineering           (verification gate)
  - closeout  (zoe-planner):  github-greptile-loop      (grep-loop until merge-ready)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from typing import Any

logger = logging.getLogger(__name__)

NAME = "kanban"

# Phases of the per-issue chain, in order. Each maps to a worker profile and the
# skills it must load. Keys double as the idempotency-key suffix (multica:{id}:<phase>).
_CHAIN = (
    (
        "implement",
        "zoe-coder",
        ("zoe-engineering", "zoe-graphify", "source-code-context", "code-structure-cleanup"),
    ),
    ("review", "zoe-reviewer", ("zoe-engineering",)),
    ("closeout", "zoe-planner", ("github-greptile-loop",)),
)

_ACTIVE_KANBAN_STATUSES = {"triage", "todo", "ready", "running", "blocked"}
_TERMINAL_KANBAN_STATUSES = {"done", "archived"}


def _hermes_bin() -> str:
    """Locate the hermes CLI; honour HERMES_BIN override."""
    override = os.environ.get("HERMES_BIN", "").strip()
    if override:
        return override
    found = shutil.which("hermes")
    if found:
        return found
    candidate = os.path.expanduser("~/.local/bin/hermes")
    return candidate


def _repo_root() -> str:
    return os.environ.get("ZOE_REPO_ROOT", "/home/zoe/assistant")


def _board() -> str:
    return os.environ.get("ZOE_KANBAN_BOARD", "default")


def _max_runtime() -> str:
    return os.environ.get("ZOE_KANBAN_MAX_RUNTIME", "45m")


class KanbanCLIError(RuntimeError):
    """Raised when a hermes kanban CLI call fails."""


class KanbanAdapter:
    """Executor adapter backed by the Hermes Kanban board."""

    name = NAME

    async def _run(self, args: list[str], *, expect_json: bool = False) -> Any:
        cmd = [_hermes_bin(), "kanban", "--board", _board(), *args]
        env = dict(os.environ)
        env.setdefault("HERMES_KANBAN_BOARD", _board())
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_repo_root(),
            env=env,
        )
        out, err = await proc.communicate()
        stdout = (out or b"").decode("utf-8", errors="replace").strip()
        stderr = (err or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            raise KanbanCLIError(
                f"`hermes kanban {' '.join(args)}` exited {proc.returncode}: {stderr or stdout}"
            )
        if not expect_json:
            return stdout
        try:
            return json.loads(stdout) if stdout else {}
        except json.JSONDecodeError as exc:
            raise KanbanCLIError(f"non-JSON output from kanban {args[0]}: {exc}: {stdout[:200]}")

    def _build_body(self, phase: str, issue: dict, identifier: str) -> str:
        title = issue.get("title") or identifier
        description = issue.get("description") or ""
        common = (
            f"Multica issue: {identifier} (id {issue.get('id')})\n"
            f"Repo: {_repo_root()}  |  Base branch: main  |  Workspace: git worktree\n\n"
            f"Title: {title}\n\n{description}\n\n"
        )
        if phase == "implement":
            return common + (
                "You are the implementer (zoe-coder).\n"
                "- Read the charter + graphify map first (graphify query/path/explain over raw grep).\n"
                "- Use opensrc for any third-party library source before guessing APIs.\n"
                "- Make the smallest reviewable change; do NOT rewrite existing functions into bloat;"
                " reuse service-layer helpers.\n"
                "- Validate: `python3 tools/audit/validate_structure.py` and focused tests for touched modules.\n"
                "- Create a feature branch, commit verified changes, push, and open a small PR (do not merge).\n"
                "- Stop and report BLOCKER=... for dirty tree, missing auth/secrets, destructive ops,"
                " DB/docker changes needing approval, or ambiguous product decisions.\n\n"
                "Final kanban handoff MUST include:\n"
                "PR_URL=<url or blank>\nBLOCKER=<reason or blank>\nTESTS=<checks run>\nSUMMARY=<short>\n"
                "Changed files + branch/worktree details for the reviewer."
            )
        if phase == "review":
            return common + (
                "You are the reviewer (zoe-reviewer). Verification-first — a task is not done until verified.\n"
                "- Confirm the change is small and in scope.\n"
                "- Run `validate_structure.py`, `validate_critical_files.py`, focused tests,"
                " and live `/health` + `/api/system/status`.\n"
                "- Do NOT approve if verification or the Greptile gate is unavailable; set the task blocked"
                " with an explicit reason instead.\n"
                "- Record findings (pass/fail/concern) and a merge-readiness verdict in your handoff."
            )
        # closeout
        return common + (
            "You are closeout (zoe-planner, orchestration only).\n"
            "- Confirm the small PR exists; run the Greptile grep loop"
            " (github-greptile-loop / greploop_guard.py --packet-only, <=3 rounds, target confidence 5).\n"
            "- Only when CI is green, Greptile is clear, and the reviewer approved: update the Multica issue"
            " to done with PR link + summary. Otherwise leave it in_progress/blocked with the reason.\n"
            "Final handoff MUST include:\nPR_URL=<url>\nGREPTILE=<status>\nMULTICA=<updated? done/blocked>\nSUMMARY=<short>"
        )

    async def dispatch(self, issue: dict) -> dict:
        """Create (idempotently) the implement->review->closeout chain for a Multica issue.

        Returns {ok, external_ref, chain:{phase:task_id}, created:[phases]}.
        """
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            return {"ok": False, "reason": "issue has no id"}
        identifier = issue.get("identifier") or issue.get("title") or issue_id
        external_ref = f"multica:{issue_id}"

        chain: dict[str, str] = {}
        created: list[str] = []
        parent: str | None = None
        for phase, assignee, skills in _CHAIN:
            args = [
                "create",
                self._title(phase, identifier, issue),
                "--assignee",
                assignee,
                "--workspace",
                "worktree",
                "--idempotency-key",
                f"{external_ref}:{phase}",
                "--max-runtime",
                _max_runtime(),
                "--created-by",
                "zoe-bridge",
                "--body",
                self._build_body(phase, issue, identifier),
                "--json",
            ]
            for skill in skills:
                args += ["--skill", skill]
            if parent:
                args += ["--parent", parent]
            result = await self._run(args, expect_json=True)
            task_id = (result or {}).get("id") or (result or {}).get("task", {}).get("id")
            if not task_id:
                raise KanbanCLIError(f"create returned no id for phase={phase}: {result}")
            chain[phase] = task_id
            if not (result or {}).get("deduplicated"):
                created.append(phase)
            parent = task_id

        logger.info(
            "kanban_adapter: dispatched %s -> chain=%s (new=%s)", identifier, chain, created
        )
        return {"ok": True, "external_ref": external_ref, "chain": chain, "created": created}

    def _title(self, phase: str, identifier: str, issue: dict) -> str:
        base = issue.get("title") or identifier
        if phase == "implement":
            return f"{identifier}: {base}"[:140]
        if phase == "review":
            return f"Review {identifier}"[:140]
        return f"Closeout {identifier}"[:140]

    async def poll(self, external_ref: str) -> dict:
        """Report aggregate state of a chain by idempotency-key prefix.

        Returns {found, status, phases:{phase:status}, pr_url, blocker}.
        status is one of: running | blocked | done | not_found.
        """
        # `hermes kanban list` has no idempotency-prefix filter flag, so we pull
        # the board once and filter by prefix in Python. This is O(board size)
        # per candidate; acceptable while the board is small. Revisit (push the
        # filter into the CLI) if the board grows large enough to matter.
        tasks = await self._run(["list", "--json"], expect_json=True)
        rows = tasks if isinstance(tasks, list) else (tasks or {}).get("tasks", [])
        prefix = f"{external_ref}:"
        phases: dict[str, dict] = {}
        for row in rows:
            key = (row or {}).get("idempotency_key") or ""
            if key.startswith(prefix):
                phases[key[len(prefix):]] = row
        if not phases:
            return {"found": False, "status": "not_found", "phases": {}, "pr_url": None, "blocker": None}

        statuses = {p: (r.get("status") or "") for p, r in phases.items()}
        blocker = None
        for p, r in phases.items():
            if (r.get("status") or "") == "blocked":
                blocker = r.get("block_reason") or f"{p} blocked"
                break

        closeout = phases.get("closeout", {})
        if (closeout.get("status") or "") in _TERMINAL_KANBAN_STATUSES:
            agg = "done"
        elif blocker:
            agg = "blocked"
        else:
            agg = "running"

        return {
            "found": True,
            "status": agg,
            "phases": statuses,
            "pr_url": await self._extract_pr_url(phases),
            "blocker": blocker,
        }

    async def _extract_pr_url(self, phases: dict[str, dict]) -> str | None:
        """Pull a PR URL from the implement/closeout task summaries or comments."""
        pattern = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+")
        for phase in ("closeout", "implement", "review"):
            row = phases.get(phase)
            if not row:
                continue
            task_id = row.get("id")
            if not task_id:
                continue
            try:
                detail = await self._run(["show", task_id, "--json"], expect_json=True)
            except KanbanCLIError:
                continue
            haystacks = [json.dumps(detail.get("latest_summary") or "")]
            for c in detail.get("comments", []) or []:
                haystacks.append(str(c.get("body") or c.get("text") or ""))
            for h in haystacks:
                m = pattern.search(h)
                if m:
                    return m.group(0)
        return None
