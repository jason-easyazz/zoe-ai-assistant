"""Structured Zoe ticket metadata embedded in Multica issue descriptions."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

FENCE_LANG = "zoe-ticket"
SCHEMA_VERSION = 1

# Default live checkout root. Tickets that hardcode this absolute path steer the
# worker out of its pinned, isolated worktree and trip the runtime
# WORKTREE_PATH_VIOLATION guard after budget is already burned, so we lint for it
# at dispatch instead.
DEFAULT_LIVE_ROOT = "/home/zoe/assistant"


def _live_root(live_root: str | None = None) -> str:
    root = (live_root or os.environ.get("ZOE_ASSISTANT_ROOT") or DEFAULT_LIVE_ROOT).rstrip("/")
    return root or DEFAULT_LIVE_ROOT

_BLOCK_RE = re.compile(
    r"(?P<prefix>^|\n)```zoe-ticket\s*\n(?P<body>.*?)\n```(?P<suffix>\n|$)",
    re.DOTALL,
)


DEFAULT_TICKET: dict[str, Any] = {
    "schema": SCHEMA_VERSION,
    "zoe_kind": "operator_task",
    "evidence_profile": "code",
    "engineering_mode": "interactive",
    "acceptance_criteria": [],
    "evidence_expectations": [],
    "parent_issue_id": None,
    "child_issue_ids": [],
    "blocked_reason": None,
    "pr_url": None,
    "merge_sha": None,
    "greptile_status": None,
    "phase": None,
    "last_evidence": None,
    "completion_reason": None,
}


def parse_ticket_block(description: str | None) -> dict[str, Any]:
    """Return parsed Zoe metadata from a Multica description, if present."""
    text = description or ""
    match = _BLOCK_RE.search(text)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group("body"))
    except json.JSONDecodeError:
        return {"schema": SCHEMA_VERSION, "parse_error": "invalid_json"}
    return parsed if isinstance(parsed, dict) else {}


def normalize_ticket_metadata(metadata: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Merge caller metadata into the stable Zoe ticket schema."""
    ticket = dict(DEFAULT_TICKET)
    for key, value in (metadata or {}).items():
        if value is not None:
            ticket[key] = value
    for key, value in overrides.items():
        if value is not None:
            ticket[key] = value
    ticket["schema"] = SCHEMA_VERSION
    ticket.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
    return ticket


def write_ticket_block(description: str | None, metadata: dict[str, Any]) -> str:
    """Replace only the fenced Zoe metadata block, preserving human prose."""
    text = (description or "").rstrip()
    block = "```zoe-ticket\n" + json.dumps(metadata, sort_keys=True, indent=2) + "\n```"
    match = _BLOCK_RE.search(text)
    if match:
        start, end = match.span()
        prefix = text[:start].rstrip()
        suffix = text[end:]
        suffix = ("\n" + suffix) if suffix and not suffix.startswith("\n") else suffix
        separator = "\n\n" if prefix else ""
        return f"{prefix}{separator}{block}{suffix}"
    if not text:
        return block
    return f"{text}\n\n{block}"


def describe_ticket(
    human_description: str,
    *,
    zoe_kind: str = "operator_task",
    evidence_profile: str = "code",
    engineering_mode: str = "interactive",
    acceptance_criteria: list[str] | None = None,
    evidence_expectations: list[str] | None = None,
    source: str | None = None,
    parent_issue_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Build a Multica description with preserved prose plus Zoe metadata."""
    metadata = normalize_ticket_metadata(
        metadata,
        **{
            "zoe_kind": zoe_kind,
            "evidence_profile": evidence_profile,
            "engineering_mode": engineering_mode,
            "acceptance_criteria": acceptance_criteria or [],
            "evidence_expectations": evidence_expectations or [],
            "source": source,
            "parent_issue_id": parent_issue_id,
        }
    )
    return write_ticket_block(human_description, metadata)


def update_ticket_progress(
    description: str | None,
    *,
    phase: str | None = None,
    evidence: str | None = None,
    pr_url: str | None = None,
    blocker: str | None = None,
    clear_blocker: bool = False,
    greptile_status: str | None = None,
    merge_sha: str | None = None,
    child_issue_ids: list[str] | None = None,
    dispatch_approved: bool | None = None,
    completion_reason: str | None = None,
) -> str:
    """Patch progress fields inside the Zoe block without touching prose."""
    current = parse_ticket_block(description)
    metadata = normalize_ticket_metadata(current)
    if phase is not None:
        metadata["phase"] = phase
    if evidence is not None:
        metadata["last_evidence"] = evidence
    if pr_url is not None:
        metadata["pr_url"] = pr_url
    if clear_blocker:
        metadata["blocked_reason"] = None
    elif blocker is not None:
        metadata["blocked_reason"] = blocker
    if greptile_status is not None:
        metadata["greptile_status"] = greptile_status
    if merge_sha is not None:
        metadata["merge_sha"] = merge_sha
    if child_issue_ids is not None:
        metadata["child_issue_ids"] = child_issue_ids
    if dispatch_approved is not None:
        metadata["dispatch_approved"] = dispatch_approved
    if completion_reason is not None:
        metadata["completion_reason"] = completion_reason
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    return write_ticket_block(description, metadata)


def append_child_id(description: str | None, child_id: str) -> str:
    """Add a child issue ID to the Zoe metadata block exactly once."""
    current = parse_ticket_block(description)
    metadata = normalize_ticket_metadata(current)
    children = [str(item) for item in metadata.get("child_issue_ids") or []]
    if child_id not in children:
        children.append(child_id)
    metadata["child_issue_ids"] = children
    metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
    return write_ticket_block(description, metadata)


@lru_cache(maxsize=8)
def _live_path_regex(live_root: str) -> re.Pattern[str]:
    # Match the live root and any path beneath it, stopping at whitespace or
    # shell/markdown delimiters so we capture a single path token cleanly. The
    # boundary lookahead prevents matching a sibling like ``<root>-backup`` or
    # ``<root>.bak`` whose name merely shares the root as a string prefix.
    return re.compile(re.escape(live_root) + r"(?![\w.\-])(?:/[^\s'\"`)\]>,;]*)?")


def _split_off_metadata_block(text: str) -> tuple[str, str, str]:
    """Return (before, block, after) splitting out the fenced Zoe block.

    The fenced metadata block is machine-managed JSON; live-path linting and
    rewriting must operate on the human prose only, never inside the block.
    """
    match = _BLOCK_RE.search(text)
    if not match:
        return text, "", ""
    start, end = match.span()
    return text[:start], text[start:end], text[end:]


def find_live_path_references(
    description: str | None, *, live_root: str | None = None
) -> list[str]:
    """Return unique live-checkout absolute paths referenced in the ticket text.

    These are the WORKTREE_PATH_VIOLATION class: any instruction that points the
    worker at the shared live checkout instead of its pinned worktree. Only the
    human prose is scanned; the fenced metadata block is machine-managed JSON
    (e.g. a ``blocked_reason`` may legitimately quote an offending path) and is
    skipped to avoid false positives.
    """
    root = _live_root(live_root)
    before, _block, after = _split_off_metadata_block(description or "")
    seen: dict[str, None] = {}
    for prose in (before, after):
        for match in _live_path_regex(root).finditer(prose):
            # Trim a trailing slash and any sentence punctuation the regex swept up.
            token = match.group(0).rstrip("/.,;:")
            seen.setdefault(token, None)
    return list(seen)


def normalize_live_paths(description: str | None, *, live_root: str | None = None) -> str:
    """Rewrite hardcoded live-checkout paths to worktree-relative equivalents.

    ``<root>/services/x.py`` becomes ``services/x.py`` and a bare ``<root>``
    (e.g. ``cd <root>``) becomes ``.`` so the same prose works inside any pinned
    worktree. Only the human prose is rewritten; the fenced metadata block is
    left byte-for-byte intact so stored JSON semantics are never altered.
    """
    root = _live_root(live_root)
    regex = _live_path_regex(root)

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        trailing = ""
        # Preserve a trailing slash if the original had one.
        if token.endswith("/") and token != root + "/":
            token, trailing = token.rstrip("/"), "/"
        if token == root:
            return "." + trailing
        relative = token[len(root) + 1 :]  # drop "<root>/"
        return (relative or ".") + trailing

    before, block, after = _split_off_metadata_block(description or "")
    return regex.sub(_replace, before) + block + regex.sub(_replace, after)


def validate_ticket_contract(
    description: str | None,
    *,
    live_root: str | None = None,
) -> dict[str, Any]:
    """Lint a ticket for dispatch-blocking contract violations.

    Currently enforces the WORKTREE_PATH_VIOLATION class: a ticket must not pin
    the worker to the shared live checkout. Returns a structured result so the
    admission gate can fail closed and an operator/auto-fixer can normalize.

    Returns ``{"ok", "violations", "normalized_description"}`` where
    ``normalized_description`` is provided only when a safe rewrite exists.
    """
    root = _live_root(live_root)
    violations: list[str] = []

    live_paths = find_live_path_references(description, live_root=root)
    for path in live_paths:
        violations.append(
            "WORKTREE_PATH_VIOLATION: ticket references live checkout path "
            f"{path}; use a worktree-relative path so the worker stays in its "
            "pinned isolated worktree"
        )

    result: dict[str, Any] = {"ok": not violations, "violations": violations}
    if live_paths:
        result["normalized_description"] = normalize_live_paths(description, live_root=root)
    return result
