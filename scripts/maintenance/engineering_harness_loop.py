#!/usr/bin/env python3
"""Deterministic engineering harness: tests, validators, pipeline findings.

Runs focused pytest + structure validators, optionally health and Kanban dispatch
checks, parses recent pipeline JSONL for fail-closed patterns, and appends a
report for greploop / human / agent follow-up. Does not auto-fix code.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

FOCUSED_TESTS = [
    "services/zoe-data/tests/test_kanban_adapter.py",
    "services/zoe-data/tests/test_pipeline_handoff.py",
    "services/zoe-data/tests/test_pipeline_evidence.py",
    "services/zoe-data/tests/test_pipeline_store.py",
    "services/zoe-data/tests/test_engineering_harness_loop.py",
    "services/zoe-data/tests/test_worktree_bootstrap.py",
    "services/zoe-data/tests/test_background_runner.py",
    "services/zoe-data/tests/test_multica_poll_dispatch.py",
    "services/zoe-data/tests/test_greploop_guard.py",
]

CRITICAL_EVENTS = frozenset({"gate_blocked", "fingerprint_abort", "scope_split_required"})
CRITICAL_BLOCK_REASONS = frozenset({"WORKTREE_NOT_READY"})

DEFAULT_PIPELINE_TAIL = 200
DEFAULT_REPORT_PATH = Path.home() / ".zoe" / "harness_loop_report.jsonl"


def _pipeline_store_path() -> Path:
    override = os.environ.get("ZOE_PIPELINE_STORE_PATH", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".zoe" / "engineering_pipeline_runs.jsonl"


def _run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = 300,
) -> dict[str, Any]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd or ROOT,
            env=merged,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        proc = exc
        timed_out = True
    return {
        "cmd": cmd,
        "ok": (not timed_out) and proc.returncode == 0,
        "exit_code": getattr(proc, "returncode", -1),
        "stdout": (proc.stdout or "")[-4000:] if getattr(proc, "stdout", None) else "",
        "stderr": (proc.stderr or "")[-4000:] if getattr(proc, "stderr", None) else "",
        "timed_out": timed_out,
    }


def run_pytest() -> dict[str, Any]:
    cmd = [sys.executable, "-m", "pytest", *FOCUSED_TESTS, "-q"]
    result = _run_command(cmd, timeout=300)
    result["step"] = "pytest"
    return result


def run_validators() -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for rel in ("tools/audit/validate_structure.py", "tools/audit/validate_critical_files.py"):
        step = _run_command([sys.executable, rel], timeout=120)
        step["step"] = rel
        steps.append(step)
    ok = all(s["ok"] for s in steps)
    return {"step": "validators", "ok": ok, "substeps": steps}


def run_health(url: str = "http://127.0.0.1:8000/health") -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read(512).decode("utf-8", errors="replace")
        return {"step": "health", "ok": True, "url": url, "body": body[:200]}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"step": "health", "ok": False, "url": url, "error": str(exc)}


def _is_test_task_ref(task_ref: str) -> bool:
    """Exclude pytest fixture refs accidentally written to the operator store."""
    ref = task_ref.strip()
    if ref in {"multica:u", "multica:"}:
        return True
    if ref.startswith("multica:uuid-"):
        return True
    return False


def parse_pipeline_findings(*, tail_lines: int = DEFAULT_PIPELINE_TAIL) -> list[dict[str, Any]]:
    path = _pipeline_store_path()
    if not path.is_file():
        return [{"kind": "pipeline_store_missing", "path": str(path)}]

    # Stream into a bounded deque: only the last `tail_lines` are ever wanted, and
    # read() would pull the whole event-sourced store into RAM to throw nearly all
    # of it away (it reached 1.59 GB before the compactor existed). Memory here is
    # now O(tail_lines) regardless of file size.
    with path.open("r", encoding="utf-8") as handle:
        tail = deque(handle, maxlen=tail_lines)
    recent = [ln for ln in (line.rstrip("\n") for line in tail) if ln.strip()]

    findings: list[dict[str, Any]] = []
    gate_counter: Counter[tuple[str, str]] = Counter()

    for line in recent:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            findings.append({"kind": "json_decode_error", "line_preview": line[:120]})
            continue

        event = str(row.get("event") or "")
        task_ref = str(row.get("task_ref") or "")
        if _is_test_task_ref(task_ref):
            continue
        phase = str(row.get("phase") or row.get("meta", {}).get("row_phase") or "")
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        status = str(row.get("status") or state.get("status") or "")

        if event in CRITICAL_EVENTS:
            findings.append(
                {
                    "kind": event,
                    "task_ref": task_ref,
                    "phase": phase,
                    "meta": row.get("meta"),
                }
            )
        if (
            event not in CRITICAL_EVENTS
            and isinstance(state, dict)
            and state.get("block_classification") == "scope_split_required"
        ):
            findings.append(
                {
                    "kind": "scope_split_required",
                    "task_ref": task_ref,
                    "phase": phase,
                    "split_packet": state.get("split_packet"),
                }
            )

        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        reason = str(meta.get("reason") or "")
        active_reason_parts = [reason, str(meta.get("block_reason") or "")]
        if isinstance(state, dict) and status == "blocked":
            active_reason_parts.append(str(state.get("block_reason") or ""))
        active_blob = " ".join(part for part in active_reason_parts if part)
        if reason in CRITICAL_BLOCK_REASONS or any(
            token in active_blob for token in CRITICAL_BLOCK_REASONS
        ):
            findings.append(
                {
                    "kind": "critical_block_reason",
                    "reason": reason or "WORKTREE_NOT_READY",
                    "task_ref": task_ref,
                    "phase": phase,
                }
            )

        block_reason = None
        if isinstance(state, dict):
            block_reason = state.get("block_reason")
            if not block_reason and status == "blocked":
                history = state.get("history") if isinstance(state.get("history"), list) else []
                for rec in reversed(history):
                    if isinstance(rec, dict) and rec.get("reason"):
                        block_reason = rec.get("reason")
                        break
        if status == "blocked" and not block_reason and not meta.get("reason") and not meta.get("block_reason"):
            findings.append(
                {
                    "kind": "block_reason_null",
                    "task_ref": task_ref,
                    "phase": phase,
                    "event": event,
                }
            )

        if event == "gate_blocked":
            gate_counter[(task_ref, phase)] += 1

    for (task_ref, phase), count in gate_counter.items():
        if count >= 5:
            findings.append(
                {
                    "kind": "gate_blocked_repeated",
                    "task_ref": task_ref,
                    "phase": phase,
                    "count": count,
                }
            )

    return findings


def run_kanban_dispatch(*, dry_run: bool, limit: int = 1, skip_scout: bool = False) -> dict[str, Any]:
    cmd = [sys.executable, "scripts/maintenance/sync_multica_to_kanban.py", "--limit", str(limit)]
    if dry_run:
        cmd.append("--dry-run")
    env = {"ZOE_KANBAN_SKIP_SCOUT": "1"} if skip_scout else None
    result = _run_command(cmd, env=env, timeout=120)
    result["step"] = "kanban_dry" if dry_run else "kanban_live"
    result["skip_scout"] = skip_scout
    return result


def monitor_pipeline(*, task_ref_prefix: str | None, seconds: int = 90) -> dict[str, Any]:
    path = _pipeline_store_path()
    start_size = path.stat().st_size if path.is_file() else 0
    deadline = time.time() + seconds
    seen: list[dict[str, Any]] = []

    while time.time() < deadline:
        time.sleep(5)
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size <= start_size:
            continue
        with path.open("r", encoding="utf-8") as handle:
            handle.seek(start_size)
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if task_ref_prefix and not str(row.get("task_ref") or "").startswith(task_ref_prefix):
                    continue
                seen.append({"event": row.get("event"), "phase": row.get("phase"), "task_ref": row.get("task_ref")})
        start_size = size

    return {"step": "monitor", "ok": True, "events": seen[-20:]}


def build_report(
    *,
    mode: str,
    steps: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    tests_ok = True
    health_ok = True
    kanban_ok = True
    for step in steps:
        if step.get("step") == "pytest":
            tests_ok = tests_ok and step.get("ok", False)
        elif step.get("step") == "validators":
            tests_ok = tests_ok and step.get("ok", False)
        elif step.get("step") == "health":
            health_ok = step.get("ok", False)
        elif step.get("step") in {"kanban_dry", "kanban_live"}:
            kanban_ok = step.get("ok", False)

    critical = [
        f
        for f in findings
        if f.get("kind")
        in {
            "fingerprint_abort",
            "scope_split_required",
            "critical_block_reason",
            "block_reason_null",
            "gate_blocked_repeated",
        }
    ]
    gate_blocked_count = sum(1 for f in findings if f.get("kind") == "gate_blocked")

    steps_ok = tests_ok and health_ok and kanban_ok
    return {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "pass": steps_ok and not critical,
        "tests_ok": tests_ok,
        "health_ok": health_ok,
        "kanban_ok": kanban_ok,
        "gate_blocked_count": gate_blocked_count,
        "findings": findings,
        "critical_count": len(critical),
        "critical_findings": critical[:20],
        "steps": [{k: v for k, v in s.items() if k not in {"stdout", "stderr"}} for s in steps],
    }


def append_report(report: dict[str, Any], path: Path = DEFAULT_REPORT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report, sort_keys=True) + "\n")


def read_latest_report(path: Path = DEFAULT_REPORT_PATH) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    last: dict[str, Any] | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                continue
    return last


def run_iteration(args: argparse.Namespace) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    if args.mode != "report":
        steps.append(run_pytest())
        if args.mode not in {"smoke"}:
            steps.append(run_validators())

    if args.mode == "full":
        steps.append(run_health())
    elif args.mode in {"kanban-dry", "kanban-live"}:
        if args.health:
            steps.append(run_health())
        steps.append(run_kanban_dispatch(dry_run=args.mode == "kanban-dry", skip_scout=args.skip_scout))
        if args.mode == "kanban-live":
            steps.append(monitor_pipeline(task_ref_prefix=args.task_ref_prefix, seconds=args.monitor_seconds))

    findings = parse_pipeline_findings(tail_lines=args.pipeline_tail) if args.pipeline else []
    report = build_report(mode=args.mode, steps=steps, findings=findings)
    append_report(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("smoke", "kanban-dry", "kanban-live", "report", "full"),
        default="full",
        help="smoke=pytest only; kanban-dry=dispatch dry-run; kanban-live=one live dispatch; report=latest JSON; full=pytest+validators+health+findings",
    )
    parser.add_argument("--health", action="store_true", help="Include curl /health check")
    parser.add_argument("--skip-scout", action="store_true", help="Set ZOE_KANBAN_SKIP_SCOUT for kanban modes")
    parser.add_argument("--pipeline-tail", type=int, default=DEFAULT_PIPELINE_TAIL)
    parser.add_argument("--monitor-seconds", type=int, default=90)
    parser.add_argument("--task-ref-prefix", default="multica:", help="Filter live monitor to task_ref prefix")
    parser.add_argument(
        "--no-pipeline",
        action="store_true",
        help="Skip pipeline JSONL scan (smoke-only quick run)",
    )
    args = parser.parse_args()
    args.pipeline = not args.no_pipeline

    if args.mode == "report":
        latest = read_latest_report()
        if latest is None:
            print(json.dumps({"error": "no reports yet"}, indent=2))
            return 1
        print(json.dumps(latest, indent=2, sort_keys=True))
        return 0 if latest.get("pass") else 2

    if args.mode == "full":
        args.health = True

    report = run_iteration(args)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("pass") else 2


if __name__ == "__main__":
    raise SystemExit(main())
