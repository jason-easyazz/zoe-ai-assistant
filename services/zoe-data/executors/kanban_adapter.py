"""Hermes Kanban executor adapter.

Turns a Multica issue into a durable evidence-gated engineering run. New runs
create only the current ready phase; pipeline_store owns phase advancement and a
later dispatch call creates the next phase. Legacy in-flight chains are still
recognized while the board drains.

Boundary: this shells the ``hermes kanban`` CLI (same SQLite DB the in-gateway
dispatcher reads) rather than importing Hermes internals — keeping Zoe
surface-agnostic.

Worker profiles + pinned skills encode Zoe's agentic-engineering loop:
  - scout     (zoe-planner):  zoe-graphify, zoe-engineering     (read-only context)
  - implement (zoe-coder):  zoe-engineering             (no preloaded skill for audit/no-PR)
  - verify    (zoe-reviewer): zoe-engineering           (no preloaded skill for audit/no-PR)
  - review    (zoe-reviewer): zoe-engineering           (no preloaded skill for audit/no-PR)
  - closeout  (zoe-planner):  github-greptile-loop      (no preloaded skill for audit/no-PR)
  - retro     (zoe-planner):  zoe-status-refresh        (no preloaded skill for audit/no-PR)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from hermes_http import hermes_bin, zoe_repo_root
from kanban_phase_budget import phase_budget_reason, terminate_running_workers

logger = logging.getLogger(__name__)

NAME = "kanban"

# Phases of the per-issue run, in order. Each maps to a worker profile and the
# skills it must load. Keys double as the idempotency-key suffix (multica:{id}:<phase>).
_CHAIN = (
    ("scout", "zoe-planner", ("zoe-graphify", "zoe-engineering")),
    (
        "implement",
        "zoe-coder",
        # Keep preloaded skills minimal — full guidance lives in zoe-engineering; large
        # preload exceeds OpenRouter per-request prompt caps (~30k tokens).
        ("zoe-engineering",),
    ),
    ("verify", "zoe-reviewer", ("zoe-engineering",)),
    ("review", "zoe-reviewer", ("zoe-engineering",)),
    ("closeout", "zoe-planner", ("github-greptile-loop",)),
    ("retro", "zoe-planner", ("zoe-status-refresh",)),
)
_LEGACY_CHAIN_PHASES = ("implement", "review", "closeout")
_V2_CHAIN_PHASES = ("implement", "verify", "review", "closeout")

_ACTIVE_KANBAN_STATUSES = {"triage", "todo", "ready", "running", "blocked"}
_TERMINAL_KANBAN_STATUSES = {"done", "archived"}
_BLOCKING_PARENT_STATUSES = {"blocked", "cancelled", "error", "failed"}

_PROTOCOL_VIOLATION_LIMIT = max(
    1, int(os.environ.get("ZOE_KANBAN_PROTOCOL_VIOLATION_LIMIT", "2") or "2")
)

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


def _board() -> str:
    return os.environ.get("ZOE_KANBAN_BOARD", "default")


def _greptile_mcp_bin() -> str:
    """Locate the operator-local greptile MCP CLI; honour GREPTILE_MCP_BIN override.

    This is an operator-installed binary (not in the repo), so mirror the
    ``hermes_bin`` pattern: prefer the env override, fall back to the standard
    install path. Keeps the closeout worker portable across hosts/users instead
    of silently stalling the Greptile loop when the path differs.
    """
    override = os.environ.get("GREPTILE_MCP_BIN", "").strip()
    if override:
        return override
    return os.path.expanduser("~/bin/greptile-mcp.py")


def _ticket_metadata(issue: dict | None = None) -> dict[str, Any]:
    issue = issue or {}
    cached = issue.get("_zoe_ticket_metadata_cache")
    if isinstance(cached, dict):
        return dict(cached)

    meta = dict(issue.get("metadata") or {})
    if issue.get("description"):
        try:
            from multica_ticket_contract import parse_ticket_block

            parsed = parse_ticket_block(issue.get("description") or "")
            if isinstance(parsed, dict):
                meta = {**parsed, **meta}
        except Exception as exc:  # noqa: BLE001
            logger.debug("_ticket_metadata: parse_ticket_block failed: %s", exc)
    issue["_zoe_ticket_metadata_cache"] = dict(meta)
    return meta


def _engineering_mode(issue: dict | None = None) -> str:
    """Resolve the engineering execution mode for a journaled phase run.

    Interactive is the default for user-visible work. Overnight mode allows
    slower, cheaper runs by extending worker runtime and making the cost
    preference explicit in every worker prompt.
    """
    issue = issue or {}
    raw = (
        issue.get("engineering_mode")
        or _ticket_metadata(issue).get("engineering_mode")
        or os.environ.get("ZOE_ENGINEERING_MODE")
        or "interactive"
    )
    mode = str(raw).strip().lower()
    if mode in {"overnight", "background", "self_evolution", "self-evolution"}:
        return "overnight"
    if mode in {"quality-escalation", "quality_escalation", "escalation"}:
        return "quality-escalation"
    return "interactive"


def _model_escalation_active(issue: dict | None, mode: str) -> bool:
    """True when review/verify/closeout may use stronger models after cheap paths fail."""
    issue = issue or {}
    meta = _ticket_metadata(issue)
    if str(meta.get("model_escalation") or issue.get("model_escalation") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return True
    return mode == "quality-escalation"


def _escalation_model_hint(issue: dict | None) -> str:
    """Paid-model escalation guard — never suggest openrouter/auto without explicit opt-in."""
    issue = issue or {}
    meta = _ticket_metadata(issue)
    paid_auto_ok = str(meta.get("confirm_paid_auto") or issue.get("confirm_paid_auto") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    lines = [
        "Model escalation: if profile defaults and fallbacks"
        " (minimax/minimax-m3, google/gemini-2.5-flash, openrouter/free) fail twice on this phase,"
        " you MAY switch to anthropic/claude-sonnet-4.6 for this task only.",
    ]
    if paid_auto_ok:
        lines.append(
            "Operator confirmed paid auto-routing: openrouter/auto is allowed as a last resort"
            " when Sonnet also fails."
        )
    else:
        lines.append(
            "Do NOT use openrouter/auto on this task — confirm_paid_auto is not set on the issue."
        )
    return "\n".join(lines) + "\n"


def _overnight_implement_cost_hint() -> str:
    return (
        "Overnight cost routing: prefer profile defaults first; after two failures on the same check,"
        " retry with openrouter/free before any paid escalation.\n"
    )


def _retro_cost_hint() -> str:
    return (
        "Retro cost routing: summaries only — prefer google/gemini-2.5-flash or openrouter/free;"
        " do not burn premium models on long re-reads.\n"
    )


def _max_runtime(mode: str = "interactive") -> str:
    if mode == "overnight":
        return os.environ.get("ZOE_KANBAN_OVERNIGHT_MAX_RUNTIME", "6h")
    if mode == "quality-escalation":
        return os.environ.get("ZOE_KANBAN_ESCALATION_MAX_RUNTIME", "90m")
    return os.environ.get("ZOE_KANBAN_MAX_RUNTIME", "45m")


_SKIP_SCOUT_TAG_RE = re.compile(r"skip_scout:\s*(true|yes|1)", re.I)
_AUDIT_NO_PR_RE = re.compile(
    r"\b(?:audit-only|smoke test only|no code change only|no code/config changes only|evidence_profile:\s*audit)\b",
    re.I,
)


def _skip_scout(issue: dict | None = None) -> bool:
    issue = issue or {}
    if str(os.environ.get("ZOE_KANBAN_SKIP_SCOUT", "")).strip().lower() in {"1", "true", "yes"}:
        return True
    meta = _ticket_metadata(issue)
    if str(meta.get("skip_scout") or issue.get("skip_scout") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return True
    if (
        str(meta.get("zoe_kind") or "").strip().lower() == "child"
        and str(meta.get("source") or "").strip().lower() == "scope_split"
        and bool(meta.get("acceptance_criteria"))
    ):
        return True
    haystack = " ".join(
        [
            str(issue.get("title") or ""),
            str(issue.get("description") or ""),
            json.dumps(meta),
        ]
    )
    return bool(_SKIP_SCOUT_TAG_RE.search(haystack))


def _audit_no_pr_issue(issue: dict | None = None) -> bool:
    issue = issue or {}
    meta = _ticket_metadata(issue)
    if str(meta.get("evidence_profile") or "").strip().lower() == "audit":
        return True
    haystack = " ".join(
        [
            str(issue.get("title") or ""),
            str(issue.get("description") or ""),
            json.dumps(meta),
        ]
    )
    return bool(_AUDIT_NO_PR_RE.search(haystack))


def _chain_for_issue(issue: dict) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    phases = _CHAIN
    if _audit_no_pr_issue(issue):
        phases = tuple((phase, assignee, ()) for phase, assignee, _skills in phases)
    if _skip_scout(issue):
        phases = tuple(p for p in phases if p[0] != "scout")
    return phases


def _phase_plan_entry(phase: str, issue: dict) -> tuple[str, str, tuple[str, ...]] | None:
    for entry in _chain_for_issue(issue):
        if entry[0] == phase:
            return entry
    return None


def _protocol_violation_count(detail: dict[str, Any]) -> int:
    events = detail.get("events") if isinstance(detail.get("events"), list) else []
    return sum(
        1 for event in events if isinstance(event, dict) and event.get("kind") == "protocol_violation"
    )


def _expected_phases(phases: dict[str, dict]) -> set[str]:
    present = set(phases)
    bodies = [(phases[p].get("body") or "") for p in present]
    if any("zoe-chain: v4" in body for body in bodies):
        return set(present)
    v3_chain = any("zoe-chain: v3" in body for body in bodies)
    if present <= set(_LEGACY_CHAIN_PHASES) and "verify" not in present:
        return set(_LEGACY_CHAIN_PHASES)
    if "scout" in present:
        return {p for p, _, _ in _CHAIN}
    if v3_chain:
        return set(_V2_CHAIN_PHASES) | {"retro"}
    if "retro" in present:
        return set(_V2_CHAIN_PHASES) | {"retro"}
    if "verify" in present:
        return set(_V2_CHAIN_PHASES)
    return {p for p, _, _ in _CHAIN}


class KanbanCLIError(RuntimeError):
    """Raised when a hermes kanban CLI call fails."""


class KanbanAdapter:
    """Executor adapter backed by the Hermes Kanban board."""

    name = NAME

    async def _run(self, args: list[str], *, expect_json: bool = False) -> Any:
        cmd = [hermes_bin(), "kanban", "--board", _board(), *args]
        env = dict(os.environ)
        env.setdefault("HERMES_KANBAN_BOARD", _board())
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=zoe_repo_root(),
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

    async def _phases_for_ref(self, external_ref: str) -> dict[str, dict]:
        tasks = await self._run(["list", "--json"], expect_json=True)
        rows = tasks if isinstance(tasks, list) else (tasks or {}).get("tasks", [])
        prefix = f"{external_ref}:"
        phases: dict[str, dict] = {}
        for row in rows:
            key = _row_ref_key(row)
            if key.startswith(prefix):
                phases[key[len(prefix):]] = row
        return phases

    def build_phase_prompt(
        self,
        phase: str,
        issue: dict,
        identifier: str,
        *,
        mode: str | None = None,
    ) -> str:
        """Build the supported Hermes prompt contract for one engineering phase."""
        return self._build_body(phase, issue, identifier, mode=mode)

    def _build_body(self, phase: str, issue: dict, identifier: str, *, mode: str | None = None) -> str:
        title = issue.get("title") or identifier
        description = issue.get("description") or ""
        mode = mode or _engineering_mode(issue)
        mode_note = (
            "Engineering mode: overnight. Prioritize free/reliable local or OpenRouter-low-cost routes;"
            " latency is secondary to evidence quality.\n"
            if mode == "overnight"
            else "Engineering mode: quality-escalation. Paid quality routes are allowed only after"
            " evidence shows cheaper paths failed or architecture sensitivity requires it.\n"
            if mode == "quality-escalation"
            else "Engineering mode: interactive. Keep user-visible latency and review size tight.\n"
        )
        # `zoe-ref:` is a machine marker that lets poll() correlate this task back
        # to its Multica issue + phase. The live `hermes kanban list --json` output
        # does NOT expose the idempotency key, so the body (which it does expose) is
        # the durable correlation channel. Strip the id the same way dispatch() does
        # so the marker stays byte-identical to the --idempotency-key (otherwise a
        # whitespace-padded id would desync the marker from external_ref and poll()
        # would never match): multica:{id}:{phase}.
        issue_id = str(issue.get("id") or "").strip()
        escalation = _model_escalation_active(issue, mode)
        escalation_marker = "zoe-model-escalation: true\n" if escalation else ""
        common = (
            f"Multica issue: {identifier} (id {issue_id})\n"
            f"zoe-ref: multica:{issue_id}:{phase}\n"
            f"zoe-chain: v4\n"
            f"{escalation_marker}"
            f"{mode_note}"
            f"Repo: {zoe_repo_root()}  |  Base branch: main  |  Workspace: git worktree\n\n"
            f"Title: {title}\n\n{description}\n\n"
        )
        if phase == "scout":
            return common + (
                "You are scout (zoe-planner, read-only). Gather context before any code changes.\n"
                "- Start with `kanban_show` and read the Multica issue acceptance criteria.\n"
                "- CHILD/FOLLOW-UP FAST PATH: if the issue is already a narrow child/follow-up with"
                " concrete acceptance criteria and an existing prerequisite artifact/PR named in the"
                " ticket block, do not inspect branch history or broad worktrees. Decide ready/blocked"
                " from the ticket plus at most the named artifact files, then hand off.\n"
                "- BROAD PARENT SPLIT FAST PATH: if the ticket asks for multiple deliverables, many files,"
                " or several domain builders/surfaces in one PR, do not map the repo first. Call"
                " `kanban_block` immediately with:\n"
                "BLOCKER=SCOPE_SPLIT_REQUIRED: <why this parent is too broad>\n"
                "NEEDS_SPLIT=1\n"
                "SPLIT_PACKET={\"child_issue_template\":{\"title\":\"<parent>: <small deliverable>\","
                "\"description\":\"Scope + acceptance criteria + evidence\"},\"reason\":\"<why split is required>\"}\n"
                "- Keep this phase bounded: run at most one focused Graphify/doc lookup and no broad repo crawl.\n"
                "- For smoke, audit-only, or harness-check tickets, do not over-investigate; summarize the"
                " observed contract and complete the scout handoff.\n"
                "- Use opensrc for third-party APIs; reference Multica comments/state as source of truth.\n"
                "- Do NOT edit code, open PRs, or mutate production config.\n"
                "- Hand off with `kanban_complete` metadata including TOOLS_USED=, SCOUT_SUMMARY=, "
                "and IMPLEMENTATION_REQUIRED=true|false.\n"
                "- Set IMPLEMENTATION_REQUIRED=false only when the acceptance criteria are already "
                "satisfied by identified merged changes; Zoe will route directly to verification.\n"
                "- Finish context gathering within 8 tool/model steps. Reserve the final 2 calls for"
                " `kanban_complete` or `kanban_block`; do not spend that terminal-call headroom on"
                " more exploration.\n"
                "- If the answer is still unclear at step 8, call `kanban_block` with"
                " BLOCKER=SCOUT_BUDGET and the missing information instead of exploring further.\n"
                "- TERMINAL PROTOCOL: end with `kanban_complete` or `kanban_block` (no silent exit)."
            )
        if phase == "implement":
            overnight_hint = _overnight_implement_cost_hint() if mode == "overnight" else ""
            # Implement body intentionally omits full prior-phase logs; workers should
            # call kanban_show and read SCOUT_SUMMARY= from scout metadata when present.
            return common + overnight_hint + (
                "You are the implementer (zoe-coder).\n"
                "- AUDIT/SMOKE FAST PATH: only if the title/body explicitly says audit-only, smoke test,"
                " no code change, or uses trace/map with an audit/no-code qualifier, do not run Graphify"
                " or repo exploration first. Complete in one bounded handoff with"
                " TOOLS_USED=audit-read, PR_URL= blank, AUDIT_ONLY=1, TESTS=not applicable/audit-only,"
                " and SUMMARY= findings. Do not open a PR.\n"
                "- Start with `kanban_show` to confirm this task id.\n"
                "- SMALL EXPLICIT CODE FAST PATH: when the ticket names the exact file, helper,"
                " function, or focused test to change, inspect only those named files plus the"
                " nearest existing test. Do not run a broad Graphify query, repo crawl, or unrelated"
                " search. Start editing within 6 tool/model steps; if the change is still unclear by"
                " then, call `kanban_block` with BLOCKER=IMPLEMENT_BUDGET instead of exploring further.\n"
                "- For broad or ambiguous code-changing tickets only: read the charter and run at"
                " most one focused Graphify map (graphify query/path/explain over raw grep).\n"
                "- Use opensrc for any third-party library source before guessing APIs.\n"
                "- Make the smallest reviewable change; do NOT rewrite existing functions into bloat;"
                " reuse service-layer helpers.\n"
                "- If the task needs more than one PR or a large refactor, call `kanban_block` and"
                " ask for a split — do not absorb unbounded work in one implement run.\n"
                "- Do NOT create additional Hermes/Kanban tasks, scaffold subtasks, or sibling work items."
                " If scope needs another task, use the NEEDS_SPLIT/SPLIT_PACKET block below and stop.\n"
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
                "Hard-ticket policy: if the issue is too broad for one small PR, repeated protocol failures,"
                " or your turn/context budget is the blocker, stop cleanly with `kanban_block` and include:\n"
                "NEEDS_SPLIT=1\n"
                "SPLIT_PACKET={\"child_issue_template\":{\"title\":\"<parent>: <small deliverable>\","
                "\"description\":\"Scope + acceptance criteria + evidence\"},\"reason\":\"<why split is required>\"}\n"
                "The parent issue will stay blocked with a scope-split packet instead of blindly dispatching"
                " verify/review/closeout.\n\n"
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
            escalation_hint = _escalation_model_hint(issue) if escalation else ""
            return common + escalation_hint + (
                "You are verify (zoe-reviewer). This is the objective test/evidence gate before review.\n"
                "- AUDIT/NO-PR FAST PATH: if this is audit-only, smoke, or has no code/config changes"
                " and no PR_URL, do not load broad skills, hunt for a PR, or explore the repo. Run"
                " `kanban_show`, compare the implementer handoff to the Multica acceptance criteria,"
                " then call `kanban_complete` in this turn with TESTS=not applicable/audit evidence,"
                " VALIDATORS=not applicable/audit-only, PR_URL= blank, and a short pass/fail summary.\n"
                "- Start with `kanban_show` to read the implementer handoff and PR_URL.\n"
                "- Do not redesign or refactor. Run the declared tests and the minimum extra checks needed"
                " for the touched surface.\n"
                "- Always run and record `python3 tools/audit/validate_structure.py` and"
                " `python3 tools/audit/validate_critical_files.py` in VALIDATORS unless the task"
                " is explicitly audit-only with no code/config changes.\n"
                "- Required evidence in your final `kanban_complete` metadata: TESTS, VALIDATORS,"
                " PR_URL, and a pass/fail summary. Include exact commands and outcomes.\n"
                "- If tests fail, evidence is missing, the PR is absent for a code task, or the task needs"
                " product clarification, call `kanban_block` with BLOCKER= and the failing output.\n"
                "- If you cannot reach a pass/block decision within 8 tool/model steps, call"
                " `kanban_block` with BLOCKER=VERIFY_BUDGET and the missing evidence.\n"
                "- TERMINAL PROTOCOL: end with `kanban_complete` or `kanban_block` (no silent exit)."
            )
        if phase == "review":
            escalation_hint = _escalation_model_hint(issue) if escalation else ""
            return common + escalation_hint + (
                "You are the reviewer (zoe-reviewer). Review the diff, scope, and verify-phase evidence.\n"
                "- POST-MERGE FAST PATH: if the Zoe ticket block already has PR_URL/pr_url,"
                " MERGE_SHA/merge_sha, and GREPTILE/greptile_status=5/5, do not explore broad"
                " worktrees. Confirm the recorded tests/validators match the acceptance criteria,"
                " write the review marker, then immediately `kanban_complete`.\n"
                "- AUDIT/NO-PR FAST PATH: for audit-only/no-code handoffs with blank PR_URL, do not load"
                " broad skills or explore the tree. Use `kanban_show`, compare verify evidence to the"
                " acceptance criteria, then `kanban_complete` with a short verification note.\n"
                "- Confirm the change is small and in scope. Audit-only / doc-only handoffs with blank PR_URL"
                " need a short verification note, then `kanban_complete` — do not re-implement or burn turns"
                " re-exploring the tree.\n"
                "- If there is no PR because verify marked the issue audit-only/no-code, confirm that"
                " evidence matches the acceptance criteria and complete the review.\n"
                "- Do not approve if verify-phase evidence is missing, stale, or inconsistent with the diff."
                " Block with a concrete reason instead.\n"
                "- If you cannot reach a pass/block decision within 8 tool/model steps, call"
                " `kanban_block` with BLOCKER=REVIEW_BUDGET and the missing evidence.\n"
                "- On approval, write the mechanical review marker before completing:\n"
                f"    PYTHONPATH={zoe_repo_root()}/services/zoe-data python3"
                f" {zoe_repo_root()}/services/zoe-data/pipeline_evidence_commands.py"
                f" mark-reviewed multica:{issue_id} --critical-count <N> --summary \"<short verdict>\"\n"
                "  Replace <N> with the actual unresolved critical finding count; approval requires zero.\n"
                "  This command shape is complete; do not call `--help` after it. If it succeeds,"
                " immediately call `kanban_complete` in the same turn.\n"
                "- TERMINAL PROTOCOL: end with `kanban_complete` or `kanban_block` (no silent exit).\n"
                "- Do NOT approve if verification or the Greptile gate is unavailable; set the task blocked"
                " with an explicit reason instead.\n"
                "- Record findings (pass/fail/concern) and a merge-readiness verdict in your handoff.\n"
                "Final handoff MUST include:\nREVIEW=<approved or blocked>\nSUMMARY=<short verdict>"
            )
        if phase == "retro":
            return common + _retro_cost_hint() + (
                "You are retro (zoe-planner). Capture learnings after closeout — no silent prod changes.\n"
                "- POST-CLOSEOUT FAST PATH: if closeout already records PR_URL, MERGE_SHA, and"
                " GREPTILE/greptile_status=5/5 or already_merged, do not inspect worktrees,"
                " branch history, or GitHub."
                " Use `kanban_show`, summarize one learning, and `kanban_complete`.\n"
                "- AUDIT/NO-PR FAST PATH: if this was an audit-only/no-code run, keep retro to one"
                " short handoff and do not load broad skills or inspect unrelated repo state.\n"
                "- Read closeout/implement handoffs and any Greptile or validator notes.\n"
                "- Summarize what worked, what failed, and one small harness improvement proposal.\n"
                "- Do NOT merge, refactor broadly, or change production behavior from this phase.\n"
                "- If the run revealed a concrete harness improvement, include FOLLOW_UP_TITLE= and"
                " FOLLOW_UP_DESCRIPTION= in the handoff; Zoe will create exactly one backlog ticket.\n"
                "- Hand off with `kanban_complete` metadata: RETRO= or LEARNINGS= plus TOOLS_USED=.\n"
                "- Do not change production behavior here; the follow-up ticket is the loop.\n"
                "- TERMINAL PROTOCOL: end with `kanban_complete` or `kanban_block`."
            )
        # closeout
        escalation_hint = _escalation_model_hint(issue) if escalation else ""
        return common + escalation_hint + (
            "You are closeout (zoe-planner, orchestration only).\n"
            "- Read the implementer's PR_URL handoff and extract the PR number N (the trailing /pull/N)."
            " If AUDIT_ONLY=1 or audit-only with blank PR_URL, `kanban_complete` with closeout evidence"
            " — no grep loop or merge.\n"
            "- For smoke/audit tickets with no code/config changes and no PR_URL, report the closeout"
            " completion reason, then `kanban_complete`; do not wait for Greptile.\n"
            "- If a code task has no PR, leave the issue blocked — do NOT open one yourself.\n"
            "- COST/ITERATION FAST PATH: after extracting N, make the first repository command:\n"
            "    scripts/maintenance/run_greploop_guard.sh --pr N --merge-when-ready\n"
            "  If it reports ok=true, hand off and `kanban_complete` immediately. Do not run broad git"
            " log/show/diff, worktree inventories, duplicate `gh pr view`, or separate comment queries"
            " before this guard; it is the source of truth for Greptile, threads, CI, and merge state.\n"
            "  If the only blocker is a behind branch, fetch origin/main, rebase the existing implementation"
            " worktree once, push with `--force-with-lease`, wait for checks, then run the same guard once more."
            " Do not repeat the rebase/guard cycle.\n"
            "- Address every substantive Greptile finding (fix_now or won't_fix with reason). Do not stop at"
            " merge-ready — merge when gates pass.\n"
            "- Drive the Greptile grep loop with the pinned github-greptile-loop skill (guard REQUIRES --pr N;"
            " <=5 rounds, target confidence 5). Each round:\n"
            "    scripts/maintenance/run_greploop_guard.sh --pr N --once\n"
            "  Apply fixes yourself when the guard returns ESCALATE_HERMES or PACKET_READY; use --packet-only"
            " only to hand off to a cheap Cursor runner.\n"
            "  Re-trigger review when needed via"
            f" `{_greptile_mcp_bin()} trigger-review jason-easyazz/zoe-ai-assistant N`"
            " (operator-local binary; override with GREPTILE_MCP_BIN).\n"
            "- When Greptile is clear (confidence 5/5, no unaddressed findings) and CI is green, squash-merge"
            " via normal GitHub (never --admin, never force, never --no-verify):\n"
            "    scripts/maintenance/run_greploop_guard.sh --pr N --merge-when-ready\n"
            "  Or one iteration then merge: --once --merge-when-ready\n"
            "  If branch protection blocks merge, leave the issue blocked with the gh error — do not admin-merge.\n"
            "- After a successful merge: report PR_URL, merge SHA, GREPTILE status, and summary in your"
            " handoff. Zoe will update Multica after the retro phase completes. If merge did not happen,"
            " leave the issue in_progress/blocked with the blocker.\n"
            "Final handoff MUST include:\nPR_URL=<url>\nMERGE_SHA=<sha or blank>\nGREPTILE=<status>\n"
            "MULTICA=<Zoe updates after retro; report blocker if any>\nAUDIT_ONLY=<1 for no-PR audit"
            " closeout, otherwise 0>\nSUMMARY=<short>\n"
            "- TERMINAL PROTOCOL: you MUST end with `kanban_complete` or `kanban_block` (no silent exit)."
        )

    async def _maybe_auto_block_protocol_violation(
        self,
        task_id: str,
        phase: str,
        row: dict[str, Any],
        detail: dict[str, Any],
    ) -> bool:
        """Block a task after repeated Hermes protocol violations (silent worker exit)."""
        status = (row.get("status") or "").lower()
        if status != "running":
            return False
        violations = _protocol_violation_count(detail)
        if violations < _PROTOCOL_VIOLATION_LIMIT:
            return False
        reason = (
            "BLOCKER=PROTOCOL_VIOLATION: worker exited without kanban_complete/kanban_block "
            f"({violations} violations on {phase})"
        )
        try:
            await self._run(["block", task_id, reason])
        except KanbanCLIError as exc:
            logger.warning(
                "kanban_adapter: protocol auto-block failed for %s (%s): %s",
                task_id,
                phase,
                exc,
            )
            return False
        row["status"] = "blocked"
        row["block_reason"] = reason
        logger.info(
            "kanban_adapter: auto-blocked %s (%s) after %d protocol violations",
            task_id,
            phase,
            violations,
        )
        return True

    async def _maybe_auto_block_phase_budget(
        self,
        task_id: str,
        phase: str,
        row: dict[str, Any],
        detail: dict[str, Any],
    ) -> bool:
        """Stop a running worker when its code-enforced phase budget is exhausted."""
        status = (row.get("status") or "").lower()
        if status != "running":
            return False
        reason = phase_budget_reason(task_id, phase, detail)
        if not reason:
            return False
        try:
            await self._run(["block", task_id, reason])
        except KanbanCLIError as exc:
            logger.warning(
                "kanban_adapter: budget auto-block failed for %s (%s): %s",
                task_id,
                phase,
                exc,
            )
            return False
        terminate_running_workers(detail)
        row["status"] = "blocked"
        row["block_reason"] = reason
        logger.warning("kanban_adapter: stopped %s (%s): %s", task_id, phase, reason)
        return True

    async def dispatch(self, issue: dict) -> dict:
        """Create the single current ready phase for a Multica engineering run.

        Returns {ok, external_ref, chain:{phase:task_id}, created:[phase], mode}.
        Repeated calls are idempotent: if the current phase already has a Kanban
        row, dispatch reports it without creating downstream phases.
        """
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            return {"ok": False, "reason": "issue has no id"}
        identifier = issue.get("identifier") or issue.get("title") or issue_id
        # external_ref prefix for idempotency keys; full key is "multica:{issue_id}:{phase}" (multica:{id}:<phase>).
        external_ref = f"multica:{issue_id}"

        chain: dict[str, str] = {}
        created: list[str] = []
        mode = _engineering_mode(issue)

        try:
            from pipeline_evidence import transition
            from pipeline_store import bootstrap_state, save_state

            state = await bootstrap_state(
                external_ref,
                start_phase="implement" if _skip_scout(issue) else "scout",
                issue=issue,
            )
        except Exception as exc:
            logger.warning("kanban_adapter: pipeline bootstrap failed for %s: %s", external_ref, exc)
            return {
                "ok": False,
                "external_ref": external_ref,
                "reason": "pipeline bootstrap failed",
                "chain": {},
                "created": [],
                "mode": mode,
            }

        if state is not None and state.status in {"blocked", "done"}:
            return {
                "ok": False,
                "external_ref": external_ref,
                "reason": f"pipeline {state.status}",
                "phase": state.phase,
                "chain": {},
                "created": [],
                "mode": mode,
            }

        phase = state.phase if state is not None else ("implement" if _skip_scout(issue) else "scout")
        plan = _chain_for_issue(issue)
        phase_order = [p for p, _, _ in plan]
        entry = next((plan_entry for plan_entry in plan if plan_entry[0] == phase), None)
        # Only adjust not-yet-started phases. A stale running phase may still
        # have an active worker/effect, so leave it blocked for operator review.
        if entry is None and state is not None and state.status == "todo" and phase_order:
            previous_phase = phase
            phase = phase_order[0]
            entry = next((plan_entry for plan_entry in plan if plan_entry[0] == phase), None)
            try:
                state = state.model_copy(update={"phase": phase})
                state = await asyncio.to_thread(
                    save_state,
                    state,
                    event="plan_adjusted",
                    extra={
                        "from_phase": previous_phase,
                        "to_phase": phase,
                        "reason": "current issue plan no longer includes journal phase",
                    },
                )
            except Exception as exc:
                logger.warning(
                    "kanban_adapter: plan_adjusted save skipped for %s: %s; "
                    "phase adjustment will still be applied via effect_requested",
                    external_ref,
                    exc,
                )
        if entry is None:
            return {
                "ok": False,
                "external_ref": external_ref,
                "reason": f"phase {phase} is not in this issue plan",
                "phase": phase,
                "chain": {},
                "created": [],
                "mode": mode,
            }

        existing_phases = await self._phases_for_ref(external_ref)
        existing_row = existing_phases.get(phase)
        if existing_row and existing_row.get("id"):
            return {
                "ok": True,
                "external_ref": external_ref,
                "chain": {phase: existing_row["id"]},
                "created": [],
                "mode": mode,
                "phase": phase,
                "ready_phase_only": True,
            }

        parent: str | None = None
        if phase in phase_order:
            idx = phase_order.index(phase)
            if idx > 0:
                previous = existing_phases.get(phase_order[idx - 1]) or {}
                if (previous.get("status") or "").lower() not in _BLOCKING_PARENT_STATUSES:
                    parent = previous.get("id")

        phase, assignee, skills = entry
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
            _max_runtime(mode),
            # Hermes trips the circuit breaker on the Nth failure; 1 means one
            # total attempt and zero automatic retries.
            "--max-retries",
            "1",
            "--created-by",
            "zoe-bridge",
            "--body",
            self._build_body(phase, issue, identifier, mode=mode),
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
        if phase in {"implement", "verify"}:
            from worktree_bootstrap import prepare_kanban_worktree

            await asyncio.to_thread(prepare_kanban_worktree, str(task_id))
        if state is not None and state.status == "todo":
            try:
                running = transition(state, "start")
                await asyncio.to_thread(
                    save_state,
                    running,
                    event="effect_requested",
                    extra={"phase": phase, "task_id": task_id},
                )
            except Exception as exc:
                logger.warning("kanban_adapter: effect_requested save skipped for %s: %s", external_ref, exc)

        logger.info(
            "kanban_adapter: dispatched %s -> phase=%s chain=%s (new=%s)", identifier, phase, chain, created
        )
        return {
            "ok": True,
            "external_ref": external_ref,
            "chain": {phase: task_id},
            "created": created,
            "mode": mode,
            "phase": phase,
            "ready_phase_only": True,
        }

    def _title(self, phase: str, identifier: str, issue: dict) -> str:
        base = issue.get("title") or identifier
        if phase == "scout":
            return f"Scout {identifier}"[:140]
        if phase == "implement":
            return f"{identifier}: {base}"[:140]
        if phase == "review":
            return f"Review {identifier}"[:140]
        if phase == "verify":
            return f"Verify {identifier}"[:140]
        if phase == "retro":
            return f"Retro {identifier}"[:140]
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
        phases = await self._phases_for_ref(external_ref)
        if not phases:
            return {"found": False, "status": "not_found", "phases": {}, "pr_url": None, "blocker": None}

        detail_cache: dict[str, dict[str, Any]] = {}
        for phase, row in list(phases.items()):
            task_id = row.get("id")
            if not task_id:
                continue
            status = (row.get("status") or "").lower()
            if status in _TERMINAL_KANBAN_STATUSES or status == "blocked":
                continue
            try:
                detail = await self._run(["show", task_id, "--json"], expect_json=True)
            except KanbanCLIError as exc:
                logger.debug("kanban_adapter: show failed for %s: %s", task_id, exc)
                continue
            detail_cache[task_id] = detail
            if await self._maybe_auto_block_phase_budget(task_id, phase, row, detail):
                phases[phase] = row
                continue
            if await self._maybe_auto_block_protocol_violation(task_id, phase, row, detail):
                phases[phase] = row

        statuses = {p: (r.get("status") or "") for p, r in phases.items()}
        blocker = None
        for p, r in phases.items():
            if (r.get("status") or "") == "blocked":
                blocker = r.get("block_reason") or f"{p} blocked"
                break

        closeout = phases.get("closeout", {})
        retro = phases.get("retro", {})
        is_v4 = any("zoe-chain: v4" in (row.get("body") or "") for row in phases.values())
        expected = _expected_phases(phases)
        missing = expected - set(phases)
        if retro and (retro.get("status") or "") in _TERMINAL_KANBAN_STATUSES:
            agg = "done"
        elif (closeout.get("status") or "") in _TERMINAL_KANBAN_STATUSES and "retro" not in expected:
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

        pipeline_info: dict = {"tracked": False}
        try:
            from pipeline_store import pipeline_summary, sync_pipeline_from_chain

            async def _fetch_detail(task_id: str) -> dict:
                cached = detail_cache.get(task_id)
                if cached is not None:
                    return cached
                detail = await self._run(["show", task_id, "--json"], expect_json=True)
                detail_cache[task_id] = detail
                return detail

            start_phase = "scout" if "scout" in phases else "implement"
            state = await sync_pipeline_from_chain(
                external_ref, phases, _fetch_detail, start_phase=start_phase  # type: ignore[arg-type]
            )
            pipeline_info = pipeline_summary(state)
            if pipeline_info.get("missing_evidence") and agg == "running":
                pipeline_info["gate"] = "evidence_required"
            current_phase = pipeline_info.get("phase")
            current_status = pipeline_info.get("status")
            if is_v4 and current_status == "done":
                agg = "done"
                blocker = None
            elif is_v4:
                if pipeline_info.get("terminal_block"):
                    agg = "blocked"
                    blocker = f"pipeline terminal block at {current_phase}"
                elif current_status == "blocked":
                    agg = "blocked"
                    current_row = phases.get(str(current_phase or ""), {})
                    blocker = current_row.get("block_reason") or f"pipeline blocked at {current_phase}"
                elif current_status == "todo":
                    agg = "partial" if current_phase not in phases else "running"
                    blocker = None
                elif current_status == "running":
                    agg = "running"
                    blocker = None
                elif agg not in {"done", "blocked"}:
                    agg = "running"
                    blocker = None
            elif pipeline_info.get("terminal_block"):
                agg = "blocked"
                blocker = blocker or f"pipeline terminal block at {current_phase}"
        except Exception as exc:
            logger.warning("kanban_adapter: pipeline sync failed for %s: %s", external_ref, exc)
            pipeline_info = {
                "tracked": False,
                "error": str(exc),
                "terminal_block": True,
                "block_reason": "pipeline_store_unavailable",
            }
            if is_v4 and agg not in {"done", "blocked"}:
                agg = "blocked"
                blocker = blocker or "pipeline_store_unavailable"

        return {
            "found": True,
            "status": agg,
            "phases": statuses,
            "pr_url": await self._extract_pr_url(phases, detail_cache=detail_cache),
            "blocker": blocker,
            "pipeline": pipeline_info,
        }

    async def _extract_pr_url(
        self,
        phases: dict[str, dict],
        *,
        detail_cache: dict[str, dict[str, Any]] | None = None,
    ) -> str | None:
        """Pull a PR URL from the implement/closeout task summaries or comments."""
        pattern = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+")
        cache = detail_cache or {}
        for phase in ("closeout", "retro", "implement", "verify", "review", "scout"):
            row = phases.get(phase)
            if not row:
                continue
            task_id = row.get("id")
            if not task_id:
                continue
            detail = cache.get(task_id)
            if detail is None:
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
