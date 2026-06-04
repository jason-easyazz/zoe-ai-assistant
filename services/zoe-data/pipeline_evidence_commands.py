"""Command helpers for marker-backed Zoe engineering evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from pipeline_evidence import EvidenceItem, content_hash, transition, with_evidence
from pipeline_store import load_latest_state, save_state


def _read_text(value: str | None, *, file_path: str | None) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8", errors="replace")
    if value:
        return value
    if not sys.stdin.isatty():
        try:
            return sys.stdin.read()
        except OSError:
            return ""
    return ""


def _load_state(task_ref: str):
    state = load_latest_state(task_ref)
    if not state:
        raise SystemExit(f"No pipeline state found for {task_ref}")
    return state


def record_evidence(
    task_ref: str,
    *,
    kind: str,
    summary: str,
    passed: bool,
    command: str | None = None,
    artifact: str | None = None,
    body: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a command-written evidence item to a pipeline state."""
    state = _load_state(task_ref)
    item = EvidenceItem(
        kind=kind,  # type: ignore[arg-type]
        summary=summary,
        command=command,
        artifact=artifact,
        content_hash=content_hash(body or summary),
        passed=passed,
        metadata={"source": "command", "phase": state.phase, **(metadata or {})},
    )
    state = with_evidence(state, item)
    saved = save_state(state, event=f"evidence_{kind}", extra={"summary": summary, "passed": passed})
    return {"ok": True, "task_ref": task_ref, "phase": saved.phase, "status": saved.status, "evidence": item.model_dump()}


def _cmd_mark_tested(args: argparse.Namespace) -> dict[str, Any]:
    output = _read_text(args.output, file_path=args.output_file)
    passed = bool(args.passed)
    summary = args.summary or ("tests passed" if passed else "tests failed")
    return record_evidence(
        args.task_ref,
        kind="test",
        summary=summary,
        passed=passed,
        command=args.command,
        artifact=args.artifact,
        body=output or summary,
    )


def _cmd_mark_reviewed(args: argparse.Namespace) -> dict[str, Any]:
    critical = int(args.critical_count or 0)
    passed = critical == 0 and not args.failed
    summary = args.summary or ("review passed" if passed else f"review blocked: {critical} critical")
    return record_evidence(
        args.task_ref,
        kind="human",
        summary=summary,
        passed=passed,
        artifact=args.artifact,
        body=summary,
        metadata={"critical_count": critical},
    )


def _cmd_mark_greptile(args: argparse.Namespace) -> dict[str, Any]:
    output = _read_text(args.output, file_path=args.output_file)
    score = str(args.score or "").strip()
    passed = score == "5/5" and not args.failed
    summary = args.summary or f"Greptile {score or 'review'} {'passed' if passed else 'not ready'}"
    return record_evidence(
        args.task_ref,
        kind="greptile",
        summary=summary,
        passed=passed,
        artifact=args.artifact,
        body=output or summary,
        metadata={"score": score},
    )


async def _cmd_split_ticket(args: argparse.Namespace) -> dict[str, Any]:
    from multica_client import get_multica_client
    from multica_ticket_contract import update_ticket_progress

    client = get_multica_client()
    if not client.is_configured():
        raise SystemExit("Multica is not configured")
    parent = await client.get_issue(args.parent_issue_id)
    if not parent:
        raise SystemExit(f"Parent issue not found: {args.parent_issue_id}")
    packet = json.loads(_read_text(args.packet, file_path=args.packet_file) or "{}")
    templates = packet.get("children") or [packet.get("child_issue_template") or {}]
    children = []
    for template in templates:
        if not isinstance(template, dict):
            continue
        child = await client.create_child_issue(parent, template)
        if child.get("id"):
            children.append(child)
    state = load_latest_state(args.task_ref) if args.task_ref else None
    if state:
        state = state.model_copy(update={"block_classification": "scope_split_required", "split_packet": packet})
        state = transition(state, "block", reason=args.reason or "scope_split_required")
        save_state(state, event="split_ticket", extra={"parent_issue_id": args.parent_issue_id, "children": children})
    child_ids = [str(child.get("id")) for child in children if child.get("id")]
    if not child_ids:
        return {
            "ok": False,
            "parent_issue_id": args.parent_issue_id,
            "child_issue_ids": [],
            "reason": "no child issues created",
        }
    await client.update_issue(
        args.parent_issue_id,
        status="blocked",
        description=update_ticket_progress(
            parent.get("description") or "",
            blocker=args.reason or packet.get("reason") or "scope split required",
            child_issue_ids=child_ids,
        ),
    )
    return {"ok": True, "parent_issue_id": args.parent_issue_id, "child_issue_ids": child_ids}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write Zoe engineering evidence markers.")
    sub = parser.add_subparsers(dest="command_name", required=True)

    tested = sub.add_parser("mark-tested")
    tested.add_argument("task_ref")
    tested.add_argument("--summary")
    tested.add_argument("--command")
    tested.add_argument("--artifact")
    tested.add_argument("--output")
    tested.add_argument("--output-file")
    tested_result = tested.add_mutually_exclusive_group(required=True)
    tested_result.add_argument("--passed", action="store_true")
    tested_result.add_argument("--failed", action="store_true")
    tested.set_defaults(func=_cmd_mark_tested)

    reviewed = sub.add_parser("mark-reviewed")
    reviewed.add_argument("task_ref")
    reviewed.add_argument("--summary")
    reviewed.add_argument("--critical-count", type=int, default=0)
    reviewed.add_argument("--artifact")
    reviewed.add_argument("--failed", action="store_true")
    reviewed.set_defaults(func=_cmd_mark_reviewed)

    greptile = sub.add_parser("mark-greptile")
    greptile.add_argument("task_ref")
    greptile.add_argument("--score")
    greptile.add_argument("--summary")
    greptile.add_argument("--artifact")
    greptile.add_argument("--output")
    greptile.add_argument("--output-file")
    greptile.add_argument("--failed", action="store_true")
    greptile.set_defaults(func=_cmd_mark_greptile)

    split = sub.add_parser("split-ticket")
    split.add_argument("parent_issue_id")
    split.add_argument("--task-ref")
    split.add_argument("--packet")
    split.add_argument("--packet-file")
    split.add_argument("--reason")
    split.set_defaults(func=_cmd_split_ticket)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = args.func(args)
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
