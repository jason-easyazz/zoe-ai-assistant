"""Hermes Kanban executor adapter.

Turns a Multica issue into a durable implement -> review -> closeout chain on
the Hermes Kanban board, where OpenRouter-routed worker profiles do the work. All
Kanban-specific behaviour lives here so it never leaks into Zoe core.

Boundary: this shells the ``hermes kanban`` CLI (same SQLite DB the in-gateway
dispatcher reads) rather than importing Hermes internals — keeping Zoe
surface-agnostic.

Worker profiles + pinned skills encode Zoe's agentic-engineering loop:
  - implement (zoe-coder):  zoe-engineering, zoe-graphify, source-code-context,
                            code-structure-cleanup  (graph-first, opensrc, lean)
  - verify    (zoe-reviewer): zoe-engineering           (tests/evidence gate)
  - review    (zoe-reviewer): zoe-engineering           (verification gate)
  - closeout  (zoe-planner):  github-greptile-loop      (grep-loop, merge, Multica done)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
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
    ("verify", "zoe-reviewer", ("zoe-engineering",)),
    ("review", "zoe-reviewer", ("zoe-engineering",)),
    ("closeout", "zoe-planner", ("github-greptile-loop",)),
)
_LEGACY_CHAIN_PHASES = ("implement", "review", "closeout")

_ACTIVE_KANBAN_STATUSES = {"triage", "todo", "ready", "running", "blocked"}
_TERMINAL_KANBAN_STATUSES = {"done", "archived"}

# Matches the `zoe-ref: multica:{id}:{phase}` marker the adapter writes into each
# task body at dispatch. Anchored to the start of a line so it never collides with
# prose elsewhere in the body.
_REF_MARKER_RE = re.compile(r"^zoe-ref:\s*(\S+)", re.MULTILINE)


def _row_ref_key(row: dict) -> str:
    """Correlate a Kanban list row back to its ``multica:{id}:{phase}`` ref.

    The live ``hermes kanban list --json`` output does NOT expose the idempotency
    key, so poll() cannot filter on it. We parse the ``zoe-ref:`` marker the adapter
    writes into the task body instead. A top-level ``idempotency_key`` field is
    preferred when present so this stays correct if a future CLI exposes it.
    """
    key = (row or {}).get("idempotency_key") or ""
    if key:
        return key
    match = _REF_MARKER_RE.search((row or {}).get("body") or "")
    return match.group(1) if match else ""


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
    env = os.environ.get("ZOE_REPO_ROOT", "").strip()
    if env:
        return env
    # Derive the repo root from this file's location so non-standard deployments
    # (different user/install path) work without ZOE_REPO_ROOT set:
    # services/zoe-data/executors/kanban_adapter.py -> repo root is 3 levels up.
    return str(Path(__file__).resolve().parents[3])


def _board() -> str:
    return os.environ.get("ZOE_KANBAN_BOARD", "default")


def _greptile_mcp_bin() -> str:
    """Locate the operator-local greptile MCP CLI; honour GREPTILE_MCP_BIN override.

    This is an operator-installed binary (not in the repo), so mirror the
    ``_hermes_bin`` pattern: prefer the env override, fall back to the standard
    install path. Keeps the closeout worker portable across hosts/users instead
    of silently stalling the Greptile loop when the path differs.
    """
    override = os.environ.get("GREPTILE_MCP_BIN", "").strip()
    if override:
        return override
    return os.path.expanduser("~/bin/greptile-mcp.py")


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
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=_repo_root(),
                env=env,
            )
        except OSError as exc:
            raise KanbanCLIError(
                f"`hermes kanban {' '.join(args)}` could not start: {exc}"
            ) from exc
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
        # `zoe-ref:` is a machine marker that lets poll() correlate this task back
        # to its Multica issue + phase. The live `hermes kanban list --json` output
        # does NOT expose the idempotency key, so the body (which it does expose) is
        # the durable correlation channel. Strip the id the same way dispatch() does
        # so the marker stays byte-identical to the --idempotency-key (otherwise a
        # whitespace-padded id would desync the marker from external_ref and poll()
        # would never match): multica:{id}:{phase}.
        issue_id = str(issue.get("id") or "").strip()
        common = (
            f"Multica issue: {identifier} (id {issue_id})\n"
            f"zoe-ref: multica:{issue_id}:{phase}\n"
            f"Repo: {_repo_root()}  |  Base branch: main  |  Workspace: git worktree\n\n"
            f"Title: {title}\n\n{description}\n\n"
        )
        if phase == "implement":
            return common + (
                "You are the implementer (zoe-coder). Start with `kanban_show` to confirm this task id.\n"
                "- Read the charter + graphify map first (graphify query/path/explain over raw grep).\n"
                "- Use opensrc for any third-party library source before guessing APIs.\n"
                "- Make the smallest reviewable change; do NOT rewrite existing functions into bloat;"
                " reuse service-layer helpers.\n"
                "- If the task needs more than one PR or a large refactor, call `kanban_block` and"
                " ask for a split — do not absorb unbounded work in one implement run.\n"
                "- Pure audit/doc issues (title mentions audit/map/trace with no code change):"
                " `kanban_complete` with PR_URL= blank, AUDIT_ONLY=1, and SUMMARY= findings —"
                " do not open a PR or keep exploring past turn budget.\n"
                "- Validate: `python3 tools/audit/validate_structure.py` and focused tests for touched modules.\n"
                "- You already run on an isolated git worktree branch. Commit verified changes, then publish"
                " the branch and open ONE small PR (do not merge) with EXACTLY these commands:\n"
                "    git push -u origin HEAD\n"
                "    gh pr create --base main --title \"<identifier>: <short>\" --body \"<summary>\"\n"
                "  (`gh pr create` defaults --head to the current branch, so do not pass --head.)\n"
                "  A bare `git push` (no `-u origin HEAD`) FAILS with exit 128 on a fresh worktree branch"
                " (no upstream) and the PR never opens — always push with `-u origin HEAD`.\n"
                "- Capture the PR URL that `gh pr create` prints and report it verbatim as PR_URL=. The"
                " closeout phase runs in a SEPARATE worktree and can only act on a PR that is pushed to origin.\n"
                "- Stop and call `kanban_block(reason=BLOCKER=...)` for dirty tree, missing auth/secrets,"
                " destructive ops, DB/docker changes needing approval, or ambiguous product decisions.\n"
                "- If the same test or check fails after TWO fix attempts, or you are near your turn budget,"
                " call `kanban_block` with BLOCKER= and the failing output — do not keep iterating.\n\n"
                "TERMINAL PROTOCOL (non-negotiable): before your last turn you MUST call either"
                " `kanban_complete(summary=..., metadata={...})` OR `kanban_block(reason=...)`."
                " Exiting without either is a protocol violation and the dispatcher will retry forever.\n"
                "- Success: `kanban_complete` after push+PR with metadata including PR_URL, TESTS, SUMMARY.\n"
                "- Failure/stuck: `kanban_block` with a clear reason (prefix BLOCKER= when applicable).\n\n"
                "Summary text should still include:\n"
                "PR_URL=<url or blank>\nBLOCKER=<reason or blank>\nTESTS=<checks run>\nSUMMARY=<short>\n"
                "Changed files + branch/worktree details for the reviewer."
            )
        if phase == "verify":
            return common + (
                "You are verify (zoe-reviewer). This is the objective test/evidence gate before review.\n"
                "- Start with `kanban_show` to read the implementer handoff and PR_URL.\n"
                "- Do not redesign or refactor. Run the declared tests and the minimum extra checks needed"
                " for the touched surface.\n"
                "- Required evidence in your final `kanban_complete` metadata: TESTS, VALIDATORS,"
                " PR_URL, and a pass/fail summary. Include exact commands and outcomes.\n"
                "- If tests fail, evidence is missing, the PR is absent for a code task, or the task needs"
                " product clarification, call `kanban_block` with BLOCKER= and the failing output.\n"
                "- TERMINAL PROTOCOL: end with `kanban_complete` or `kanban_block` (no silent exit)."
            )
        if phase == "review":
            return common + (
                "You are the reviewer (zoe-reviewer). Review the diff, scope, and verify-phase evidence.\n"
                "- Confirm the change is small and in scope. Audit-only / doc-only handoffs with blank PR_URL"
                " need a short verification note, then `kanban_complete` — do not re-implement or burn turns"
                " re-exploring the tree.\n"
                "- Do not approve if verify-phase evidence is missing, stale, or inconsistent with the diff."
                " Block with a concrete reason instead.\n"
                "- TERMINAL PROTOCOL: end with `kanban_complete` or `kanban_block` (no silent exit).\n"
                "- Do NOT approve if verification or the Greptile gate is unavailable; set the task blocked"
                " with an explicit reason instead.\n"
                "- Record findings (pass/fail/concern) and a merge-readiness verdict in your handoff."
            )
        # closeout
        return common + (
            "You are closeout (zoe-planner, orchestration only).\n"
            "- Read the implementer's PR_URL handoff and extract the PR number N (the trailing /pull/N)."
            " If AUDIT_ONLY=1 or audit-only with blank PR_URL, `kanban_complete` and note Multica done"
            " — no grep loop or merge.\n"
            " If a code task has no PR, leave the issue blocked — do NOT open one yourself.\n"
            "- Address every substantive Greptile finding (fix_now or won't_fix with reason). Do not stop at"
            " merge-ready — merge when gates pass.\n"
            "- Drive the Greptile grep loop with the pinned github-greptile-loop skill (guard REQUIRES --pr N;"
            " <=5 rounds, target confidence 5). Each round:\n"
            "    python3 scripts/maintenance/greploop_guard.py --pr N --once\n"
            "  Apply fixes yourself when the guard returns ESCALATE_HERMES or PACKET_READY; use --packet-only"
            " only to hand off to a cheap Cursor runner.\n"
            "  Re-trigger review when needed via"
            f" `{_greptile_mcp_bin()} trigger-review jason-easyazz/zoe-ai-assistant N`"
            " (operator-local binary; override with GREPTILE_MCP_BIN).\n"
            "- When Greptile is clear (confidence 5/5, no unaddressed findings) and CI is green, squash-merge"
            " via normal GitHub (never --admin, never force, never --no-verify):\n"
            "    python3 scripts/maintenance/greploop_guard.py --pr N --merge-when-ready\n"
            "  Or one iteration then merge: --once --merge-when-ready\n"
            "  If branch protection blocks merge, leave the issue blocked with the gh error — do not admin-merge.\n"
            "- After a successful merge: update the Multica issue to done with PR_URL, merge SHA, GREPTILE status,"
            " and summary. If merge did not happen, leave in_progress/blocked with the blocker.\n"
            "Final handoff MUST include:\nPR_URL=<url>\nMERGE_SHA=<sha or blank>\nGREPTILE=<status>\n"
            "MULTICA=<updated? done/blocked>\nSUMMARY=<short>"
        )

    async def dispatch(self, issue: dict) -> dict:
        """Create (idempotently) the implement->verify->review->closeout chain for a Multica issue.

        Returns {ok, external_ref, chain:{phase:task_id}, created:[phases]}.
        """
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            return {"ok": False, "reason": "issue has no id"}
        identifier = issue.get("identifier") or issue.get("title") or issue_id
        # external_ref prefix for idempotency keys; full key is "multica:{issue_id}:{phase}" (multica:{id}:<phase>).
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
        if phase == "verify":
            return f"Verify {identifier}"[:140]
        return f"Closeout {identifier}"[:140]

    async def poll(self, external_ref: str) -> dict:
        """Report aggregate state of a chain by idempotency-key prefix.

        Returns {found, status, phases:{phase:status}, pr_url, blocker}.
        status is one of: running | blocked | done | partial | not_found.

        ``partial`` means some but not all chain phases exist (e.g. a CLI error
        interrupted chain creation). Callers treat it as re-dispatchable so the
        idempotent ``dispatch`` can backfill the missing phases, rather than
        leaving the chain wedged in ``running`` forever.
        """
        # `hermes kanban list` has no idempotency-prefix filter flag (and does not
        # even surface the idempotency key), so we pull the board once and correlate
        # each row via _row_ref_key (the `zoe-ref:` body marker) in Python. This is
        # O(board size) per candidate; acceptable while the board is small. Revisit
        # (push the filter into the CLI) if the board grows large enough to matter.
        tasks = await self._run(["list", "--json"], expect_json=True)
        rows = tasks if isinstance(tasks, list) else (tasks or {}).get("tasks", [])
        prefix = f"{external_ref}:"
        phases: dict[str, dict] = {}
        for row in rows:
            key = _row_ref_key(row)
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
        expected = set(tuple(p for p, _, _ in _CHAIN) if "verify" in phases else _LEGACY_CHAIN_PHASES)
        missing = expected - set(phases)
        if (closeout.get("status") or "") in _TERMINAL_KANBAN_STATUSES:
            agg = "done"
        elif blocker:
            agg = "blocked"
        elif missing:
            # Some phases never got created (e.g. CLI error mid-chain). Report
            # "partial" so the sync path re-dispatches and idempotently backfills
            # the missing phases instead of skipping it as "running" forever.
            agg = "partial"
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
        for phase in ("closeout", "implement", "verify", "review"):
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
