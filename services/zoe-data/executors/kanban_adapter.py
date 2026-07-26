"""Hermes Kanban executor adapter.

Turns a Multica issue into a durable evidence-gated engineering run. New runs
create only the current ready phase; pipeline_store owns phase advancement and a
later dispatch call creates the next phase. Legacy in-flight chains are still
recognized while the board drains.

Boundary: this shells the ``hermes kanban`` CLI (same SQLite DB the in-gateway
dispatcher reads) rather than importing Hermes internals — keeping Zoe
surface-agnostic.

Worker profiles + pinned skills encode Zoe's agentic-engineering loop:
  - scout     (zoe-planner):  codebase-memory, zoe-engineering  (read-only context)
  - implement (zoe-coder):  zoe-engineering             (no preloaded skill for audit/no-PR)
  - verify    (zoe-reviewer): zoe-engineering           (no preloaded skill for audit/no-PR)
  - review    (zoe-reviewer): zoe-engineering           (no preloaded skill for audit/no-PR)
  - closeout  (zoe-planner):  github-greptile-loop      (no preloaded skill for audit/no-PR)
  - retro     (zoe-planner):  zoe-status-refresh        (no preloaded skill for audit/no-PR)
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from hermes_http import hermes_bin
from repo_paths import zoe_repo_root
from kanban_phase_budget import (
    dead_worker_reason,
    latest_log_session,
    phase_budget_reason,
    phase_budget_reason_from_log,
    task_log_tail,
    terminate_running_workers,
)

_REAP_DEAD_WORKERS = (
    os.environ.get("ZOE_KANBAN_REAP_DEAD_WORKERS", "true") or "true"
).strip().lower() not in {"0", "false", "no"}

# When an implement row is gate-blocked for a missing PR but the task worktree
# is provably empty (no diff, nothing unpushed), there is genuinely nothing to
# ship — the work is already present on the base. Converge it to the proven
# ALREADY_COVERED -> skip_implementation -> audit path instead of stranding the
# single lane on a permanent gate block. Default on; disable with =0/false/no.
_CONVERGE_NOOP_IMPLEMENT = (
    os.environ.get("ZOE_KANBAN_CONVERGE_NOOP_IMPLEMENT", "true") or "true"
).strip().lower() not in {"0", "false", "no"}

logger = logging.getLogger(__name__)

NAME = "kanban"

# Phases of the per-issue run, in order. Each maps to a worker profile and the
# skills it must load. Keys double as the idempotency-key suffix (multica:{id}:<phase>).
_CHAIN = (
    ("scout", "zoe-planner", ("codebase-memory", "zoe-engineering")),
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
_JOKE_INTENT_GAP_TITLE_RE = re.compile(
    r"\bintent[- ]gap\b.*['\"]?(?:tell\s+me\s+(?:another\s+)?joke|joke)['\"]?",
    re.IGNORECASE,
)
_SAY_EXACTLY_INTENT_GAP_TITLE_RE = re.compile(
    r"\bintent[- ]gap\b.*\bsay\s+exactly(?:[: ]+zoe\s+chat\s+integration\s+ok)?\b",
    re.IGNORECASE,
)
_GITHUB_PR_URL_RE = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+")
_SHELL_TOOL_LINE_RE = re.compile(r"(?:^|\s)(?:💻\s+)?\$\s+(?P<command>.+)$")


def _detail_text(detail: dict[str, Any]) -> str:
    parts: list[str] = []
    summary = detail.get("latest_summary")
    if summary:
        parts.append(summary if isinstance(summary, str) else json.dumps(summary))
    for comment in detail.get("comments") or []:
        if isinstance(comment, dict):
            body = comment.get("body") or comment.get("text")
            if body:
                parts.append(str(body))
    for run in detail.get("runs") or []:
        if isinstance(run, dict):
            for key in ("summary", "error"):
                value = run.get(key)
                if value:
                    parts.append(str(value))
    metadata = detail.get("metadata")
    if metadata:
        parts.append(metadata if isinstance(metadata, str) else json.dumps(metadata))
    return "\n".join(parts)


def _already_covered_row(phase: str, row: dict) -> bool:
    if phase != "implement":
        return False
    text = "\n".join(
        str(row.get(key) or "")
        for key in ("block_reason", "reason", "latest_summary", "result")
    )
    return "ALREADY_COVERED" in text.upper()


def _already_covered_detail(phase: str, detail: dict[str, Any]) -> bool:
    return phase == "implement" and "ALREADY_COVERED" in _detail_text(detail).upper()


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


def _kanban_backend() -> str:
    """Which executor serves the kanban verbs: ``hermes`` (default) or ``executor``.

    Read per call, never cached, so the revert is an env flip + restart with no
    code change (docs/architecture/perf-hardening-plan.md discipline).
    """
    return (os.environ.get("ZOE_KANBAN_BACKEND", "executor") or "executor").strip().lower()


def _workspace_for_phase(phase: str) -> str:
    """Choose the Hermes workspace for a phase.

    Retro is read-only orchestration/learning work. Running it from the main
    repo avoids phantom task worktrees after closeout has already merged.
    """
    if phase == "retro":
        return f"dir:{zoe_repo_root()}"
    return "worktree"


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
        except (AttributeError, ImportError, KeyError, TypeError, ValueError) as exc:
            logger.debug("_ticket_metadata: parse_ticket_block failed: %s", exc)
    issue["_zoe_ticket_metadata_cache"] = dict(meta)
    return meta


def _existing_pr_url(issue: dict | None = None) -> str:
    issue = issue or {}
    raw = issue.get("pr_url") or _ticket_metadata(issue).get("pr_url") or ""
    return str(raw).strip()


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


def _is_code_audit_actionable(meta: dict[str, Any]) -> bool:
    """Return True for code-audit bug tickets that already have acceptance criteria."""
    return (
        str(meta.get("zoe_kind") or "").strip().lower() == "bug"
        and str(meta.get("source") or "").strip().lower().startswith("code_audit_")
        and bool(meta.get("acceptance_criteria"))
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
    if (
        str(meta.get("zoe_kind") or "").strip().lower() == "harness_fix"
        and bool(meta.get("acceptance_criteria"))
    ):
        return True
    if _is_code_audit_actionable(meta):
        return True
    haystack = " ".join(
        [
            str(issue.get("title") or ""),
            str(issue.get("description") or ""),
            json.dumps(meta),
        ]
    )
    return bool(_SKIP_SCOUT_TAG_RE.search(haystack))


def _code_audit_implement_hint(issue: dict | None = None) -> str:
    """Return extra bounded instructions for actionable code-audit tickets."""
    meta = _ticket_metadata(issue)
    if not _is_code_audit_actionable(meta):
        return ""
    issue = issue or {}
    haystack = " ".join(
        [
            str(issue.get("title") or ""),
            str(issue.get("description") or ""),
            json.dumps(meta),
        ]
    ).lower()
    nginx_security_header_hint = ""
    nginx_header_terms = (
        "security header",
        "security-header",
        "content-security-policy",
        "x-frame-options",
        "strict-transport-security",
        "hsts",
        "x-content-type-options",
        "referrer-policy",
        "permissions-policy",
    )
    nginx_context = "nginx" in haystack
    if nginx_context and any(term in haystack for term in nginx_header_terms):
        nginx_security_header_hint = (
            "For nginx.conf security-header tickets, do not hand-patch repeated `server {}` "
            "blocks. Run `python3 tools/audit/ensure_nginx_security_headers.py`, then "
            "`python3 tools/audit/ensure_nginx_security_headers.py --check`, "
            "then `python3 tools/audit/validate_structure.py`, "
            "then use the chained ship command. "
        )
    return (
        "- CODE-AUDIT FAST PATH: this is an actionable code-audit bug with acceptance criteria. "
        "Do not re-audit the whole repo, compare every possible helper, or search broadly for patterns. "
        f"{nginx_security_header_hint}"
        "After `kanban_show`, inspect only the named vulnerable file/endpoint plus at most one nearest "
        "focused test file. Apply the smallest patch that satisfies the acceptance criteria before the "
        "8th model/tool step. If the ticket lists acceptable alternatives, choose the least invasive "
        "in-process guard unless the acceptance criteria requires a different one. After the first patch, "
        "spend at most 2 more tool calls locating validation; do not inspect docker-compose, .github, "
        "or broad shell-script inventories unless the named file directly requires it. If no focused test "
        "is obvious, run `python3 tools/audit/validate_structure.py`, commit the patch, run "
        "`git push -u origin HEAD`, open the PR, and report TESTS=validate_structure.py only. "
        "After validation, do not run `git add` by itself; use one chained shell command for "
        "`git add ... && git commit ... && git push -u origin HEAD && gh pr create ...`, or call "
        "`kanban_block` if you cannot ship. This code-audit ship instruction overrides the generic "
        "separate push/PR command sequence below. If a patch attempt says it found multiple matches, "
        "retry once with a unique surrounding block; on a second ambiguous patch, call `kanban_block` "
        "with BLOCKER=PATCH_AMBIGUITY_DRIFT instead of reading more context. "
        "If a product/security "
        "decision is still missing after the named file is inspected, call `kanban_block` with "
        "BLOCKER=IMPLEMENT_BUDGET and the missing decision.\n"
    )


def _harness_implement_hint(issue: dict | None = None) -> str:
    """Return a small repo map for harness/self-improvement tickets."""
    issue = issue or {}
    meta = _ticket_metadata(issue)
    title = str(issue.get("title") or "").lower()
    source = str(meta.get("source") or "").lower()
    kind = str(meta.get("zoe_kind") or "").lower()
    harness_sources = {"retro_followup", "engineering_blocker_followup"}
    harness_title = title.startswith(("harness:", "zoe harness", "hermes harness"))
    if kind != "harness_fix" and not harness_title and source not in harness_sources:
        return ""
    blocker = str(meta.get("source_blocker") or "").upper()
    blocker_followup_hint = ""
    if source == "engineering_blocker_followup":
        if blocker == "ITERATION_BUDGET":
            focused_test = (
                "services/zoe-data/tests/test_main_multica_poll.py::"
                "test_record_blocked_multica_chain_creates_iteration_budget_followup"
            )
        elif blocker in {"IMPLEMENT_BUDGET", "IMPLEMENT_HANDOFF_DRIFT"}:
            focused_test = (
                "services/zoe-data/tests/test_main_multica_poll.py::"
                "test_record_blocked_multica_chain_creates_budget_followup_once"
            )
        elif blocker == "PROTOCOL_VIOLATION":
            focused_test = (
                "services/zoe-data/tests/test_main_multica_poll.py::"
                "test_record_blocked_multica_chain_creates_protocol_followup"
            )
        else:
            focused_test = "services/zoe-data/tests/test_main_multica_poll.py"
        blocker_followup_hint = (
            "  For engineering_blocker_followup tickets, inspect only"
            " services/zoe-data/main.py and services/zoe-data/tests/test_main_multica_poll.py"
            f" first. Run focused test: `PYTHONPATH=services/zoe-data python3 -m pytest -q {focused_test}`."
            " Use the existing repo/runtime environment only: do not create `.venv`, run `pip install`,"
            " or install missing Python packages from inside a worker. If that exact command reports"
            " a missing dependency/import that is not fixed by `PYTHONPATH=services/zoe-data`, call"
            " `kanban_block` with BLOCKER=TEST_ENVIRONMENT and the first import error instead of"
            " spending turns on environment repair."
            " If the focused test passes before any edit, do not inspect more blocker code:"
            " use at most three symbol greps total, up to four reads of the focused test file, and two reads per other named file, then edit the"
            " named harness file already in scope (usually services/zoe-data/main.py,"
            " services/zoe-data/tests/test_main_multica_poll.py, or"
            " services/zoe-data/executors/kanban_adapter.py) with the smallest guard, or call `kanban_block` with"
            " BLOCKER=ALREADY_COVERED and the passing test output. Do not rework blocker"
            " creation after a passing focused test.\n"
        )
    return (
        "- HARNESS FAST PATH: this is a Zoe/Hermes harness ticket. Do not spend budget"
        " searching ~/.local, Hermes internals, or broad worktree inventories unless a named file"
        " explicitly requires it. Start from this repo map:\n"
        "  * phase prompt/dispatch/task creation: services/zoe-data/executors/kanban_adapter.py\n"
        "  * task worktree creation and workspace_path pinning: services/zoe-data/worktree_bootstrap.py\n"
        "  * phase handoff/evidence parsing: services/zoe-data/pipeline_handoff.py\n"
        "  * journal/cache state: services/zoe-data/pipeline_store.py\n"
        "  * Multica ticket metadata/progress: services/zoe-data/multica_ticket_contract.py and multica_client.py\n"
        "  * poll/admission/blocked follow-ups: services/zoe-data/main.py, multica_admission.py,"
        " multica_poll_dispatch.py\n"
        f"{blocker_followup_hint}"
        "  For worktree-missing/retro fallback tickets, inspect kanban_adapter.py and"
        " worktree_bootstrap.py first; decide whether the fix belongs before Hermes starts,"
        " not inside the external Hermes worker. Start editing within 6 tool/model steps or"
        " call `kanban_block` with BLOCKER=IMPLEMENT_BUDGET and the missing locator.\n"
    )


def _issue_with_phase_handoff(issue: dict, phase: str, state: Any | None) -> dict:
    if state is None or phase not in {"verify", "review", "closeout", "retro"}:
        return issue
    evidence = list(getattr(state, "evidence", []) or [])
    already_covered = any(
        getattr(item, "metadata", {}).get("source") == "already_covered"
        for item in evidence
    )
    audit_profile = getattr(state, "evidence_profile", "") == "audit"
    description = str(issue.get("description") or "")
    if "Zoe pipeline handoff (authoritative):" in description:
        return issue

    # Authoritative PR URL from the implement phase's recorded evidence. This is
    # written synchronously when implement completes, so it is available even
    # before the poll loop's async write of pr_url onto the Multica ticket. The
    # downstream verify/review/closeout worker must not have to race that write
    # or hunt for the PR — without it, verify blocks "awaiting PR evidence" and
    # the chain bounces back to implement.
    def _pr_artifact(item: Any) -> str:
        if getattr(item, "kind", "") != "pr" or not getattr(item, "artifact", None):
            return ""
        return str(getattr(item, "artifact", "") or "").strip()

    # Prefer the implement phase's recorded PR (the authoritative one this
    # ticket is shipping); only fall back to any other recorded PR, then the
    # ticket. A later phase that recorded its own pr item must not shadow it.
    pr_url = (
        next(
            (
                _pr_artifact(item)
                for item in reversed(evidence)
                if _pr_artifact(item) and (getattr(item, "metadata", {}) or {}).get("phase") == "implement"
            ),
            "",
        )
        or next((_pr_artifact(item) for item in reversed(evidence) if _pr_artifact(item)), "")
        or _existing_pr_url(issue)
    )

    if not already_covered and not audit_profile:
        # Normal (real-PR) path: inject only the authoritative PR_URL so the
        # worker can act on the implement output directly. If there is no PR URL
        # yet there is nothing to hand off, so preserve the prior behavior.
        if not pr_url:
            return issue
        if phase == "verify":
            role_summary = (
                "Verify this PR: check out the PR head from your workspace_path, run the focused "
                "tests/validators against the diff, then call kanban_complete with TESTS/VALIDATORS; "
                "call kanban_block only on a concrete verification failure (not for missing evidence)."
            )
        elif phase == "review":
            role_summary = (
                "Review this PR: grade the diff and scope against the acceptance criteria and "
                "verify-phase evidence, write the review marker, then call kanban_complete with "
                "REVIEW=approved; call kanban_block only with a concrete review concern."
            )
        else:  # closeout / retro
            role_summary = (
                "This is the authoritative PR to close out: run the Greptile/merge closeout steps "
                "against it, then call kanban_complete; call kanban_block only on a concrete blocker."
            )
        handoff = (
            "Zoe pipeline handoff (authoritative):\n"
            f"PR_URL={pr_url}\n"
            "SUMMARY=Use this PR_URL as the authoritative PR for this ticket. Do not wait for, "
            f"re-derive, or hunt for a different PR. {role_summary}\n\n"
        )
        updated = dict(issue)
        updated["description"] = handoff + description
        return updated

    validator_summary = next(
        (getattr(item, "summary", "") for item in reversed(evidence) if getattr(item, "kind", "") == "validator"),
        "",
    )
    if phase == "verify" and already_covered:
        handoff = (
            "Zoe pipeline handoff (authoritative):\n"
            "IMPLEMENT_ALREADY_COVERED=1\n"
            "AUDIT_ONLY=1\n"
            "PR_URL=\n"
            "TESTS=focused intent helper and router tests passed before edit; no PR required\n"
            "VALIDATORS=run focused validation from this verify worktree\n"
            "SUMMARY=Implementation was already present. Do not rerun zoe_apply_intent_gap_contract.py. "
            "Verify the acceptance criteria with focused checks from workspace_path, then call kanban_complete "
            "with VALIDATORS and TESTS if they pass; call kanban_block only if focused validation fails.\n\n"
        )
    else:
        handoff = (
            "Zoe pipeline handoff (authoritative):\n"
            f"IMPLEMENT_ALREADY_COVERED={1 if already_covered else 0}\n"
            f"AUDIT_ONLY={1 if audit_profile else 0}\n"
            "PR_URL=\n"
            f"VERIFY_EVIDENCE={validator_summary or 'audit/no-PR evidence recorded in pipeline journal'}\n"
            "SUMMARY=This is the no-code/no-PR audit path. Do not hunt for PRs, branches, or rerun "
            "zoe_apply_intent_gap_contract.py. Use the recorded pipeline evidence and acceptance criteria, "
            "then complete this phase or block with a concrete evidence mismatch.\n\n"
        )
    updated = dict(issue)
    updated["description"] = handoff + description
    return updated


def _intent_gap_implement_hint(issue: dict | None = None, *, phase: str = "implement") -> str:
    issue = issue or {}
    title = str(issue.get("title") or "")
    haystack = " ".join(
        [
            str(issue.get("identifier") or ""),
            title,
            str(issue.get("description") or ""),
        ]
    ).lower()
    if "intent gap" not in haystack and "intent-gap" not in haystack:
        return ""
    edit_deadline = (
        " Start editing within 4 tool/model steps after `kanban_show`.\n"
        if phase == "implement"
        else " After the existing-PR checkout checks succeed, start the focused revision edit within 4 tool/model steps.\n"
    )
    say_exactly_contract = ""
    if (
        _SAY_EXACTLY_INTENT_GAP_TITLE_RE.search(title)
        or "say exactly: zoe chat integration ok" in haystack
        or ("intent gap" in title.lower() and "say exactly" in title.lower())
    ):
        say_exactly_contract = (
            " Concrete edit contract for this exact-repeat gap: update `_AGENT_CHAT_RE` so"
            " `Say exactly: Zoe chat integration ok` routes to `extend_capability` through"
            " the existing open-domain branch. Preferred deterministic path: after `kanban_show`,"
            " your NEXT tool call must be the terminal command"
            " `cd <workspace_path> && python3 ./scripts/maintenance/zoe_apply_intent_gap_contract.py say_exactly --repo-root ."
            " --run-focused-checks --kanban-task <task_id_from_kanban_show>`"
            " using the exact task `workspace_path`. Do not narrate, wait, plan, or heartbeat first."
            " If you cannot run that exact helper as the next tool call, call `kanban_block`"
            " with BLOCKER=INTENT_GAP_HELPER_UNAVAILABLE. Never run this from the live"
            " checkout. The helper runs `python3 -m py_compile services/zoe-data/intent_router.py`"
            " and `PYTHONPATH=services/zoe-data python3 -m pytest -q"
            " services/zoe-data/tests/test_intent_open_domain.py` for you."
            " The helper is the edit/context authority for this fast path; do not read"
            " `services/zoe-data/intent_router.py` after it runs. If the helper reports"
            " `terminal_action: kanban_block:ALREADY_COVERED`, stop immediately; the task"
            " already has its terminal Kanban action. Do not inspect more files, call"
            " `kanban_complete`, or open a PR. Do not add a bespoke"
            " say/echo executor in this ticket; the acceptance goal is routing the request"
            " to the agent path while preserving the raw phrase.\n"
        )
    joke_contract = ""
    if _JOKE_INTENT_GAP_TITLE_RE.search(title):
        joke_contract = (
            " Concrete edit contract for this joke gap: update `_AGENT_CHAT_RE` so"
            " `Tell me a joke.`, `Tell me a joke`, and `Tell me another joke.` route"
            " to `extend_capability` through the existing open-domain/creative branch."
            " Preferred deterministic path: from the repo root, run"
            " `python3 ./scripts/maintenance/zoe_apply_intent_gap_contract.py joke --repo-root .`, then"
            " immediately run `python3 -m py_compile services/zoe-data/intent_router.py`"
            " and `PYTHONPATH=services/zoe-data python3 -m pytest -q"
            " services/zoe-data/tests/test_intent_open_domain.py`."
            " Add or extend the focused detect_intent test coverage for those examples;"
            " the expected result is `Intent(\"extend_capability\", {\"raw\": <original text>})`."
            " Do not add a joke bank or a brittle per-joke executor in this ticket; the"
            " acceptance goal is routing the creative request to the agent path.\n"
        )
    return (
        "- INTENT-GAP IMPLEMENT FAST PATH: this ticket is already scoped by scout as"
        " a routing/intent gap. Do not re-scout the repo. Start from"
        " services/zoe-data/intent_router.py and the nearest intent_router tests;"
        " inspect each at most once, then patch the intent route and focused tests."
        " For creative/open-domain gaps like jokes, use these exact anchors:"
        " `_AGENT_CHAT_RE` and the `Open-domain Q&A / creative` branch in"
        " `detect_intent`. Your first search should be one of those anchors;"
        " do not grep `_CALCULATE_`, `_execute_`, or unrelated domain sections"
        " for creative intent gaps."
        f"{say_exactly_contract}"
        f"{joke_contract}"
        " If those files are not the right location, call `kanban_block` with"
        " BLOCKER=IMPLEMENT_BUDGET and the missing locator instead of exploring."
        f"{edit_deadline}"
    )


def _intent_gap_first_action_preamble(issue: dict | None = None) -> str:
    """Put the deterministic helper command before issue evidence paths."""
    issue = issue or {}
    title = str(issue.get("title") or "")
    description = str(issue.get("description") or "")
    haystack = f"{title} {description}".lower()
    if not (
        _SAY_EXACTLY_INTENT_GAP_TITLE_RE.search(title)
        or "say exactly: zoe chat integration ok" in haystack
        or ("intent gap" in title.lower() and "say exactly" in title.lower())
    ):
        return ""
    return (
        "CRITICAL FIRST ACTION FOR THIS INTENT-GAP TICKET:\n"
        "1. Call `kanban_show`.\n"
        "2. As the very next tool call, run exactly:\n"
        "`cd <workspace_path> && python3 ./scripts/maintenance/zoe_apply_intent_gap_contract.py say_exactly --repo-root . "
        "--run-focused-checks --kanban-task <task_id_from_kanban_show>`\n"
        "Do not read issue evidence files, grep, inspect code, or open the live checkout before this helper.\n\n"
    )


def _is_bounded_goal_phase(phase: str, issue: dict | None = None) -> bool:
    """Return True when this phase should run in bounded goal mode with one retry."""
    return phase == "implement" and _is_code_audit_actionable(_ticket_metadata(issue))


def _goal_mode_args(phase: str, issue: dict | None = None) -> list[str]:
    """Return bounded Hermes goal-mode args for phases that benefit from one continuation."""
    if _is_bounded_goal_phase(phase, issue):
        return ["--goal", "--goal-max-turns", "2"]
    return []


def _max_retries_for_phase(phase: str, issue: dict | None = None) -> str:
    """Return the Hermes consecutive-failure limit for this task."""
    if _is_bounded_goal_phase(phase, issue):
        # Goal mode gives these tickets one bounded continuation in the same worktree.
        return "2"
    return "1"


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


def _with_recovered_log_budget(task_id: str, phase: str, detail: dict[str, Any]) -> dict[str, Any]:
    """Attach Hermes log evidence when a silent exit was really a budget stop."""
    reason = phase_budget_reason_from_log(task_id, phase)
    if not reason:
        return detail
    enriched = dict(detail)
    latest = str(enriched.get("latest_summary") or "").strip()
    enriched["latest_summary"] = f"{latest}\n{reason}".strip() if latest else reason
    if not (enriched.get("logs") or enriched.get("log") or enriched.get("log_tail")):
        tail = task_log_tail(task_id)
        if tail:
            enriched["log_tail"] = tail
    return enriched


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


# The kanban/git CLI spawns below must never fork() on the event loop thread.
#
# asyncio.create_subprocess_exec performs the fork+exec SYNCHRONOUSLY inside the
# coroutine, i.e. on the event loop thread. zoe-data is a large (multi-GB RSS)
# multi-threaded process (Chroma, fastembed, watchdog, thread pools); fork() of
# such a process can deadlock post-fork/pre-exec — the child hangs on an atfork
# lock some other thread held at fork time and never reaches exec, while the
# parent blocks forever reading the exec-status pipe. Seen live on 2026-06-29:
# one wedged `hermes kanban` fork from the poll loop froze the event loop, the
# accept queue filled, and every endpoint (/health, /api/memories/for-prompt)
# timed out until restart. The asyncio.wait_for guard in main.py could not fire
# because the loop thread itself was blocked.
#
# Running the spawn in this small dedicated pool contains the hazard: a wedged
# fork costs one bounded worker thread, the event loop keeps serving, and the
# outer wait_for in _spawn_cli still bounds the awaiting coroutine.
_CLI_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="kanban-cli"
)

# `hermes kanban` calls previously had no bound at all; generous because the
# box can be slow under memory pressure, but finite because infinite was the bug.
_KANBAN_CLI_TIMEOUT_S = 120.0

# Extra slack for the coroutine-side wait_for on top of subprocess.run()'s own
# timeout: run()'s timeout only starts once Popen() returns, so it can never
# fire if the fork itself wedges — the outer bound covers that case.
_CLI_WAIT_GRACE_S = 15.0


async def _spawn_cli(
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
    timeout: float,
) -> "subprocess.CompletedProcess[bytes]":
    """Run a CLI command off the event loop, bounded even if fork() wedges.

    Raises asyncio.TimeoutError if the worker thread does not come back within
    timeout + grace (fork wedged), subprocess.TimeoutExpired if the child ran
    too long (run() kills it), or OSError if the executable could not start.
    """
    loop = asyncio.get_running_loop()

    def _blocking() -> "subprocess.CompletedProcess[bytes]":
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    return await asyncio.wait_for(
        loop.run_in_executor(_CLI_POOL, _blocking),
        timeout=timeout + _CLI_WAIT_GRACE_S,
    )


class KanbanAdapter:
    """Executor adapter backed by the Hermes Kanban board."""

    name = NAME

    async def _run(self, args: list[str], *, expect_json: bool = False) -> Any:
        # PHASE-2 SEAM (docs/architecture/multica-executor-migration.md §2):
        # the ONLY Hermes coupling in this adapter is this CLI call site. With
        # ZOE_KANBAN_BACKEND=executor the identical verb surface is served by
        # the Zoe-native executor against Multica's own agent_task_queue, so
        # every phase, gate and deterministic override above stays untouched.
        # Default is `hermes` — shipping this file changes no behaviour, and
        # the revert path is one env var.
        if _kanban_backend() == "executor":
            from executors import executor_queue_backend

            try:
                return await executor_queue_backend.run_kanban_command(
                    args, expect_json=expect_json
                )
            except executor_queue_backend.ExecutorBackendError as exc:
                # Surface as the adapter's own error type so all existing
                # recovery paths treat it exactly like a CLI failure.
                raise KanbanCLIError(f"executor backend: {exc}") from exc
        cmd = [hermes_bin(), "kanban", "--board", _board(), *args]
        env = dict(os.environ)
        env.setdefault("HERMES_KANBAN_BOARD", _board())
        try:
            proc = await _spawn_cli(
                cmd, cwd=str(zoe_repo_root()), env=env, timeout=_KANBAN_CLI_TIMEOUT_S
            )
        except OSError as exc:
            raise KanbanCLIError(
                f"`hermes kanban {' '.join(args)}` could not start: {exc}"
            ) from exc
        except (subprocess.TimeoutExpired, asyncio.TimeoutError) as exc:
            raise KanbanCLIError(
                f"`hermes kanban {' '.join(args)}` timed out after "
                f"{_KANBAN_CLI_TIMEOUT_S:.0f}s"
            ) from exc
        stdout = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
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

    async def _run_worktree_command(
        self,
        args: list[str],
        *,
        cwd: Path,
        timeout: float = 45.0,
    ) -> str:
        try:
            proc = await _spawn_cli(args, cwd=str(cwd), timeout=timeout)
        except (subprocess.TimeoutExpired, asyncio.TimeoutError) as exc:
            raise KanbanCLIError(f"`{' '.join(args)}` timed out after {timeout:.0f}s") from exc
        stdout = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            raise KanbanCLIError(f"`{' '.join(args)}` exited {proc.returncode}: {stderr or stdout}")
        return stdout

    async def _phases_for_ref(self, external_ref: str) -> dict[str, dict]:
        tasks = await self._run(["list", "--json"], expect_json=True)
        if isinstance(tasks, list):
            rows = tasks
        elif isinstance(tasks, dict) and isinstance(tasks.get("tasks"), list):
            rows = tasks["tasks"]
        else:
            raise KanbanCLIError(f"kanban list returned malformed JSON: {tasks!r}")
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
        workspace = _workspace_for_phase(phase)
        workspace_label = "main repo checkout" if workspace.startswith("dir:") else "git worktree"
        common = (
            f"Multica issue: {identifier} (id {issue_id})\n"
            f"zoe-ref: multica:{issue_id}:{phase}\n"
            f"zoe-chain: v4\n"
            f"{escalation_marker}"
            f"{mode_note}"
            f"Repo: {zoe_repo_root()}  |  Base branch: main  |  Workspace: {workspace_label}\n\n"
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
                "- INTENT GAP FAST PATH: if the ticket is an intent-gap/evolution proposal such as"
                " \"Tell me a joke\", do not map the repo. Use the ticket evidence plus at most one"
                " focused lookup of the routing/intent file named by the issue or obvious from the"
                " title, then hand off. Include IMPLEMENTATION_REQUIRED=true unless the exact"
                " behavior is already handled by merged code.\n"
                "- Keep this phase bounded: run at most one focused codebase-memory/doc lookup and no broad repo crawl.\n"
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
        if phase in {"implement", "implement_revision"}:
            overnight_hint = _overnight_implement_cost_hint() if mode == "overnight" else ""
            code_audit_hint = _code_audit_implement_hint(issue)
            harness_hint = _harness_implement_hint(issue)
            intent_gap_hint = _intent_gap_implement_hint(issue, phase=phase)
            intent_gap_first_action = (
                _intent_gap_first_action_preamble(issue) if phase == "implement" else ""
            )
            # Implement body intentionally omits full prior-phase logs; workers should
            # call kanban_show and read SCOUT_SUMMARY= from scout metadata when present.
            return intent_gap_first_action + common + overnight_hint + (
                "You are the implementer (zoe-coder).\n"
                "- COMPLETION MEANS YOU SHIP A PR (read this first): when your edits are made and"
                " focused tests pass, you MUST commit, publish the branch, and open ONE PR (exact"
                " push/PR commands are given below), then call `kanban_complete` with PR_URL=. Greptile"
                " and CI review the PR automatically — there is NO separate human-review step to wait"
                " for. Stopping with a finished diff and calling `kanban_block` to 'block for review',"
                " 'hand off for review', or because a human should look at it is a PROTOCOL VIOLATION"
                " that wastes the run: open the PR yourself instead. `kanban_block` is ONLY for genuine"
                " blockers where you cannot ship (dirty tree, missing auth, ambiguous product decision,"
                " repeated test failure, scope too broad).\n"
                "- ALREADY DONE / NO CHANGE NEEDED: if the acceptance criteria are already met on the base"
                " branch and no code change is required (e.g. the fix already landed, or the target file"
                " does not exist in the repo), call `kanban_block` with BLOCKER=ALREADY_COVERED and the"
                " evidence (e.g. the passing test output). NEVER call `kanban_complete` without a PR_URL —"
                " a completion with an empty diff and no PR fails the evidence gate and strands the ticket.\n"
                f"{code_audit_hint}"
                f"{harness_hint}"
                f"{intent_gap_hint}"
                "- AUDIT/SMOKE FAST PATH: only if the title/body explicitly says audit-only, smoke test,"
                " no code change, or uses trace/map with an audit/no-code qualifier, do not run a"
                " codebase-memory query or repo exploration first. Complete in one bounded handoff with"
                " TOOLS_USED=audit-read, PR_URL= blank, AUDIT_ONLY=1, TESTS=not applicable/audit-only,"
                " and SUMMARY= findings. Do not open a PR.\n"
                "- Start with `kanban_show` to confirm this task id.\n"
                "- SCOUT HANDOFF FAST PATH: when `kanban_show` includes SCOUT_SUMMARY with an exact"
                " file/function and a narrow implementation plan, treat that as the accepted context."
                " Do not re-scout, re-map, or repeatedly read the same file. For intent-gap tickets,"
                " inspect the named routing/intent file at most once, then edit, add the focused test,"
                " and open the PR. If you cannot start editing within 4 tool/model steps after"
                " `kanban_show`, call `kanban_block` with BLOCKER=IMPLEMENT_BUDGET and the missing"
                " decision instead of continuing exploration.\n"
                "- SMALL EXPLICIT CODE FAST PATH: when the ticket names the exact file, helper,"
                " function, or focused test to change, inspect only those named files plus the"
                " nearest existing test. Do not run a broad codebase-memory query, repo crawl, or unrelated"
                " search. Start editing within 6 tool/model steps; if the change is still unclear by"
                " then, call `kanban_block` with BLOCKER=IMPLEMENT_BUDGET instead of exploring further.\n"
                "- For broad or ambiguous code-changing tickets only: read the charter and run at"
                " most one focused codebase-memory map (codebase-memory who-calls-what/architecture"
                " + Serena for symbol read/edit, over raw grep).\n"
                "- Use opensrc for any third-party library source before guessing APIs.\n"
                "- Make the smallest reviewable change; do NOT rewrite existing functions into bloat;"
                " reuse service-layer helpers.\n"
                "- EDIT SAFETY LOOP: after every patch, immediately run the narrowest syntax check"
                " for touched Python files, usually `python3 -m py_compile <file>`, before any"
                " second patch or more exploration. If syntax fails, revert or fix that exact patch"
                " once; if the insertion point or indentation is still unclear, call `kanban_block`"
                " with BLOCKER=IMPLEMENT_EDIT_SAFETY instead of continuing. Never leave a malformed"
                " partial edit and keep exploring.\n"
                "- If the task needs more than one PR or a large refactor, call `kanban_block` and"
                " ask for a split — do not absorb unbounded work in one implement run.\n"
                "- Do NOT create additional Hermes/Kanban tasks, scaffold subtasks, or sibling work items."
                " If scope needs another task, use the NEEDS_SPLIT/SPLIT_PACKET block below and stop.\n"
                "- Validate: `python3 tools/audit/validate_structure.py` and focused tests for touched modules.\n"
                f"- EXISTING PR REVISION FAST PATH: if the ticket block already contains `pr_url`/PR_URL,"
                " this is a revision task. Do not rediscover the original fix and do not create a new PR."
                " This revision path takes precedence over all file inspection: after `kanban_show`,"
                " do not read, grep, or edit files until the existing PR checkout below succeeds."
                " Zoe dispatch pre-checks this task worktree to the existing PR head before the worker"
                " starts. Run `gh pr view <PR_URL> --json url,number,headRefName,headRefOid,"
                f"headRepositoryOwner,mergeStateStatus,statusCheckRollup` and `{_greptile_mcp_bin()}"
                " pr-comments --unaddressed-only jason-easyazz/zoe-ai-assistant <number>`."
                " That Greptile command may exit nonzero when it prints unresolved comments; read stdout"
                " as the action list instead of treating the nonzero exit alone as failure. Run"
                " `git rev-parse HEAD` and compare it to the gh `headRefOid`; if it does not match,"
                " immediately call `kanban_block` with BLOCKER=PR_REVISION_CHECKOUT_FAILED."
                " Do not run `gh pr checkout`, `git checkout`, `git fetch`, or `git reset` yourself"
                " in this phase; if the pre-checked worktree is wrong, block instead of repairing it."
                " Address only the unresolved review/Greptile/CI action list, run focused tests plus validators."
                " Before pushing, self-review the full diff against `.greptile/rules.md` (test hygiene +"
                " Pre-PR checklist) so the re-review lands clean rather than drawing a new round of comments. Commit,"
                " `git push origin HEAD:<headRefName>` (use headRefName from the gh pr view output above),"
                " and report the SAME PR_URL. Run Python tests with"
                " `PYTHONPATH=services/zoe-data python3 -m pytest ...`. For tests involving module-level"
                " env-derived constants, patch the module variable with monkeypatch/setattr after import;"
                " do not set os.environ after importing the module and expect the already-loaded constant"
                " to change. If the action list is ambiguous, call `kanban_block` with"
                " BLOCKER=PR_REVISION_BLOCKED.\n"
                "- You already run on an isolated git worktree branch. After `kanban_show`, run"
                " `cd <workspace_path> && pwd && git branch --show-current` before reading, editing,"
                " testing, committing, pushing, or opening a PR. The working directory must match the"
                " exact task `workspace_path` shown by Kanban. File reads must use paths under that"
                " workspace_path, never absolute live-checkout paths. If it shows the live repo checkout"
                " instead of the task worktree, call `kanban_block` with BLOCKER=WORKTREE_PATH_VIOLATION"
                " immediately. NEVER touch the live checkout"
                f" `{zoe_repo_root()}` in ANY command — not via `cd`, not via"
                f" `git -C {zoe_repo_root()} ...`, not as an absolute-path argument, and not for"
                " orientation/inspection commands like `git worktree list` or `git status`. To orient"
                " yourself, run those against your OWN worktree only (e.g. `cd <workspace_path> &&"
                " git status` or `git -C <workspace_path> worktree list`). A SINGLE command referencing"
                f" `{zoe_repo_root()}` (for git, test, patch, read, or PR purposes) trips"
                " BLOCKER=WORKTREE_PATH_VIOLATION and ends the run.\n"
                "- SELF-REVIEW BEFORE PR (target: Greptile 5/5, zero comments): before"
                " `gh pr create`, read `.greptile/rules.md` (Pre-PR Self-Review Checklist plus the"
                " Python And Test Hygiene rules) and `.greptile/config.json` rules, then review your own"
                " `git diff main` against every item — scope, test hygiene (monkeypatch.setattr for"
                " module globals, no os.environ leaks, consistent across the whole file), patterns, no"
                " junk/backup files, no secrets, conventional commit. Fix every violation now; the PR"
                " should be clean on the first review, not after Greptile comments.\n"
                "- Commit verified changes from the isolated worktree, then publish"
                " the branch and open ONE small PR (do not merge) with these commands"
                " unless a phase fast path above explicitly requires a chained ship command:\n"
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
                "  Security-sensitive changes still complete implement after the PR is opened; PR review/Greptile is the review gate.\n"
                "  Do not call `kanban_block` merely because a human/security reviewer should inspect the PR.\n"
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
                "- PR_URL FAST PATH: if `kanban_show` or the ticket block includes PR_URL, do not"
                " hunt branches or commits. Use `gh pr view <url> --json url,headRefName,headRefOid,"
                "mergeStateStatus,statusCheckRollup` and inspect the PR diff/checks directly, then"
                " run the validators below and complete/block from that evidence.\n"
                "- Start with `kanban_show` to read the implementer handoff and PR_URL.\n"
                "- Before any repo command, run `pwd && git branch --show-current`; if pwd is not the"
                " exact task `workspace_path`, call `kanban_block` with BLOCKER=WORKTREE_PATH_VIOLATION."
                " Every repo/test/validator command must be run from the exact task `workspace_path`,"
                " e.g. `cd <workspace_path> && PYTHONPATH=services/zoe-data python3 -m pytest ...`;"
                " do not use relative `cd services/zoe-data` from an unknown cwd.\n"
                "- Do not redesign or refactor. Run the declared tests and the minimum extra checks needed"
                " for the touched surface.\n"
                "- MANDATORY for any PR (PR_URL present / code or test changes): you MUST check out the"
                " PR head into your workspace_path and actually RUN the focused pytest for the changed"
                " files (e.g. `cd <workspace_path> && PYTHONPATH=services/zoe-data python3 -m pytest -q"
                " <changed test/module paths>`), then report the exact command(s) and pass/fail in TESTS=."
                " The structure validators (validate_structure/validate_critical_files) are NOT a"
                " substitute for running the PR's tests — completing with only VALIDATORS and no real"
                " TESTS= pytest run is REJECTED by the evidence gate (verify requires `test` evidence)."
                " If you cannot run the focused tests (e.g. cannot check out the PR), call `kanban_block`"
                " with BLOCKER=VERIFY_BUDGET and say so — do NOT call `kanban_complete` without a pytest run.\n"
                "- Always run and record `python3 tools/audit/validate_structure.py` and"
                " `python3 tools/audit/validate_critical_files.py` in VALIDATORS unless the task"
                " is explicitly audit-only with no code/config changes.\n"
                "- Required evidence in your final `kanban_complete` metadata: TESTS, VALIDATORS,"
                " PR_URL, and a pass/fail summary. Include exact commands and outcomes.\n"
                "- If tests fail, evidence is missing, the PR is absent for a code task, or the task needs"
                " product clarification, call `kanban_block` with BLOCKER= and the failing output.\n"
                "- If Greptile/CI has unresolved comments or failures, call `kanban_block` with"
                " BLOCKER=PR_REVIEW_REQUIRED, PR_URL, and a concise action list; do not spend this"
                " phase rewriting the PR.\n"
                "- If you cannot reach a pass/block decision within 14 tool/model steps, call"
                # Keep in sync with _TOOL_DEFAULTS["verify"] - terminal grace
                # in kanban_phase_budget.py.
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
                "- GRADE AGAINST THE SHARED STANDARD: read `.greptile/rules.md` (Pre-PR Self-Review"
                " Checklist + Python And Test Hygiene) and `.greptile/config.json` rules, then check the"
                " diff against every item. If the diff would draw a Greptile comment — test-hygiene"
                " (direct module-global assignment instead of monkeypatch.setattr, os.environ leaks,"
                " inconsistent pattern across a file), scope creep, junk files, or a principle violation"
                " — do NOT approve. Call `kanban_block` with BLOCKER=REVIEW_STANDARD and a concrete,"
                " file:line action list so implement fixes it before the Greptile gate, rather than"
                " letting closeout absorb a comment round. The goal is 5/5 with zero comments on first"
                " review.\n"
                "- If you cannot reach a pass/block decision within 8 tool/model steps, call"
                " `kanban_block` with BLOCKER=REVIEW_BUDGET and the missing evidence.\n"
                "- On approval, write the mechanical review marker before completing:\n"
                f"    PYTHONPATH={zoe_repo_root()}/services/zoe-data python3"
                f" {zoe_repo_root()}/services/zoe-data/pipeline_evidence_commands.py"
                f" mark-reviewed multica:{issue_id} --critical-count <N> --summary \"<short verdict>\"\n"
                "  Replace <N> with the actual unresolved critical finding count; approval requires zero.\n"
                "  This command shape is complete; do not call `--help` after it. If it succeeds,"
                " immediately call `kanban_complete` in the same turn with a non-empty summary.\n"
                "- For audit/no-PR approval, the complete call must include a summary/result, for example:\n"
                "  kanban_complete(summary=\"REVIEW=approved\\nSUMMARY=audit/no-PR evidence matches acceptance criteria\", "
                "result={\"review\":\"approved\",\"summary\":\"audit/no-PR evidence matches acceptance criteria\"}).\n"
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
            "  Repair-capable modes auto-switch to the matching PR branch worktree when it exists; if"
            " no matching worktree is found, create/check out that PR worktree before rerunning.\n"
            "  Apply fixes yourself when the guard returns ESCALATE_HERMES or PACKET_READY; use --packet-only"
            " only to hand off to a cheap Cursor runner.\n"
            "  Re-trigger review when needed via the guarded loop only:\n"
            "    scripts/maintenance/run_greploop_guard.sh --pr N --once\n"
            "  Do not call Greptile trigger-review directly; the guard dedupes running/successful reviews"
            " and records state for other agents.\n"
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

    async def _maybe_auto_block_dead_worker(
        self,
        task_id: str,
        phase: str,
        row: dict[str, Any],
        detail: dict[str, Any],
    ) -> bool:
        """Reap a zombie 'running' task whose worker process has died.

        A crashed/killed worker (e.g. out-of-context HTTP error, OOM) can leave its
        run 'running' forever, holding the single lane. Block it so the row becomes
        terminal — which both frees the lane and lets the deterministic verify/
        review/closeout overrides engage (they fire on a terminal row).

        Covered by tests/test_kanban_adapter.py::test_auto_block_dead_worker_*
        (blocks zombie, no-op when worker alive / row not running / kill-switch
        off, swallows KanbanCLIError); detector covered by
        tests/test_kanban_phase_budget.py::test_dead_worker_reason_*."""
        if not _REAP_DEAD_WORKERS:
            return False
        if (row.get("status") or "").lower() != "running":
            return False
        reason = dead_worker_reason(detail)
        if not reason:
            return False
        reason = f"BLOCKER={reason}"
        try:
            await self._run(["block", task_id, reason])
        except KanbanCLIError as exc:
            logger.warning(
                "kanban_adapter: dead-worker reap failed for %s (%s): %s", task_id, phase, exc
            )
            return False
        row["status"] = "blocked"
        row["block_reason"] = reason
        logger.warning(
            "kanban_adapter: reaped zombie running task %s (%s): %s", task_id, phase, reason
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

    def _pushed_branch_without_pr_handoff(self, task_id: str) -> bool:
        """True when a worker pushed HEAD then got interrupted before PR handoff."""
        log = latest_log_session(task_id, max_lines=120)
        shell_commands: list[str] = []
        for line in log.splitlines():
            match = _SHELL_TOOL_LINE_RE.search(line)
            if match:
                shell_commands.append(match.group("command"))
        if "PR_URL=" in log:
            return False
        for command in shell_commands:
            if "git push -u origin HEAD" not in command:
                continue
            if "gh pr create" in command:
                return True
            if re.search(r"\[exit\s+[1-9]\d*\]", command, re.IGNORECASE):
                continue
            return True
        return False

    async def _complete_recovered_pr_handoff(
        self,
        task_id: str,
        row: dict[str, Any],
        pr_url: str,
        *,
        branch: str | None = None,
        recovery: str = "pushed_branch_without_pr_handoff",
        summary_note: str = "Recovered PR handoff after worker interruption following git push",
    ) -> str:
        summary = (
            f"PR_URL={pr_url}\n"
            "BLOCKER=\n"
            "TESTS=recovered; downstream verify/review must validate\n"
            f"SUMMARY={summary_note}"
        )
        metadata = {
            "pr_url": pr_url,
            "recovery": recovery,
        }
        if branch:
            metadata["branch"] = branch
        await self._run(
            [
                "complete",
                "--result",
                summary,
                "--summary",
                summary,
                "--metadata",
                json.dumps(metadata, sort_keys=True),
                task_id,
            ]
        )
        row["status"] = "done"
        row["result"] = summary
        row["block_reason"] = None
        return pr_url

    async def _maybe_recover_pushed_pr(
        self,
        task_id: str,
        phase: str,
        row: dict[str, Any],
        *,
        issue: dict | None = None,
        detail: dict[str, Any] | None = None,
    ) -> str | None:
        """Create/record a PR after the worker pushed but lost the terminal handoff."""
        if phase != "implement" or (row.get("status") or "").lower() != "blocked":
            return None
        if not self._pushed_branch_without_pr_handoff(task_id):
            return None

        detail = detail or {}
        for haystack in (
            json.dumps(detail.get("latest_summary") or ""),
            json.dumps(detail.get("comments") or []),
            str(row.get("result") or ""),
        ):
            match = _GITHUB_PR_URL_RE.search(haystack)
            if match:
                try:
                    return await self._complete_recovered_pr_handoff(task_id, row, match.group(0))
                except Exception as exc:  # noqa: BLE001 - recovery must not break normal poll.
                    logger.warning(
                        "kanban_adapter: pushed PR recovery from existing URL failed for %s: %s",
                        task_id,
                        exc,
                    )
                    return None

        try:
            from worktree_bootstrap import worktree_path

            task = detail.get("task") if isinstance(detail.get("task"), dict) else {}
            wt_path = Path(
                row.get("workspace_path")
                or task.get("workspace_path")
                or worktree_path(task_id)
            ).expanduser()
            if not wt_path.exists():
                return None
            branch = (
                await self._run_worktree_command(
                    ["git", "branch", "--show-current"],
                    cwd=wt_path,
                    timeout=10,
                )
            ).strip()
            if not branch:
                return None

            body = (
                "Recovered by Zoe after Hermes pushed the branch but was interrupted "
                "before `gh pr create`/`kanban_complete`.\n\n"
                f"Kanban task: `{task_id}`\n"
                f"Branch: `{branch}`"
            )
            pr_url = await self._pr_url_from_worktree(
                task_id, wt_path, issue=issue, row=row, body=body
            )
            if not pr_url:
                return None
            return await self._complete_recovered_pr_handoff(task_id, row, pr_url, branch=branch)
        except Exception as exc:  # noqa: BLE001 - recovery must not break normal poll.
            logger.warning("kanban_adapter: pushed PR recovery failed for %s: %s", task_id, exc)
            return None

    async def _pr_url_from_worktree(
        self,
        task_id: str,
        wt_path: Path,
        *,
        issue: dict | None,
        row: dict[str, Any],
        body: str,
    ) -> str | None:
        """Return the worktree branch's existing PR URL, or open one. None on failure.

        Tries ``gh pr view`` first (a PR may already exist for the branch), then
        falls back to ``gh pr create`` against the repo's default base branch.
        Shared by the pushed-branch and unshipped-diff recovery paths.
        """
        try:
            pr_url = (
                await self._run_worktree_command(
                    ["gh", "pr", "view", "--json", "url", "--jq", ".url"],
                    cwd=wt_path,
                    timeout=20,
                )
            ).strip()
        except KanbanCLIError:
            pr_url = ""
        if pr_url and not _GITHUB_PR_URL_RE.search(pr_url):
            logger.warning(
                "kanban_adapter: gh pr view returned no PR URL for recovered task %s: %s",
                task_id,
                pr_url,
            )
            pr_url = ""
        if not pr_url:
            identifier = (issue or {}).get("identifier") or row.get("title") or task_id
            title = str(row.get("title") or (issue or {}).get("title") or identifier)
            if not title.startswith(str(identifier)):
                title = f"{identifier}: {title}"
            # No --base: gh defaults to the repo's default branch (main), which is
            # what task worktrees branch off. Do NOT derive the base from
            # HEAD@{upstream}: `git push -u origin <branch>` sets the upstream to
            # the task branch itself, so that would yield `gh pr create --base
            # wt/<task_id>` (head == base) and fail.
            pr_url = (
                await self._run_worktree_command(
                    ["gh", "pr", "create", "--title", title[:240], "--body", body],
                    cwd=wt_path,
                    timeout=45,
                )
            ).strip()
        match = _GITHUB_PR_URL_RE.search(pr_url)
        if not match:
            return None
        return match.group(0)

    async def _maybe_recover_unshipped_diff(
        self,
        task_id: str,
        phase: str,
        row: dict[str, Any],
        *,
        issue: dict | None = None,
        detail: dict[str, Any] | None = None,
    ) -> str | None:
        """Salvage a finished implement diff the worker never committed/pushed.

        ``_maybe_recover_pushed_pr`` handles a worker that pushed then lost the PR
        handoff. This handles the other interruption: the worker edited (and often
        wrote tests) but exited — e.g. turn budget — before ``git add/commit/push``
        or ``gh pr create``, leaving a complete diff in the isolated task worktree.
        Rather than block (and let a fresh-worktree resume discard the work and
        re-spend), commit + push it and open a PR so the chain advances to verify.

        Safety: operates ONLY inside the task's own isolated worktree branch
        (``wt/<task_id>``); never main/master, never the live checkout, never
        ``--force``; and never opens an empty PR (requires real unpushed content).
        """
        if phase != "implement" or (row.get("status") or "").lower() != "blocked":
            return None
        try:
            from worktree_bootstrap import worktree_branch, worktree_path

            # A pushed branch is the other recovery's job; a handed-off PR needs
            # nothing. Kept inside the try so a latest_log_session failure can
            # never escape into poll() (recovery is best-effort).
            log = latest_log_session(task_id, max_lines=120)
            if "PR_URL=" in log:
                return None
            task = detail.get("task") if isinstance((detail or {}).get("task"), dict) else {}
            wt_path = Path(
                row.get("workspace_path")
                or task.get("workspace_path")
                or worktree_path(task_id)
            ).expanduser()
            if not wt_path.exists():
                return None
            branch = (
                await self._run_worktree_command(
                    ["git", "branch", "--show-current"],
                    cwd=wt_path,
                    timeout=10,
                )
            ).strip()
            # Hard safety gate: only the task's own worktree branch. The live
            # checkout and shared branches are never named wt/<task_id>, so this
            # fails closed if workspace_path is ever wrong.
            if not branch or branch in {"main", "master"} or branch != worktree_branch(task_id):
                return None

            dirty = bool(
                (
                    await self._run_worktree_command(
                        ["git", "status", "--porcelain"], cwd=wt_path, timeout=20
                    )
                ).strip()
            )
            if dirty:
                await self._run_worktree_command(["git", "add", "-A"], cwd=wt_path, timeout=30)
                commit_msg = (
                    f"harness: salvage unshipped implement diff for {task_id}\n\n"
                    "Auto-committed by the Multica->Hermes harness because the implement "
                    "worker left a complete diff but exited before committing/pushing."
                )
                await self._run_worktree_command(
                    ["git", "commit", "-m", commit_msg], cwd=wt_path, timeout=30
                )

            # Require real content not already on a remote, or we'd open an empty PR.
            try:
                unpushed = (
                    await self._run_worktree_command(
                        ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
                        cwd=wt_path,
                        timeout=15,
                    )
                ).strip()
            except KanbanCLIError:
                unpushed = "0"
            if unpushed in {"", "0"}:
                return None

            await self._run_worktree_command(
                ["git", "push", "-u", "origin", branch], cwd=wt_path, timeout=120
            )
            body = (
                "Recovered by Zoe after the Hermes implement worker left a complete diff "
                "but exited before committing/pushing or opening a PR (e.g. turn budget).\n\n"
                f"Kanban task: `{task_id}`\n"
                f"Branch: `{branch}`\n\n"
                "Downstream verify/review must validate."
            )
            pr_url = await self._pr_url_from_worktree(
                task_id, wt_path, issue=issue, row=row, body=body
            )
            if not pr_url:
                return None
            logger.info(
                "kanban_adapter: salvaged unshipped implement diff for %s -> %s",
                task_id,
                pr_url,
            )
            return await self._complete_recovered_pr_handoff(
                task_id,
                row,
                pr_url,
                branch=branch,
                recovery="unshipped_diff_salvage",
                summary_note="Recovered PR after worker left an unshipped implement diff",
            )
        except Exception as exc:  # noqa: BLE001 - recovery must not break normal poll.
            logger.warning(
                "kanban_adapter: unshipped-diff recovery failed for %s: %s", task_id, exc
            )
            return None

    async def _maybe_converge_noop_implement(
        self,
        task_id: str,
        phase: str,
        row: dict[str, Any],
        *,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        """Converge a no-op implement (empty worktree, missing-PR gate) to ALREADY_COVERED.

        ``_maybe_recover_unshipped_diff`` salvages a *non-empty* diff the worker
        never pushed. This handles the other case observed live (ZOE-5856): the fix
        was already present on the base branch, so implement produced NO diff, then
        completed without a PR -> the evidence gate blocks on missing ``pr`` and the
        single lane strands on a permanent gate block. When the task worktree is
        provably empty (clean tree, nothing unpushed) there is nothing to ship, so
        re-block with ``BLOCKER=ALREADY_COVERED`` and let pipeline_store converge it
        via the proven skip_implementation -> audit path (no PR required).

        Safety: only fires for an implement row blocked specifically on the
        missing-PR evidence gate (never real blockers like TEST_ENVIRONMENT /
        IMPLEMENT_BUDGET / a dirty or unpushed tree), and only inside the task's own
        ``wt/<task_id>`` branch. Gated by ZOE_KANBAN_CONVERGE_NOOP_IMPLEMENT.
        """
        if not _CONVERGE_NOOP_IMPLEMENT:
            return False
        if phase != "implement" or (row.get("status") or "").lower() != "blocked":
            return False
        low = str(row.get("block_reason") or "").lower()
        if "already_covered" in low:
            return False  # already on the convergence path
        # Strictly the missing-PR evidence gate, not a substantive blocker. Match
        # "pr" as a whole word (the gate joins the missing-evidence kinds, e.g.
        # "missing required evidence pr" or "...pr,tool") so reasons like
        # "process"/"approved"/"prior" never trip this.
        if not ("missing required evidence" in low and re.search(r"\bpr\b", low)):
            return False
        try:
            from worktree_bootstrap import worktree_branch, worktree_path

            if "PR_URL=" in latest_log_session(task_id, max_lines=120):
                return False  # worker did hand off a PR -> not a no-op
            task = detail.get("task") if isinstance((detail or {}).get("task"), dict) else {}
            wt_path = Path(
                row.get("workspace_path") or task.get("workspace_path") or worktree_path(task_id)
            ).expanduser()
            if not wt_path.exists():
                return False
            branch = (
                await self._run_worktree_command(
                    ["git", "branch", "--show-current"], cwd=wt_path, timeout=10
                )
            ).strip()
            # Hard safety gate: only the task's own worktree branch.
            if not branch or branch in {"main", "master"} or branch != worktree_branch(task_id):
                return False
            dirty = bool(
                (
                    await self._run_worktree_command(
                        ["git", "status", "--porcelain"], cwd=wt_path, timeout=20
                    )
                ).strip()
            )
            if dirty:
                return False  # there IS uncommitted work -> unshipped-diff salvage owns it
            unpushed = (
                await self._run_worktree_command(
                    ["git", "rev-list", "--count", "HEAD", "--not", "--remotes"],
                    cwd=wt_path,
                    timeout=15,
                )
            ).strip()
            if unpushed not in {"", "0"}:
                return False  # commits to push -> not a no-op
        except Exception as exc:  # noqa: BLE001 - convergence check must not break poll.
            logger.warning(
                "kanban_adapter: no-op implement convergence check failed for %s: %s",
                task_id,
                exc,
            )
            return False

        reason = (
            "BLOCKER=ALREADY_COVERED: implement produced no diff and the task worktree "
            "is empty (no changes, nothing to push) — the work is already present on the "
            "base branch; converging to audit (no PR required)."
        )
        try:
            await self._run(["block", task_id, reason])
        except KanbanCLIError as exc:
            logger.warning(
                "kanban_adapter: no-op implement converge-block failed for %s: %s", task_id, exc
            )
            return False
        row["block_reason"] = reason
        logger.warning(
            "kanban_adapter: converged no-op implement %s to ALREADY_COVERED "
            "(empty worktree, missing-pr gate)",
            task_id,
        )
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
        existing_phases = await self._phases_for_ref(external_ref)
        plan = _chain_for_issue(issue)
        phase_order = [p for p, _, _ in plan]
        entry = next((plan_entry for plan_entry in plan if plan_entry[0] == phase), None)
        current_row = existing_phases.get(phase) or {}
        current_status = (current_row.get("status") or "").lower()
        if (
            state is not None
            and state.status == "todo"
            and (current_status == "blocked" or current_status in _TERMINAL_KANBAN_STATUSES)
        ):
            task_id = current_row.get("id")
            if task_id:
                try:
                    await self._run(["archive", str(task_id)])
                except KanbanCLIError as exc:
                    logger.warning(
                        "kanban_adapter: archive of stale %s task %s failed for %s: %s",
                        current_status,
                        task_id,
                        external_ref,
                        exc,
                    )
                    return {
                        "ok": False,
                        "external_ref": external_ref,
                        "reason": "stale phase archive failed",
                        "phase": phase,
                        "chain": {},
                        "created": [],
                        "mode": mode,
                    }
            existing_phases.pop(phase, None)
            current_row = {}
            current_status = ""
        can_adjust_stale_phase = bool(
            state is not None
            and phase_order
            and (
                state.status == "todo"
                or (
                    state.status == "running"
                    and (not current_row or current_status in _TERMINAL_KANBAN_STATUSES)
                )
            )
        )
        # Only adjust phases with no active effect. A stale running journal is
        # reset only when its Kanban row is absent/terminal; active rows stay
        # blocked for operator review.
        if entry is None and can_adjust_stale_phase:
            previous_phase = phase
            previous_status = state.status
            phase = phase_order[0]
            entry = next((plan_entry for plan_entry in plan if plan_entry[0] == phase), None)
            try:
                state = state.model_copy(update={"phase": phase, "status": "todo"})
                state = await asyncio.to_thread(
                    save_state,
                    state,
                    event="plan_adjusted",
                    extra={
                        "from_phase": previous_phase,
                        "from_status": previous_status,
                        "to_phase": phase,
                        "to_status": "todo",
                        "kanban_row_absent": not bool(current_row),
                        "terminal_kanban_status": current_status or None,
                        "reason": "current issue plan no longer includes inactive journal phase",
                    },
                )
            except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
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
        task_issue = _issue_with_phase_handoff(issue, phase, state)
        args = [
            "create",
            self._title(phase, identifier, task_issue),
            "--assignee",
            assignee,
            "--workspace",
            _workspace_for_phase(phase),
            "--idempotency-key",
            f"{external_ref}:{phase}",
            "--max-runtime",
            _max_runtime(mode),
            # Hermes trips the circuit breaker on the Nth failure; 1 means one
            # total attempt and zero automatic retries. Code-audit goal tasks get
            # one same-worktree continuation so a first turn that made a patch can
            # still commit/push instead of starting over.
            "--max-retries",
            _max_retries_for_phase(phase, issue),
            *_goal_mode_args(phase, issue),
            "--created-by",
            "zoe-bridge",
            "--body",
            self._build_body(phase, task_issue, identifier, mode=mode),
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
            from worktree_bootstrap import prepare_existing_pr_revision_worktree, prepare_kanban_worktree

            pr_url = _existing_pr_url(issue)
            worktree_failure_reason = "kanban worktree preparation failed"
            worktree_blocker = "BLOCKER=WORKTREE_PREPARATION_FAILED"
            try:
                if phase == "implement" and pr_url:
                    worktree_failure_reason = "existing PR worktree preparation failed"
                    worktree_blocker = "BLOCKER=PR_REVISION_CHECKOUT_FAILED"
                    await asyncio.to_thread(
                        prepare_existing_pr_revision_worktree,
                        str(task_id),
                        pr_url,
                    )
                else:
                    await asyncio.to_thread(prepare_kanban_worktree, str(task_id))
            except Exception as exc:  # noqa: BLE001
                reason = f"{worktree_blocker}: {exc}"
                logger.warning(
                    "kanban_adapter: worktree preparation failed for %s (%s): %s",
                    task_id,
                    phase,
                    exc,
                )
                try:
                    await self._run(["block", str(task_id), reason[:1000]])
                except KanbanCLIError as block_exc:
                    logger.warning(
                        "kanban_adapter: failed to block %s after worktree preparation failure: %s",
                        task_id,
                        block_exc,
                    )
                return {
                    "ok": False,
                    "external_ref": external_ref,
                    "reason": worktree_failure_reason,
                    "phase": phase,
                    "chain": {phase: task_id},
                    "created": created,
                    "mode": mode,
                    "ready_phase_only": True,
                }
        if state is not None and state.status == "todo":
            try:
                running = transition(state, "start")
                await asyncio.to_thread(
                    save_state,
                    running,
                    event="effect_requested",
                    extra={"phase": phase, "task_id": task_id},
                )
            except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
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

    async def poll(self, external_ref: str, *, issue: dict | None = None) -> dict:
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

        if issue:
            try:
                from pipeline_store import load_latest_state, pipeline_summary

                existing_state = await asyncio.to_thread(load_latest_state, external_ref)
                expected_phase_names = {entry[0] for entry in _chain_for_issue(issue)}
                if existing_state is not None and existing_state.status == "todo":
                    stale_executor_phase = None
                    stale_executor_status = None
                    if existing_state.phase not in expected_phase_names:
                        stale_executor_phase = existing_state.phase
                        phases = {
                            phase: row
                            for phase, row in phases.items()
                            if phase in expected_phase_names
                        }
                    else:
                        current_phase = str(existing_state.phase or "")
                        current_row = phases.get(current_phase) or {}
                        current_status = (current_row.get("status") or "").lower()
                        already_covered = _already_covered_row(current_phase, current_row)
                        if (
                            current_status == "blocked"
                            and not already_covered
                            and current_phase == "implement"
                            and current_row.get("id")
                        ):
                            try:
                                detail = await self._run(
                                    ["show", str(current_row.get("id")), "--json"],
                                    expect_json=True,
                                )
                                already_covered = _already_covered_detail(current_phase, detail)
                            except KanbanCLIError:
                                already_covered = False
                        keep_terminal_for_sync = (
                            current_phase == "closeout"
                            and current_status in _TERMINAL_KANBAN_STATUSES
                            and getattr(existing_state, "evidence_profile", "") == "audit"
                        )
                        if (
                            current_status == "blocked"
                            and not already_covered
                        ) or (
                            current_status in _TERMINAL_KANBAN_STATUSES
                            and not keep_terminal_for_sync
                        ):
                            stale_executor_phase = current_phase
                            stale_executor_status = current_status
                            phases = {
                                phase: row
                                for phase, row in phases.items()
                                if phase != stale_executor_phase
                            }
                    if stale_executor_phase and (
                        stale_executor_status == "blocked"
                        or stale_executor_status in _TERMINAL_KANBAN_STATUSES
                        or not phases
                    ):
                        pipeline = {
                            **pipeline_summary(existing_state),
                            "stale_executor_phase": stale_executor_phase,
                        }
                        if stale_executor_status:
                            pipeline["stale_executor_status"] = stale_executor_status
                        return {
                            "found": True,
                            "status": "partial",
                            "phases": {phase: (row.get("status") or "") for phase, row in phases.items()},
                            "pr_url": None,
                            "blocker": None,
                            "pipeline": pipeline,
                        }
            except Exception as exc:
                logger.debug("kanban_adapter: stale phase filter skipped for %s: %s", external_ref, exc)

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
            detail = _with_recovered_log_budget(task_id, phase, detail)
            detail_cache[task_id] = detail
            if await self._maybe_auto_block_phase_budget(task_id, phase, row, detail):
                phases[phase] = row
                continue
            if await self._maybe_auto_block_protocol_violation(task_id, phase, row, detail):
                phases[phase] = row
                continue
            if await self._maybe_auto_block_dead_worker(task_id, phase, row, detail):
                phases[phase] = row

        for phase, row in list(phases.items()):
            if phase != "implement" or (row.get("status") or "").lower() != "blocked":
                continue
            task_id = row.get("id")
            if not task_id:
                continue
            detail = detail_cache.get(task_id)
            if detail is None:
                try:
                    detail = await self._run(["show", task_id, "--json"], expect_json=True)
                    detail = _with_recovered_log_budget(task_id, phase, detail)
                except KanbanCLIError:
                    detail = {}
                detail_cache[task_id] = detail
            pr_url = await self._maybe_recover_pushed_pr(
                task_id,
                phase,
                row,
                issue=issue,
                detail=detail,
            )
            if not pr_url:
                # Worker left a finished diff but never committed/pushed (e.g. ran
                # out of turns before shipping): commit + push it and open a PR so
                # the work is salvaged instead of discarded by a fresh resume.
                pr_url = await self._maybe_recover_unshipped_diff(
                    task_id,
                    phase,
                    row,
                    issue=issue,
                    detail=detail,
                )
            if pr_url:
                detail["latest_summary"] = (
                    f"PR_URL={pr_url}\n"
                    "BLOCKER=\n"
                    "TESTS=recovered; downstream verify/review must validate\n"
                    "SUMMARY=Recovered PR handoff after worker interruption"
                )
                phases[phase] = row
            elif await self._maybe_converge_noop_implement(
                task_id, phase, row, detail=detail
            ):
                # No PR and an empty worktree: the work is already present, so
                # converge to ALREADY_COVERED rather than strand the lane.
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
                phase = next(
                    (phase for phase, row in phases.items() if row.get("id") == task_id),
                    "",
                )
                if phase:
                    detail = _with_recovered_log_budget(task_id, phase, detail)
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
                    blocker = pipeline_info.get("block_reason") or f"pipeline terminal block at {current_phase}"
                elif current_status == "blocked":
                    agg = "blocked"
                    current_row = phases.get(str(current_phase or ""), {})
                    blocker = (
                        pipeline_info.get("block_reason")
                        or current_row.get("block_reason")
                        or f"pipeline blocked at {current_phase}"
                    )
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
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
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

        if agg == "done":
            await self._cleanup_chain_worktrees(phases)

        return {
            "found": True,
            "status": agg,
            "phases": statuses,
            "pr_url": await self._extract_pr_url(phases, detail_cache=detail_cache),
            "blocker": blocker,
            "pipeline": pipeline_info,
        }

    async def _cleanup_chain_worktrees(self, phases: dict[str, dict]) -> None:
        """Remove task worktrees once a chain reaches terminal ``done``.

        Best-effort and idempotent: ``remove_task_worktree`` only acts when the
        task branch is merged (ancestor or squash-merged PR) and the worktree is
        clean, so repeated calls across polls are no-ops. Failures here must never
        affect reported chain status.
        """
        try:
            from worktree_bootstrap import remove_task_worktree, resolve_base_ref
        except Exception:  # noqa: BLE001 - cleanup is optional, never fatal.
            return
        try:
            # Resolve (and fetch) the base ref once per chain, not once per phase.
            base_ref = await asyncio.to_thread(resolve_base_ref)
        except Exception as exc:  # noqa: BLE001 - skip cleanup, never fatal.
            logger.warning("kanban_adapter: base ref resolve failed, skipping cleanup: %s", exc)
            return
        seen: set[str] = set()
        for row in phases.values():
            task_id = str(row.get("id") or "").strip()
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            try:
                await asyncio.to_thread(remove_task_worktree, task_id, base_ref=base_ref)
            except Exception as exc:  # noqa: BLE001 - log and continue.
                logger.warning(
                    "kanban_adapter: worktree cleanup failed for %s: %s", task_id, exc
                )

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
